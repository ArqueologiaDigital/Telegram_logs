#!/usr/bin/env python3
"""Cross-check the 2018 telegram-cli dump against the new Telethon archive.

Question it answers: does every 2018 message still exist, with the same text, and how many
message ids in the whole history are gone (deleted before this archive started)?

    python3 -I notes/probes/legacy_vs_new.py
"""
import glob, json, os
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
new = {}
for p in glob.glob(os.path.join(REPO, "messages", "*", "*.jsonl")):
    for l in open(p, encoding="utf-8"):
        r = json.loads(l)
        new[r["id"]] = r
leg = [json.loads(l) for l in open(os.path.join(REPO, "legacy", "2018-telegram-cli", "messages.jsonl"), encoding="utf-8")]
missing = [r["msg_id"] for r in leg if r["msg_id"] not in new]
same = diff = 0
for r in leg:
    n = new.get(r["msg_id"])
    if n and n["raw"] and "text" in r:
        if (n["raw"].get("message") or "").strip() == r["text"].strip():
            same += 1
        else:
            diff += 1
top = max(new)
print(f"legacy messages: {len(leg)}; missing from new archive: {len(missing)}")
print(f"legacy text vs new text: same {same}, different {diff}")
print(f"ids 1..4703 present now: {sum(1 for i in range(1, 4704) if i in new)}")
print(f"ids 1..{top}: present {len(new)}, absent {top - len(new)} (deleted before 2026-11-09, unrecoverable)")
