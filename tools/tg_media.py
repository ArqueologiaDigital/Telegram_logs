#!/usr/bin/env python3
"""Pass 2: download media for archived messages (layer 2), resumably.

    ../.venv/bin/python tools/tg_media.py legacy     copy the 2018 media files into the media repo
    ../.venv/bin/python tools/tg_media.py fetch      download whatever the manifest lacks
                                     [--limit N] [--max-bytes 10000000]

Where files go:
  size <= --max-bytes (default 10 MB)  ../Telegram_logs_media/YYYY/YYYY-MM/<msgid>-<name>   (git)
  larger                               ../ia_staging/YYYY/YYYY-MM/<msgid>-<name>            (for the
                                       Internet Archive item; not in git; ia_url filled in on upload)
  2018 files                           ../Telegram_logs_media/legacy/2018-telegram-cli/<name>
  link-preview photos                  ../Telegram_logs_media/YYYY/YYYY-MM/<msgid>-preview.jpg  (git)

Link previews: when Telegram attached a photo to a message's link preview (YouTube, GitHub,
articles...), `fetch` stores its version at most PREVIEW_MAX_W pixels wide (320x180 for YouTube,
~16 KB) as kind "preview", with its "w"/"h". A YouTube link whose message has no preview photo
(and no other media) gets https://i.ytimg.com/vi/<id>/mqdefault.jpg instead, "via": "ytimg".

The manifest is media/manifest.jsonl in THIS repo, one line per message with media:
  {"msg_id", "kind", "expected_size", "size", "sha256", "store": "git"|"ia",
   "path" (relative to the media repo or ia_staging), "ia_url"?,
   "via": "message"|"admin_log"|"legacy-copy"|"ytimg", "fetched_at", "w"?, "h"? (previews), "source_url"? (ytimg)}
So the text archive can verify media without carrying it.

Deleted messages: their media can only be fetched through the admin-log event (~48 h), so
`fetch` tries those first.

If a 2018 file has exactly the size Telegram reports, it is copied instead of downloaded;
git stores the identical bytes once, so the copy costs nothing.
"""
import argparse
import asyncio
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tg_sync import CHAT, REPO, Archive, atomic_write, client, dumps, media_size, now  # noqa: E402

MEDIA_REPO = os.path.join(os.path.dirname(REPO), "Telegram_logs_media")
IA_STAGING = os.path.join(os.path.dirname(REPO), "ia_staging")
MANIFEST = os.path.join(REPO, "media", "manifest.jsonl")
LEGACY_SRC = os.path.join(os.path.dirname(REPO), "Telegram_logs-2018-private", "media",
                          "Arqueologia_Digital_e_Preservação_Histórica_por_meio_da_Emulação_de_Dispositivos_Eletrônicos")
LEGACY_MAP = os.path.join(REPO, "legacy", "2018-telegram-cli", "media_map.jsonl")
LEGACY_DST = os.path.join(MEDIA_REPO, "legacy", "2018-telegram-cli")
DOWNLOADABLE = {"photo", "document", "video", "audio", "sticker", "gif"}
PREVIEW_MAX_W = 320
YOUTUBE = re.compile(r"(?:youtube\.com/(?:watch\?(?:[^\s]*&)?v=|shorts/|embed/|live/)|youtu\.be/)([A-Za-z0-9_-]{11})")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest():
    m = {}
    if os.path.exists(MANIFEST):
        for line in open(MANIFEST, encoding="utf-8"):
            r = json.loads(line)
            m[r["msg_id"]] = r
    return m


def save_manifest(m):
    atomic_write(MANIFEST, "".join(dumps(m[i]) + "\n" for i in sorted(m)))


def safe(name):
    name = re.sub(r"[\x00-\x1f/\\:*?\"<>|]", "_", name).strip(". ")
    root, ext = os.path.splitext(name)
    return (root[:100] + ext[:12]) or "file"


def preview_size(sizes):
    """The size of a link-preview photo to keep: the widest at most PREVIEW_MAX_W pixels wide,
    else the narrowest. `sizes` are raw dicts or Telethon objects; None if there is no real size."""
    get = (lambda x, k: x.get(k)) if sizes and isinstance(sizes[0], dict) else (lambda x, k: getattr(x, k, None))
    real = [x for x in sizes or [] if get(x, "w") and get(x, "type") not in ("i", "j")]
    small = [x for x in real if get(x, "w") <= PREVIEW_MAX_W]
    if small:
        return max(small, key=lambda x: get(x, "w"))
    return min(real, key=lambda x: get(x, "w")) if real else None


