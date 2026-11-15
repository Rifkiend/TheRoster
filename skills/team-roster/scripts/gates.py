#!/usr/bin/env python3
"""Decision gates for team-roster v2.6: a named question that blocks
work until it is answered — the file-based version of Orca's
gate-create / gate-resolve.

Until v2.6, "wait for a decision" was an informal mailbox message:
no record that a task is blocked on a named question, no list of
open gates, and no enforcement — a worker could blunder past a
question nobody answered. A gate makes the block explicit, owned,
and auditable in git.

Sharded layout (v2.6): one file per gate in team/gates/<id>.json.
team/index.json is GENERATED (scripts/rebuild_index.py), never
hand-edited. team/events.jsonl is the append-only audit log.

Usage:
    python3 gates.py list   [team/gates]
    python3 gates.py check  [team/gates] [--roster team/roster]
    python3 gates.py create --id g-001 --question "Merge the winner, or keep both?" \
        --options merge-winner keep-both --blocks-kind prd --blocks-id prd-003 --by owner
    python3 gates.py resolve --id g-001 --resolution merge-winner --by teammate-1 \
        [--review r-001]
    python3 gates.py waive --id g-001 --by owner --reason "decided offline in the meeting"

Rules:
- A gate blocks exactly one subject: a claim (one task id), a PRD
  item, or a feature. `options` may be empty (free-form
  resolution); when non-empty, the resolution MUST be one of them.
- Statuses: open, resolved, waived. Allowed transitions:
  open->resolved, open->waived. Both terminal — a resolved gate
  is not re-opened; a new question gets a new gate.
- Enforcement (same shape as a cancelled feature): a new claim
  bound to an open gate is REFUSED at claim time (claim.py), and
  a blocking claim bound to an open gate FAILS claim.py check
  and validate.py. A PRD item with an open gate on it (directly,
  or via one of its features) cannot be marked done. A gate does
  NOT freeze a worker mid-keystroke — the worker finds out at the
  next check / push (same honest limit as cancellation: visibility
  is bounded by fetch/sync_check, not eliminated).
- Resolving: --by must be an active roster member AND must differ
  from the subject's owner — the worker cannot resolve their own
  blocker (same rule as stable promotion's reviewer). For a claim
  subject that does not exist yet there is no owner to differ
  from, so any active member may resolve. --review cites the
  review justifying the resolution: it must exist, target the
  same subject, and its verdict must be approve — a
  request-changes review cannot satisfy a gate.
- Waiving is a human act: --by must be a roster member of type
  human. An agent waiving its own blocker is meaningless — the
  gate exists to force a human decision. A human waiving is still
  recorded: who, when, why.
- Every mutating command bumps lamport (+1), appends one line to
  team/events.jsonl, and rebuilds team/index.json.

Exit 0 = ok. Exit 1 = invalid, duplicate, or forbidden transition.
Stdlib only, Python 3.9+.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import team_common as tc

STATUSES = {"open", "resolved", "waived"}
ALLOWED_TRANSITIONS = {("open", "resolved"), ("open", "waived")}


def gate_path(gates_dir, gid):
    return os.path.join(gates_dir, gid + ".json")


def _guard_mutation(gates_dir, action):
    """Error string when gates_dir is inside a linked worktree, else None.

    team/ state is only written from the main checkout (v2.3,
    Dziatkovskii rule) — a mutation from inside a worktree lands on
    the worktree branch and is invisible to the rest of the team."""
    return tc.guard_team_mutation(tc.team_dir_of(gates_dir), action)


def _resolve_gates_dir(gates_dir):
    """Redirect a gates dir inside a linked worktree to the main
    checkout's gates dir (reads only; prints a note on stderr)."""
    team_dir = tc.team_dir_of(gates_dir)
    team_dir, note = tc.resolve_team_dir(team_dir)
    if note:
        print("GATES: note — " + note, file=sys.stderr)
        return os.path.join(team_dir, "gates")
    return gates_dir


def load_gates(gates_dir):
    return tc.load_records(gates_dir)


def load_roster(team_dir):
    """(active_ids, human_ids, by_id, error)."""
    roster_dir = os.path.join(team_dir, "roster")
    active, humans, by_id = set(), set(), {}
    if not os.path.isdir(roster_dir):
        return active, humans, by_id, "roster dir not found: %s" % roster_dir
    for _stem, (_path, rec) in tc.load_records(roster_dir).items():
        if not isinstance(rec, dict) or not rec.get("id"):
            continue
        by_id[rec["id"]] = rec
        if rec.get("status") == "active":
            active.add(rec["id"])
        if rec.get("type") == "human":
            humans.add(rec["id"])
    return active, humans, by_id, None


