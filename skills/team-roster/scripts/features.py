#!/usr/bin/env python3
"""Feature registry for team-roster: search before you build, reuse what
exists, register what you build, never delete what others use.

One capability = one entry in team/features.json. A feature that is not
registered does not exist for the next AI — it will rebuild it, and the
team ends up with three logins and zero maintained ones.

Usage:
    python3 features.py list   [team/features.json]
    python3 features.py search <keyword> [team/features.json]
    python3 features.py check  [team/features.json] [--roster team/roster.json]
    python3 features.py register --id login-api --name "Login API" \
        --does "Login with email + password, returns a session token" \
        --entry src/auth/login.py:login \
        --files src/auth/login.py tests/test_login.py \
        --owner rian --keywords login auth sesi [--status stable] \
        [--version 1.0.0] [--work team/features.json]
    python3 features.py use --id login-api --by t-002 [--work team/features.json]
    python3 features.py improve --id login-api --by rean --bump minor \
        --note "Tambah login Google" [--does "Login email/password atau Google"] \
        [--files src/auth/google.py] [--work team/features.json]
    python3 features.py deprecate --id old-login [--replacement login-api] \
        [--work team/features.json]

Version bumps for improve: patch = bug fix, same entry (1.0.0 -> 1.0.1);
minor = new compatible capability, same entry (1.0.0 -> 1.1.0, default);
major = entry changes / old callers break (1.0.0 -> 2.0.0). If --entry changes,
--bump must be major.

Exit 0 = ok / found. Exit 1 = invalid, duplicate, or not found (for use/deprecate).
Search exit 0 even with no matches — it prints "no match" instead of failing,
because "not found" is a valid answer that means "you may build it".
Stdlib only.
"""
import argparse
import json
import re
import sys
from datetime import date

STATUSES = {"stable", "experimental", "deprecated"}
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def bump_version(version, bump):
    if not SEMVER_RE.match(version or ""):
        return None
    major, minor, patch = (int(x) for x in version.split("."))
    if bump == "patch":
        patch += 1
    elif bump == "minor":
        minor += 1
        patch = 0
    elif bump == "major":
        major += 1
        minor = 0
        patch = 0
    else:
        return None
    return f"{major}.{minor}.{patch}"


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"team_name": "", "updated": "", "features": []}
    except json.JSONDecodeError as e:
        print(f"FEATURES: FAIL — {path} is not valid JSON: {e}")
        sys.exit(1)


def save(path, data):
    data["updated"] = date.today().isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def find(data, fid):
    for feat in data.get("features", []):
        if feat.get("id") == fid:
            return feat
    return None


def score(feat, query):
    q = query.lower()
    text = " ".join([
        feat.get("id", ""), feat.get("name", ""), feat.get("does", ""),
        " ".join(feat.get("keywords", [])),
    ]).lower()
    if q in feat.get("id", "").lower():
        return 3
    if q in feat.get("name", "").lower():
        return 2
    if q in text:
        return 1
    return 0


def cmd_list(path):
    data = load(path)
    feats = data.get("features", [])
    if not feats:
        print("FEATURES: registry is empty — nothing built yet. Build, then register it.")
        return 0
    print(f"FEATURES: {len(feats)} feature(s):")
    for f in feats:
        print(f"  - {f.get('id')} [{f.get('status')}] v{f.get('version')} — {f.get('does')} (entry: {f.get('entry')}, used by: {', '.join(f.get('used_by', [])) or 'nobody yet'})")
    return 0


def cmd_search(path, query):
    data = load(path)
    scored = [(score(f, query), f) for f in data.get("features", [])]
    hits = sorted([s for s in scored if s[0] > 0], reverse=True)
    if not hits:
        print(f"FEATURES: no match for '{query}' — you may build it, then register it.")
        print("Try 2-3 different keywords first (e.g. login, then auth, then sesi) — one empty search does not prove it does not exist.")
        return 0
    print(f"FEATURES: {len(hits)} match(es) for '{query}' — REUSE, do not rebuild:")
    for _, f in hits:
        print(f"  - {f.get('id')} [{f.get('status')}] — {f.get('does')}")
        print(f"    entry: {f.get('entry')} | owner: {f.get('owner_id')} | used by: {', '.join(f.get('used_by', [])) or 'nobody yet'}")
        if f.get("status") == "deprecated":
            print("    WARNING: deprecated — do not use for new work.")
        elif f.get("status") == "experimental":
            print("    NOTE: experimental — use with care, or improve it in place instead of copying it.")
    return 0


