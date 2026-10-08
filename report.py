#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rapport horaire par e-mail du bot paper-trading : état du portefeuille,
P&L vs hold-SOL, positions ouvertes, trades des dernières 24 h.

Envoi via Brevo (plan gratuit, 300 mails/jour) avec la clé stockée dans le
secret GitHub BREVO_API_KEY. Sans clé : message clair dans le log, exit 0
(pas de notifications d'échec GitHub).

Usage : python report.py   (appelé par le workflow email.yml toutes les heures)
"""
import html
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta

from bot import SOL, P, load_state, tokens_info

MAIL_TO = os.environ.get("MAIL_TO", "t0avina@proton.me")
MAIL_FROM = os.environ.get("MAIL_FROM", MAIL_TO)
KEY = os.environ.get("BREVO_API_KEY", "")
BREVO_URL = "https://api.brevo.com/v3/smtp/email"


def fmt_sol(v: float) -> str:
    return f"{v:.4f} SOL"


def build_report() -> tuple[str, str, str]:
    """Retourne (sujet, html, texte)."""
    st = load_state()
    infos = tokens_info(list(st["positions"]) + [SOL])
    sol_usd = (infos.get(SOL) or {}).get("usdPrice") or st.get("sol_usd") or 0.0

    pos_val = unrealized = 0.0
    rows, dead = [], []
    for mint, pos in st["positions"].items():
        if pos.get("dead"):
            dead.append(pos["symbol"])
            continue
        info = infos.get(mint) or {}
        if info.get("usdPrice") and sol_usd:
            val = pos["token_amount"] * info["usdPrice"] / sol_usd
        else:
            val = pos["cost_sol"]
        pos_val += val
        unrealized += val - pos["cost_sol"]
        gain = (val / pos["cost_sol"] - 1) * 100
        held_h = (time.time() - pos["opened_at"]) / 3600
        rows.append((pos["symbol"], pos["cost_sol"], val, gain,
                     pos["peak_mult"], held_h))
    total = st["sol"] + pos_val
    init = st.get("initial_sol") or total
    pnl_pct = (total / init - 1) * 100 if init else 0.0
    # hold-SOL : prix SOL du départ ≈ initial_usdc / initial_sol (fixé à la conversion)
    bh_pct = (sol_usd * init / st.get("initial_usdc", 1) - 1) * 100 if init else 0.0
    pnl_usd = total * sol_usd - st.get("initial_usdc", 0)

    # trades des dernières 24 h
    day = []
    try:
        cut = datetime.now() - timedelta(hours=24)
        with open("trades.jsonl") as f:
            for line in f:
                try:
                    t = json.loads(line)
                    ts = datetime.strptime(t["ts"], "%Y-%m-%d %H:%M:%S")
                    if ts >= cut and "token" in t:  # époque memecoins uniquement
                        day.append(t)
                except Exception:
                    continue
    except FileNotFoundError:
        pass

    emoji = "📈" if pnl_pct >= 0 else "📉"
    subject = (f"{emoji} Paper-Bot {total * sol_usd:.2f}$ ({pnl_pct:+.2f}%) "
               f"— {len(rows)} positions, {len(day)} trades/24h")

    # ---- HTML
    esc = html.escape
    def tr(cells, bold=False):
        tag = "th" if bold else "td"
        return "<tr>" + "".join(f"<{tag} style='padding:4px 10px;text-align:left'>{c}</{tag}>" for c in cells) + "</tr>"
    h = [f"<p>Portefeuille : <b>{total:.4f} SOL</b> ≈ <b>{total * sol_usd:.2f} $</b> "
         f"(départ {st.get('initial_usdc', 0):.0f} $) → <b style='color:{'green' if pnl_pct >= 0 else 'red'}'>"
         f"{pnl_pct:+.2f} %</b> vs départ · hold-SOL : {bh_pct:+.2f} %</p>",
         f"<p>SOL = {sol_usd:.2f} $ · Réalisé {st['realized_sol']:+.4f} SOL · "
         f"Latent {unrealized:+.4f} SOL · Frais {st['fees_sol_cum']:.4f} SOL"
         + (f" · 💀 mortes : {', '.join(esc(d) for d in dead)}" if dead else "") + "</p>",
         "<table border='1' cellpadding='0' cellspacing='0' style='border-collapse:collapse'>",
         tr(["Position", "Entrée", "Valeur", "P&L", "Pic", "Durée"], bold=True)]
    for sym, cost, val, gain, peak, held in rows:
        color = "green" if gain >= 0 else "red"
        h.append(tr([esc(sym), f"{cost:.3f}", f"{val:.3f}",
                     f"<span style='color:{color}'>{gain:+.1f} %</span>",
                     f"{peak:.2f}x", f"{held:.1f} h"]))
    h.append("</table>")
    if day:
        h.append("<p><b>Trades des dernières 24 h :</b></p><ul>")
        for t in reversed(day):
            tok = esc(str(t.get("token", "?")))
            if t.get("side") == "BUY":
                h.append(f"<li>🟢 BUY {tok} — {t.get('in_sol', t.get('amount_in', 0)):.3f} SOL ({esc(t['ts'])})</li>")
            else:
                color = "green" if t.get("pnl_sol", 0) >= 0 else "red"
                h.append(f"<li>🔴 SELL {tok} — {t.get('pnl_sol', 0):+.4f} SOL "
                         f"(<span style='color:{color}'>{esc(str(t.get('reason', '')))}</span>, {esc(t['ts'])})</li>")
        h.append("</ul>")
    else:
        h.append("<p>Aucun trade dans les dernières 24 h.</p>")

    # ---- texte brut
    lines = [f"Portefeuille : {total:.4f} SOL ≈ {total * sol_usd:.2f} $ "
             f"({pnl_pct:+.2f} % vs départ, hold-SOL {bh_pct:+.2f} %)",
             f"SOL = {sol_usd:.2f} $ | Réalisé {st['realized_sol']:+.4f} SOL | "
             f"Latent {unrealized:+.4f} SOL | Frais {st['fees_sol_cum']:.4f} SOL",
             "", "Positions :"]
    for sym, cost, val, gain, peak, held in rows:
        lines.append(f"  {sym:10} {cost:.3f} -> {val:.3f} SOL ({gain:+.1f} %, pic {peak:.2f}x, {held:.1f} h)")
    if dead:
        lines.append(f"  MORTES : {', '.join(dead)}")
    lines.append("")
    lines.append("Trades 24h : " + ("aucun" if not day else ""))
    for t in reversed(day):
        tok = t.get("token", "?")
        if t.get("side") == "BUY":
            lines.append(f"  BUY  {tok} {t.get('in_sol', t.get('amount_in', 0)):.3f} SOL ({t['ts']})")
        else:
            lines.append(f"  SELL {tok} {t.get('pnl_sol', 0):+.4f} SOL ({t.get('reason', '')}) ({t['ts']})")
    return subject, "<html><body>" + "".join(h) + "</body></html>", "\n".join(lines)


def send(subject: str, html_body: str, text: str) -> None:
    payload = {
        "sender": {"name": "Paper-Bot Memecoins", "email": MAIL_FROM},
        "to": [{"email": MAIL_TO}],
        "subject": subject,
        "htmlContent": html_body,
        "textContent": text,
    }
    req = urllib.request.Request(
        BREVO_URL, data=json.dumps(payload).encode(),
        headers={"api-key": KEY, "content-type": "application/json",
                 "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()
    print(f"mail envoyé à {MAIL_TO} : {subject}")


if __name__ == "__main__":
    if not KEY:
        print("BREVO_API_KEY absente : voir README section 'Rapport e-mail'. "
              "Run sans échec volontaire (pas de notifications GitHub).")
        raise SystemExit(0)
    subject, html_body, text = build_report()
    send(subject, html_body, text)
