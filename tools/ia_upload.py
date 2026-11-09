#!/usr/bin/env python3
"""Large media files (store == "ia") -> one Internet Archive item. Run BY HAND, after review.

The daily job only downloads these files into ../ia_staging/; uploading is always a manual,
reviewed step.

    ../.venv/bin/python tools/ia_upload.py list      # review: what is pending, with message context
    ../.venv/bin/python tools/ia_upload.py upload    # upload pending files, then record their URLs
    ../.venv/bin/python tools/ia_upload.py record    # only record URLs of files uploaded some other
                                                     # way (e.g. your own `ia upload` commands)
    --item arqueologiadigital-telegram-media (default)
    --only 1234,5678                                 # restrict to these message ids

Setup, once: ../.venv/bin/pip install internetarchive && ../.venv/bin/ia configure

Remote path = the manifest "path" (YYYY/YYYY-MM/<msgid>-<name>), so the URL is
https://archive.org/download/<item>/<path>. `ia_url` is written into media/manifest.jsonl only
when archive.org lists the file with the same md5 as the local copy (IA publishes md5/sha1;
the sha256 stays in the manifest). Commit media/manifest.jsonl afterwards.
"""
import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tg_media import IA_STAGING, load_manifest, save_manifest  # noqa: E402
from tg_sync import CONF_DIR, Archive, load_users  # noqa: E402

METADATA = {
    "title": "Arqueologia Digital: Telegram group media (large files)",
    "description": ("Large media files (videos, documents) posted in the Telegram group "
                    "@arqueologiadigital (Arqueologia Digital e Preservação Histórica por meio da "
                    "Emulação de Dispositivos Eletrônicos). Smaller files and the message archive "
                    "are at https://github.com/ArqueologiaDigital/Telegram_logs and "
                    "https://github.com/ArqueologiaDigital/Telegram_logs_media; "
                    "media/manifest.jsonl there maps each file to its message id and sha256."),
    "mediatype": "data",
    "subject": ["Telegram", "digital archaeology", "emulation", "preservation", "Brazil"],
    "language": "por",
}


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pending(manifest, only):
    out = [e for e in manifest.values() if e.get("store") == "ia" and not e.get("ia_url")]
    return [e for e in out if not only or e["msg_id"] in only]


def cmd_list(todo):
    archive, users = Archive(), load_users()
    print(f"{len(todo)} files pending, {sum(e['size'] for e in todo) / 1e9:.3f} GB, in {IA_STAGING}\n")
    for e in sorted(todo, key=lambda e: e["msg_id"]):
        rec = archive.get(e["msg_id"]) or {}
        raw = rec.get("raw") or {}
        uid = (raw.get("from_id") or {}).get("user_id")
        u = users.get(uid, {})
        who = str(uid)
        for n in [u] + list(reversed(u.get("names_seen", []))):   # current name, else last known one
            name = n.get("username") and "@" + n["username"] or " ".join(filter(None, [n.get("first_name"), n.get("last_name")]))
            if name:
                who = name + (" (deleted account)" if u.get("deleted") else "")
                break
        flag = "  [DELETED in Telegram]" if rec.get("archive", {}).get("deletion") else ""
        text = (raw.get("message") or "").replace("\n", " ")
        print(f"msg {e['msg_id']}  {rec.get('date', '?')}  {who}  {e['size'] / 1e6:.1f} MB{flag}")
        print(f"    {e['path']}")
        if text:
            print(f"    \"{text[:160]}{'…' if len(text) > 160 else ''}\"")


def record(item_id, todo, manifest):
    import internetarchive as ia
    files = {f["name"]: f for f in ia.get_item(item_id).files}
    n = 0
    for e in todo:
        f = files.get(e["path"])
        if f and f.get("md5") == md5(os.path.join(IA_STAGING, e["path"])):
            e["ia_url"] = f"https://archive.org/download/{item_id}/{e['path']}"
            n += 1
    save_manifest(manifest)
    print(f"recorded ia_url for {n} of {len(todo)} pending files")


def cmd_upload(item_id, todo, manifest):
    import internetarchive as ia
    item = ia.get_item(item_id)
    for e in todo:
        print(f"  uploading {e['path']} ({e['size'] / 1e6:.1f} MB)", flush=True)
        r = item.upload({e["path"]: os.path.join(IA_STAGING, e["path"])},
                        metadata=None if item.exists else METADATA,
                        verify=True, checksum=True, retries=5, retries_sleep=30)
        if any(x.status_code != 200 for x in r):
            print(f"  failed: {e['path']}")
        item = ia.get_item(item_id)
    record(item_id, todo, manifest)   # IA may take a while to list new files; rerun `record` later


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["list", "upload", "record"])
    p.add_argument("--item", default="arqueologiadigital-telegram-media")
    p.add_argument("--only", help="comma-separated message ids")
    a = p.parse_args()
    only = {int(x) for x in a.only.split(",")} if a.only else set()
    if a.cmd != "list":   # upload/record rewrite the manifest: never alongside the daily job
        import fcntl
        lock = open(os.path.join(CONF_DIR, "run.lock"), "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit("the daily sync (or a fetch) is running; try again when it finishes")
    manifest = load_manifest()
    todo = pending(manifest, only)
    if a.cmd == "list":
        cmd_list(todo)
    elif not todo:
        print("nothing pending")
    elif a.cmd == "upload":
        cmd_upload(a.item, todo, manifest)
    else:
        record(a.item, todo, manifest)


if __name__ == "__main__":
    main()
