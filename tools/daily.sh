#!/bin/bash
# Daily archive run: text sync (+ admin log), media pass, redaction check, local commits.
# Never pushes: pushing is done by hand. Installed as a systemd user timer, see tools/systemd/.
# Logs go to the journal: journalctl --user -u tg-arqueologia-sync
set -euo pipefail
ROOT=/home/fsanches/compartilhado/Telegram_Arqueologia_Digital
PY="$ROOT/.venv/bin/python"
export TMPDIR=/home/fsanches/compartilhado/tmp

# Only one process may use the Telegram session at a time: client() in tg_sync.py takes
# ~/.config/tg_arqueologia/run.lock and exits if it is held, which fails this run (set -e).

cd "$ROOT/Telegram_logs"
SYNC=$("$PY" -u tools/tg_sync.py sync | tee /dev/stderr | tail -1)
MEDIA=$("$PY" -u tools/tg_media.py fetch | tee /dev/stderr | tail -1)
if [ -f "$HOME/.config/internetarchive/ia.ini" ]; then   # large files, once IA is configured
  "$PY" -u tools/ia_upload.py
fi
python3 -I tools/check_redaction.py "$ROOT/Telegram_logs-2018-private"   # exits 1 on a leak

DAY=$(date -u +%F)
git add -A
git diff --cached --quiet || git commit -q -m "Daily sync $DAY" -m "$SYNC" -m "$MEDIA" -m "Automated run of tools/daily.sh."
cd "$ROOT/Telegram_logs_media"
git add -A
git diff --cached --quiet || git commit -q -m "Daily media $DAY" -m "$MEDIA" -m "Automated run of Telegram_logs/tools/daily.sh."
echo "daily run finished"