def subject_exists(team_dir, kind, sid):
    """True when the blocked subject exists (claim / PRD item / feature)."""
    subdir = {"claim": "claims", "prd": "prd", "feature": "features"}.get(kind)
    if not subdir:
        return False
    data, _err = tc.load_json_file(os.path.join(team_dir, subdir, sid + ".json"))
    return isinstance(data, dict)


def rebuild(team_dir):
    try:
        import rebuild_index
        rebuild_index.rebuild(team_dir)
    except Exception as e:  # index rebuild must never fail a mutation
        print(f"GATES: warning — could not rebuild team/index.json: {e}", file=sys.stderr)


def mutate(gates_dir, gid, actor, event, updater, extra=None):
    """Load, update (bumps lamport), save, log event, rebuild index.
    Returns (record, None) or (None, error)."""
    path = gate_path(gates_dir, gid)
    data, err = tc.load_json_file(path)
    if err:
        return None, "no gate with id %s (%s)" % (gid, err)
    if not isinstance(data, dict):
        return None, "gate %s is not a JSON object" % gid
    updater(data)
    data["lamport"] = int(data.get("lamport") or 0) + 1
    tc.save_json(path, data)
    team_dir = tc.team_dir_of(gates_dir)
    tc.append_event(team_dir, event, data["lamport"], actor,
                    base_commit=tc.git_head_commit(), extra=extra)
    rebuild(team_dir)
    return data, None


def check_gates(gates_dir, roster_path=None):
    """Validate every gate record. Returns (errors, warnings).

    Shared by `gates.py check` and validate.py — the per-script
    check is the single source of the rules."""
    gates_dir = _resolve_gates_dir(gates_dir)
    team_dir = tc.team_dir_of(gates_dir)
    errors, warnings = [], []
    gates = load_gates(gates_dir)
    active = humans = None
    by_id = {}
    if roster_path:
        roster_dir = roster_path
        if os.path.isdir(roster_dir):
            for _stem, (_path, rec) in tc.load_records(roster_dir).items():
                if isinstance(rec, dict) and rec.get("id"):
                    by_id[rec["id"]] = rec
            active = {rid for rid, m in by_id.items() if m.get("status") == "active"}
            humans = {rid for rid, m in by_id.items() if m.get("type") == "human"}
    for gid, (_path, rec) in sorted(gates.items()):
        label = gid
        if not tc.valid_id(gid):
            errors.append(f"{label}: id fails the id grammar ^[a-z0-9][a-z0-9-]*$ (max 64 chars, ASCII only)")
            continue
        if not isinstance(rec, dict):
            errors.append(f"{label}: not a JSON object")
            continue
        if rec.get("id") != gid:
            errors.append(f"{label}: id inside the file ({rec.get('id')!r}) does not match the filename — one gate per file, named <id>.json")
        for field in ("id", "question", "blocks_kind", "blocks_id",
                      "status", "created_by", "created_at", "lamport"):
            if field not in rec or rec[field] in (None, ""):
                if field == "lamport" and rec.get(field) == 0:
                    continue
                errors.append(f"{label}: missing/empty field: {field}")
        if not isinstance(rec.get("question"), str) or not rec.get("question", "").strip():
            errors.append(f"{label}: question must be a non-empty string")
        if rec.get("blocks_kind") not in tc.GATE_KINDS:
            errors.append(f"{label}: blocks_kind must be one of {sorted(tc.GATE_KINDS)} (got {rec.get('blocks_kind')!r})")
        if rec.get("blocks_id") and not tc.valid_id(rec["blocks_id"]):
            errors.append(f"{label}: blocks_id fails the id grammar")
        if rec.get("status") not in STATUSES:
            errors.append(f"{label}: status must be one of {sorted(STATUSES)} (got {rec.get('status')!r})")
        options = rec.get("options")
        if options is None:
            errors.append(f"{label}: missing options (use [] for a free-form resolution)")
        elif not isinstance(options, list) or any(not isinstance(o, str) or not o.strip() for o in options):
            errors.append(f"{label}: options must be a list of non-empty strings")
        if rec.get("status") == "resolved":
            if not rec.get("resolution"):
                errors.append(f"{label}: resolved gate without a resolution")
            elif isinstance(options, list) and options and rec["resolution"] not in options:
                errors.append(f"{label}: resolution {rec['resolution']!r} is not one of the options {options}")
            if not rec.get("resolved_by"):
                errors.append(f"{label}: resolved gate without resolved_by")
            elif active is not None and rec["resolved_by"] not in active:
                errors.append(f"{label}: resolved_by {rec['resolved_by']} is not an active roster member")
            if rec.get("resolved_by_review"):
                rpath = os.path.join(team_dir, "reviews", rec["resolved_by_review"] + ".json")
                rdata, _err = tc.load_json_file(rpath)
                if not isinstance(rdata, dict):
                    errors.append(f"{label}: resolved_by_review {rec['resolved_by_review']} does not exist")
                else:
                    kind, field = {"claim": ("claim", "task_id"),
                                   "prd": ("prd", "prd_item"),
                                   "feature": ("feature", "feature_id")}[rec.get("blocks_kind") or ""]
                    if rdata.get(field) != rec.get("blocks_id"):
                        errors.append(f"{label}: resolved_by_review {rec['resolved_by_review']} does not target "
                                      f"the same subject ({rec.get('blocks_kind')} {rec.get('blocks_id')})")
                    if rdata.get("verdict") != "approve":
                        errors.append(f"{label}: resolved_by_review {rec['resolved_by_review']} is "
                                      f"{rdata.get('verdict')} — only an approve review can satisfy a gate")
        if rec.get("status") == "waived":
            if not rec.get("waived_by"):
                errors.append(f"{label}: waived gate without waived_by")
            elif humans is not None and rec["waived_by"] not in humans:
                errors.append(f"{label}: waived_by {rec['waived_by']} is not a human roster member — waiving is a human act")
            if not rec.get("waive_reason"):
                errors.append(f"{label}: waived gate without a waive_reason")
        if active is not None and rec.get("created_by") not in active:
            errors.append(f"{label}: created_by {rec.get('created_by')} is not an active roster member")
        for tf in ("created_at", "resolved_at", "waived_at"):
            if rec.get(tf) and tc.parse_time(rec[tf]) is None:
                errors.append(f"{label}: {tf} must be a UTC ISO-8601 timestamp ending in Z (got {rec[tf]!r})")
        if not isinstance(rec.get("lamport"), int) or rec.get("lamport", 0) < 1:
            errors.append(f"{label}: lamport must be an int >= 1")
        # The blocked subject must exist — except a claim gate, which
        # may be created before the claim it will block.
        if rec.get("blocks_kind") in ("prd", "feature") and rec.get("blocks_id"):
            if not subject_exists(team_dir, rec["blocks_kind"], rec["blocks_id"]):
                errors.append(f"{label}: blocks {rec['blocks_kind']} {rec['blocks_id']}, which does not exist")
        if rec.get("status") == "open" and rec.get("resolution"):
            warnings.append(f"{label}: open gate already carries a resolution — it is ignored until resolved")
    return errors, warnings


