#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot paper-trading memecoins Solana via Jupiter — SIMULATION UNIQUEMENT.
Aucune clé privée, aucun fonds réel. Prix et exécutions simulés avec de
vraies quotes Jupiter (lite-api) : si un token rug, la quote de sortie
s'effondre comme en vrai — le bot vit les rugs sans argent réel.

Univers de memecoins : API tokens v2 de Jupiter.
  source="organic" : toporganicScore 24h, filtré small-caps (mcap, liquidité,
                     momentum, audit mint/freeze, acheteurs réels).
  source="recent"  : tokens tout juste lancés (pump.fun...) — mode très risqué.

Stratégie momentum :
  entrée  : token small-cap qui monte (1h entre +4 % et +150 %), quote entrée
            avec price impact <= 2 %.
  sortie  : take-profit +40 %, stop-loss -20 %, trailing à partir de +15 %,
            sortie forcée après 6 h, position « morte » si le token devient
            inexitable (rug/illiquide).

Usage :
  python3 bot.py run     # boucle continue (mode service)
  python3 bot.py once    # un seul tick
  python3 bot.py status  # portefeuille + P&L vs benchmark hold-SOL
  python3 bot.py reset   # remise à zéro
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG = json.loads((BASE / "config.json").read_text())
P = CONFIG["params"]
STATE_F = BASE / "state.json"
TRADES_F = BASE / "trades.jsonl"
LOG_F = BASE / "bot.log"

SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
DEC = {SOL: 9, USDC: 6}
QUOTE_URL = "https://lite-api.jup.ag/swap/v1/quote"
TOK_URL = "https://lite-api.jup.ag/tokens/v2"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with open(LOG_F, "a") as f:
        f.write(line + "\n")
    print(line, flush=True)


def http_json(url: str, retries: int = 3):
    last = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json",
                                                       "User-Agent": "paper-bot/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last = e
            time.sleep(2)
    raise RuntimeError(f"API injoignable ({url.split('?')[0]} : {last})")


def quote(in_mint: str, out_mint: str, amount_int: int) -> dict:
    """Quote réelle Jupiter (frais de pool + price impact inclus dans outAmount)."""
    if amount_int <= 0:
        raise ValueError("montant <= 0")
    q = urllib.parse.urlencode({
        "inputMint": in_mint, "outputMint": out_mint,
        "amount": amount_int, "slippageBps": CONFIG["slippage_bps"],
        "restrictIntermediateTokens": "true",
    })
    return http_json(f"{QUOTE_URL}?{q}")


# ----------------------------------------------------------------- univers

def fetch_candidates() -> list[dict]:
    """Liste de tokens candidats selon la source configurée."""
    if CONFIG["source"] == "recent":
        toks = http_json(f"{TOK_URL}/recent")
    else:  # organic
        toks = http_json(f"{TOK_URL}/toporganicscore/24h")
    out = []
    for t in toks:
        if t["id"] in (SOL, USDC):
            continue
        if not (t.get("usdPrice") and t.get("mcap") and t.get("liquidity")):
            continue
        if t["mcap"] > P["max_mcap_usd"] or t["liquidity"] < P["min_liquidity_usd"]:
            continue
        s1 = t.get("stats1h") or {}
        mom = s1.get("priceChange")
        if mom is None or not (P["min_momentum_1h_pct"] <= mom <= P["max_momentum_1h_pct"]):
            continue
        if (s1.get("numBuys") or 0) < P["min_buys_1h"]:
            continue
        a = t.get("audit") or {}
        if not (a.get("mintAuthorityDisabled") and a.get("freezeAuthorityDisabled")):
            continue  # risque de mint/freeze scam
        out.append(t)
    out.sort(key=lambda t: (t.get("stats1h") or {}).get("priceChange", 0), reverse=True)
    return out


