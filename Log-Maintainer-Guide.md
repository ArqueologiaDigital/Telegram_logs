# Log Maintainer Guide

How to run and look after the archive of the Telegram group
[@arqueologiadigital](https://t.me/arqueologiadigital). `README.md` explains what is stored and
in which format; this guide covers what a maintainer does, and how often.

All commands assume this directory layout (the scripts expect the repos side by side):

```
/home/fsanches/compartilhado/Telegram_Arqueologia_Digital/
├── Telegram_logs/                 this repo: text archive, tools, media manifest
├── Telegram_logs_media/           media repo: files of 10 MB or less
├── Telegram_logs_site/            site generator (branch main) and built site (branch gh-pages)
├── Telegram_logs_site-gh-pages/   git worktree of the gh-pages branch (written by publish.sh)
├── ia_staging/                    larger files waiting for a manual archive.org upload (not in git)
├── Telegram_logs-2018-private/    clone of the old private repo (source of legacy/, redaction check)
└── .venv/                         Python virtualenv with Telethon
```

and that you start in `Telegram_logs/`:

```
cd /home/fsanches/compartilhado/Telegram_Arqueologia_Digital/Telegram_logs
```

## At a glance

| how often      | what                                   | who               | section                                     |
|----------------|----------------------------------------|-------------------|---------------------------------------------|
| daily          | sync text, admin log and media; commit | the systemd timer | [Daily job](#daily-job-automatic)           |
| weekly         | review and upload large files          | maintainer        | [Weekly](#weekly-large-files-to-archiveorg) |
| when pushing   | redaction check, then `git push`       | maintainer        | [Publishing](#publishing-push-by-hand)      |
| now and then   | check the timer ran                    | maintainer        | [Health checks](#health-checks)             |
| once (per box) | venv, API keys, login, timer           | maintainer        | [Setup](#setup-on-a-new-machine)            |

Nothing is ever pushed to GitHub or uploaded to archive.org automatically.

## Daily job (automatic)

A systemd user timer runs `tools/daily.sh` every day at 06:00 UTC (03:00 in Brasília), and after a
reboot if a run was missed. It:

1. `tools/tg_sync.py sync`: fetches new messages, re-scans the last 30 days for edits and
   deletions, and saves the admin log;
2. `tools/tg_media.py fetch`: downloads new media (10 MB or less into `Telegram_logs_media`,
   larger into `ia_staging`);
3. `tools/check_redaction.py`: stops the run if a phone number appears anywhere;
4. commits in `Telegram_logs` and in `Telegram_logs_media`, if anything changed;
5. `Telegram_logs_site/publish.sh`: rebuilds the website into the `gh-pages` branch of
   `Telegram_logs_site` (after checking the 2018 page compatibility) and commits it if it changed.

**It must run at least once every 48 hours.** Telegram keeps the admin log for only ~48 h, and
the admin log is the only source of *who* deleted or edited a message and *when* (and the only
way to save the media of a deleted message). Everything else can be caught up later; that
cannot.

Run it by hand, the same way the timer does:

```
systemctl --user start tg-arqueologia-sync      # waits until finished
journalctl --user -u tg-arqueologia-sync -n 50  # its output
```

## Weekly: large files to archive.org

Files over 10 MB (mostly videos, disk images, big PDFs) wait in `../ia_staging/` and go to the
Internet Archive item `arqueologiadigital-telegram-media`. Uploading publishes them, so review first.

```
../.venv/bin/python tools/ia_upload.py list              # pending files: date, sender, size, caption, [DELETED]
../.venv/bin/python tools/ia_upload.py upload            # upload everything pending
../.venv/bin/python tools/ia_upload.py upload --only 3616,3618   # or only these message ids
git add media/manifest.jsonl
git commit -m "Record archive.org URLs"
```

`upload` records each file's URL (`ia_url`) in `media/manifest.jsonl` once archive.org lists it
with the same md5 as the local file. Archive.org can take a while to list new files; if some
were uploaded but not recorded, run `record` later:

```
../.venv/bin/python tools/ia_upload.py record
```

To upload with your own `ia` commands instead, use the remote path `list` prints
(`YYYY/YYYY-MM/<msgid>-<name>`), for example:

```
../.venv/bin/ia upload arqueologiadigital-telegram-media \
    ../ia_staging/2018/2018-01/3616.mp4 --remote-name=2018/2018-01/3616.mp4
../.venv/bin/python tools/ia_upload.py record
```

`upload` and `record` refuse to run while the daily job is running (they share a lock); try again
a few minutes later.

Files that you decide **not** to publish: leave them in `ia_staging`; they stay listed as
pending. Do not delete them, since the manifest still points at them.

Once uploaded and recorded, a file in `ia_staging` can be deleted to free disk space; its
sha256 stays in the manifest and its URL in `ia_url`.

## Publishing: push by hand

Always run the redaction check first. It must say `OK`:

```
python3 -I tools/check_redaction.py ../Telegram_logs-2018-private
```

Then push the three repos (data, media, site):

```
git push origin main
git -C ../Telegram_logs_media push origin main
git -C ../Telegram_logs_site push origin main gh-pages
```

Push the media before the site: the site links to media files on `raw.githubusercontent.com`,
which only exist once `Telegram_logs_media` is pushed.

### First push only

Create three **empty** public repos on github.com under ArqueologiaDigital (no README, no license,
no .gitignore): `Telegram_logs`, `Telegram_logs_media` and `Telegram_logs_site`. Then:

```
git remote add origin git@github.com:ArqueologiaDigital/Telegram_logs.git
git push -u origin main

cd ../Telegram_logs_media
git remote add origin git@github.com:ArqueologiaDigital/Telegram_logs_media.git
git push -u origin main

cd ../Telegram_logs_site
git remote add origin git@github.com:ArqueologiaDigital/Telegram_logs_site.git
git push -u origin main gh-pages
```

Then, on github.com, in `Telegram_logs_site` → Settings → Pages: *Deploy from a branch*, branch
`gh-pages`, folder `/ (root)`. The site appears at
<https://arqueologiadigital.org/Telegram_logs_site/> (the organisation's custom domain applies to
all its project sites).

(With HTTPS instead of SSH, use `https://github.com/ArqueologiaDigital/<repo>.git`.) The first
media push is about 1 GB; GitHub accepts up to 2 GB per push.

## Health checks

```
systemctl --user list-timers tg-arqueologia-sync.timer   # next and last run
journalctl --user -u tg-arqueologia-sync --since -3d     # recent runs; look for "daily run finished"
git log --oneline -5                                     # one "Daily sync YYYY-MM-DD" per day with activity
../.venv/bin/python tools/tg_sync.py stats               # message counts, deletions, media bill
```

A day without a `Daily sync` commit is normal if nobody posted, edited or deleted anything.
`state/sync_state.json` (`last_sync`) shows the last successful sync either way.

## Setup on a new machine

1. **Repos and venv**

   ```
   cd /home/fsanches/compartilhado/Telegram_Arqueologia_Digital
   git clone git@github.com:ArqueologiaDigital/Telegram_logs.git
   git clone git@github.com:ArqueologiaDigital/Telegram_logs_media.git
   git clone git@github.com:ArqueologiaDigital/Telegram_logs_old_repo_now_private.git Telegram_logs-2018-private
   python3 -m venv .venv
   .venv/bin/pip install telethon internetarchive      # tested with telethon 1.45.0
   .venv/bin/ia configure                              # your archive.org account
   ```

2. **Telegram API keys.** Create an app at <https://my.telegram.org> (API development tools) and
   store its keys outside every repo:

   ```
   mkdir -p ~/.config/tg_arqueologia && chmod 700 ~/.config/tg_arqueologia
   printf '{"api_id": 1234567, "api_hash": "..."}\n' > ~/.config/tg_arqueologia/config.json
   chmod 600 ~/.config/tg_arqueologia/config.json
   ```

3. **Login**, with an account that is an **admin** of the group (needed for the admin log):

   ```
   cd Telegram_logs
   ../.venv/bin/python tools/tg_sync.py login --phone +55XXXXXXXXXXX   # code arrives in the Telegram app
   ../.venv/bin/python tools/tg_sync.py login --code 12345
   ```

   With two-step verification: `export TGPW='...'` and add `--password-env TGPW` to the second
   command. The session file `~/.config/tg_arqueologia/arqueologia.session` grants **full access
   to the account**: never commit, copy or share it. To revoke it, end the session under
   Telegram → Settings → Devices.

4. **Timer** (needs `loginctl enable-linger $USER` once, so it runs without a login session):

   ```
   ln -sf "$PWD"/tools/systemd/tg-arqueologia-sync.{service,timer} ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now tg-arqueologia-sync.timer
   ```

   `tools/daily.sh` has the directory layout hard-coded in `ROOT=`; change it if the layout differs.

## Other commands

| command                                                                       | when                                                             |
|-------------------------------------------------------------------------------|------------------------------------------------------------------|
| `../.venv/bin/python tools/tg_sync.py sync`                                   | a sync by hand (same as the daily one)                           |
| `../.venv/bin/python tools/tg_sync.py sync --rescan-days 90`                  | look further back for edits and deletions                        |
| `../.venv/bin/python tools/tg_sync.py sync --full`                            | re-scan the whole history (a few minutes; monthly is plenty)     |
| `../.venv/bin/python tools/tg_sync.py normalize`                              | after adding a field to `REDACT`: apply it to all stored records |
| `../Telegram_logs_site/publish.sh`                                            | rebuild the site into gh-pages by hand (same as the daily run)   |
| `python3 -I ../Telegram_logs_site/build.py --out ../Telegram_logs_site/build` | local preview build (see the site README)                        |
| `../.venv/bin/python tools/tg_media.py fetch`                                 | media by hand; resumable, rerun to retry failures                |
| `../.venv/bin/python tools/tg_sync.py merge-legacy`                           | after a `--full` sync: 2018-only messages and 2018 names         |
| `python3 -I tools/import_legacy.py ../Telegram_logs-2018-private`             | rebuild `legacy/` from the old private repo                      |
| `python3 -I notes/probes/legacy_vs_new.py`                                    | compare the 2018 dump with the current archive                   |

After running any of these by hand, commit as usual (`git add -A && git commit`), in both repos
if media changed.

## Troubleshooting

| symptom                                                          | cause and fix                                                                                                                                         |
|------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------|
| `another tg_sync/tg_media process is using the Telegram session` | a run is in progress (maybe the timer). Wait; check `journalctl --user -u tg-arqueologia-sync`.                                                       |
| `admin log: not an admin, skipped`                               | the logged-in account lost admin rights. Deletions are then only detected by re-scan, without time or actor.                                          |
| login errors, `AuthKeyUnregistered`, `SessionRevoked`            | the session was ended (Telegram → Devices) or expired. Run the two `login` steps again.                                                               |
| a long pause during a run                                        | Telegram rate limiting (FloodWait). The tools wait it out on their own; no action needed.                                                             |
| `check_redaction.py` says `FAIL`                                 | a phone number got into the data. Do not push. The run did not commit; find the file it names and fix the redaction in `tools/tg_sync.py` (`REDACT`). |
| `unavailable: msg N` from `tg_media.py`                          | the message was deleted before its media was saved (or after the 48 h admin-log window). It cannot be recovered.                                      |
| disk filling up                                                  | `ia_staging` holds files not yet uploaded. Upload them, then delete the uploaded ones.                                                                |

## Phone numbers typed into messages

The raw archive keeps message text verbatim, including any phone number a member typed (decision
of 2026-11-09). The website hides them: Telegram tags candidates, and
`Telegram_logs_site/phone_review.json` holds a verdict per message id (`show` for year ranges,
prices and other false positives, `mask` for real numbers). Unreviewed tags are masked, and
`publish.sh` prints `WARNING: ... phone tag(s) not in phone_review.json` with their message ids
(it shows up in `journalctl --user -u tg-arqueologia-sync`). Look at each one, add it to the file,
commit in `Telegram_logs_site`.

## Rules for maintainers

- Only the tools write `messages/`, `users/`, `adminlog/`, `state/`, `media/` and the media repo.
  Do not edit those files by hand; derived views (search, topics, the website) live elsewhere.
- Deleted messages and earlier versions of edits are **kept**, with metadata saying so. Removal
  requests from members are a policy decision for the group admins, not something to do
  silently in the data.
- Phone numbers are never stored. Run the redaction check before every push.
- Secrets (`~/.config/tg_arqueologia/`, `~/.config/internetarchive/`) never go into a repo.
