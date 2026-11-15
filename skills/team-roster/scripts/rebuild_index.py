#!/usr/bin/env python3
"""Rebuild team/index.json (GENERATED, never hand-edited) from the
sharded team-roster v2 records.

Usage:
    python3 rebuild_index.py [team]

Reads team/roster/, team/claims/, team/features/, team/prd/,
team/fanout/, team/gates/, team/receipts/, team/reviews/ (ids +
one-line descriptions + status) and writes team/index.json, sorted.
Idempotent: if the rebuilt content is identical to the existing file,
the file is left untouched and the script reports "unchanged". A team
without team/prd/ (pre-v2.2) rebuilds with an empty prd list; a team
without team/fanout/, team/gates/, team/receipts/ or team/reviews/
(pre-v2.6) rebuilds with empty lists for those.

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
            "prd_item": rec.get("prd_item", ""),
            # v2.3: where the work happens ("" = shared checkout).
            "branch": rec.get("branch", ""),
            "worktree": rec.get("worktree", ""),
            # v2.4: creation order ("" = pre-v2.4 record, ordered by lamport).
            "created_seq": rec.get("created_seq", ""),
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
            "contract": rec.get("contract", False),
        })
    prd = []
    for stem, (_path, rec) in tc.load_records(os.path.join(team_dir, "prd")).items():
        if not isinstance(rec, dict):
            continue
        prd.append({
            "id": rec.get("id", stem),
            "title": rec.get("title", ""),
            "status": rec.get("status", ""),
            "features": rec.get("features") or [],
        })
    # v2.6: fan-out groups, decision gates, completion receipts,
    # formal reviews — one line each, so an agent sees what is open
    # without loading every record.
    fanout = []
    for stem, (_path, rec) in tc.load_records(os.path.join(team_dir, "fanout")).items():
        if not isinstance(rec, dict):
            continue
        fanout.append({
            "id": rec.get("id", stem),
            "title": rec.get("title", ""),
            "status": rec.get("status", ""),
            "members": [m.get("task_id") for m in (rec.get("members") or [])
                        if isinstance(m, dict)],
            "winner_task_id": rec.get("winner_task_id", ""),
        })
    gates = []
    for stem, (_path, rec) in tc.load_records(os.path.join(team_dir, "gates")).items():
        if not isinstance(rec, dict):
            continue
        gates.append({
            "id": rec.get("id", stem),
            "question": rec.get("question", ""),
            "status": rec.get("status", ""),
            "blocks_kind": rec.get("blocks_kind", ""),
            "blocks_id": rec.get("blocks_id", ""),
        })
    receipts = []
    for stem, (_path, rec) in tc.load_records(os.path.join(team_dir, "receipts")).items():
        if not isinstance(rec, dict):
            continue
        receipts.append({
            "task_id": rec.get("task_id", stem),
            "outcome": rec.get("outcome", ""),
            "summary": rec.get("summary", ""),
            "done_by": rec.get("done_by", ""),
        })
    reviews = []
    for stem, (_path, rec) in tc.load_records(os.path.join(team_dir, "reviews")).items():
        if not isinstance(rec, dict):
            continue
        reviews.append({
            "id": rec.get("id", stem),
            "task_id": rec.get("task_id", ""),
            "prd_item": rec.get("prd_item", ""),
            "feature_id": rec.get("feature_id", ""),
            "reviewer": rec.get("reviewer", ""),
            "verdict": rec.get("verdict", ""),
        })
    roster.sort(key=lambda r: r["id"])
    claims.sort(key=lambda c: c["task_id"])
    features.sort(key=lambda f: f["id"])
    prd.sort(key=lambda p: p["id"])
    fanout.sort(key=lambda g: g["id"])
    gates.sort(key=lambda g: g["id"])
    receipts.sort(key=lambda r: r["task_id"])
    reviews.sort(key=lambda r: r["id"])
    return {
        "_generated": GENERATED_NOTE,
        "roster": roster,
        "claims": claims,
        "features": features,
        "prd": prd,
        "fanout": fanout,
        "gates": gates,
        "receipts": receipts,
        "reviews": reviews,
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
    n = (len(index["roster"]), len(index["claims"]), len(index["features"]), len(index["prd"]),
         len(index["fanout"]), len(index["gates"]), len(index["receipts"]), len(index["reviews"]))
    print(f"INDEX: {'rebuilt' if changed else 'unchanged'} team/index.json — "
          f"{n[0]} roster member(s), {n[1]} claim(s), {n[2]} feature(s), {n[3]} PRD item(s), "
          f"{n[4]} fan-out group(s), {n[5]} gate(s), {n[6]} receipt(s), {n[7]} review(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
