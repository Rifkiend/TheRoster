#!/usr/bin/env python3
"""Rebuild team/index.json (GENERATED, never hand-edited) from the
sharded team-roster v2 records.

Usage:
    python3 rebuild_index.py [team]

Reads team/roster/, team/claims/, team/features/ (ids + one-line
descriptions + status) and writes team/index.json, sorted. Idempotent:
if the rebuilt content is identical to the existing file, the file is
left untouched and the script reports "unchanged".

team/index.json always contains:
    "_generated": "do not hand-edit; rebuilt by scripts/rebuild_index.py"

Agents load this compact index first (cold start) and fetch individual
record files only when relevant — at 100 agents / 500 features, loading
every record on every task would cost thousands of context tokens.

Stdlib only, Python 3.9+.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import team_common as tc

GENERATED_NOTE = "do not hand-edit; rebuilt by scripts/rebuild_index.py"


def build_index(team_dir):
    roster = []
    for stem, (_path, rec) in tc.load_records(os.path.join(team_dir, "roster")).items():
        if not isinstance(rec, dict):
            continue
        roster.append({
            "id": rec.get("id", stem),
            "name": rec.get("name", ""),
            "type": rec.get("type", ""),
            "status": rec.get("status", ""),
            "role": rec.get("role", ""),
        })
    claims = []
    for stem, (_path, rec) in tc.load_records(os.path.join(team_dir, "claims")).items():
        if not isinstance(rec, dict):
            continue
        claims.append({
            "task_id": rec.get("task_id", stem),
            "task": rec.get("task", ""),
            "owner_id": rec.get("owner_id", ""),
            "status": rec.get("status", ""),
        })
    features = []
    for stem, (_path, rec) in tc.load_records(os.path.join(team_dir, "features")).items():
        if not isinstance(rec, dict):
            continue
        features.append({
            "id": rec.get("id", stem),
            "name": rec.get("name", ""),
            "status": rec.get("status", ""),
            "version": rec.get("version", ""),
            "entry": rec.get("entry", ""),
            "does": rec.get("does", ""),
        })
    roster.sort(key=lambda r: r["id"])
    claims.sort(key=lambda c: c["task_id"])
    features.sort(key=lambda f: f["id"])
    return {
        "_generated": GENERATED_NOTE,
        "roster": roster,
        "claims": claims,
        "features": features,
    }


def rebuild(team_dir):
    """Rebuild team/index.json. Returns (changed: bool, index: dict)."""
    index = build_index(team_dir)
    path = os.path.join(team_dir, "index.json")
    text = json.dumps(index, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    existing = ""
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            existing = f.read()
    if existing == text:
        return False, index
    os.makedirs(team_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    return True, index


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    team_dir = sys.argv[1] if len(sys.argv) > 1 else "team"
    for sub in ("roster", "claims", "features"):
        if not os.path.isdir(os.path.join(team_dir, sub)):
            print(f"INDEX: warning — {os.path.join(team_dir, sub)} does not exist; treating it as empty.")
    changed, index = rebuild(team_dir)
    n = (len(index["roster"]), len(index["claims"]), len(index["features"]))
    print(f"INDEX: {'rebuilt' if changed else 'unchanged'} team/index.json — "
          f"{n[0]} roster member(s), {n[1]} claim(s), {n[2]} feature(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
