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
# Output goes to the journal (stdout); the last line of each step also goes into the commit message.
# (Not `tee /dev/stderr`: under systemd stderr is a socket that cannot be reopened.)
OUT=$(mktemp)
trap 'rm -f -- "${OUT:?}"' EXIT
"$PY" -u tools/tg_sync.py sync 2>&1 | tee "$OUT"
SYNC=$(tail -1 "$OUT")
"$PY" -u tools/tg_media.py fetch 2>&1 | tee "$OUT"
MEDIA=$(tail -1 "$OUT")
"$PY" -u tools/tg_avatars.py 2>&1 | tee "$OUT"         # profile photos, only when changed
AVATARS=$(tail -1 "$OUT")
# Large files stay in ../ia_staging: Felipe reviews and uploads them by hand (tools/ia_upload.py).
python3 -I tools/check_redaction.py "$ROOT/Telegram_logs-2018-private"   # exits 1 on a leak

DAY=$(date -u +%F)
# Data only, never uncommitted tool edits. A run that changed nothing but state/ (scan list,
# last_sync) makes no commit; the state file goes in with the next real change.
git add -- messages users adminlog media
if ! git diff --cached --quiet; then
  git add -- state
  git commit -q -m "Daily sync $DAY" -m "$SYNC" -m "$MEDIA" -m "$AVATARS" -m "Automated run of tools/daily.sh."
fi
cd "$ROOT/Telegram_logs_media"
git add -A
git diff --cached --quiet || git commit -q -m "Daily media $DAY" -m "$MEDIA" -m "$AVATARS" -m "Automated run of Telegram_logs/tools/daily.sh."
# Rebuild the public site (gh-pages branch of Telegram_logs_site), committed locally, not pushed.
if [ -x "$ROOT/Telegram_logs_site/publish.sh" ]; then
  "$ROOT/Telegram_logs_site/publish.sh"
fi
echo "daily run finished"