def tokens_info(mints: list[str]) -> dict:
    """Infos (usdPrice, decimals...) par mint, en un seul appel par lot."""
    if not mints:
        return {}
    q = urllib.parse.urlencode({"query": ",".join(mints)})
    return {t["id"]: t for t in http_json(f"{TOK_URL}/search?{q}")}


def sol_usd_price() -> float:
    return tokens_info([SOL])[SOL]["usdPrice"]


# ------------------------------------------------------------------- état

def load_state() -> dict:
    st = {  # valeurs par défaut (aussi utilisées pour migrer un ancien état)
        "sol": 0.0, "usdc": CONFIG["initial_usdc"],  # usdc : converti en SOL au 1er tick
        "initial_usdc": CONFIG["initial_usdc"], "initial_sol": None,
        "started_at": time.time(),
        "n_trades": 0, "fees_sol_cum": 0.0, "realized_sol": 0.0,
        "positions": {},   # mint -> {symbol, decimals, token_amount, cost_sol, peak_mult, opened_at, missing}
        "cooldowns": {},   # mint -> ts de sortie
        "last_scan_ts": 0.0, "sol_usd": None,
    }
    if STATE_F.exists():
        st.update(json.loads(STATE_F.read_text()))
    return st


def save_state(st: dict) -> None:
    tmp = STATE_F.with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    tmp.replace(STATE_F)


def record_trade(trade: dict) -> None:
    with open(TRADES_F, "a") as f:
        f.write(json.dumps(trade) + "\n")


def pay_fees(st: dict, sol_amount: float) -> float:
    """Déduit priority + réseau, compte les frais cumulés."""
    fees = CONFIG["priority_fee_sol"] + CONFIG["tx_fee_sol"]
    st["fees_sol_cum"] += fees
    return sol_amount - fees


# ------------------------------------------------------------------ trades

def buy(st: dict, tok: dict) -> None:
    """Achète position_size_sol du token avec du SOL (simulation sur vraie quote)."""
    size = P["position_size_sol"]
    send = pay_fees(st, size)
    if send <= 0 or send > st["sol"]:
        return
    try:
        q = quote(SOL, tok["id"], round(send * 10**DEC[SOL]))
    except Exception as e:
        log(f"  !! entrée {tok['symbol']} impossible ({e})")
        return
    if float(q.get("priceImpactPct", 1)) * 100 > P["max_price_impact_pct"]:
        log(f"  !! entrée {tok['symbol']} refusée (price impact {float(q['priceImpactPct']) * 100:.2f} %)")
        return
    st["sol"] -= size
    amt = int(q["outAmount"]) / 10 ** tok["decimals"]
    st["positions"][tok["id"]] = {
        "symbol": tok["symbol"], "decimals": tok["decimals"], "token_amount": amt,
        "cost_sol": size, "peak_mult": 1.0, "opened_at": time.time(), "missing": 0,
    }
    st["n_trades"] += 1
    log(f"  BUY  {tok['symbol']:10} {size:.3f} SOL -> {amt:,.0f} tokens (impact {float(q['priceImpactPct']) * 100:.2f} %)")
    record_trade({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "side": "BUY",
                  "token": tok["symbol"], "mint": tok["id"], "in_sol": round(size, 6),
                  "tokens": amt})