def size_bytes(x):
    get = x.get if isinstance(x, dict) else (lambda k: getattr(x, k, None))
    return get("size") or max(get("sizes") or [0])


def youtube_id(raw):
    """First YouTube video id linked from a message (text, text links or preview URL), or None."""
    urls = [raw.get("message") or ""] + [e.get("url") or "" for e in raw.get("entities") or []]
    urls.append(((raw.get("media") or {}).get("webpage") or {}).get("url") or "")
    m = YOUTUBE.search(" ".join(urls))
    return m.group(1) if m else None


def file_name(rec, kind=None):
    media = rec["raw"]["media"]
    mid = rec["id"]
    if kind == "preview":
        return f"{mid}-preview.jpg"
    if media.get("_") == "MessageMediaPhoto":
        return f"{mid}.jpg"
    doc = media["document"]
    for at in doc.get("attributes", []):
        if at.get("_") == "DocumentAttributeFilename" and at.get("file_name"):
            return f"{mid}-{safe(at['file_name'])}"
    ext = mimetypes.guess_extension(doc.get("mime_type") or "") or ""
    ext = {".jpe": ".jpg", ".oga": ".ogg"}.get(ext, ext)
    if doc.get("mime_type") == "application/x-tgsticker":
        ext = ".tgs"
    return f"{mid}{ext}"


def wanted(archive):
    """(record, kind, expected_size) for every message whose media can be downloaded."""
    out = []
    for ym in sorted(set(archive.where.values())):
        for rec in archive.month(ym).values():
            media = (rec.get("raw") or {}).get("media")
            kind, size = media_size(media)
            if kind in DOWNLOADABLE:
                out.append((rec, kind, size))
            elif kind == "webpage":
                photo = (media.get("webpage") or {}).get("photo") or {}
                best = preview_size(photo.get("sizes"))
                if best:
                    out.append((rec, "preview", size_bytes(best)))
    return out


def youtube_fallbacks(archive, manifest):
    """(record, video id) for messages linking to YouTube that have neither a preview photo
    nor any other media (the manifest has one entry per message)."""
    out = []
    for ym in sorted(set(archive.where.values())):
        for rec in archive.month(ym).values():
            raw = rec.get("raw") or {}
            media = raw.get("media")
            if rec["id"] in manifest or (media and media.get("_") != "MessageMediaWebPage"):
                continue
            if ((media or {}).get("webpage") or {}).get("photo"):
                continue          # Telegram has a photo: the Telegram pass fetches it
            vid = youtube_id(raw)
            if vid:
                out.append((rec, vid))
    return out


def destination(rec, store, kind=None):
    ym = rec["date"][:7]
    rel = os.path.join(ym[:4], ym, file_name(rec, kind))
    return rel, os.path.join(MEDIA_REPO if store == "git" else IA_STAGING, rel)


def cmd_legacy(a):
    os.makedirs(LEGACY_DST, exist_ok=True)
    n = 0
    for line in open(LEGACY_MAP, encoding="utf-8"):
        e = json.loads(line)
        dst = os.path.join(LEGACY_DST, e["file"])
        if not os.path.exists(dst):
            shutil.copy2(os.path.join(LEGACY_SRC, e["file"]), dst)
            n += 1
        assert sha256(dst) == e["sha256"], dst
    print(f"legacy files copied: {n} (all checksums match media_map.jsonl)")


