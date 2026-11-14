#!/usr/bin/env python3
"""Remote sync check for team-roster: close the blind spot between two
disconnected AIs working on the same feature. AI A pulls, works an hour,
then pushes — meanwhile AI B improved the same feature and pushed, and
A never saw it. This shrinks that window to seconds: fetch the remote
and compare, right before the push (the pre-push hook runs it automatically).

Usage:
    python3 sync_check.py team/features.json team/work.json [--branch main]
        [--remote origin] [--files f1 f2 ...] [--owner <my-roster-id>]
Exit 0 = local is up to date with the remote (or there is no remote
yet — first sync). Exit 1 = the remote is ahead: a feature version is
newer there, or a remote active claim covers one of your --files and
its owner is not you (--owner, or no local claim by that owner).
If the fetch fails (no network), exit 0 with a WARNING — offline work
is not blocked, but GitHub will reject the push if you are behind.
Stdlib only, Python 3.9+.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def parse_time(s):
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def semver(s):
    try:
        parts = [int(x) for x in str(s).split(".")]
        return tuple(parts + [0] * (3 - len(parts)))
    except ValueError:
        return (0, 0, 0)


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_remote(remote, branch, path):
    r = run(["git", "show", f"{remote}/{branch}:{path}"])
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def active_claims(data):
    t = datetime.now(timezone.utc)
    out = []
    for c in data.get("claims", []):
        if c.get("status") != "doing":
            continue
        exp = parse_time(c.get("expires_at", ""))
        if exp and exp <= t:
            continue  # expired — does not block
        out.append(c)
    return out


def main():
    p = argparse.ArgumentParser(description="Check the remote before you push.")
    p.add_argument("features", nargs="?", default="team/features.json")
    p.add_argument("work", nargs="?", default="team/work.json")
    p.add_argument("--branch", default="main")
    p.add_argument("--remote", default="origin")
    p.add_argument("--files", nargs="*", default=[])
    p.add_argument("--owner", default="")
    args = p.parse_args()

    # No remote configured at all -> nothing to compare against.
    if run(["git", "remote", "get-url", args.remote]).returncode != 0:
        print("SYNC CHECK: OK — no remote to compare (no remote configured). "
              "This push is the first sync: push, and the next AI will see it.")
        return 0
    # Fetch fails (usually no network) -> warn, do not block offline work.
    if run(["git", "fetch", args.remote, "--quiet"]).returncode != 0:
        print(f"SYNC CHECK: WARNING — could not fetch from {args.remote}; working offline.")
        print("Push at your own risk: GitHub will reject the push if you are behind.")
        return 0

    remote_features = load_remote(args.remote, args.branch, args.features)
    remote_work = load_remote(args.remote, args.branch, args.work)
    if remote_features is None and remote_work is None:
        print(f"SYNC CHECK: OK — no remote to compare (no {args.remote}/{args.branch} yet). "
              "This push is the first sync: push, and the next AI will see it.")
        return 0

    local_features = load_json(args.features)
    local_work = load_json(args.work)
    errors, notes = [], []

    # 1. Feature versions: remote newer than local = you are behind.
    local_by_id = {f.get("id"): f for f in local_features.get("features", [])}
    for rf in (remote_features or {}).get("features", []):
        fid = rf.get("id")
        rv, lv = semver(rf.get("version")), semver(local_by_id.get(fid, {}).get("version"))
        if fid not in local_by_id:
            notes.append(f"new on remote: {fid} v{rf.get('version')} — pull it before you build on it.")
        elif rv > lv:
            errors.append(f"{fid}: remote v{rf.get('version')} is NEWER than your local "
                          f"v{local_by_id[fid].get('version')} (changed by "
                          f"{rf.get('contributors', [None])[-1]})")

    # 2. Claims: a remote active claim on your files, owned by someone
    # else = stop. "Someone else" is judged by --owner when given; when
    # it is not, by whether a local claim by that same owner covers it.
    if args.files:
        wanted = set(args.files)
        my_claims = active_claims(local_work)
        for rc in active_claims(remote_work or {}):
            overlap = sorted(set(rc.get("files", [])) & wanted)
            if not overlap:
                continue
            mine = (rc.get("owner_id") == args.owner if args.owner else
                    any(c.get("owner_id") == rc.get("owner_id") and
                        set(c.get("files", [])) & set(overlap) for c in my_claims))
            if not mine:
                errors.append(f"file(s) {', '.join(overlap)} are claimed on the remote by "
                              f"{rc.get('owner_id')} ({rc.get('task_id')}, until {rc.get('expires_at')})")

    [print(f"SYNC CHECK: note — {n}") for n in notes]
    if errors:
        print("SYNC CHECK: FAIL — the remote moved while you were working:")
        print("  - " + "\n  - ".join(errors))
        print("STOP: git pull, re-read team/features.json and team/work.json, then continue")
        print("on top of the new version — or stop if it conflicts. Never --force push.")
        return 1
    print("SYNC CHECK: OK — local matches the remote. Safe to push.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
