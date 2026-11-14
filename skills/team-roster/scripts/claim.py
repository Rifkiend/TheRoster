#!/usr/bin/env python3
"""Work ledger for team-roster: claim work before you do it, so two
disconnected AIs / humans don't edit the same file or task at once.

No server. Git is the sync: pull -> claim -> push the claim -> work.
First push wins; a rejected push means someone claimed it first — pull,
re-read, and stop if it conflicts.

Usage:
    python3 claim.py list   [team/work.json]
    python3 claim.py check  [team/work.json] [--roster team/roster.json]
    python3 claim.py claim  --task t-002 --title "Kerjakan checkout" \
        --owner dev-1 --files src/checkout.py tests/test_checkout.py \
        [--branch feat/checkout] [--hours 2] [--work team/work.json]
    python3 claim.py release --task t-002 [--work team/work.json]
    python3 claim.py done    --task t-002 [--work team/work.json]

NOTE: the work file is --work, not a trailing positional argument, because
--files takes multiple values and would swallow a trailing path.

Exit 0 = ok. Exit 1 = conflict / invalid / unknown owner.
Claim lifetime: 2 hours by default. Expired claims don't block anyone.
Stdlib only.
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

DEFAULT_HOURS = 2


def parse_time(s):
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def now():
    return datetime.now(timezone.utc)


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"team_name": "", "updated": "", "claims": []}
    except json.JSONDecodeError as e:
        print(f"CLAIM: FAIL — {path} is not valid JSON: {e}")
        sys.exit(1)


def save(path, data):
    data["updated"] = now().date().isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def active_claims(data):
    t = now()
    out = []
    for c in data.get("claims", []):
        if c.get("status") != "doing":
            continue
        exp = parse_time(str(c.get("expires_at", "")))
        if exp and exp <= t:
            continue  # expired — does not block
        out.append(c)
    return out


def find_conflicts(data, files, exclude_task=None):
    conflicts = []
    for c in active_claims(data):
        if exclude_task and c.get("task_id") == exclude_task:
            continue
        overlap = sorted(set(c.get("files", [])) & set(files))
        if overlap:
            conflicts.append((c, overlap))
    return conflicts


def cmd_list(path):
    data = load(path)
    claims = active_claims(data)
    if not claims:
        print("CLAIM: no one is working on anything right now (no active claims).")
        return 0
    print(f"CLAIM: {len(claims)} active claim(s):")
    for c in claims:
        print(f"  - {c.get('task_id')}: {c.get('task')} — {c.get('owner_id')} "
              f"until {c.get('expires_at')} — files: {', '.join(c.get('files', []))}")
    return 0


def cmd_check(path, roster_path):
    data = load(path)
    errors = []
    if not isinstance(data.get("claims", None), list):
        print("CLAIM CHECK: FAIL — claims must be a list")
        return 1
    roster_ids = None
    if roster_path:
        try:
            roster = json.load(open(roster_path, encoding="utf-8"))
            roster_ids = {m["id"] for m in roster.get("members", [])
                          if m.get("status") == "active"}
        except (FileNotFoundError, json.JSONDecodeError) as e:
            errors.append(f"cannot read roster {roster_path}: {e}")
    seen_files = {}
    for c in active_claims(data):
        for field in ("task_id", "task", "owner_id", "files", "started_at", "expires_at", "status"):
            if field not in c or c[field] in (None, "", []):
                errors.append(f"{c.get('task_id', '?')}: missing/empty field: {field}")
        if roster_ids is not None and c.get("owner_id") not in roster_ids:
            errors.append(f"{c.get('task_id')}: owner {c.get('owner_id')} is not an active roster member")
        for f in c.get("files", []):
            if f in seen_files:
                errors.append(f"CONFLICT: file {f} claimed by both {seen_files[f]} and {c.get('task_id')} ({c.get('owner_id')})")
            else:
                seen_files[f] = c.get("task_id")
    if errors:
        print("CLAIM CHECK: FAIL — do not push until this is fixed:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"CLAIM CHECK: OK — {len(active_claims(data))} active claim(s), no file claimed twice.")
    return 0


def cmd_claim(args, path):
    data = load(path)
    files = sorted(set(args.files))
    if not files:
        print("CLAIM: FAIL — give at least one --files entry")
        return 1
    conflicts = find_conflicts(data, files, exclude_task=args.task)
    if conflicts:
        print("CLAIM: FAIL — someone else is already working on this:")
        for c, overlap in conflicts:
            print(f"  - {c.get('task_id')} ({c.get('owner_id')}, until {c.get('expires_at')}): {', '.join(overlap)}")
        print("Pick other work, or wait until their claim expires / is released.")
        return 1
    # Renewal: same task_id re-claims (extend expiry, update files)
    for c in data.get("claims", []):
        if c.get("task_id") == args.task and c.get("status") == "doing":
            c["files"] = files
            c["expires_at"] = (now() + timedelta(hours=args.hours)).isoformat()
            save(path, data)
            print(f"CLAIM: renewed {args.task} for {args.hours}h — files: {', '.join(files)}")
            print("Remember: commit + push team/work.json NOW, before you start working.")
            return 0
    start = now()
    data.setdefault("claims", []).append({
        "task_id": args.task,
        "task": args.title,
        "owner_id": args.owner,
        "files": files,
        "branch": args.branch or "",
        "started_at": start.isoformat(),
        "expires_at": (start + timedelta(hours=args.hours)).isoformat(),
        "status": "doing",
    })
    save(path, data)
    print(f"CLAIM: OK — {args.task} claimed by {args.owner} for {args.hours}h — files: {', '.join(files)}")
    print("Remember: commit + push team/work.json NOW, before you start working.")
    return 0


def cmd_set_status(args, path, status):
    data = load(path)
    for c in data.get("claims", []):
        if c.get("task_id") == args.task:
            c["status"] = status
            save(path, data)
            print(f"CLAIM: {args.task} -> {status}. Commit + push team/work.json.")
            return 0
    print(f"CLAIM: FAIL — no claim with task_id {args.task}")
    return 1


def main():
    p = argparse.ArgumentParser(description="Claim work so teammates don't collide.")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("list", "check"):
        sp = sub.add_parser(name)
        sp.add_argument("path", nargs="?", default="team/work.json")
        if name == "check":
            sp.add_argument("--roster", default="team/roster.json")
    sp = sub.add_parser("claim")
    sp.add_argument("--task", required=True)
    sp.add_argument("--title", required=True)
    sp.add_argument("--owner", required=True)
    sp.add_argument("--files", nargs="+", required=True)
    sp.add_argument("--branch", default="")
    sp.add_argument("--hours", type=float, default=DEFAULT_HOURS)
    sp.add_argument("--work", default="team/work.json")
    sp = sub.add_parser("release")
    sp.add_argument("--task", required=True)
    sp.add_argument("--work", default="team/work.json")
    sp = sub.add_parser("done")
    sp.add_argument("--task", required=True)
    sp.add_argument("--work", default="team/work.json")

    args = p.parse_args()
    if args.cmd == "list":
        return cmd_list(args.path)
    if args.cmd == "check":
        return cmd_check(args.path, args.roster)
    if args.cmd == "claim":
        return cmd_claim(args, args.work)
    if args.cmd == "release":
        return cmd_set_status(args, args.work, "released")
    if args.cmd == "done":
        return cmd_set_status(args, args.work, "done")
    return 1


if __name__ == "__main__":
    sys.exit(main())
