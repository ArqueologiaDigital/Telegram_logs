# Telegram_logs: arquivo do grupo @arqueologiadigital

Raw archive of the Telegram group [@arqueologiadigital](https://t.me/arqueologiadigital)
(*Arqueologia Digital e Preservação Histórica por meio da Emulação de Dispositivos Eletrônicos*),
from its creation on 2017-11-06 onward.

This repo is **layer 1, raw text data only**. Everything else is derived from it, lives
elsewhere, and can be regenerated:

| layer            | repo                   | contents                                                           |
|------------------|------------------------|--------------------------------------------------------------------|
| 1. raw text      | `Telegram_logs` (here) | messages, users, admin log, sync tool, 2018 legacy dump            |
| 2. raw media     | `Telegram_logs_media`  | media of 10 MB or less, by message id; larger files on archive.org |
| 3. derived index | (planned)              | full-text search, topic segmentation, link and file catalogues     |
| 4. visualization | site repo (planned)    | static HTML for arqueologiadigital.org                             |

Only `tools/tg_sync.py` writes `messages/`, `users/`, `adminlog/` and `state/`.

## Layout

| path                               | what                                                                                              |
|------------------------------------|---------------------------------------------------------------------------------------------------|
| `messages/YYYY/YYYY-MM.jsonl`      | one message per line, UTC month, sorted by message id                                             |
| `users/users.jsonl`                | senders: id, names and usernames over time (`names_seen`, incl. 2018 names); **no phone numbers** |
| `adminlog/YYYY/YYYY-MM.jsonl`      | raw admin-log events (Telegram keeps only ~48 h, so every sync saves them)                        |
| `state/sync_state.json`            | last message id and the list of scans (used to date deletions)                                    |
| `legacy/2018-telegram-cli/`        | the 2018 dump (ids 1..4703), phone numbers redacted, plus its media map                           |
| `tools/tg_sync.py`                 | login, sync, merge-legacy, stats                                                                  |
| `tools/import_legacy.py`           | rebuilds `legacy/` from the old private repo and checks that no phone number leaks                |
| `tools/check_redaction.py`         | scans the repo for phone fields and known numbers; run before every push                          |
| `media/manifest.jsonl`             | per message with media: file path or archive.org URL, size, sha256, how it was fetched            |
| `tools/tg_media.py`                | pass 2: downloads media into `../Telegram_logs_media` or `../ia_staging`                          |
| `tools/ia_upload.py`               | uploads `../ia_staging` to the archive.org item and records `ia_url`                              |
| `tools/daily.sh`, `tools/systemd/` | daily run (sync, media, IA upload, redaction check, local commits; never pushes)                  |
| `notes/`                           | measurements and the probe scripts that produced them                                             |

## Message records

```
{"id": 123, "date": "2017-11-06T20:20:40Z",
 "raw": { Telethon Message.to_dict(), redacted },
 "archive": {"source": "telegram" | "legacy-2018-telegram-cli",
             "first_fetched": "...",
             "previous_versions": [ {"edit_date", "message", "entities", "media", "via", ...} ],
             "deletion": { ... } }}
```

`raw` drops `phone`, `phone_number` (shared contact cards), `file_reference`, `access_hash`
and stripped-thumbnail `bytes` at every depth.

**Deleted messages are kept** and flagged in `archive.deletion`, so that renderers can decide
how to show them:

| `method`      | how it was found                                   | `deleted_at` / `deleted_by` | otherwise                                  |
|---------------|----------------------------------------------------|-----------------------------|--------------------------------------------|
| `admin_log`   | admin-log delete event (exact)                     | yes / yes (user id)         |                                            |
| `rescan`      | present in an earlier scan, missing in a later one | no / no                     | `not_before` = last scan that still saw it |
| `legacy-only` | in the 2018 dump, never seen by the new tool       | no / no                     | `not_before` = 2018-05-08T03:53:10Z        |

Edits are kept the same way in `archive.previous_versions` (`via`: `rescan` or `admin_log`;
admin-log ones also carry `edited_at`/`edited_by`).

Deleted Telegram accounts (120 of 244 on 2026-11-09) no longer have names; for 18 of them
the 2018 dump supplies one (`names_seen` entries with `"source": "legacy-2018"`). Renderers
should use the last non-empty entry of `names_seen`.

## Running it

For the day-to-day routine (daily timer, weekly archive.org uploads, pushing, troubleshooting)
see **[Log-Maintainer-Guide.md](Log-Maintainer-Guide.md)**.


Secrets live outside every repo, in `~/.config/tg_arqueologia/` (mode 700):
`config.json` = `{"api_id": ..., "api_hash": "..."}` from <https://my.telegram.org>, and the
Telethon session, which grants **full access to the account**. Never commit either.

```
python3 -m venv ../.venv && ../.venv/bin/pip install telethon      # tested with 1.45.0
../.venv/bin/python tools/tg_sync.py login --phone +55...           # code arrives in the app
../.venv/bin/python tools/tg_sync.py login --code 12345             # --password-env VAR for 2FA
../.venv/bin/python tools/tg_sync.py sync --full                    # first run: everything
../.venv/bin/python tools/tg_sync.py merge-legacy                   # 2018-only messages
../.venv/bin/python tools/tg_sync.py sync                           # daily: new + 30-day rescan + admin log
../.venv/bin/python tools/tg_sync.py stats                          # counts and the media bill
../.venv/bin/python tools/tg_media.py legacy                        # copy the 2018 media files
../.venv/bin/python tools/tg_media.py fetch                         # pass 2, resumable
../.venv/bin/python tools/ia_upload.py                              # large files to archive.org
```

The admin log is only readable by an admin and only covers ~48 h, so run `sync` at least
daily to learn who deleted what.

## History

The 2018 logs were taken with `vysheng/telegram-cli` + `telegram-history-dump` into the
original `ArqueologiaDigital/Telegram_logs`, which exposed members' phone numbers and is now
private, as `ArqueologiaDigital/Telegram_logs_old_repo_now_private`. This repo was started from scratch on 2026-11-09. `legacy/` is rebuilt from that repo by
`python3 -I tools/import_legacy.py ../Telegram_logs-2018-private` (a clone of that repo): 4640 messages, 401 media
references, 11 distinct phone numbers removed, 0 found in the output by the built-in check.