def cmd_check(path, roster_path):
    data = load(path)
    errors, warnings = [], []
    feats = data.get("features")
    if not isinstance(feats, list):
        print("FEATURES CHECK: FAIL — features must be a list")
        return 1
    roster_ids = None
    if roster_path:
        try:
            roster = json.load(open(roster_path, encoding="utf-8"))
            roster_ids = {m["id"] for m in roster.get("members", [])
                          if m.get("status") == "active"}
        except (FileNotFoundError, json.JSONDecodeError) as e:
            errors.append(f"cannot read roster {roster_path}: {e}")
    seen_ids, seen_entries = set(), {}
    for f in feats:
        label = f.get("id") or f.get("name") or "?"
        for field in ("id", "name", "does", "entry", "files", "keywords", "owner_id", "status", "version", "last_verified"):
            if field not in f or f[field] in (None, "", []):
                errors.append(f"{label}: missing/empty field: {field}")
        if f.get("id") in seen_ids:
            errors.append(f"{label}: duplicate id")
        seen_ids.add(f.get("id"))
        if f.get("entry") in seen_entries:
            errors.append(f"{label}: duplicate entry {f.get('entry')} — already used by {seen_entries[f.get('entry')]}. Reuse that feature instead of registering a second one.")
        seen_entries[f.get("entry")] = f.get("id")
        if f.get("status") and f["status"] not in STATUSES:
            errors.append(f"{label}: status must be stable, experimental, or deprecated")
        if f.get("version") and not SEMVER_RE.match(str(f["version"])):
            errors.append(f"{label}: version must be semver x.y.z (got {f.get('version')})")
        if roster_ids is not None and f.get("owner_id") not in roster_ids:
            errors.append(f"{label}: owner {f.get('owner_id')} is not an active roster member")
        if f.get("status") == "deprecated" and f.get("used_by"):
            warnings.append(f"{label}: deprecated but still used by {', '.join(f['used_by'])} — do not delete it.")
    for w in warnings:
        print(f"  ! {w}")
    if errors:
        print("FEATURES CHECK: FAIL — do not push until this is fixed:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"FEATURES CHECK: OK — {len(feats)} feature(s), no duplicate id or entry.")
    return 0


def cmd_register(args):
    data = load(args.work)
    if find(data, args.id):
        print(f"FEATURES: FAIL — id {args.id} already exists. Use it instead of registering a duplicate.")
        print("Run: python3 features.py search " + " ".join(args.keywords))
        return 1
    for f in data.get("features", []):
        if f.get("entry") == args.entry:
            print(f"FEATURES: FAIL — entry {args.entry} is already registered as {f.get('id')}. Reuse that feature.")
            return 1
    data.setdefault("features", []).append({
        "id": args.id,
        "name": args.name,
        "does": args.does,
        "entry": args.entry,
        "files": sorted(set(args.files)),
        "keywords": sorted(set(args.keywords)),
        "owner_id": args.owner,
        "contributors": [args.owner],
        "status": args.status,
        "version": args.version,
        "used_by": [],
        "changes": [{"version": args.version, "by": args.owner,
                     "note": "First version", "date": date.today().isoformat()}],
        "last_verified": date.today().isoformat(),
    })
    save(args.work, data)
    print(f"FEATURES: registered {args.id} [{args.status}] — entry {args.entry}.")
    print("Remember: commit + push team/features.json so the next AI can find it.")
    return 0


def cmd_use(args):
    data = load(args.work)
    f = find(data, args.id)
    if not f:
        print(f"FEATURES: FAIL — no feature with id {args.id}")
        return 1
    if f.get("status") == "deprecated":
        print(f"FEATURES: WARNING — {args.id} is deprecated. Do not use it for new work.")
        return 1
    if args.by not in f.setdefault("used_by", []):
        f["used_by"].append(args.by)
    save(args.work, data)
    print(f"FEATURES: {args.id} is now used by {args.by} (entry: {f.get('entry')}).")
    return 0


def cmd_improve(args):
    """Continue / improve an existing feature in place — do not fork it.

    Rules enforced here:
    - deprecated features cannot be improved (use their replacement).
    - changing --entry requires --bump major (old callers would break).
    - version always goes up; owner stays the original owner, the improver
      is added to contributors and to the changes log.
    Claim the feature's files with claim.py BEFORE improving — this script
    cannot see the work ledger, the SKILL.md workflow enforces that step.
    """
    data = load(args.work)
    f = find(data, args.id)
    if not f:
        print(f"FEATURES: FAIL — no feature with id {args.id}. Search first, then register it if it truly does not exist.")
        return 1
    if f.get("status") == "deprecated":
        print(f"FEATURES: FAIL — {args.id} is deprecated. Improve its replacement instead, or ask the owner to un-deprecate it.")
        return 1
    old_version = f.get("version", "1.0.0")
    new_version = bump_version(old_version, args.bump)
    if new_version is None:
        print(f"FEATURES: FAIL — current version {old_version} is not semver (x.y.z), cannot bump it.")
        return 1
    if args.entry and args.entry != f.get("entry") and args.bump != "major":
        print(f"FEATURES: FAIL — changing entry {f.get('entry')} -> {args.entry} breaks old callers, so --bump must be major, not {args.bump}.")
        return 1
    if args.entry:
        for other in data.get("features", []):
            if other.get("id") != args.id and other.get("entry") == args.entry:
                print(f"FEATURES: FAIL — entry {args.entry} is already used by {other.get('id')}.")
                return 1
        f["entry"] = args.entry
    if args.does:
        f["does"] = args.does
    if args.files:
        f["files"] = sorted(set(f.get("files", [])) | set(args.files))
    if args.keywords:
        f["keywords"] = sorted(set(f.get("keywords", [])) | set(args.keywords))
    if args.by not in f.setdefault("contributors", [f.get("owner_id")]):
        f["contributors"].append(args.by)
    f["version"] = new_version
    f["last_verified"] = date.today().isoformat()
    f.setdefault("changes", []).append({
        "version": new_version, "by": args.by,
        "note": args.note or f"Improved ({args.bump})", "date": date.today().isoformat(),
    })
    save(args.work, data)
    print(f"FEATURES: {args.id} improved {old_version} -> {new_version} by {args.by} (entry: {f.get('entry')}).")
    print("Remember: commit + push team/features.json so the next AI builds on this version, not the old one.")
    return 0


def cmd_deprecate(args):
    data = load(args.work)
    f = find(data, args.id)
    if not f:
        print(f"FEATURES: FAIL — no feature with id {args.id}")
        return 1
    f["status"] = "deprecated"
    if args.replacement:
        f["replacement"] = args.replacement
    save(args.work, data)
    print(f"FEATURES: {args.id} deprecated (kept, not deleted). Used by: {', '.join(f.get('used_by', [])) or 'nobody'}." +
          (f" Replacement: {args.replacement}." if args.replacement else ""))
    return 0


def main():
    p = argparse.ArgumentParser(description="Search before you build; reuse what exists.")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("list", "check"):
        sp = sub.add_parser(name)
        sp.add_argument("path", nargs="?", default="team/features.json")
        if name == "check":
            sp.add_argument("--roster", default="team/roster.json")
    sp = sub.add_parser("search")
    sp.add_argument("query")
    sp.add_argument("path", nargs="?", default="team/features.json")
    sp = sub.add_parser("register")
    sp.add_argument("--id", required=True)
    sp.add_argument("--name", required=True)
    sp.add_argument("--does", required=True)
    sp.add_argument("--entry", required=True)
    sp.add_argument("--files", nargs="+", required=True)
    sp.add_argument("--owner", required=True)
    sp.add_argument("--keywords", nargs="+", required=True)
    sp.add_argument("--status", default="stable", choices=sorted(STATUSES))
    sp.add_argument("--version", default="1.0.0")
    sp.add_argument("--work", default="team/features.json")
    sp = sub.add_parser("use")
    sp.add_argument("--id", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--work", default="team/features.json")
    sp = sub.add_parser("improve")
    sp.add_argument("--id", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--bump", default="minor", choices=["patch", "minor", "major"])
    sp.add_argument("--note", default="")
    sp.add_argument("--does", default="")
    sp.add_argument("--entry", default="")
    sp.add_argument("--files", nargs="*", default=[])
    sp.add_argument("--keywords", nargs="*", default=[])
    sp.add_argument("--work", default="team/features.json")
    sp = sub.add_parser("deprecate")
    sp.add_argument("--id", required=True)
    sp.add_argument("--replacement", default="")
    sp.add_argument("--work", default="team/features.json")

    args = p.parse_args()
    if args.cmd == "list":
        return cmd_list(args.path)
    if args.cmd == "search":
        return cmd_search(args.path, args.query)
    if args.cmd == "check":
        return cmd_check(args.path, args.roster)
    if args.cmd == "register":
        return cmd_register(args)
    if args.cmd == "use":
        return cmd_use(args)
    if args.cmd == "improve":
        return cmd_improve(args)
    if args.cmd == "deprecate":
        return cmd_deprecate(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
