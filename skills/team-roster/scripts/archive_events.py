#!/usr/bin/env python3
"""Archive past months out of team/events.jsonl (v2.1.0).

The event log is append-only and unbounded: about 200-400 bytes per
event, so a busy team writes ~1 MB per few thousand events — kept
forever in every clone, fetch, and validation scan. On a long-lived,
big project that is real weight (tens of MB, and hundreds of MB at
extreme scale), for a file no check ever reads: validate.py, the hook,
and sync_check all read the record files (roster/claims/features),
never old event lines. The log is an audit trail for humans.

Rule: team/events.jsonl holds the CURRENT month only. Everything older
moves to team/events-archive/YYYY-MM.jsonl — same content, still in
git, still grep-able, same union merge driver. History is archived,
never deleted.

Usage:
    python3 scripts/archive_events.py [team] [--dry-run]
Run once a month (doctor.py warns when events.jsonl passes 1 MB).
Safety: only lines whose timestamp parses to a past month are moved;
lines without a parseable timestamp stay in the hot log; archive files
are only appended to, never rewritten; the hot log is replaced
atomically (temp file + os.replace). Exit 0, prints a summary.
Stdlib only, Python 3.9+.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import team_common as tc

ARCHIVE_DIRNAME = "events-archive"
WARN_BYTES = 1_000_000


def parse_ts(line):
    """Timestamp of one event line. Accepts both the old 'ts' key and
    the newer 'at' key written by team_common.append_event."""
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    raw = obj.get("ts") or obj.get("at")
    if not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def main():
    p = argparse.ArgumentParser(
        description="Archive past months of team/events.jsonl.")
    p.add_argument("team", nargs="?", default="team")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would move, change nothing")
    args = p.parse_args()

    team_dir = args.team
    hot_path = os.path.join(team_dir, "events.jsonl")
    if not os.path.isfile(hot_path):
        print(f"archive: no {hot_path} — nothing to do")
        return 0

    now = datetime.now(timezone.utc)
    cur = (now.year, now.month)
    keep, move = [], {}  # move: (year, month) -> [lines]
    unparseable = 0
    with open(hot_path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            dt = parse_ts(line)
            if dt is None or (dt.year, dt.month) == cur:
                keep.append(line)
                if dt is None:
                    unparseable += 1
            else:
                move.setdefault((dt.year, dt.month), []).append(line)

    total_moved = sum(len(v) for v in move.values())
    if args.dry_run:
        for (y, m) in sorted(move):
            print(f"archive: would move {len(move[(y, m)])} event(s) to "
                  f"events-archive/{y:04d}-{m:02d}.jsonl")
        print(f"archive: dry run — {total_moved} would move, "
              f"{len(keep)} would stay ({unparseable} unparseable, kept)")
        return 0

    archive_dir = os.path.join(team_dir, ARCHIVE_DIRNAME)
    for (y, m) in sorted(move):
        os.makedirs(archive_dir, exist_ok=True)
        dest = os.path.join(archive_dir, f"{y:04d}-{m:02d}.jsonl")
        # Newline-safe append (v2.6): a hand-edited archive file
        # without a trailing newline would otherwise fuse with the
        # first moved line.
        tc.append_text_lines(dest, move[(y, m)])
        print(f"archive: moved {len(move[(y, m)])} event(s) to "
              f"events-archive/{y:04d}-{m:02d}.jsonl")

    tmp_path = hot_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        if keep:
            f.write("\n".join(keep) + "\n")
    os.replace(tmp_path, hot_path)
    print(f"archive: {hot_path} now holds {len(keep)} event(s) "
          f"({unparseable} unparseable, kept); {total_moved} archived")
    return 0


if __name__ == "__main__":
    sys.exit(main())
