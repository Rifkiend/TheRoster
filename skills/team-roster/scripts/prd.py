#!/usr/bin/env python3
"""PRD layer for team-roster v2.2: where the work comes from.

A PRD (Product Requirements Document) item is the unit of intent:
one numbered item, one owner, a list of the features that implement
it. Claims point at a PRD item (--prd) so every task traces back to
a decision, and validation can catch the two lies a PRD makes:
an item marked done whose features are not stable/cancelled, and a
claim on an item that was cancelled or finished.

Sharded layout (v2.2): one file per item in team/prd/<id>.json.
team/index.json is GENERATED (scripts/rebuild_index.py), never
hand-edited. team/events.jsonl is the append-only audit log.

Usage:
    python3 prd.py list   [team/prd]
    python3 prd.py check  [team/prd] [--roster team/roster]
    python3 prd.py create --id prd-001 --title "User login" \
        --owner owner [--source "docs/prd.md#login"] \
        [--features login-api] [--prd-dir team/prd]
    python3 prd.py status --id prd-001 --to in_progress --by owner \
        --reason "work started" [--prd-dir team/prd]

Rules:
- Statuses: planned, in_progress, done, cancelled. Allowed
  transitions: planned->in_progress, in_progress->done,
  planned->cancelled, in_progress->cancelled. done and cancelled
  are TERMINAL — nothing moves out of them (a done item that needs
  more work gets a NEW item; a cancelled item stays as the record
  of a decision, it is never deleted).
- An item may reference features that do not exist yet (planned
  work). Marking it done is the honesty gate: --to done FAILS while
  any referenced feature is unregistered. Whether those features
  are stable or cancelled is checked by validate.py (cross-file).
- A claim on a cancelled or done item is refused at claim time
  (claim.py --prd) and at push time (validate.py) — the item is
  finished, there is nothing to work on.
- Every mutating command bumps lamport (+1), appends one line to
  team/events.jsonl, and rebuilds team/index.json.
- Config knob (team/config.json): prd_required=false by default.
  When true, claim.py refuses a new claim without --prd. Teams
  opt in to the discipline; small teams are not forced into it.

Exit 0 = ok. Exit 1 = invalid, duplicate, or forbidden transition.
Stdlib only, Python 3.9+.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import team_common as tc

STATUSES = {"planned", "in_progress", "done", "cancelled"}
ALLOWED_TRANSITIONS = {
    ("planned", "in_progress"),
    ("in_progress", "done"),
    ("planned", "cancelled"),
    ("in_progress", "cancelled"),
}


def prd_path(prd_dir, pid):
    return os.path.join(prd_dir, pid + ".json")


def _guard_mutation(prd_dir, action):
    """Error string when prd_dir is inside a linked worktree, else None.

    team/ state is only written from the main checkout (v2.3,
    Dziatkovskii rule) — a mutation from inside a worktree lands on
    the worktree branch and is invisible to the rest of the team."""
    return tc.guard_team_mutation(tc.team_dir_of(prd_dir), action)


def _resolve_prd_dir(prd_dir):
    """Redirect a prd dir inside a linked worktree to the main
    checkout's prd dir (reads only; prints a note on stderr)."""
    team_dir = tc.team_dir_of(prd_dir)
    team_dir, note = tc.resolve_team_dir(team_dir)
    if note:
        print("PRD: note — " + note, file=sys.stderr)
        return os.path.join(team_dir, "prd")
    return prd_dir


def load_prd(prd_dir):
    return tc.load_records(prd_dir)


def load_roster_active(roster_dir):
    out, humans = set(), set()
    for _stem, (_path, rec) in tc.load_records(roster_dir).items():
        if not isinstance(rec, dict):
            continue
        if rec.get("status") == "active":
            out.add(rec.get("id", ""))
            if rec.get("type") == "human":
                humans.add(rec.get("id", ""))
    return out, humans