def sell(st: dict, mint: str, pos: dict, reason: str) -> None:
    """Revend toute la position en SOL ; si inexitable -> position morte."""
    amt_int = round(pos["token_amount"] * 10 ** pos["decimals"])
    try:
        q = quote(mint, SOL, amt_int)
        out = int(q["outAmount"]) / 10**DEC[SOL]
        out = out * (1 - CONFIG["fee_bps"] / 10_000)
        out = pay_fees(st, out)
    except Exception:
        pos["missing"] += 1
        if pos["missing"] >= P["dead_after_missing_ticks"]:
            pos["dead"] = True
            st["realized_sol"] -= pos["cost_sol"]
            log(f"  DEAD {pos['symbol']:10} -{pos['cost_sol']:.3f} SOL (inexitable, rug probable)")
        return
    st["sol"] += out
    st["realized_sol"] += out - pos["cost_sol"]
    st["n_trades"] += 1
    pnl = out - pos["cost_sol"]
    log(f"  SELL {pos['symbol']:10} {pos['cost_sol']:.3f} SOL -> {out:.3f} SOL"
        f" ({pnl / pos['cost_sol'] * 100:+.1f} %, {reason})")
    record_trade({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "side": "SELL",
                  "token": pos["symbol"], "mint": mint, "out_sol": round(out, 6),
                  "cost_sol": round(pos["cost_sol"], 6), "pnl_sol": round(pnl, 6),
                  "reason": reason})
    st["cooldowns"][mint] = time.time()
    del st["positions"][mint]


def check_exits(st: dict) -> None:
    """Valorise les positions (prix réels) et applique TP/SL/trailing/temps."""
    if not st["positions"]:
        return
    infos = tokens_info(list(st["positions"]) + [SOL])
    st["sol_usd"] = infos.get(SOL, {}).get("usdPrice") or st["sol_usd"]
    for mint, pos in list(st["positions"].items()):
        if pos.get("dead"):
            continue
        info = infos.get(mint)
        if not info or not info.get("usdPrice") or not st["sol_usd"]:
            pos["missing"] += 1
            if pos["missing"] >= P["dead_after_missing_ticks"]:
                pos["dead"] = True
                st["realized_sol"] -= pos["cost_sol"]
                log(f"  DEAD {pos['symbol']:10} -{pos['cost_sol']:.3f} SOL (token disparu)")
            continue
        pos["missing"] = 0
        val = pos["token_amount"] * info["usdPrice"] / st["sol_usd"]
        mult = val / pos["cost_sol"]
        pos["peak_mult"] = max(pos["peak_mult"], mult)
        gain = (mult - 1) * 100
        held = time.time() - pos["opened_at"]
        if gain <= P["stop_loss_pct"]:
            sell(st, mint, pos, "stop-loss")
        elif gain >= P["take_profit_pct"]:
            sell(st, mint, pos, "take-profit")
        elif pos["peak_mult"] >= 1 + P["trail_arm_pct"] / 100 and \
                mult <= pos["peak_mult"] * (1 - P["trail_drop_pct"] / 100):
            sell(st, mint, pos, f"trailing (pic +{(pos['peak_mult'] - 1) * 100:.0f} %)")
        elif held > P["max_hold_sec"]:
            sell(st, mint, pos, "durée max")


def scan_entries(st: dict) -> None:
    """Toutes les scan_interval_sec : cherche un nouveau memecoin à acheter."""
    if time.time() - st["last_scan_ts"] < CONFIG["scan_interval_sec"]:
        return
    st["last_scan_ts"] = time.time()
    if len([p for p in st["positions"].values() if not p.get("dead")]) >= P["max_positions"]:
        return
    if st["sol"] < P["position_size_sol"] * 1.1:
        return
    now = time.time()
    for tok in fetch_candidates():
        if tok["id"] in st["positions"]:
            continue
        if now - st["cooldowns"].get(tok["id"], 0) < P["cooldown_per_token_sec"]:
            continue
        buy(st, tok)
        return  # au plus une entrée par scan


# ------------------------------------------------------------------- ticks

def migrate(st: dict) -> None:
    """Conversion unique de l'ancien solde USDC en SOL (vraie quote + frais)."""
    if st.get("usdc", 0) <= 0:
        return
    amount = st["usdc"]
    try:
        q = quote(USDC, SOL, round(amount * 10**DEC[USDC]))
        out = int(q["outAmount"]) / 10**DEC[SOL]
        out = out * (1 - CONFIG["fee_bps"] / 10_000)
        out = pay_fees(st, out)
        st["usdc"] = 0.0
        st["sol"] += out
        log(f"Conversion du portefeuille : {amount:.2f} USDC -> {out:.4f} SOL (frais inclus)")
    except Exception as e:
        log(f"  !! conversion USDC reportée ({e})")


