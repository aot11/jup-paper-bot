#!/usr/bin/env bash
# Enregistre la clé API Brevo comme secret GitHub (saisie masquée, rien dans l'historique).
# Usage : ./set-mail-key.sh   (coller la clé quand demandé, Entrée)
set -e
cd "$(dirname "$0")"
read -rsp "Clé API Brevo (saisie masquée) : " KEY
echo
[ -n "$KEY" ] || { echo "clé vide, abandon"; exit 1; }
gh secret set BREVO_API_KEY --body "$KEY" -R aot11/jup-paper-bot
echo "✅ Secret BREVO_API_KEY enregistré sur aot11/jup-paper-bot"