def rebuild(team_dir):
    try:
        import rebuild_index
        rebuild_index.rebuild(team_dir)
    except Exception as e:  # index rebuild must never fail a mutation
        print(f"PRD: warning — could not rebuild team/index.json: {e}", file=sys.stderr)


def mutate(prd_dir, pid, actor, event, updater, extra=None):
    """Load, update (bumps lamport), save, log event, rebuild index.
    Returns (record, None) or (None, error)."""
    path = prd_path(prd_dir, pid)
    data, err = tc.load_json_file(path)
    if err:
        return None, "no PRD item with id %s (%s)" % (pid, err)
    if not isinstance(data, dict):
        return None, "PRD item %s is not a JSON object" % pid
    updater(data)
    data["lamport"] = int(data.get("lamport") or 0) + 1
    tc.save_json(path, data)
    team_dir = tc.team_dir_of(prd_dir)
    tc.append_event(team_dir, event, data["lamport"], actor,
                    base_commit=tc.git_head_commit(), extra=extra)
    rebuild(team_dir)
    return data, None


def cmd_list(prd_dir):
    prd_dir = _resolve_prd_dir(prd_dir)
    items = load_prd(prd_dir)
    if not items:
        print("PRD: no items yet — create one with: python3 prd.py create --id prd-001 --title ... --owner <you>")
        return 0
    print(f"PRD: {len(items)} item(s):")
    for pid in sorted(items):
        rec = items[pid][1]
        if not isinstance(rec, dict):
            print(f"  - {pid}: UNREADABLE (not a JSON object)")
            continue
        feats = ", ".join(rec.get("features") or []) or "no features yet"
        print(f"  - {pid} [{rec.get('status')}] — {rec.get('title')} "
              f"(owner: {rec.get('owner_id')}, features: {feats})")
    return 0