async def cmd_fetch(a):
    archive, manifest = Archive(), load_manifest()
    legacy = {}
    if os.path.exists(LEGACY_MAP):
        for line in open(LEGACY_MAP, encoding="utf-8"):
            e = json.loads(line)
            legacy[e["msg_id"]] = e
    todo = [(r, k, s) for r, k, s in wanted(archive) if r["id"] not in manifest]
    # deleted (admin-log) first: they expire; then oldest first
    todo.sort(key=lambda t: (t[0]["archive"].get("deletion") is None, t[0]["id"]))
    if a.limit:
        todo = todo[:a.limit]
    print(f"to fetch: {len(todo)} files, {sum(s for _, _, s in todo) / 1e9:.3f} GB expected", flush=True)

    done = failed = 0
    async with client() as c:
        c.flood_sleep_threshold = 24 * 3600
        entity = await c.get_entity(CHAT)
        adminlog_msgs = {}
        if any(r["archive"].get("deletion", {}).get("method") == "admin_log" for r, _, _ in todo):
            async for ev in c.iter_admin_log(entity, delete=True):
                if ev.deleted_message and ev.old is not None:
                    adminlog_msgs[ev.old.id] = ev.old

        for i in range(0, len(todo), 100):
            batch = todo[i:i + 100]
            live_ids = [r["id"] for r, _, _ in batch if "deletion" not in r["archive"]]
            live = {m.id: m for m in await c.get_messages(entity, ids=live_ids) if m} if live_ids else {}
            for rec, kind, size in batch:
                store = "git" if size <= a.max_bytes else "ia"
                rel, path = destination(rec, store, kind)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                entry = {"msg_id": rec["id"], "kind": kind, "expected_size": size, "store": store, "path": rel}
                leg = legacy.get(rec["id"]) if kind != "preview" else None
                if leg and leg.get("size") == size:
                    shutil.copy2(os.path.join(LEGACY_SRC, leg["file"]), path)
                    entry["via"] = "legacy-copy"
                else:
                    msg = adminlog_msgs.get(rec["id"]) if "deletion" in rec["archive"] else live.get(rec["id"])
                    if msg is None or msg.media is None:
                        print(f"  unavailable: msg {rec['id']} ({kind})", flush=True)
                        failed += 1
                        continue
                    part = path + ".part"
                    target, thumb = msg, None
                    if kind == "preview":
                        target = getattr(getattr(msg.media, "webpage", None), "photo", None)
                        thumb = preview_size(getattr(target, "sizes", None))
                        if thumb is None:
                            print(f"  unavailable: msg {rec['id']} (preview photo gone)", flush=True)
                            failed += 1
                            continue
                        entry.update(w=thumb.w, h=thumb.h)
                    try:
                        if await c.download_media(target, file=part, thumb=thumb) is None:
                            raise RuntimeError("nothing downloaded")
                    except Exception as ex:  # noqa: BLE001 - log and move on; rerun retries
                        print(f"  failed: msg {rec['id']}: {type(ex).__name__}: {ex}", flush=True)
                        failed += 1
                        continue
                    os.replace(part, path)
                    entry["via"] = "admin_log" if "deletion" in rec["archive"] else "message"
                entry.update(size=os.path.getsize(path), sha256=sha256(path), fetched_at=now())
                manifest[rec["id"]] = entry
                done += 1
            save_manifest(manifest)
            print(f"  {done} fetched, {failed} failed ({i + len(batch)}/{len(todo)})", flush=True)
    yt_done, yt_failed = fetch_youtube(archive, manifest, a.limit)
    print(f"done: fetched {done}, failed {failed}; YouTube thumbnails from ytimg: {yt_done}, unavailable {yt_failed}")


def fetch_youtube(archive, manifest, limit=None):
    """The ytimg fallback for YouTube links without a Telegram preview photo. No Telegram session."""
    todo = youtube_fallbacks(archive, manifest)[:limit] if limit else youtube_fallbacks(archive, manifest)
    done = failed = 0
    for rec, vid in todo:
        url = f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"
        rel, path = destination(rec, "git", "preview")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (archive thumbnail fetcher)"})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
        except (urllib.error.URLError, TimeoutError) as ex:   # 404: video removed; rerun retries
            print(f"  ytimg unavailable: msg {rec['id']} {vid}: {ex}", flush=True)
            failed += 1
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        atomic_bytes(path, body)
        manifest[rec["id"]] = {"msg_id": rec["id"], "kind": "preview", "expected_size": len(body),
                               "store": "git", "path": rel, "via": "ytimg", "source_url": url,
                               "w": 320, "h": 180, "size": len(body), "sha256": sha256(path),
                               "fetched_at": now()}
        done += 1
    if todo:
        save_manifest(manifest)
    return done, failed


def atomic_bytes(path, body):
    with open(path + ".part", "wb") as f:
        f.write(body)
    os.replace(path + ".part", path)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("legacy")
    f = sub.add_parser("fetch")
    f.add_argument("--limit", type=int)
    f.add_argument("--max-bytes", type=int, default=10_000_000)
    a = p.parse_args()
    if a.cmd == "legacy":
        cmd_legacy(a)
    else:
        asyncio.run(cmd_fetch(a))


if __name__ == "__main__":
    main()
