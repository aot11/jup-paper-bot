# Bot paper-trading memecoins Solana — SIMULATION

Bot qui trade un portefeuille **virtuel** (100 $ convertis en SOL) sur des
**memecoins small-cap** via de **vraies quotes Jupiter** (lite-api.jup.ag).
Aucune clé privée, aucun fonds réel.

**Hébergement : GitHub Actions** (repo public, tick toutes les 5 min via
`.github/workflows/tick.yml`). Chaque tick sauvegarde `state.json` et
`trades.jsonl` dans le dépôt — c'est la mémoire du bot, et l'historique git
fournit gratuitement la trace de chaque tick. Le service systemd local est
désactivé pour éviter le double emploi.

Comme les exécutions sont simulées sur des prix réels en temps réel, si un
token rug, la quote de sortie s'effondre — le bot vit le rug comme en vrai
(position « MORTE »), sans argent réel perdu. Seul biais flatteur connu : le
sandwich/MEV n'existe pas en simulation.

## Suivre le bot

```bash
./status.sh                        # état + P&L (récupère le dernier tick)
gh run list -R aot11/jup-paper-bot # historique des ticks
gh workflow run tick -R aot11/jup-paper-bot   # tick manuel
```

Ou depuis l'iPhone : app **GitHub** → dépôt `jup-paper-bot` → onglet Actions
pour voir chaque tick, et `trades.jsonl`/`state.json` pour les trades.

## Stratégie momentum (active)

- **Univers** : top organique Jupiter 24 h, filtré : mcap < 10 M$, liquidité
  > 50 k$, momentum 1 h entre +4 % et +150 %, ≥ 50 achats/1 h, audit OK
  (mint/freeze authority désactivées — pas de scam classique).
- **Entrée** : au plus 1 achat par scan (3 min), max 3 positions simultanées,
  0,15 SOL par position, price impact d'entrée ≤ 2 %.
- **Sortie** : stop-loss -20 %, take-profit +40 %, trailing stop (armé à +15 %,
  lâche 10 % du pic), sortie forcée après 6 h, cooldown 2 h par token.
- **Frais simulés** : frais de pool réels (dans la quote) + slippage 1 % +
  taker 10 bps + priority 0,0003 SOL + réseau. Volontairement pessimiste.

## Commandes locales (dans le dossier du bot)

```bash
./status.sh                     # état + P&L vs hold-SOL
python3 bot.py once             # tick manuel local (déconseillé : voir ci-dessous)
python3 bot.py reset            # remise à zéro — puis committer/pousser l'état
```

⚠️ Ne pas faire tourner le bot localement ET sur GitHub en même temps : deux
états divergeraient. Le `reset` local doit être suivi d'un
`git commit state.json trades.jsonl && git push`.

## Fichiers

- `bot.py` — le bot (Python standard uniquement, zéro dépendance)
- `config.json` — paramètres (source, filtres, tailles, TP/SL, frais simulés)
- `state.json` — portefeuille virtuel, positions, cooldowns
- `trades.jsonl` — journal des trades simulés (avec raison de sortie)
- `bot.log` — journal d'exécution

## Réglages utiles (config.json)

Modifier → `git commit config.json && git push` → le prochain tick applique.
(Une exécution manuelle `gh workflow run tick` accélère la prise en compte.)

- `"source": "organic"` (qualité) ou `"recent"` (tokens sortis il y a quelques
  minutes sur pump.fun — mode extrême, la plupart meurent).
- `position_size_sol`, `max_positions`, `min_liquidity_usd`, TP/SL…

## Rappel honnête

Le but est de **mesurer** ce que donne un petit bot memecoin après frais.
Attends-toi à voir des rugs, des positions mortes et une sous-performance vs
le simple fait de garder tes SOL — c'est exactement la leçon que cette
simulation doit t'enseigner avant d'engager le moindre euro réel. Si après
1–2 semaines le bot bat le benchmark hold-SOL, on en discutera ; sinon, tu
auras appris gratuitement ce qui aurait coûté cher en réel.