def cmd_check(prd_dir, roster_path):
    team_dir = tc.team_dir_of(prd_dir)
    eff_team, note = tc.resolve_team_dir(team_dir)
    if note:
        print("PRD: note — " + note, file=sys.stderr)
        prd_dir = os.path.join(eff_team, "prd")
        if roster_path and os.path.abspath(roster_path).startswith(os.path.abspath(team_dir) + os.sep):
            roster_path = os.path.join(eff_team, "roster")
    items = load_prd(prd_dir)
    errors, warnings = [], []
    roster_ids = None
    if roster_path:
        roster_ids, _humans = load_roster_active(roster_path)
    for pid, (_path, rec) in sorted(items.items()):
        label = pid
        if not tc.valid_id(pid):
            errors.append(f"{label}: id fails the id grammar ^[a-z0-9][a-z0-9-]*$ (max 64 chars, ASCII only)")
        if not isinstance(rec, dict):
            errors.append(f"{label}: not a JSON object")
            continue
        if rec.get("id") != pid:
            errors.append(f"{label}: id inside the file ({rec.get('id')!r}) does not match the filename — one item per file, named <id>.json")
        for field in ("id", "title", "owner_id", "status", "created_at",
                      "updated_at", "status_reason", "status_changed_by",
                      "status_changed_at", "lamport"):
            if field not in rec or rec[field] in (None, ""):
                if field == "lamport" and rec.get(field) == 0:
                    continue
                errors.append(f"{label}: missing/empty field: {field}")
        if rec.get("status") not in STATUSES:
            errors.append(f"{label}: status must be one of {sorted(STATUSES)} (got {rec.get('status')!r})")
        if roster_ids is not None and rec.get("owner_id") not in roster_ids:
            errors.append(f"{label}: owner {rec.get('owner_id')} is not an active roster member")
        for fid in rec.get("features") or []:
            if not tc.valid_id(fid):
                errors.append(f"{label}: feature id {fid!r} fails the id grammar")
        for tf in ("created_at", "updated_at", "status_changed_at"):
            if rec.get(tf) and tc.parse_time(rec[tf]) is None:
                errors.append(f"{label}: {tf} must be a UTC ISO-8601 timestamp ending in Z (got {rec[tf]!r})")
        if not isinstance(rec.get("lamport"), int) or rec.get("lamport", 0) < 1:
            errors.append(f"{label}: lamport must be an int >= 1")
        # A planned item pointing at a feature that does not exist yet
        # is normal (future work). Warn only — the done-gate in
        # `prd.py status --to done` and validate.py enforce reality
        # at the moment an item is marked done.
        features_dir = os.path.join(tc.team_dir_of(prd_dir), "features")
        if isinstance(rec.get("features"), list) and os.path.isdir(features_dir):
            have = set(tc.load_records(features_dir))
            for fid in rec.get("features") or []:
                if fid not in have:
                    warnings.append(f"{label}: feature {fid} is not registered yet (planned work — "
                                    "it must exist before this item can be marked done)")
    for w in warnings:
        print(f"  ! {w}")
    if errors:
        print("PRD CHECK: FAIL — do not push until this is fixed:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"PRD CHECK: OK — {len(items)} item(s), statuses valid.")
    return 0


def cmd_create(args):
    guard = _guard_mutation(args.prd_dir, "create a PRD item")
    if guard:
        print("PRD: FAIL — " + guard)
        return 1
    prd_dir = args.prd_dir
    os.makedirs(prd_dir, exist_ok=True)
    team_dir = tc.team_dir_of(prd_dir)
    if not tc.valid_id(args.id):
        print(f"PRD: FAIL — id {args.id!r} fails the id grammar ^[a-z0-9][a-z0-9-]*$ (max 64 chars, ASCII only)")
        return 1
    if not args.title.strip():
        print("PRD: FAIL — --title must not be empty")
        return 1
    roster_dir = os.path.join(team_dir, "roster")
    active, _humans = load_roster_active(roster_dir)
    if active and args.owner not in active:
        print(f"PRD: FAIL — owner {args.owner} is not an active roster member")
        return 1
    path = prd_path(prd_dir, args.id)
    if os.path.exists(path):
        print(f"PRD: FAIL — id {args.id} already exists. Use it, or pick a new id.")
        return 1
    features = sorted({f for f in (args.features or []) if tc.valid_id(f)})
    bad = [f for f in (args.features or []) if not tc.valid_id(f)]
    if bad:
        print(f"PRD: FAIL — feature id(s) fail the id grammar: {', '.join(bad)}")
        return 1
    now = tc.now_iso()
    rec = {
        "id": args.id,
        "title": args.title,
        "source": args.source or "",
        "status": "planned",
        "features": features,
        "owner_id": args.owner,
        "created_at": now,
        "updated_at": now,
        "status_reason": "created as planned",
        "status_changed_by": args.owner,
        "status_changed_at": now,
        "lamport": 1,
    }
    tc.save_json(path, rec)
    tc.append_event(team_dir, "prd_created", 1, args.owner,
                    base_commit=tc.git_head_commit())
    rebuild(team_dir)
    print(f"PRD: created {args.id} [planned] — \"{args.title}\" (owner: {args.owner}).")
    print("Claims on this item: python3 claim.py claim --task <id> --title ... --owner <you> --prd " + args.id)
    return 0


def cmd_status(args):
    guard = _guard_mutation(args.prd_dir, "change a PRD item's status")
    if guard:
        print("PRD: FAIL — " + guard)
        return 1
    data, err = tc.load_json_file(prd_path(args.prd_dir, args.id))
    if err or not isinstance(data, dict):
        print(f"PRD: FAIL — no PRD item with id {args.id}")
        return 1
    if args.to not in STATUSES:
        print(f"PRD: FAIL — --to must be one of {sorted(STATUSES)} (got {args.to!r})")
        return 1
    old_status = data.get("status")
    if (old_status, args.to) not in ALLOWED_TRANSITIONS:
        print(f"PRD: FAIL — cannot move {old_status} -> {args.to} "
              f"(allowed: {', '.join(sorted('%s->%s' % t for t in ALLOWED_TRANSITIONS))}; "
              "done and cancelled are terminal — a done item that needs more work gets a NEW item)")
        return 1
    if args.to == "done":
        # The honesty gate: every referenced feature must exist.
        # Whether they are stable or cancelled is validate.py's job.
        team_dir = tc.team_dir_of(args.prd_dir)
        features_dir = os.path.join(team_dir, "features")
        have = set(tc.load_records(features_dir))
        missing = [f for f in (data.get("features") or []) if f not in have]
        if missing:
            print(f"PRD: FAIL — cannot mark {args.id} done: feature(s) not registered yet: {', '.join(missing)}. "
                  "Register them first, or remove them from this item.")
            return 1
        # v2.6: an open decision gate on this item (directly, or
        # via one of its features) blocks done — resolve or waive
        # the gate first.
        gates = {}
        for stem, (_path, rec) in tc.load_records(os.path.join(team_dir, "gates")).items():
            if isinstance(rec, dict):
                gates[stem] = rec
        gate = tc.open_gate_for_prd(args.id, gates, (data.get("features") or []))
        if gate:
            print(f"PRD: FAIL — cannot mark {args.id} done: decision gate {gate.get('id')} is OPEN "
                  f"({gate.get('blocks_kind')} {gate.get('blocks_id')}): \"{gate.get('question')}\" — "
                  "resolve or waive the gate first (gates.py)")
            return 1
        # v2.6: the latest review on this item (or its features, or
        # claims bound to it) is request-changes — the work is not
        # done until the reviewer's changes are addressed and a
        # newer approve clears it.
        reviews = {}
        for stem, (_path, rec) in tc.load_records(os.path.join(team_dir, "reviews")).items():
            if isinstance(rec, dict):
                reviews[stem] = rec
        claims = {}
        for stem, (_path, rec) in tc.load_records(os.path.join(team_dir, "claims")).items():
            if isinstance(rec, dict):
                claims[stem] = rec
        blocked = tc.prd_blocked_by_review(reviews, args.id,
                                           (data.get("features") or []), claims)
        if blocked:
            review, label = blocked
            print(f"PRD: FAIL — cannot mark {args.id} done: latest review {review.get('id')} on "
                  f"{label} is request-changes (reviewer {review.get('reviewer')}): "
                  f"\"{review.get('note')}\" — address it (claim.py claim --review <id>) "
                  "and get a newer approve first")
            return 1

    def upd(rec):
        rec["status"] = args.to
        rec["updated_at"] = tc.now_iso()
        rec["status_reason"] = args.reason
        rec["status_changed_by"] = args.by
        rec["status_changed_at"] = tc.now_iso()
    rec, err = mutate(args.prd_dir, args.id, args.by, "prd_status", upd,
                      extra={"from": old_status, "to": args.to, "reason": args.reason})
    if err:
        print("PRD: FAIL — " + err)
        return 1
    print(f"PRD: {args.id} {old_status} -> {args.to} by {args.by} ({args.reason}). Commit + push the item file.")
    return 0


def main():
    p = argparse.ArgumentParser(description="PRD layer for team-roster v2.2: where the work comes from.")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("list", "check"):
        sp = sub.add_parser(name)
        sp.add_argument("path", nargs="?", default="team/prd")
        if name == "check":
            sp.add_argument("--roster", default="team/roster")
    sp = sub.add_parser("create")
    sp.add_argument("--id", required=True)
    sp.add_argument("--title", required=True)
    sp.add_argument("--owner", required=True)
    sp.add_argument("--source", default="")
    sp.add_argument("--features", nargs="*", default=[])
    sp.add_argument("--prd-dir", default="team/prd")
    sp = sub.add_parser("status")
    sp.add_argument("--id", required=True)
    sp.add_argument("--to", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--prd-dir", default="team/prd")

    args = p.parse_args()
    if args.cmd == "list":
        return cmd_list(args.path)
    if args.cmd == "check":
        return cmd_check(args.path, args.roster)
    if args.cmd == "create":
        return cmd_create(args)
    if args.cmd == "status":
        return cmd_status(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
