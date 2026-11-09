#!/usr/bin/env python3
"""Profile photos of the people in the archive, for the site's avatars (small 160 px version).

    ../.venv/bin/python tools/tg_avatars.py           # download new or changed photos

For every live account in users/users.jsonl, asks Telegram for its current profile photo (one
batched request per 200 users) and downloads it only when its photo id differs from the one
recorded. Files go to the media repo as avatars/<user id>-<photo id>.jpg; an older photo is
kept when a user changes theirs (the archive keeps history). Deleted accounts have no photo.
Telegram only returns a photo the user lets everyone see, so privacy settings are respected.

The record is media/avatars.jsonl in THIS repo, one line per user that was checked:
  {"user_id", "photo_id" (null = no visible photo), "path"?, "size"?, "sha256"?, "fetched_at"?}
It changes only when a photo changes, so the daily job does not commit churn.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tg_media import MEDIA_REPO, sha256  # noqa: E402
from tg_sync import REPO, atomic_write, client, dumps, load_users, now  # noqa: E402

AVATARS = os.path.join(REPO, "media", "avatars.jsonl")


def load():
    out = {}
    if os.path.exists(AVATARS):
        for line in open(AVATARS, encoding="utf-8"):
            r = json.loads(line)
            out[r["user_id"]] = r
    return out


async def main():
    users, rec = load_users(), load()
    ids = sorted(u["id"] for u in users.values() if u["kind"] == "User" and not u.get("deleted"))
    fetched = changed = unresolved = 0
    async with client() as c:
        for i in range(0, len(ids), 200):
            batch = ids[i:i + 200]
            try:
                ents = await c.get_entity(batch)
            except ValueError:          # one id not in the session cache: resolve one by one
                ents = []
                for uid in batch:
                    try:
                        ents.append(await c.get_entity(uid))
                    except ValueError:
                        unresolved += 1
            for ent in ents:
                photo = getattr(ent, "photo", None)
                pid = getattr(photo, "photo_id", None)
                old = rec.get(ent.id, {})
                if old.get("photo_id") == pid and (pid is None or os.path.exists(os.path.join(MEDIA_REPO, old["path"]))):
                    continue
                changed += 1
                if pid is None:
                    rec[ent.id] = {"user_id": ent.id, "photo_id": None}
                    continue
                rel = f"avatars/{ent.id}-{pid}.jpg"
                path = os.path.join(MEDIA_REPO, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if await c.download_profile_photo(ent, file=path + ".part", download_big=False) is None:
                    rec[ent.id] = {"user_id": ent.id, "photo_id": None}
                    continue
                os.replace(path + ".part", path)
                rec[ent.id] = {"user_id": ent.id, "photo_id": pid, "path": rel, "size": os.path.getsize(path),
                               "sha256": sha256(path), "fetched_at": now()}
                fetched += 1
    atomic_write(AVATARS, "".join(dumps(rec[k]) + "\n" for k in sorted(rec)))
    have = sum(1 for r in rec.values() if r.get("photo_id"))
    print(f"avatars: {len(ids)} live accounts, {have} with a visible photo; {changed} changed, "
          f"{fetched} downloaded, {unresolved} not resolvable")


if __name__ == "__main__":
    asyncio.run(main())
