# Bot paper-trading memecoins Solana — SIMULATION

Bot qui trade un portefeuille **virtuel** (100 $ convertis en SOL) sur des
**memecoins small-cap** via de **vraies quotes Jupiter** (lite-api.jup.ag).
Aucune clé privée, aucun fonds réel.

Comme les exécutions sont simulées sur des prix réels en temps réel, si un
token rug, la quote de sortie s'effondre — le bot vit le rug comme en vrai
(position « MORTE »), sans argent réel perdu. Seul biais flatteur connu : le
sandwich/MEV n'existe pas en simulation.

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

## Commandes

```bash
python3 bot.py status                       # positions + P&L vs hold-SOL
python3 bot.py once                         # un tick manuel
python3 bot.py reset                        # remise à zéro (100 $ en SOL)
systemctl --user status jup-paper-bot       # état du service
journalctl --user -u jup-paper-bot -f       # trades en direct
systemctl --user stop jup-paper-bot         # arrêter
```

## Fichiers

- `bot.py` — le bot (Python standard uniquement, zéro dépendance)
- `config.json` — paramètres (source, filtres, tailles, TP/SL, frais simulés)
- `state.json` — portefeuille virtuel, positions, cooldowns
- `trades.jsonl` — journal des trades simulés (avec raison de sortie)
- `bot.log` — journal d'exécution

## Réglages utiles (config.json puis `systemctl --user restart jup-paper-bot`)

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
