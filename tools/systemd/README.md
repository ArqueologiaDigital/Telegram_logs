# Daily sync timer (systemd user units)

Runs `tools/daily.sh` every day at 06:00 UTC (03:00 in Brasília); `Persistent=true` catches up after
downtime. The admin log keeps only ~48 h, so the daily run is what records who deleted or
edited what.

Install (the user has linger enabled, so it runs without a login session):

    ln -sf "$PWD"/tools/systemd/tg-arqueologia-sync.{service,timer} ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now tg-arqueologia-sync.timer

Check: `systemctl --user list-timers tg-arqueologia-sync.timer`,
`journalctl --user -u tg-arqueologia-sync`. Run once by hand: `systemctl --user start tg-arqueologia-sync`.

It commits locally in both repos and never pushes. It never uploads to archive.org either:
large files wait in `../ia_staging/` for a manual, reviewed upload (`tools/ia_upload.py`).
