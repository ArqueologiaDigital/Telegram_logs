#!/usr/bin/env python3
"""Media bill split by size threshold: what fits in a git repo vs what needs other hosting.

Question it answers: for pass 2, how many files and bytes are at or under / over each threshold?
Uses media_size() from tools/tg_sync.py (largest photo size; document size).

    ../.venv/bin/python notes/probes/media_split.py
"""
import glob, json, os, sys
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "tools"))
from tg_sync import media_size
sizes = []
for p in glob.glob(os.path.join(REPO, "messages", "*", "*.jsonl")):
    for l in open(p, encoding="utf-8"):
        r = json.loads(l)
        k, s = media_size((r.get("raw") or {}).get("media"))
        if s:
            sizes.append((k, s))
for t in (10e6, 25e6, 50e6, 100e6):
    lo = [s for _, s in sizes if s <= t]; hi = [s for _, s in sizes if s > t]
    print(f"<= {t/1e6:>4.0f} MB: {len(lo):>5} files {sum(lo)/1e9:6.3f} GB | > : {len(hi):>4} files {sum(hi)/1e9:6.3f} GB")
