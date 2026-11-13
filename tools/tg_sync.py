#!/usr/bin/env python3
"""Archive the @arqueologiadigital Telegram group into this repo (layer 1: raw text).

Subcommands (run from the repo root, with the venv one level up):

  login --phone +55...      ask Telegram to send a login code to your app
  login --code 12345        finish the login (add --password-env VAR if you use 2FA)
  sync [--rescan-days N] [--full]
                            fetch new messages, re-scan the recent window for edits and
                            deletions, then read the admin log (last 48 h) to learn who
                            deleted or edited what, and when
  merge-legacy              add 2018-only messages (ids 1..4703 deleted since) as records
                            with deletion metadata; needs a --full sync first
  stats                     message counts and the media bill (what pass 2 would download)
  normalize                 apply the REDACT list and users/dropped_reactors.json to all stored
                            records (run after changing either)

Secrets live outside every repo, in ~/.config/tg_arqueologia/ (mode 700):
  config.json   {"api_id": 123, "api_hash": "..."}   from https://my.telegram.org
  arqueologia.session                                  Telethon session = full account access

Text only. Media is recorded as metadata (type, size, name, mime); bytes are fetched by a
separate pass into the Telegram_logs_media repo.

Record layout, one JSON object per line in messages/YYYY/YYYY-MM.jsonl (UTC month, sorted by id):
  {"id": 123, "date": "...Z",
   "raw": <Telethon Message.to_dict(), redacted: no phone numbers, file references, access hashes>,
   "archive": {"first_fetched": "...", "source": "telegram"|"legacy-2018-telegram-cli",
               "previous_versions": [{"edit_date", "message", "entities", "media", "via"}],
               "deletion": {"method": "admin_log"|"rescan"|"legacy-only",
                            "deleted_at"?, "deleted_by"?, "not_before"?, "detected_at"}}}
"""
import argparse
import asyncio
import base64
import copy
import datetime as dt
import json
import os
import sys

CONF_DIR = os.path.expanduser("~/.config/tg_arqueologia")
CHAT = "arqueologiadigital"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(REPO, "state", "sync_state.json")
MSG_DIR = os.path.join(REPO, "messages")
ADMINLOG_DIR = os.path.join(REPO, "adminlog")
USERS = os.path.join(REPO, "users", "users.jsonl")
LEGACY = os.path.join(REPO, "legacy", "2018-telegram-cli", "messages.jsonl")
LEGACY_LAST_SYNC = "2018-05-08T03:53:10Z"  # progress.json "last_modified" of the 2018 dump
# Users whose reactions are not archived (e.g. banned spammers), {"<user id>": {"since", "reason"}}.
DROPPED_REACTORS = os.path.join(REPO, "users", "dropped_reactors.json")

# Keys removed from every raw record, at any depth. phone/phone_number: members' numbers and
# shared contact cards. file_reference/access_hash: expiring credentials, useless in an archive.
# bytes: stripped thumbnail blobs. replies_pts: an update counter that moves with every new message
# (pure churn). read_max_id: the archiving account's own read position, not group data.
REDACT = {"phone", "phone_number", "file_reference", "access_hash", "bytes", "replies_pts", "read_max_id"}
# Fields that change without the author doing anything; ignored when detecting an edit.
VOLATILE = {"views", "forwards", "replies", "reactions", "edit_hide", "pinned", "ttl_period"}


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso(d):
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean(obj):
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items() if k not in REDACT}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, dt.datetime):
        return iso(obj)
    if isinstance(obj, bytes):
        return {"_b64": base64.b64encode(obj).decode()}
    return obj


_DROPPED = None


def drop_reactors(raw):
    """Remove the reactions of the users in DROPPED_REACTORS from a raw message, in place.

    Only reactions Telegram attributes (recent_reactions) can be removed: each one found is taken
    out of that list and its emoji's count lowered; an emoji left at 0 goes, and a message left
    with no reactions gets reactions = None, as if it never had any. Returns how many were removed.
    """
    global _DROPPED
    if _DROPPED is None:
        try:
            with open(DROPPED_REACTORS, encoding="utf-8") as f:
                _DROPPED = {int(k) for k in json.load(f)}
        except FileNotFoundError:
            _DROPPED = set()
    rx = (raw or {}).get("reactions")
    if not _DROPPED or not rx:
        return 0
    gone = [p for p in rx.get("recent_reactions") or []
            if (p.get("peer_id") or {}).get("user_id") in _DROPPED]
    if not gone:
        return 0
    rx["recent_reactions"] = [p for p in rx["recent_reactions"] if p not in gone]
    for p in gone:
        for res in rx.get("results") or []:
            if res.get("reaction") == p.get("reaction"):
                res["count"] = max(0, res.get("count", 0) - 1)
                break
    rx["results"] = [res for res in rx.get("results") or [] if res.get("count", 0) > 0]
    if not rx["results"]:
        raw["reactions"] = None
    return len(gone)


def atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def dumps(o):
    return json.dumps(o, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------- storage

class Archive:
    """Month files loaded lazily, written back only if touched."""

    def __init__(self):
        self.months = {}   # "YYYY-MM" -> {id: record}
        self.dirty = set()
        self.where = {}    # id -> "YYYY-MM", for every month on disk
        self.written = set()  # months written during this run, over all save() calls

        for root, _, files in os.walk(MSG_DIR):
            for fn in files:
                if fn.endswith(".jsonl"):
                    ym = fn[:-6]
                    with open(os.path.join(root, fn), encoding="utf-8") as f:
                        for line in f:
                            self.where[json.loads(line)["id"]] = ym

    def _path(self, ym):
        return os.path.join(MSG_DIR, ym[:4], ym + ".jsonl")

    def month(self, ym):
        if ym not in self.months:
            recs = {}
            if os.path.exists(self._path(ym)):
                with open(self._path(ym), encoding="utf-8") as f:
                    for line in f:
                        r = json.loads(line)
                        recs[r["id"]] = r
            self.months[ym] = recs
        return self.months[ym]

    def get(self, mid):
        ym = self.where.get(mid)
        return self.month(ym).get(mid) if ym else None

    def put(self, rec):
        ym = rec["date"][:7]
        self.month(ym)[rec["id"]] = rec
        self.where[rec["id"]] = ym
        self.dirty.add(ym)

    def ids_between(self, lo, hi):
        return [i for i in self.where if lo <= i <= hi]

    def save(self):
        for ym in sorted(self.dirty):
            recs = self.months[ym]
            atomic_write(self._path(ym), "".join(dumps(recs[i]) + "\n" for i in sorted(recs)))
        self.written |= self.dirty
        self.dirty.clear()
        return len(self.written)


def load_state():
    if os.path.exists(STATE):
        return json.load(open(STATE, encoding="utf-8"))
    return {"chat": CHAT, "last_id": 0, "scans": []}


def save_state(st):
    st["scans"] = st["scans"][-400:]
    atomic_write(STATE, json.dumps(st, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def last_scan_covering(st, mid):
    best = None
    for s in st["scans"]:
        if s["min_id"] <= mid <= s["max_id"] and (best is None or s["at"] > best):
            best = s["at"]
    return best


def content_view(raw):
    return {k: v for k, v in (raw or {}).items() if k not in VOLATILE}


def snapshot(raw, via):
    return {"edit_date": raw.get("edit_date"), "message": raw.get("message"),
            "entities": raw.get("entities"), "media": raw.get("media"), "via": via}


# ---------------------------------------------------------------- users

def load_users():
    users = {}
    if os.path.exists(USERS):
        for line in open(USERS, encoding="utf-8"):
            u = json.loads(line)
            users[u["id"]] = u
    return users


def note_user(users, ent):
    """Track names/usernames over time; never store phone numbers."""
    if ent is None or not hasattr(ent, "id"):
        return
    kind = type(ent).__name__
    cur = {"first_name": getattr(ent, "first_name", None), "last_name": getattr(ent, "last_name", None),
           "title": getattr(ent, "title", None), "username": getattr(ent, "username", None)}
    u = users.setdefault(ent.id, {"id": ent.id, "kind": kind, "names_seen": []})
    u["bot"] = bool(getattr(ent, "bot", False))
    u["deleted"] = bool(getattr(ent, "deleted", False))
    if not u["names_seen"] or {k: u["names_seen"][-1].get(k) for k in cur} != cur:
        u["names_seen"].append(dict(cur, seen=now()))
    u.update(cur)


def save_users(users):
    atomic_write(USERS, "".join(dumps(users[i]) + "\n" for i in sorted(users)))


# ---------------------------------------------------------------- telegram

_LOCK = None


def client():
    """One Telethon client per session at a time: two processes on one session file corrupt it."""
    global _LOCK
    import fcntl
    from telethon import TelegramClient
    if _LOCK is None:
        _LOCK = open(os.path.join(CONF_DIR, "run.lock"), "w")
        try:
            fcntl.flock(_LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit("another tg_sync/tg_media process is using the Telegram session; try later")
    conf = json.load(open(os.path.join(CONF_DIR, "config.json")))
    return TelegramClient(os.path.join(CONF_DIR, "arqueologia"), conf["api_id"], conf["api_hash"])


async def cmd_login(a):
    from telethon.errors import SessionPasswordNeededError
    pending = os.path.join(CONF_DIR, "login_pending.json")
    c = client()
    await c.connect()
    if await c.is_user_authorized():
        me = await c.get_me()
        print(f"already logged in as user id {me.id} (@{me.username})")
    elif a.phone:
        sent = await c.send_code_request(a.phone)
        atomic_write(pending, json.dumps({"phone": a.phone, "hash": sent.phone_code_hash}))
        os.chmod(pending, 0o600)
        print("code sent to your Telegram app; now run: login --code <code>")
    elif a.code:
        p = json.load(open(pending))
        try:
            await c.sign_in(p["phone"], a.code, phone_code_hash=p["hash"])
        except SessionPasswordNeededError:
            if not a.password_env:
                sys.exit("2FA is on: rerun with --password-env VAR, VAR holding the password")
            await c.sign_in(password=os.environ[a.password_env])
        os.remove(pending)
        me = await c.get_me()
        print(f"logged in as user id {me.id} (@{me.username})")
    else:
        sys.exit("give --phone or --code")
    for f in os.listdir(CONF_DIR):
        os.chmod(os.path.join(CONF_DIR, f), 0o600)
    await c.disconnect()


def make_record(msg, arch_prev=None):
    raw = clean(msg.to_dict())
    drop_reactors(raw)
    rec = {"id": msg.id, "date": iso(msg.date), "raw": raw,
           "archive": {"first_fetched": now(), "source": "telegram"}}
    if arch_prev:
        rec["archive"] = copy.deepcopy(arch_prev["archive"])
    return rec


def merge(archive, msg, stats):
    old = archive.get(msg.id)
    new = make_record(msg, old)
    if old is None:
        stats["new"] += 1
    else:
        if old.get("raw") and content_view(old["raw"]) != content_view(new["raw"]):
            if old["raw"].get("edit_date") != new["raw"].get("edit_date"):
                new["archive"].setdefault("previous_versions", []).append(snapshot(old["raw"], "rescan"))
            stats["changed"] += 1
        if "deletion" in new["archive"]:   # seen again: record it, keep the history
            new["archive"]["deletion"]["reappeared_at"] = now()
        if old == new:
            return
    archive.put(new)


async def fetch_range(c, entity, archive, users, st, lo, hi, stats):
    """Fetch ids lo..hi (inclusive; hi=None: up to the newest). Returns the set of ids seen."""
    seen = set()
    kw = {"reverse": True, "min_id": lo - 1}
    if hi is not None:
        kw["max_id"] = hi + 1
    async for msg in c.iter_messages(entity, **kw):
        seen.add(msg.id)
        merge(archive, msg, stats)
        note_user(users, msg.sender)
        for e in getattr(msg, "action_entities", None) or []:
            note_user(users, e)
        if len(seen) % 2000 == 0:
            print(f"  ...{len(seen)} messages, at id {msg.id} ({iso(msg.date)[:10]})", flush=True)
            archive.save()
    return seen


def mark_deleted(archive, st, lo, hi, seen, scan_at, stats):
    for mid in archive.ids_between(lo, hi):
        if mid in seen:
            continue
        rec = archive.get(mid)
        if rec["archive"].get("source") != "telegram" or "deletion" in rec["archive"]:
            continue
        rec["archive"]["deletion"] = {"method": "rescan", "detected_at": scan_at,
                                      "not_before": last_scan_covering(st, mid)}
        archive.put(rec)
        stats["deleted"] += 1


async def read_admin_log(c, entity, archive, st, stats):
    """Admin log keeps ~48 h. Store every event raw; apply deletes/edits to the records."""
    from telethon.errors import ChatAdminRequiredError
    events = []
    try:
        async for ev in c.iter_admin_log(entity, min_id=st.get("adminlog_last_id", 0)):
            events.append(ev)
    except ChatAdminRequiredError:
        print("  admin log: not an admin, skipped")
        return
    for ev in sorted(events, key=lambda e: e.id):
        raw = clean(ev.original.to_dict())
        ym = iso(ev.date)[:7]
        path = os.path.join(ADMINLOG_DIR, ym[:4], ym + ".jsonl")
        existing = {}
        if os.path.exists(path):
            for line in open(path, encoding="utf-8"):
                r = json.loads(line)
                existing[r["id"]] = r
        existing[ev.id] = {"id": ev.id, "date": iso(ev.date), "user_id": ev.user_id, "raw": raw}
        atomic_write(path, "".join(dumps(existing[i]) + "\n" for i in sorted(existing)))
        st["adminlog_last_id"] = max(st.get("adminlog_last_id", 0), ev.id)
        stats["adminlog"] += 1

        if ev.deleted_message and ev.old is not None:
            m = ev.old
            rec = archive.get(m.id) or make_record(m)   # posted and deleted between two syncs
            rec["archive"]["deletion"] = {"method": "admin_log", "deleted_at": iso(ev.date),
                                          "deleted_by": ev.user_id, "admin_log_event": ev.id,
                                          "detected_at": now()}
            archive.put(rec)
            stats["deleted_by_known"] += 1
        elif ev.changed_message and ev.old is not None:
            rec = archive.get(ev.old.id)
            if rec is not None:
                prev = rec["archive"].setdefault("previous_versions", [])
                snap = snapshot(clean(ev.old.to_dict()), "admin_log")
                snap.update(edited_at=iso(ev.date), edited_by=ev.user_id)
                if snap["message"] not in [p.get("message") for p in prev]:
                    prev.append(snap)
                    archive.put(rec)


async def cmd_sync(a):
    st = load_state()
    archive, users = Archive(), load_users()
    stats = dict(new=0, changed=0, deleted=0, adminlog=0, deleted_by_known=0)
    async with client() as c:
        entity = await c.get_entity(CHAT)
        st.update(chat_id=entity.id, title=entity.title, username=entity.username)
        scan_at = now()
        top = (await c.get_messages(entity, limit=1))[0].id

        if a.full or not st["last_id"]:
            lo = 1
        else:
            # re-scan: from the first message of the last N days, or the last id, whichever is lower
            since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=a.rescan_days)
            older = await c.get_messages(entity, limit=1, offset_date=since)
            lo = min(older[0].id if older else 1, st["last_id"] + 1)
        print(f"scanning ids {lo}..{top} (newest is {top})", flush=True)
        seen = await fetch_range(c, entity, archive, users, st, lo, top, stats)
        mark_deleted(archive, st, lo, top, seen, scan_at, stats)
        st["scans"].append({"min_id": lo, "max_id": top, "at": scan_at})
        st["last_id"] = max(st["last_id"], top)
        archive.save()
        await read_admin_log(c, entity, archive, st, stats)
    n = archive.save()
    save_users(users)
    st["last_sync"] = now()
    save_state(st)
    print(f"done: {stats}, month files written: {n}")


def cmd_merge_legacy(a):
    st = load_state()
    if not any(s["min_id"] <= 1 and s["max_id"] >= 4703 for s in st["scans"]):
        sys.exit("run a --full sync first, so missing ids really mean deleted")
    archive = Archive()
    added = 0
    for line in open(LEGACY, encoding="utf-8"):
        leg = json.loads(line)
        mid = leg["msg_id"]
        if archive.get(mid) is not None:
            continue
        date = dt.datetime.fromtimestamp(leg["date"], dt.timezone.utc)
        archive.put({"id": mid, "date": iso(date), "raw": None, "legacy": leg,
                     "archive": {"first_fetched": LEGACY_LAST_SYNC, "source": "legacy-2018-telegram-cli",
                                 "deletion": {"method": "legacy-only", "not_before": LEGACY_LAST_SYNC,
                                              "detected_at": now()}}})
        added += 1
    archive.save()
    print(f"legacy-only messages added (deleted from Telegram since 2018): {added}")

    # Names from 2018: many of those accounts are deleted now, and Telegram no longer gives names.
    users, named = load_users(), 0
    for line in open(LEGACY, encoding="utf-8"):
        leg = json.loads(line)
        for lu in (leg.get("from") or {}, (leg.get("action") or {}).get("user") or {}):
            uid = lu.get("peer_id")
            if not uid or lu.get("peer_type") != "user":
                continue
            u = users.setdefault(uid, {"id": uid, "kind": "User", "names_seen": [], "first_name": None,
                                       "last_name": None, "title": None, "username": None})
            entry = {"first_name": lu.get("first_name") or None, "last_name": lu.get("last_name") or None,
                     "title": None, "username": lu.get("username") or None,
                     "seen": LEGACY_LAST_SYNC, "source": "legacy-2018"}
            if not any(n.get("source") == "legacy-2018" for n in u["names_seen"]):
                u["names_seen"].insert(0, entry)
                named += 1
    save_users(users)
    print(f"users given 2018 names: {named}")


def cmd_normalize(a):
    """Apply REDACT and DROPPED_REACTORS to everything already stored (records that are never
    re-fetched, such as deleted messages and admin-log events, keep old fields otherwise). Run after
    changing either."""
    n = dropped = 0
    for d in (MSG_DIR, ADMINLOG_DIR):
        for root, _, files in os.walk(d):
            for fn in sorted(files):
                if not fn.endswith(".jsonl"):
                    continue
                path = os.path.join(root, fn)
                text = open(path, encoding="utf-8").read()
                recs = [clean(json.loads(line)) for line in text.splitlines()]
                for rec in recs:
                    if isinstance(rec.get("raw"), dict):
                        dropped += drop_reactors(rec["raw"])
                new = "".join(dumps(rec) + "\n" for rec in recs)
                if new != text:
                    atomic_write(path, new)
                    n += 1
    print(f"files rewritten: {n}; reactions dropped: {dropped}")


def media_size(media):
    """Best-quality byte size of a raw media dict, as pass 2 would download it."""
    if not media:
        return None, 0
    t = media.get("_")
    if t == "MessageMediaPhoto" and media.get("photo"):
        best = 0
        for s in media["photo"].get("sizes", []):
            best = max(best, s.get("size") or 0, max(s.get("sizes") or [0]))
        return "photo", best
    if t == "MessageMediaDocument" and media.get("document"):
        d = media["document"]
        kind = "document"
        for at in d.get("attributes", []):
            kind = {"DocumentAttributeVideo": "video", "DocumentAttributeAudio": "audio",
                    "DocumentAttributeSticker": "sticker", "DocumentAttributeAnimated": "gif"}.get(at.get("_"), kind)
        return kind, d.get("size") or 0
    if t == "MessageMediaWebPage":
        return "webpage", 0
    return t, 0


def cmd_stats(a):
    from collections import Counter, defaultdict
    archive = Archive()
    per_year, kinds, sizes = Counter(), Counter(), defaultdict(int)
    big50 = big100 = 0
    deletions = Counter()
    for ym in sorted(set(archive.where.values())):
        for rec in archive.month(ym).values():
            per_year[ym[:4]] += 1
            d = rec["archive"].get("deletion")
            if d:
                deletions[d["method"]] += 1
            kind, size = media_size((rec.get("raw") or {}).get("media"))
            if kind:
                kinds[kind] += 1
                sizes[kind] += size
                big50 += size > 50e6
                big100 += size > 100e6
    print(f"messages: {sum(per_year.values())}")
    for y in sorted(per_year):
        print(f"  {y}: {per_year[y]}")
    print(f"deleted (kept, marked): {dict(deletions)}")
    print("media (pass 2 bill):")
    for k in sorted(kinds, key=lambda k: -sizes[k]):
        print(f"  {k:<24} {kinds[k]:>7} files  {sizes[k] / 1e9:8.3f} GB")
    print(f"  total {sum(sizes.values()) / 1e9:.3f} GB; files > 50 MB: {big50}, > 100 MB: {big100}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    lg = sub.add_parser("login")
    lg.add_argument("--phone")
    lg.add_argument("--code")
    lg.add_argument("--password-env")
    sy = sub.add_parser("sync")
    sy.add_argument("--rescan-days", type=int, default=30)
    sy.add_argument("--full", action="store_true")
    sub.add_parser("merge-legacy")
    sub.add_parser("stats")
    sub.add_parser("normalize")
    a = p.parse_args()
    if a.cmd == "login":
        asyncio.run(cmd_login(a))
    elif a.cmd == "sync":
        asyncio.run(cmd_sync(a))
    elif a.cmd == "merge-legacy":
        cmd_merge_legacy(a)
    elif a.cmd == "normalize":
        cmd_normalize(a)
    else:
        cmd_stats(a)


if __name__ == "__main__":
    main()
