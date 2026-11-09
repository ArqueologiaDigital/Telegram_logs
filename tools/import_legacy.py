#!/usr/bin/env python3
"""Import the 2018 telegram-cli dump into the new archive, with phone numbers redacted.

Question it answers: what did the 2018 dump (telegram-cli + telegram-history-dump, message ids
1..4703) contain, in a form that is safe to publish?

Reads the legacy JSONL from a checkout of the old (now private) repo and writes:

  legacy/2018-telegram-cli/messages.jsonl   original records, every "phone" key removed,
                                            plus "msg_id" (the real Telegram message id
                                            decoded from telegram-cli's hex id)
  legacy/2018-telegram-cli/media_map.jsonl  msg_id -> legacy media file name, size, sha256
                                            (the files themselves go to the media repo in pass 2)

Then it verifies that none of the removed phone numbers occurs anywhere in the output, as a
digit string, and exits non-zero if one does. The numbers are never printed.

    python3 -I tools/import_legacy.py ../Telegram_logs-2018-private
"""
import hashlib
import json
import os
import struct
import sys

CHANNEL_ID = 1148437212
LEGACY_NAME = "Arqueologia_Digital_e_Preservação_Histórica_por_meio_da_Emulação_de_Dispositivos_Eletrônicos"


def strip_phones(obj, removed):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "phone":
                if v:
                    removed.add(str(v))
                continue
            out[k] = strip_phones(v, removed)
        return out
    if isinstance(obj, list):
        return [strip_phones(v, removed) for v in obj]
    return obj


def decode_msg_id(hexid):
    """telegram-cli ids: 4-byte peer type, 4-byte peer id, 8-byte msg id (all little endian)."""
    b = bytes.fromhex(hexid)
    peer = struct.unpack("<I", b[4:8])[0]
    assert peer == CHANNEL_ID, (hexid, peer)
    return struct.unpack("<Q", b[8:16])[0]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    src = os.path.abspath(sys.argv[1])
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(repo, "legacy", "2018-telegram-cli")
    os.makedirs(out_dir, exist_ok=True)
    media_dir = os.path.join(src, "media", LEGACY_NAME)

    removed = set()
    records, media = [], []
    with open(os.path.join(src, "json", LEGACY_NAME + ".jsonl"), encoding="utf-8") as f:
        for line in f:
            rec = strip_phones(json.loads(line), removed)
            rec["msg_id"] = decode_msg_id(rec["id"])
            m = rec.get("media") or {}
            if m.get("file"):
                name = os.path.basename(m["file"])
                m["file"] = name  # drop the absolute /home/... path
                p = os.path.join(media_dir, name)
                entry = {"msg_id": rec["msg_id"], "type": m.get("type"), "file": name}
                if os.path.exists(p):
                    entry.update(size=os.path.getsize(p), sha256=sha256(p))
                else:
                    entry["missing"] = True
                media.append(entry)
            records.append(rec)

    records.sort(key=lambda r: r["msg_id"])
    media.sort(key=lambda r: r["msg_id"])
    paths = []
    for name, rows in (("messages.jsonl", records), ("media_map.jsonl", media)):
        p = os.path.join(out_dir, name)
        with open(p, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
        paths.append(p)

    # Verification: no removed number may survive anywhere in the output, as a digit run.
    leaks = 0
    for p in paths:
        digits = "".join(c if c.isdigit() else " " for c in open(p, encoding="utf-8").read())
        runs = set(digits.split())
        leaks += sum(1 for n in removed if n in runs or any(n in r for r in runs if len(r) > len(n)))
    ids = [r["msg_id"] for r in records]
    print(f"messages: {len(records)}  msg_id range: {min(ids)}..{max(ids)}")
    print(f"media files referenced: {len(media)}  missing on disk: {sum(1 for m in media if m.get('missing'))}")
    print(f"distinct phone numbers removed: {len(removed)}  leaks found in output: {leaks}")
    sys.exit(1 if leaks else 0)


if __name__ == "__main__":
    main()