def do_tick(st: dict) -> None:
    migrate(st)
    if st["initial_sol"] is None and st["usdc"] == 0:
        st["initial_sol"] = st["sol"]  # capital de départ une fois converti
    check_exits(st)
    scan_entries(st)


def cmd_run() -> None:
    st = load_state()
    log(f"Bot memecoins démarré (source '{CONFIG['source']}', tick {CONFIG['tick_seconds']}s) — SIMULATION, aucun fonds réel")
    while True:
        try:
            do_tick(st)
            save_state(st)
        except Exception as e:
            log(f"  (tick ignoré : {e})")
        time.sleep(CONFIG["tick_seconds"])


def cmd_once() -> None:
    st = load_state()
    try:
        do_tick(st)
        save_state(st)
    except Exception as e:  # sur GitHub Actions : loguer sans faire échouer le run
        log(f"  (tick ignoré : {e})")


def cmd_status() -> None:
    st = load_state()
    sol_usd = sol_usd_price()
    infos = tokens_info(list(st["positions"]))
    pos_val = 0.0      # valeur totale des positions vivantes
    unrealized = 0.0   # gain/perte latente
    dead = 0
    print(f"SOL = {sol_usd:.2f} USDC   (source : {CONFIG['source']}, SIMULATION)\n")
    print(f"Positions ({len(st['positions'])}/{P['max_positions']}) :")
    for mint, pos in st["positions"].items():
        if pos.get("dead"):
            dead += 1
            print(f"  {pos['symbol']:10} MORTE  -{pos['cost_sol']:.3f} SOL (rug/inexitable)")
            continue
        info = infos.get(mint) or {}
        if info.get("usdPrice") and st.get("sol_usd"):
            val = pos["token_amount"] * info["usdPrice"] / st["sol_usd"]
        else:
            val = pos["cost_sol"]
        pos_val += val
        unrealized += val - pos["cost_sol"]
        gain = (val / pos["cost_sol"] - 1) * 100
        held = (time.time() - pos["opened_at"]) / 60
        print(f"  {pos['symbol']:10} {pos['cost_sol']:.3f} -> {val:.3f} SOL  ({gain:+.1f} %, pic {pos['peak_mult']:.2f}x, {held:.0f} min)")
    total = st["sol"] + pos_val
    print(f"\nPortefeuille : {st['sol']:.4f} SOL libres + positions = {total:.4f} SOL")
    if st.get("initial_sol"):
        init = st["initial_sol"]
        rows = [("Bot", total, total / init - 1),
                ("Hold-SOL (ne rien faire)", init, 0.0)]
        print(f"\n{'':26}{'valeur':>10}  {'P&L':>8}")
        for name, v, r in rows:
            print(f"{name:26}{v:>9.4f} SOL  {r * 100:>7.2f}%")
        print(f"  (en USDC : {total * sol_usd:.2f} $, capital de départ {st['initial_usdc']:.0f} $)")
    print(f"\nRéalisé : {st['realized_sol']:+.4f} SOL   Non réalisé : {unrealized:+.4f} SOL"
          f"   Mortes : {dead}")
    print(f"Trades : {st['n_trades']}   Frais cumulés : {st['fees_sol_cum']:.4f} SOL"
          f"   Démarré : {time.strftime('%d/%m %H:%M', time.localtime(st['started_at']))}")


def cmd_reset() -> None:
    STATE_F.unlink(missing_ok=True)
    TRADES_F.unlink(missing_ok=True)
    log("Portefeuille réinitialisé")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"run": cmd_run, "once": cmd_once, "status": cmd_status, "reset": cmd_reset}.get(
        cmd, lambda: print(__doc__ or "commande inconnue"))()
