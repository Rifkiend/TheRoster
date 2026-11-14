#!/usr/bin/env python3
"""Migrate a team-roster v1 tree to the v2 sharded layout.

Reads the v1 monolithic files — team/roster.json, team/work.json,
team/features.json — and writes:
    team/roster/<id>.json      (one member per file)
    team/claims/<task-id>.json (one claim per file)
    team/features/<id>.json    (one feature per file)
    team/config.json           (v2 defaults)
    team/index.json            (GENERATED)
    team/events.jsonl          (one "migrated_v1_to_v2" line per record)
and moves the old monolithic files to team/v1-backup/ (never deletes
them outright).

Usage:
    python3 migrate_v1_to_v2.py [team] [--dry-run]

Timestamp conversion: v1 examples use +08:00 offsets — all timestamps
are converted to UTC ISO-8601 ending in Z. Claim status "doing"
becomes "active". New v2 fields are filled with honest defaults:
lamport 1, heartbeat_at = started_at, claim_seq per file, scope
files, work_type code, base_commit "" (not yet synced).
Roster entries get verification "migrated-v1" — they are NOT
human-approved, so check_roster.py will FAIL on a migrated roster
until a human re-approves each entry (added_by / added_in_pr are
filled with "v1-migration" / ""). That is deliberate: approval must
not be fabricated by a script.
used_by holds TASK ids in v2, so every used_by entry is preserved
(only non-id-shaped entries are dropped); contributors that are not
roster ids are dropped and reported — contributors are people, not
tasks.

Idempotent: if a sharded layout is already present (team/config.json
exists AND team/roster/ or team/claims/ holds .json files), the script
prints a message and does nothing, exit 0. Running it twice is a
no-op. --dry-run prints what would happen and writes nothing.

Stdlib only, Python 3.9+.
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import team_common as tc

# Also accept the v1 example filenames (roster.example.json etc.) when
# the canonical names are absent — a team migrating from the shipped
# examples copies them as-is; requiring a rename first is a needless
# paper cut.
V1_EXAMPLE_FILES = ("roster.example.json", "work.example.json", "features.example.json")


def find_v1_file(team_dir, canonical, example):
    """Return (path, name) of the v1 source file, preferring the
    canonical name, falling back to the *.example.json name."""
    for name in (canonical, example):
        path = os.path.join(team_dir, name)
        if os.path.isfile(path):
            return path, name
    return os.path.join(team_dir, canonical), canonical


def to_z(value):
    """Convert an ISO timestamp (any offset) to UTC Z. Dates
    (YYYY-MM-DD) are returned unchanged."""
    if not value:
        return ""
    dt = tc.parse_time(str(value))
    if dt is not None:
        return tc.to_utc_z(dt)
    return str(value)


def main():
    p = argparse.ArgumentParser(description="Migrate team-roster v1 monoliths to the v2 sharded layout.")
    p.add_argument("team", nargs="?", default="team")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    team_dir = args.team

    # Idempotency: sharded layout already present -> no-op.
    sharded = False
    if os.path.isfile(os.path.join(team_dir, "config.json")):
        for sub in ("roster", "claims", "features"):
            d = os.path.join(team_dir, sub)
            if os.path.isdir(d) and any(n.endswith(".json") for n in os.listdir(d)):
                sharded = True
                break
    if sharded:
        print("MIGRATE: already migrated — sharded layout present (team/config.json + team/roster|claims|features). No-op.")
        return 0

    roster_src, roster_name = find_v1_file(team_dir, "roster.json", "roster.example.json")
    work_src, work_name = find_v1_file(team_dir, "work.json", "work.example.json")
    feat_src, feat_name = find_v1_file(team_dir, "features.json", "features.example.json")
    roster_v1, err = tc.load_json_file(roster_src)
    work_v1, err_w = tc.load_json_file(work_src)
    feat_v1, err_f = tc.load_json_file(feat_src)
    if roster_v1 is None and work_v1 is None and feat_v1 is None:
        print("MIGRATE: nothing to migrate — no v1 monolithic files found in %s." % team_dir)
        print("  Looked for: roster.json / roster.example.json, work.json / work.example.json, "
              "features.json / features.example.json.")
        return 0
    roster_v1 = roster_v1 or {}
    work_v1 = work_v1 or {}
    feat_v1 = feat_v1 or {}

    members = roster_v1.get("members") or []
    roster_ids = {m.get("id") for m in members if isinstance(m, dict) and m.get("id")}
    claims_v1 = work_v1.get("claims") or []
    features_v1 = feat_v1.get("features") or []

    plan = {"roster": [], "claims": [], "features": [], "dropped_used_by": [],
            "dropped_contributors": []}
    events = []

    # ---- roster ------------------------------------------------
    for m in members:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        rec = {
            "id": m["id"],
            "name": m.get("name", ""),
            "type": m.get("type", "ai-agent"),
            "role": m.get("role", ""),
            "can_do": m.get("can_do") or [],
            "contact": m.get("contact", ""),
            "status": m.get("status", "active"),
            "last_verified": m.get("last_verified", ""),
            "added_by": "v1-migration",
            "added_in_pr": "",
            "verification": "migrated-v1",
        }
        if m.get("last_seen_at"):
            rec["last_seen_at"] = to_z(m["last_seen_at"])
        plan["roster"].append(rec)
        events.append({"kind": "roster", "id": m["id"]})

    # ---- claims ------------------------------------------------
    seq_by_file = {}
    for c in claims_v1:
        if not isinstance(c, dict) or not c.get("task_id"):
            continue
        files = sorted(set(c.get("files") or []))
        seq = 1
        for f in files:
            seq = max(seq, seq_by_file.get(tc.canonical_path(f), 0) + 1)
        for f in files:
            seq_by_file[tc.canonical_path(f)] = seq
        started = to_z(c.get("started_at") or "")
        expires = to_z(c.get("expires_at") or "")
        if not expires and started:
            dt = tc.parse_time(started)
            expires = tc.to_utc_z(dt + tc.timedelta(hours=2)) if dt else ""
        rec = {
            "task_id": c["task_id"],
            "task": c.get("task", ""),
            "owner_id": c.get("owner_id", ""),
            "status": "active" if c.get("status") == "doing" else (c.get("status") or "active"),
            "files": files,
            "scope": {"type": "files"},
            "work_type": "code",
            "branch": c.get("branch", ""),
            "base_commit": "",
            "started_at": started,
            "expires_at": expires,
            "heartbeat_at": started,
            "lamport": 1,
            "claim_seq": seq,
            "reserves_feature": c.get("reserves_feature", ""),
            "expected_duration": "",
            "handoff_note": "",
            "released_by": "",
            "stalled_by": "",
            "stall_reason": "",
        }
        plan["claims"].append(rec)
        events.append({"kind": "claim", "id": c["task_id"]})

    # ---- features ------------------------------------------------
    for f in features_v1:
        if not isinstance(f, dict) or not f.get("id"):
            continue
        # used_by holds TASK ids in v2 — preserve them all; only
        # non-id-shaped entries are dropped. Contributors are people.
        used_by = [u for u in (f.get("used_by") or []) if tc.valid_id(u)]
        plan["dropped_used_by"].extend(
            "%s: %s" % (f["id"], u) for u in (f.get("used_by") or []) if not tc.valid_id(u))
        contributors = [u for u in (f.get("contributors") or []) if u in roster_ids]
        plan["dropped_contributors"].extend(
            "%s: %s" % (f["id"], u) for u in (f.get("contributors") or []) if u not in roster_ids)
        version = f.get("version") or "1.0.0"
        # status_changed_at must be a full UTC-Z timestamp, not a bare
        # date: v1 last_verified is YYYY-MM-DD, which is not one.
        sca = to_z(f.get("last_verified") or "")
        if tc.parse_time(sca) is None:
            sca = tc.now_iso()
        blob_sha = ""
        entry = f.get("entry", "")
        if entry and ":" in entry:
            fpath = entry.rsplit(":", 1)[0]
            if os.path.isfile(fpath):
                blob_sha = tc.git_blob_sha(fpath) or ""
        rec = {
            "id": f["id"],
            "name": f.get("name", ""),
            "kind": "code",
            "does": f.get("does", ""),
            "entry": f.get("entry", ""),
            "entry_blob_sha": blob_sha,
            "files": sorted(set(f.get("files") or [])),
            "keywords": sorted({tc.normalize_keyword(k) for k in (f.get("keywords") or []) if tc.normalize_keyword(k)}),
            "owner_id": f.get("owner_id", ""),
            "contributors": contributors or ([f["owner_id"]] if f.get("owner_id") in roster_ids else []),
            "status": f.get("status", "experimental"),
            "version": version,
            "breaking": False,
            "breaking_notes": "",
            "canonical": False,
            "used_by": used_by,
            "changes": f.get("changes") or [{"version": version, "by": f.get("owner_id", ""),
                                             "note": "Migrated from v1",
                                             "date": (f.get("last_verified") or "")}],
            "last_verified": f.get("last_verified", ""),
            "status_reason": "migrated from v1",
            "status_changed_by": f.get("owner_id", ""),
            "status_changed_at": sca,
            "lamport": 1,
        }
        plan["features"].append(rec)
        events.append({"kind": "feature", "id": f["id"]})

    total = len(plan["roster"]) + len(plan["claims"]) + len(plan["features"])
    if args.dry_run:
        print(f"MIGRATE (dry-run): would migrate {len(plan['roster'])} roster member(s), "
              f"{len(plan['claims'])} claim(s), {len(plan['features'])} feature(s) in {team_dir}.")
        print("Would write team/config.json (v2 defaults), team/index.json, team/events.jsonl,")
        print("and move roster.json / work.json / features.json to team/v1-backup/.")
        for line in plan["dropped_used_by"]:
            print(f"  would drop non-roster used_by entry: {line}")
        for line in plan["dropped_contributors"]:
            print(f"  would drop non-roster contributor entry: {line}")
        print("NOTE: migrated roster entries get verification \"migrated-v1\" — a human must")
        print("re-approve each entry before check_roster.py passes.")
        return 0

    for rec in plan["roster"]:
        tc.save_json(os.path.join(team_dir, "roster", rec["id"] + ".json"), rec)
    for rec in plan["claims"]:
        tc.save_json(os.path.join(team_dir, "claims", rec["task_id"] + ".json"), rec)
    for rec in plan["features"]:
        tc.save_json(os.path.join(team_dir, "features", rec["id"] + ".json"), rec)
    tc.save_json(os.path.join(team_dir, "config.json"), dict(tc.DEFAULT_CONFIG))
    events_path = os.path.join(team_dir, "events.jsonl")
    os.makedirs(team_dir, exist_ok=True)
    with open(events_path, "a", encoding="utf-8") as fh:
        for ev in events:
            line = {"event": "migrated_v1_to_v2", "at": tc.now_iso(), "lamport": 1,
                    "actor": "v1-migration", "base_commit": ""}
            if ev["kind"] == "claim":
                line["task_id"] = ev["id"]
            elif ev["kind"] == "feature":
                line["feature_id"] = ev["id"]
            else:
                line["member_id"] = ev["id"]
            fh.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
    backup = os.path.join(team_dir, "v1-backup")
    os.makedirs(backup, exist_ok=True)
    for src, name in ((roster_src, roster_name), (work_src, work_name), (feat_src, feat_name)):
        if os.path.isfile(src):
            shutil.move(src, os.path.join(backup, name))
    # Merge story for the sharded layout: the event log is append-only,
    # so git's union driver is safe for it (and only for it); the index
    # is generated — a local cache, never committed, rebuilt locally.
    with open(os.path.join(team_dir, ".gitattributes"), "w", encoding="utf-8") as fh:
        fh.write("# team-roster v2: the event log is append-only (one line per\n"
                 "# event), so git's union merge driver — take lines from both\n"
                 "# sides — is safe here and ONLY here. Never use merge=union\n"
                 "# on record files.\n"
                 "events.jsonl merge=union\n")
    with open(os.path.join(team_dir, ".gitignore"), "w", encoding="utf-8") as fh:
        fh.write("# team/index.json is GENERATED (scripts/rebuild_index.py) —\n"
                 "# a local cache, never hand-edited, never committed.\n"
                 "# Rebuild after any merge/rebase:\n"
                 "#   python3 scripts/rebuild_index.py team\n"
                 "index.json\n")
    try:
        import rebuild_index
        rebuild_index.rebuild(team_dir)
    except Exception as e:
        print(f"MIGRATE: warning — could not rebuild team/index.json: {e}", file=sys.stderr)

    print(f"MIGRATE: migrated {len(plan['roster'])} roster member(s), {len(plan['claims'])} claim(s), "
          f"{len(plan['features'])} feature(s) to the sharded v2 layout.")
    print(f"  config: team/config.json (v2 defaults, skill_version 2.0.0)")
    print(f"  events: {total} line(s) appended to team/events.jsonl (migrated_v1_to_v2)")
    moved = [n for s, n in ((roster_src, roster_name), (work_src, work_name), (feat_src, feat_name))
             if os.path.isfile(os.path.join(backup, n))]
    print("  old files moved to team/v1-backup/ (%s) — never deleted." % ", ".join(moved))
    print("  merge story written: team/.gitattributes (events.jsonl merge=union), "
          "team/.gitignore (index.json is generated, never committed).")
    for line in plan["dropped_used_by"]:
        print(f"  dropped non-roster used_by entry: {line}")
    for line in plan["dropped_contributors"]:
        print(f"  dropped non-roster contributor entry: {line}")
    print("NOTE: migrated roster entries have verification \"migrated-v1\" — a human must")
    print("re-approve each entry (verification: \"human-approved\") before check_roster.py passes.")
    print("Run it again to confirm idempotency: it must report 'already migrated' and change nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
