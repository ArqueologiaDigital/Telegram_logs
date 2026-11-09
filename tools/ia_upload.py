#!/usr/bin/env python3
"""Upload the large media files (store == "ia") to one Internet Archive item, then record their URLs.

Question it answers: which large files are on archive.org, at what URL, and does the uploaded
copy have the checksum recorded in media/manifest.jsonl?

    ../.venv/bin/pip install internetarchive && ../.venv/bin/ia configure    # once, your IA account
    ../.venv/bin/python tools/ia_upload.py [--item arqueologiadigital-telegram-media] [--dry-run]

Files come from ../ia_staging/<path>, go to https://archive.org/download/<item>/<path>, and
`ia_url` is written into the manifest entry only once archive.org lists the file with the
same md5 as the local copy (IA publishes md5/sha1, not sha256; the sha256 stays in the manifest).
Re-running is safe: entries that already have ia_url are skipped.
"""
import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tg_media import IA_STAGING, load_manifest, save_manifest  # noqa: E402

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


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--item", default="arqueologiadigital-telegram-media")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    manifest = load_manifest()
    todo = [e for e in manifest.values() if e.get("store") == "ia" and not e.get("ia_url")]
    print(f"to upload: {len(todo)} files, {sum(e['size'] for e in todo) / 1e9:.3f} GB -> item {a.item}")
    if a.dry_run or not todo:
        return
    import internetarchive as ia
    item = ia.get_item(a.item)
    for e in todo:
        local = os.path.join(IA_STAGING, e["path"])
        remote = e["path"]
        r = item.upload({remote: local}, metadata=METADATA if not item.exists else None,
                        verify=True, checksum=True, retries=5, retries_sleep=30)
        if any(x.status_code != 200 for x in r):
            print(f"  failed: {remote}")
            continue
        item = ia.get_item(a.item)  # refresh file list
        f = next((x for x in item.files if x["name"] == remote), None)
        if f and f.get("md5") == md5(local):
            e["ia_url"] = f"https://archive.org/download/{a.item}/{remote}"
            save_manifest(manifest)
            print(f"  ok: {remote}")
        else:
            print(f"  uploaded but not yet listed or md5 differs, will retry: {remote}")
    print("done")


if __name__ == "__main__":
    main()