# ---------------------------------------------------------------- commands

def cmd_list(gates_dir):
    gates_dir = _resolve_gates_dir(gates_dir)
    gates = load_gates(gates_dir)
    if not gates:
        print("GATES: no gates yet — create one with: python3 gates.py create --id g-001 --question ... --blocks-kind prd --blocks-id prd-001 --by <you>")
        return 0
    print(f"GATES: {len(gates)} gate(s):")
    for gid in sorted(gates):
        rec = gates[gid][1]
        if not isinstance(rec, dict):
            print(f"  - {gid}: UNREADABLE (not a JSON object)")
            continue
        res = f" -> {rec.get('resolution')}" if rec.get("status") == "resolved" else ""
        print(f"  - {gid} [{rec.get('status')}] — {rec.get('question')} "
              f"(blocks {rec.get('blocks_kind')} {rec.get('blocks_id')}, by {rec.get('created_by')}{res})")
    return 0


def cmd_check(gates_dir, roster_path):
    errors, warnings = check_gates(gates_dir, roster_path)
    for w in warnings:
        print(f"  ! {w}")
    if errors:
        print("GATES CHECK: FAIL — do not push until this is fixed:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"GATES CHECK: OK — gate records valid.")
    return 0


def cmd_create(args):
    guard = _guard_mutation(args.gates_dir, "create a gate")
    if guard:
        print("GATES: FAIL — " + guard)
        return 1
    gates_dir = args.gates_dir
    os.makedirs(gates_dir, exist_ok=True)
    team_dir = tc.team_dir_of(gates_dir)
    if not tc.valid_id(args.id):
        print(f"GATES: FAIL — id {args.id!r} fails the id grammar ^[a-z0-9][a-z0-9-]*$ (max 64 chars, ASCII only)")
        return 1
    if not args.question.strip():
        print("GATES: FAIL — --question must not be empty")
        return 1
    if args.blocks_kind not in tc.GATE_KINDS:
        print(f"GATES: FAIL — --blocks-kind must be one of {sorted(tc.GATE_KINDS)}")
        return 1
    if not tc.valid_id(args.blocks_id):
        print(f"GATES: FAIL — blocks-id {args.blocks_id!r} fails the id grammar")
        return 1
    options = sorted({o.strip() for o in (args.options or []) if o.strip()})
    if len(options) != len(args.options or []):
        print("GATES: FAIL — options must be non-empty and unique")
        return 1
    # The blocked subject must exist — except a claim gate, which may
    # be created before the claim it will block (the coordinator asks
    # the question first, the worker claims after the answer).
    if args.blocks_kind in ("prd", "feature") and not subject_exists(team_dir, args.blocks_kind, args.blocks_id):
        print(f"GATES: FAIL — blocks {args.blocks_kind} {args.blocks_id}, which does not exist")
        return 1
    active, _humans, _by_id, rerr = load_roster(team_dir)
    if rerr:
        print(f"GATES: warning — {rerr}; creator not verified against the roster.")
    elif args.by not in active:
        print(f"GATES: FAIL — creator {args.by} is not an active roster member")
        return 1
    path = gate_path(gates_dir, args.id)
    if os.path.exists(path):
        print(f"GATES: FAIL — id {args.id} already exists. Use it, or pick a new id.")
        return 1
    now = tc.now_iso()
    rec = {
        "id": args.id,
        "question": args.question,
        "options": options,
        "blocks_kind": args.blocks_kind,
        "blocks_id": args.blocks_id,
        "status": "open",
        "resolution": "",
        "resolved_by": "",
        "resolved_at": "",
        "resolved_by_review": "",
        "waived_by": "",
        "waived_at": "",
        "waive_reason": "",
        "created_by": args.by,
        "created_at": now,
        "lamport": 1,
    }
    tc.save_json(path, rec)
    tc.append_event(team_dir, "gate_created", 1, args.by,
                    base_commit=tc.git_head_commit(),
                    extra={"gate_id": args.id, "blocks_kind": args.blocks_kind,
                           "blocks_id": args.blocks_id})
    rebuild(team_dir)
    print(f"GATES: created {args.id} [open] — \"{args.question}\" (blocks {args.blocks_kind} {args.blocks_id}).")
    print("Work on that subject is now refused at claim time until the gate is resolved or waived.")
    return 0


def cmd_resolve(args):
    guard = _guard_mutation(args.gates_dir, "resolve a gate")
    if guard:
        print("GATES: FAIL — " + guard)
        return 1
    team_dir = tc.team_dir_of(args.gates_dir)
    data, err = tc.load_json_file(gate_path(args.gates_dir, args.id))
    if err or not isinstance(data, dict):
        print(f"GATES: FAIL — no gate with id {args.id}")
        return 1
    if data.get("status") != "open":
        print(f"GATES: FAIL — {args.id} is {data.get('status')}, not open; a resolved/waived gate is terminal — "
              "a new question gets a new gate")
        return 1
    if not args.resolution.strip():
        print("GATES: FAIL — --resolution must not be empty")
        return 1
    options = data.get("options") or []
    if options and args.resolution not in options:
        print(f"GATES: FAIL — resolution {args.resolution!r} is not one of the options {options}")
        return 1
    active, _humans, _by_id, rerr = load_roster(team_dir)
    if rerr:
        print(f"GATES: warning — {rerr}; resolver not verified against the roster.")
    elif args.by not in active:
        print(f"GATES: FAIL — resolver {args.by} is not an active roster member")
        return 1
    # The worker cannot resolve their own blocker: the resolver must
    # differ from the subject's owner. For a claim subject that does
    # not exist yet there is no owner — any active member may resolve.
    owner = tc.gated_subject_owner(team_dir, data.get("blocks_kind") or "",
                                   data.get("blocks_id") or "")
    if owner and args.by == owner:
        print(f"GATES: FAIL — {args.by} owns the blocked {data.get('blocks_kind')} {data.get('blocks_id')} — "
              "the resolver must differ from the subject's owner (the worker cannot resolve their own blocker)")
        return 1
    review_id = args.review or ""
    if review_id:
        if not tc.valid_id(review_id):
            print(f"GATES: FAIL — review id {review_id!r} fails the id grammar")
            return 1
        rdata, _rerr = tc.load_json_file(os.path.join(team_dir, "reviews", review_id + ".json"))
        if not isinstance(rdata, dict):
            print(f"GATES: FAIL — review {review_id} does not exist")
            return 1
        kind, field = {"claim": ("claim", "task_id"),
                       "prd": ("prd", "prd_item"),
                       "feature": ("feature", "feature_id")}[data.get("blocks_kind") or ""]
        if rdata.get(field) != data.get("blocks_id"):
            print(f"GATES: FAIL — review {review_id} targets {rdata.get(field)}, not the gated "
                  f"{data.get('blocks_kind')} {data.get('blocks_id')}")
            return 1
        if rdata.get("verdict") != "approve":
            print(f"GATES: FAIL — review {review_id} is {rdata.get('verdict')} — only an approve review "
                  "can satisfy a gate")
            return 1

    def upd(rec):
        rec["status"] = "resolved"
        rec["resolution"] = args.resolution
        rec["resolved_by"] = args.by
        rec["resolved_at"] = tc.now_iso()
        rec["resolved_by_review"] = review_id
    rec, err = mutate(args.gates_dir, args.id, args.by, "gate_resolved", upd,
                      extra={"gate_id": args.id, "resolution": args.resolution,
                             "review_id": review_id} if review_id else
                      {"gate_id": args.id, "resolution": args.resolution})
    if err:
        print("GATES: FAIL — " + err)
        return 1
    print(f"GATES: {args.id} resolved by {args.by} -> {args.resolution!r}"
          + (f" (review {review_id})" if review_id else "") + ". Work on the subject may proceed.")
    return 0


def cmd_waive(args):
    guard = _guard_mutation(args.gates_dir, "waive a gate")
    if guard:
        print("GATES: FAIL — " + guard)
        return 1
    team_dir = tc.team_dir_of(args.gates_dir)
    data, err = tc.load_json_file(gate_path(args.gates_dir, args.id))
    if err or not isinstance(data, dict):
        print(f"GATES: FAIL — no gate with id {args.id}")
        return 1
    if data.get("status") != "open":
        print(f"GATES: FAIL — {args.id} is {data.get('status')}, not open; a resolved/waived gate is terminal")
        return 1
    if not args.reason.strip():
        print("GATES: FAIL — --reason must not be empty (a waived gate is still a recorded decision)")
        return 1
    _active, humans, _by_id, rerr = load_roster(team_dir)
    if rerr:
        print(f"GATES: warning — {rerr}; waiving member not verified against the roster.")
    elif args.by not in humans:
        print(f"GATES: FAIL — {args.by} is not a human roster member — waiving is a human act; "
              "an agent cannot waive its own blocker")
        return 1

    def upd(rec):
        rec["status"] = "waived"
        rec["waived_by"] = args.by
        rec["waived_at"] = tc.now_iso()
        rec["waive_reason"] = args.reason
    rec, err = mutate(args.gates_dir, args.id, args.by, "gate_waived", upd,
                      extra={"gate_id": args.id, "reason": args.reason})
    if err:
        print("GATES: FAIL — " + err)
        return 1
    print(f"GATES: {args.id} waived by {args.by} ({args.reason}). Work on the subject may proceed.")
    return 0


def main():
    p = argparse.ArgumentParser(description="Decision gates for team-roster v2.6: a named question that blocks work.")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("list", "check"):
        sp = sub.add_parser(name)
        sp.add_argument("path", nargs="?", default="team/gates")
        if name == "check":
            sp.add_argument("--roster", default="team/roster")
    sp = sub.add_parser("create")
    sp.add_argument("--id", required=True)
    sp.add_argument("--question", required=True)
    sp.add_argument("--options", nargs="*", default=[])
    sp.add_argument("--blocks-kind", required=True, choices=sorted(tc.GATE_KINDS))
    sp.add_argument("--blocks-id", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--gates-dir", default="team/gates")
    sp = sub.add_parser("resolve")
    sp.add_argument("--id", required=True)
    sp.add_argument("--resolution", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--review", default="")
    sp.add_argument("--gates-dir", default="team/gates")
    sp = sub.add_parser("waive")
    sp.add_argument("--id", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--gates-dir", default="team/gates")

    args = p.parse_args()
    if args.cmd == "list":
        return cmd_list(args.path)
    if args.cmd == "check":
        return cmd_check(args.path, args.roster)
    if args.cmd == "create":
        return cmd_create(args)
    if args.cmd == "resolve":
        return cmd_resolve(args)
    if args.cmd == "waive":
        return cmd_waive(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
