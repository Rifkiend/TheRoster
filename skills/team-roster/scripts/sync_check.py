#!/usr/bin/env python3
"""Remote sync check for team-roster v2: close the blind spot between
two disconnected AIs working on the same feature. AI A pulls, works an
hour, then pushes — meanwhile AI B improved the same feature and
pushed, and A never saw it. This shrinks that window to seconds:
fetch the remote and compare, right before the push (the pre-push
hook runs it automatically, and the fetch happens INSIDE this script
— never run a bare `git pull` between the check and the push, or the
check is void).

Sharded layout (v2): remote records are compared per-record file via
`git show <remote>/<branch>:team/features/<id>.json` and
`git show <remote>/<branch>:team/claims/<id>.json`, so a remote change
to a record you never touched cannot block you.

Usage:
    python3 sync_check.py team/features team/claims [--branch main]
        [--remote origin] [--files f1 f2 ...] [--owner <my-roster-id>]
Exit 0 = local is up to date with the remote (or there is no remote
yet — first sync). Exit 1 = the remote is ahead: a feature version is
newer there, a feature was cancelled there while you hold an active
claim on its files, or a remote active claim covers one of your
--files and its owner is not you (--owner, or no local claim by that
owner).
Remote active claims use the v2 grace window: a remote claim blocks
only while it is active AND NOT stealable (now > expires_at + 15 min
AND heartbeat stale 30+ min).
If the fetch fails (no network), exit 0 with a WARNING — offline work
is not blocked, but GitHub will reject the push if you are behind.
--branch/--remote default to team/config.json's canonical_branch /
canonical_remote when that file exists.
Stdlib only, Python 3.9+.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import team_common as tc


def run(cmd):
    return tc.run_git(cmd)


def load_remote_file(remote, branch, path):
    r = run(["show", "%s/%s:%s" % (remote, branch, path)])
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def main():
    p = argparse.ArgumentParser(description="Check the remote before you push (team-roster v2).")
    p.add_argument("features", nargs="?", default="team/features")
    p.add_argument("claims", nargs="?", default="team/claims")
    p.add_argument("--branch", default="")
    p.add_argument("--remote", default="")
    p.add_argument("--files", nargs="*", default=[])
    p.add_argument("--owner", default="")
    args = p.parse_args()

    team_dir = tc.team_dir_of(args.features)
    cfg = tc.load_config(team_dir)
    branch = args.branch or cfg["canonical_branch"]
    remote = args.remote or cfg["canonical_remote"]

    # No remote configured at all -> nothing to compare against.
    if run(["remote", "get-url", remote]).returncode != 0:
        print("SYNC CHECK: OK — no remote to compare (no remote configured). "
              "This push is the first sync: push, and the next AI will see it.")
        return 0
    # Fetch fails (usually no network) -> warn, do not block offline work.
    # The fetch happens HERE, inside sync_check, immediately before the
    # comparison — never insert a `git pull` between check and push.
    if run(["fetch", remote, "--quiet"]).returncode != 0:
        print(f"SYNC CHECK: WARNING — could not fetch from {remote}; working offline.")
        print("Push at your own risk: GitHub will reject the push if you are behind.")
        return 0
    if run(["rev-parse", "--verify", "--quiet", "%s/%s" % (remote, branch)]).returncode != 0:
        print(f"SYNC CHECK: OK — no remote to compare (no {remote}/{branch} yet). "
              "This push is the first sync: push, and the next AI will see it.")
        return 0

    # Remote record inventory: which per-record files exist remotely.
    r = run(["ls-tree", "-r", "--name-only", "%s/%s" % (remote, branch),
             "--", args.features, args.claims])
    remote_paths = [ln for ln in r.stdout.splitlines() if ln.strip()] if r.returncode == 0 else []
    remote_feature_ids = sorted(
        os.path.basename(p)[:-5] for p in remote_paths
        if p.startswith(args.features.rstrip("/") + "/") and p.endswith(".json"))
    remote_claim_ids = sorted(
        os.path.basename(p)[:-5] for p in remote_paths
        if p.startswith(args.claims.rstrip("/") + "/") and p.endswith(".json"))

    local_features = {}
    for stem, (_path, rec) in tc.load_records(args.features).items():
        if isinstance(rec, dict):
            local_features[stem] = rec
    local_claims = {}
    for stem, (_path, rec) in tc.load_records(args.claims).items():
        if isinstance(rec, dict):
            local_claims[stem] = rec

    errors, notes = [], []

    # 1. Feature versions: remote newer than local = you are behind.
    cancelled_remote = {}
    for fid in remote_feature_ids:
        rf = load_remote_file(remote, branch,
                              os.path.join(args.features, fid + ".json"))
        if not isinstance(rf, dict):
            notes.append("remote feature %s is unreadable — pull and inspect it before building on it" % fid)
            continue
        if rf.get("status") == "cancelled":
            cancelled_remote[fid] = rf
        if fid not in local_features:
            notes.append("new on remote: %s v%s — pull it before you build on it." % (fid, rf.get("version")))
            continue
        rv = tc.parse_semver(rf.get("version"))
        lv = tc.parse_semver(local_features[fid].get("version"))
        if rv is not None and lv is not None and rv > lv:
            errors.append("%s: remote v%s is NEWER than your local v%s (changed by %s)"
                          % (fid, rf.get("version"), local_features[fid].get("version"),
                             (rf.get("contributors") or [None])[-1]))

    # 1b. Cancelled on the remote: a feature cancelled upstream must
    # stop local work on its files. A cancellation is visible only at
    # the next fetch — this is the control that makes that delay
    # safe: you discover it here, before the push, not after it.
    # (A canceller also releases overlapping claims in the same push;
    # this catches a claim made before the cancellation landed.)
    for fid, rf in sorted(cancelled_remote.items()):
        rfiles = rf.get("files") or []
        if not rfiles:
            continue
        for tid, lc in sorted(local_claims.items()):
            if not tc.is_blocking(lc, cfg=cfg):
                continue
            overlap = sorted({f for f in (lc.get("files") or []) for g in rfiles
                              if tc.paths_overlap(f, g)})
            if not overlap:
                continue
            errors.append("feature %s was CANCELLED on the remote by %s (%s) — "
                          "your claim %s still touches %s; release it and stop work"
                          % (fid, rf.get("status_changed_by"), rf.get("status_reason"),
                             tid, ", ".join(overlap)))

    # 2. Claims: a remote active (not stealable) claim on your files,
    # owned by someone else = stop. "Someone else" is judged by
    # --owner when given; when it is not, by whether a local claim by
    # that same owner covers it.
    if args.files:
        wanted = set(args.files)
        for tid in remote_claim_ids:
            rc = load_remote_file(remote, branch,
                                  os.path.join(args.claims, tid + ".json"))
            if not isinstance(rc, dict):
                continue
            if not tc.is_blocking(rc, cfg=cfg):
                continue
            overlap = sorted({f for f in wanted for g in (rc.get("files") or [])
                              if tc.paths_overlap(f, g)})
            if not overlap:
                continue
            mine = (rc.get("owner_id") == args.owner if args.owner else
                    any(c.get("owner_id") == rc.get("owner_id")
                        and any(tc.paths_overlap(f, g) for f in (c.get("files") or []) for g in overlap)
                        for c in local_claims.values() if tc.is_blocking(c, cfg=cfg)))
            if not mine:
                errors.append("file(s) %s are claimed on the remote by %s (%s, until %s)"
                              % (", ".join(overlap), rc.get("owner_id"),
                                 rc.get("task_id"), rc.get("expires_at")))

    for n in notes:
        print(f"SYNC CHECK: note — {n}")
    if errors:
        print("SYNC CHECK: FAIL — the remote moved while you were working:")
        print("  - " + "\n  - ".join(errors))
        print("STOP: git fetch + rebase onto %s/%s, re-read team/features and team/claims, then continue"
              % (remote, branch))
        print("on top of the new version — or stop if it conflicts. Never --force push.")
        return 1
    print("SYNC CHECK: OK — local matches the remote. Safe to push.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
