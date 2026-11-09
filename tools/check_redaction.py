#!/usr/bin/env python3
"""Check that no phone number is stored anywhere in this repo. Run before every push.

Question it answers: does any tracked or about-to-be-committed file contain
  (a) a JSON key named "phone" or "phone_number" with a non-empty value, or
  (b) any of the phone numbers known from the 2018 dump (read from the old private repo,
      if its path is given), as a digit run, or
  (c) a "+<country><number>" string of 10-15 digits outside message text (reported as a
      count per file, for a human to look at; members sometimes post numbers on purpose)?

Numbers are never printed. Exit status 1 if (a) or (b) finds anything.

    python3 -I tools/check_redaction.py [../Telegram_logs-2018-private]
"""
import glob
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEGACY_NAME = "Arqueologia_Digital_e_Preservação_Histórica_por_meio_da_Emulação_de_Dispositivos_Eletrônicos"
PHONE_KEYS = {"phone", "phone_number"}
INTL = re.compile(r"\+\d{10,15}\b")


def known_numbers(old_repo):
    found = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in PHONE_KEYS and v:
                    found.add(re.sub(r"\D", "", str(v)))
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    with open(os.path.join(old_repo, "json", LEGACY_NAME + ".jsonl"), encoding="utf-8") as f:
        for line in f:
            walk(json.loads(line))
    return {n for n in found if len(n) >= 8}


def keys_with_values(o, hits):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in PHONE_KEYS and v:
                hits.append(k)
            keys_with_values(v, hits)
    elif isinstance(o, list):
        for v in o:
            keys_with_values(v, hits)


def strip_text(o):
    """Drop message bodies, so (c) only looks at structured fields."""
    if isinstance(o, dict):
        return {k: strip_text(v) for k, v in o.items() if k not in ("message", "text", "caption")}
    if isinstance(o, list):
        return [strip_text(v) for v in o]
    return o


def main():
    numbers = known_numbers(sys.argv[1]) if len(sys.argv) > 1 else set()
    bad = 0
    files = [p for p in glob.glob(os.path.join(REPO, "**", "*"), recursive=True)
             if os.path.isfile(p) and "/.git/" not in p and p.endswith((".json", ".jsonl", ".md", ".py", ".txt"))]
    for p in sorted(files):
        rel = os.path.relpath(p, REPO)
        text = open(p, encoding="utf-8", errors="replace").read()
        runs = set(re.findall(r"\d{8,}", text))
        n_known = sum(1 for n in numbers if any(n in r for r in runs))
        n_keys, n_intl = 0, 0
        if p.endswith(".jsonl"):
            for line in text.splitlines():
                o = json.loads(line)
                hits = []
                keys_with_values(o, hits)
                n_keys += len(hits)
                n_intl += len(INTL.findall(json.dumps(strip_text(o))))
        if n_known or n_keys:
            bad += 1
            print(f"FAIL {rel}: phone keys with values={n_keys}, known numbers={n_known}")
        elif n_intl:
            print(f"look {rel}: {n_intl} '+digits' strings in structured fields")
    print(f"checked {len(files)} files against {len(numbers)} known numbers: "
          f"{'FAIL' if bad else 'OK'} ({bad} files with problems)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
