# Notes and probes

Measurements quoted in commits and docs, with the script that produced each.

## First full sync, 2026-11-09

`../.venv/bin/python tools/tg_sync.py sync --full`, then `merge-legacy`, then `stats`:

| measure                                       | value                                       |
|-----------------------------------------------|---------------------------------------------|
| newest message id                             | 42270                                       |
| messages archived                             | 40968 (40962 live + 6 from the admin log)   |
| message ids absent (deleted before archiving) | 1302, unrecoverable                         |
| users seen                                    | 244                                         |
| admin-log events in the 48 h window           | 18 (6 delete, 4 edit, 6 ban toggle, 2 join) |
| 2018 messages since deleted (`legacy-only`)   | 0                                           |

Messages per year: 2017 2702, 2018 6089, 2019 1446, 2020 1480, 2021 6483, 2022 6472,
2023 3616, 2024 4725, 2025 2905, 2026 5050.

The admin log records **self-deletions** too: in all 6 delete events `deleted_by` equals the
author. So with a daily sync, deletions get an exact time and actor.

## 2018 dump vs new archive: `probes/legacy_vs_new.py`

`python3 -I notes/probes/legacy_vs_new.py`: all 4640 messages from 2018 still exist in
Telegram; 4190 have identical text, 2 differ (later edits are the likely cause). So the 2018
dump holds no unique messages, but it does hold 2018 copies of photos (pass 2 can reuse them).

## Media bill: `tools/tg_sync.py stats` and `probes/media_split.py`

| kind     | files | GB    |
|----------|-------|-------|
| video    | 96    | 2.443 |
| document | 243   | 2.173 |
| photo    | 3485  | 0.424 |
| gif      | 145   | 0.035 |
| audio    | 17    | 0.023 |
| sticker  | 216   | 0.006 |
| total    | 4202  | 5.104 |

`../.venv/bin/python notes/probes/media_split.py`:

| threshold | files at or under | GB    | files over | GB    |
|-----------|-------------------|-------|------------|-------|
| 10 MB     | 4121              | 0.923 | 81         | 4.181 |
| 25 MB     | 4156              | 1.482 | 46         | 3.622 |
| 50 MB     | 4178              | 2.302 | 24         | 2.802 |
| 100 MB    | 4194              | 3.507 | 8          | 1.597 |

Sizes are Telegram's own metadata (largest photo size, document size), not downloaded bytes.

## Redaction check: `tools/check_redaction.py`

`python3 -I tools/check_redaction.py ../Telegram_logs-2018-private`: on 2026-11-09, 118 files
checked against the 11 numbers known from the 2018 dump, 0 problems. Run it before every push.
