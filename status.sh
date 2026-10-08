#!/usr/bin/env bash
# Statut du bot hébergé sur GitHub Actions : récupère le dernier état et l'affiche.
# Usage : ./status.sh   (depuis le dossier du bot)
set -e
cd "$(dirname "$0")"
git pull --quiet origin main 2>/dev/null || true
python3 bot.py status
