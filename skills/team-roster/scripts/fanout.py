#!/usr/bin/env python3
"""Fan-out for team-roster v2.6: one prompt across N agents, compare
the results, merge the winner — Orca's headline pattern, file-based.

Orca's own hands-on test is blunt about the cost: five agents means
five times the token consumption, ~2 GB RAM per live agent, disk
×N worktrees. So fan-out in TheRoster is OPT-IN and EXPLICIT —
never a default. Use it only for high-uncertainty work (a bug with
several valid fixes, a refactor with several defensible
architectures), never for routine work, never for anything with one
right answer, and never when the registry already has a stable
feature (search first — the registry is the counterweight to
rebuilding the same thing N times).

Sharded layout (v2.6): one file per group in team/fanout/<id>.json.
Each member is a normal claim (team/claims/<task-id>.json) carrying
`fanout_group: <id>`, on its own branch — and, for same-file
members, its own worktree (worktree-per-claim mode, v2.3: two
sibling claims on the same file in a shared checkout conflict by
the existing rule, so the second sibling is refused at claim time
— that is the v2.3 rule doing its job, not a special case).
team/index.json is GENERATED (scripts/rebuild_index.py), never
hand-edited. team/events.jsonl is the append-only audit log.

Usage:
    python3 fanout.py list   [team/fanout]
    python3 fanout.py check  [team/fanout] [--roster team/roster]
    python3 fanout.py create --id t-101 --title "Fix the checkout race" \
        --prompt "Fix the race in src/checkout.py; keep the public API." \
        --by owner [--max-members 5]
    python3 fanout.py add --id t-101 --task t-101-a --title "Fix race (approach A)" \
        --owner assistant --files src/checkout.py --branch wt/t-101-a --worktree ../wt-t-101-a
    python3 fanout.py compare --id t-101
    python3 fanout.py decide --id t-101 --winner t-101-a --by owner --reason "cleanest diff, test covers the timeout"
    python3 fanout.py cancel --id t-101 --by owner --reason "race fixed another way"

Rules:
- N is 2–8, default 5 (Orca's canonical five). One member is not
  a fan-out — create with --max-members 1 FAILS.
- Statuses: open, decided, cancelled. Both terminal — a decided
  group is not re-opened; a new attempt gets a new group.
- The group's `members` list is an append-only roster (task,
  owner, branch, worktree, added_at) — it does NOT cache a copy
  of each member claim's status. The claim file is the source of
  truth for member status; `compare` and `list` read each member
  claim's live status + receipt. A cached copy would go stale the
  moment a member is released directly.
- Decide only when every member is settled (done with a receipt,
  or released). A member still active without a receipt blocks
  the decision. The winner must be settled too, and a winner
  whose receipt says failed cannot win. The decider must be an
  active roster member AND must not be the owner of the winning
  claim — nobody picks their own work as the winner.
- Deciding releases every other member claim in the same call
  (released_by: "fanout:<winner>", one claim_released event per
  loser — the same shape as feature cancellation). Cancelling
  releases every ACTIVE member claim (released_by:
  "fanout-cancel:<by>").
- `compare` is a scorecard, not a merge tool: per member it
  shows the live claim status, the receipt outcome + summary +
  files modified (or "no receipt yet"), and the branch diffstat
  vs the claim's base_commit when the branch exists on this
  machine. Picking the winner is a human or coordinator judgment
  call; the script records the decision, it does not make it.
- Every mutating command bumps lamport (+1), appends one line to
  team/events.jsonl, and rebuilds team/index.json.

Exit 0 = ok. Exit 1 = invalid, duplicate, or forbidden.
Stdlib only, Python 3.9+.
"""
import argparse
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import team_common as tc

STATUSES = {"open", "decided", "cancelled"}
DEFAULT_MAX_MEMBERS = 5
MIN_MEMBERS = 2
MAX_MEMBERS = 8


def fanout_path(fanout_dir, gid):
    return os.path.join(fanout_dir, gid + ".json")


def _guard_mutation(fanout_dir, action):
    """Error string when fanout_dir is inside a linked worktree, else None.

    team/ state is only written from the main checkout (v2.3,
    Dziatkovskii rule) — a mutation from inside a worktree lands on
    the worktree branch and is invisible to the rest of the team."""
    return tc.guard_team_mutation(tc.team_dir_of(fanout_dir), action)


def _resolve_fanout_dir(fanout_dir):
    """Redirect a fanout dir inside a linked worktree to the main
    checkout's fanout dir (reads only; prints a note on stderr)."""
    team_dir = tc.team_dir_of(fanout_dir)
    team_dir, note = tc.resolve_team_dir(team_dir)
    if note:
        print("FANOUT: note — " + note, file=sys.stderr)
        return os.path.join(team_dir, "fanout")
    return fanout_dir


def load_fanout(fanout_dir):
    return tc.load_records(fanout_dir)


def load_roster_active(roster_dir):
    """(active_ids, error). error is None on success."""
    if not roster_dir or not os.path.isdir(roster_dir):
        return None, "roster dir not found: %s" % roster_dir
    active = set()
    for _stem, (_path, rec) in tc.load_records(roster_dir).items():
        if isinstance(rec, dict) and rec.get("status") == "active" and rec.get("id"):
            active.add(rec["id"])
    return active, None


def rebuild(team_dir):
    try:
        import rebuild_index
        rebuild_index.rebuild(team_dir)
    except Exception as e:  # index rebuild must never fail a mutation
        print(f"FANOUT: warning — could not rebuild team/index.json: {e}", file=sys.stderr)


def mutate(fanout_dir, gid, actor, event, updater, extra=None):
    """Load, update (bumps lamport), save, log event, rebuild index.
    Returns (record, None) or (None, error)."""
    path = fanout_path(fanout_dir, gid)
    data, err = tc.load_json_file(path)
    if err:
        return None, "no fan-out group with id %s (%s)" % (gid, err)
    if not isinstance(data, dict):
        return None, "fan-out group %s is not a JSON object" % gid
    updater(data)
    data["lamport"] = int(data.get("lamport") or 0) + 1
    tc.save_json(path, data)
    team_dir = tc.team_dir_of(fanout_dir)
    tc.append_event(team_dir, event, data["lamport"], actor,
                    base_commit=tc.git_head_commit(), extra=extra)
    rebuild(team_dir)
    return data, None


def check_fanout(fanout_dir, roster_path=None, claims=None):
    """Validate every fan-out group record. Returns (errors, warnings).

    Shared by `fanout.py check` and validate.py — the per-script
    check is the single source of the rules. `claims` (task_id ->
    claim record) may be passed in by validate.py so the caller
    does not load the claims twice."""
    fanout_dir = _resolve_fanout_dir(fanout_dir)
    team_dir = tc.team_dir_of(fanout_dir)
    if claims is None:
        claims = {}
        for _stem, (_path, rec) in tc.load_records(os.path.join(team_dir, "claims")).items():
            if isinstance(rec, dict):
                claims[_stem] = rec
    errors, warnings = [], []
    groups = load_fanout(fanout_dir)
    active = None
    if roster_path and os.path.isdir(roster_path):
        active, _err = load_roster_active(roster_path)
    for gid, (_path, rec) in sorted(groups.items()):
        label = gid
        if not tc.valid_id(gid):
            errors.append(f"{label}: id fails the id grammar ^[a-z0-9][a-z0-9-]*$ (max 64 chars, ASCII only)")
            continue
        if not isinstance(rec, dict):
            errors.append(f"{label}: not a JSON object")
            continue
        if rec.get("id") != gid:
            errors.append(f"{label}: id inside the file ({rec.get('id')!r}) does not match the filename — one group per file, named <id>.json")
        for field in ("id", "title", "prompt", "status", "max_members",
                      "created_by", "created_at", "lamport"):
            if field not in rec or rec[field] in (None, ""):
                if field == "lamport" and rec.get(field) == 0:
                    continue
                errors.append(f"{label}: missing/empty field: {field}")
        if not isinstance(rec.get("title"), str) or not rec.get("title", "").strip():
            errors.append(f"{label}: title must be a non-empty string")
        if not isinstance(rec.get("prompt"), str) or not rec.get("prompt", "").strip():
            errors.append(f"{label}: prompt must be a non-empty string")
        if rec.get("status") not in STATUSES:
            errors.append(f"{label}: status must be one of {sorted(STATUSES)} (got {rec.get('status')!r})")
        try:
            max_m = int(rec.get("max_members") or 0)
        except (TypeError, ValueError):
            max_m = 0
        if max_m < MIN_MEMBERS or max_m > MAX_MEMBERS:
            errors.append(f"{label}: max_members must be {MIN_MEMBERS}–{MAX_MEMBERS} (got {rec.get('max_members')!r})")
        members = rec.get("members")
        if members is None:
            errors.append(f"{label}: missing members (use [] for none)")
            members = []
        elif not isinstance(members, list):
            errors.append(f"{label}: members must be a list")
            members = []
        else:
            seen_tasks = set()
            for m in members:
                if not isinstance(m, dict):
                    errors.append(f"{label}: member {m!r} is not an object")
                    continue
                tid = m.get("task_id") or ""
                if not tc.valid_id(tid):
                    errors.append(f"{label}: member task_id {tid!r} fails the id grammar")
                    continue
                if tid in seen_tasks:
                    errors.append(f"{label}: member {tid} is listed twice")
                    continue
                seen_tasks.add(tid)
                claim = claims.get(tid)
                if claim is None:
                    errors.append(f"{label}: member {tid} has no claim file — "
                                  "a member without a claim is not a member")
                    continue
                if (claim.get("fanout_group") or "") != gid:
                    errors.append(f"{label}: member {tid}'s claim does not name this group "
                                  f"(fanout_group={claim.get('fanout_group')!r}) — membership is two-sided")
                if m.get("owner_id") and m["owner_id"] != claim.get("owner_id"):
                    errors.append(f"{label}: member {tid} lists owner {m['owner_id']}, but the claim "
                                  f"is owned by {claim.get('owner_id')}")
            if len(members) > max_m:
                errors.append(f"{label}: {len(members)} members exceeds max_members={max_m}")
        if active is not None and rec.get("created_by") not in active:
            errors.append(f"{label}: created_by {rec.get('created_by')} is not an active roster member")
        if rec.get("status") == "decided":
            winner = rec.get("winner_task_id") or ""
            if not winner:
                errors.append(f"{label}: decided group without a winner_task_id")
            elif winner not in {m.get("task_id") for m in members if isinstance(m, dict)}:
                errors.append(f"{label}: winner {winner} is not a member of this group")
            else:
                for m in members:
                    if not isinstance(m, dict) or m.get("task_id") == winner:
                        continue
                    c = claims.get(m.get("task_id") or "")
                    if isinstance(c, dict) and c.get("status") == "active":
                        errors.append(f"{label}: decided, but loser {m.get('task_id')} is still an ACTIVE claim — "
                                      "a decided group with a still-active loser is an unfinished decision")
            if not rec.get("decided_by"):
                errors.append(f"{label}: decided group without decided_by")
            elif active is not None and rec["decided_by"] not in active:
                errors.append(f"{label}: decided_by {rec['decided_by']} is not an active roster member")
            if not rec.get("decision_reason"):
                errors.append(f"{label}: decided group without a decision_reason")
        if rec.get("status") == "cancelled":
            if not rec.get("cancelled_by"):
                errors.append(f"{label}: cancelled group without cancelled_by")
            if not rec.get("cancel_reason"):
                errors.append(f"{label}: cancelled group without a cancel_reason")
        if rec.get("created_at") and tc.parse_time(rec["created_at"]) is None:
            errors.append(f"{label}: created_at must be a UTC ISO-8601 timestamp ending in Z (got {rec['created_at']!r})")
        if rec.get("decided_at") and tc.parse_time(rec["decided_at"]) is None:
            errors.append(f"{label}: decided_at must be a UTC ISO-8601 timestamp ending in Z (got {rec['decided_at']!r})")
        if not isinstance(rec.get("lamport"), int) or rec.get("lamport", 0) < 1:
            errors.append(f"{label}: lamport must be an int >= 1")
    return errors, warnings


def member_claims(team_dir, group):
    """(task_id -> claim record) for a group's members."""
    claims = {}
    for m in group.get("members") or []:
        if not isinstance(m, dict):
            continue
        tid = m.get("task_id") or ""
        if not tid:
            continue
        data, _err = tc.load_json_file(os.path.join(team_dir, "claims", tid + ".json"))
        if isinstance(data, dict):
            claims[tid] = data
    return claims


def member_receipt(team_dir, task_id):
    """A member's receipt record, or None."""
    data, _err = tc.load_json_file(os.path.join(team_dir, "receipts", task_id + ".json"))
    return data if isinstance(data, dict) else None


def release_claim(team_dir, claims_dir, task_id, by, released_by, reason):
    """Release one member claim (status + released_by + event).

    Uses claim.py's mutate so the release is recorded exactly like
    any other release. Returns an error string, or None."""
    import claim as claim_mod
    def upd(c):
        c["status"] = "released"
        c["released_by"] = released_by
    _rec, err = claim_mod.mutate(claims_dir, task_id, by, "claim_released",
                                 upd, extra={"reason": reason})
    return err


# ---------------------------------------------------------------- commands

def cmd_list(fanout_dir):
    fanout_dir = _resolve_fanout_dir(fanout_dir)
    groups = load_fanout(fanout_dir)
    if not groups:
        print("FANOUT: no groups yet — create one with: python3 fanout.py create --id t-101 --title ... --prompt ... --by <you>")
        return 0
    team_dir = tc.team_dir_of(fanout_dir)
    print(f"FANOUT: {len(groups)} group(s):")
    for gid in sorted(groups):
        rec = groups[gid][1]
        if not isinstance(rec, dict):
            print(f"  - {gid}: UNREADABLE (not a JSON object)")
            continue
        claims = member_claims(team_dir, rec)
        settled = sum(1 for c in claims.values() if c.get("status") in ("done", "released"))
        winner = f" — winner: {rec.get('winner_task_id')}" if rec.get("status") == "decided" else ""
        print(f"  - {gid} [{rec.get('status')}] — {rec.get('title')} "
              f"({len(rec.get('members') or [])} member(s), {settled} settled, by {rec.get('created_by')}{winner})")
    return 0


def cmd_check(fanout_dir, roster_path):
    errors, warnings = check_fanout(fanout_dir, roster_path)
    for w in warnings:
        print(f"  ! {w}")
    if errors:
        print("FANOUT CHECK: FAIL — do not push until this is fixed:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("FANOUT CHECK: OK — fan-out group records valid.")
    return 0


def cmd_create(args):
    guard = _guard_mutation(args.fanout_dir, "create a fan-out group")
    if guard:
        print("FANOUT: FAIL — " + guard)
        return 1
    fanout_dir = args.fanout_dir
    os.makedirs(fanout_dir, exist_ok=True)
    team_dir = tc.team_dir_of(fanout_dir)
    if not tc.valid_id(args.id):
        print(f"FANOUT: FAIL — id {args.id!r} fails the id grammar ^[a-z0-9][a-z0-9-]*$ (max 64 chars, ASCII only)")
        return 1
    if not args.title.strip():
        print("FANOUT: FAIL — --title must not be empty")
        return 1
    if not args.prompt.strip():
        print("FANOUT: FAIL — --prompt must not be empty (the shared prompt is the whole point)")
        return 1
    if args.max_members < MIN_MEMBERS or args.max_members > MAX_MEMBERS:
        print(f"FANOUT: FAIL — --max-members must be {MIN_MEMBERS}–{MAX_MEMBERS} (got {args.max_members}) — "
              "one member is not a fan-out, and more than eight is a token fire")
        return 1
    active, rerr = load_roster_active(os.path.join(team_dir, "roster"))
    if rerr:
        print(f"FANOUT: warning — {rerr}; creator not verified against the roster.")
    elif args.by not in active:
        print(f"FANOUT: FAIL — creator {args.by} is not an active roster member")
        return 1
    cfg = tc.load_config(team_dir)
    if cfg.get("worktree_mode") != "per-claim":
        print("FANOUT: warning — worktree_mode is not 'per-claim': members on the SAME file will conflict "
              "in the shared checkout (the second sibling claim is refused). Turn on worktree_mode first "
              "for same-file fan-out, or fan out across different files.")
    path = fanout_path(fanout_dir, args.id)
    if os.path.exists(path):
        print(f"FANOUT: FAIL — id {args.id} already exists. Use it, or pick a new id.")
        return 1
    now = tc.now_iso()
    rec = {
        "id": args.id,
        "title": args.title,
        "prompt": args.prompt,
        "status": "open",
        "max_members": args.max_members,
        "members": [],
        "winner_task_id": "",
        "decided_by": "",
        "decided_at": "",
        "decision_reason": "",
        "cancelled_by": "",
        "cancel_reason": "",
        "created_by": args.by,
        "created_at": now,
        "lamport": 1,
    }
    tc.save_json(path, rec)
    tc.append_event(team_dir, "fanout_created", 1, args.by,
                    base_commit=tc.git_head_commit(),
                    extra={"fanout_id": args.id, "max_members": args.max_members})
    rebuild(team_dir)
    print(f"FANOUT: created {args.id} [open] — \"{args.title}\" (max {args.max_members} members, by {args.by}).")
    print(f"Add members with: python3 fanout.py add --id {args.id} --task <task-id> --title ... --owner <member> --files ...")
    print("Cost reminder: fan-out costs xN tokens — one member per approach, and only for high-uncertainty work.")
    return 0


def cmd_add(args):
    guard = _guard_mutation(args.fanout_dir, "add a fan-out member")
    if guard:
        print("FANOUT: FAIL — " + guard)
        return 1
    fanout_dir = args.fanout_dir
    team_dir = tc.team_dir_of(fanout_dir)
    data, err = tc.load_json_file(fanout_path(fanout_dir, args.id))
    if err or not isinstance(data, dict):
        print(f"FANOUT: FAIL — no fan-out group with id {args.id}")
        return 1
    if data.get("status") != "open":
        print(f"FANOUT: FAIL — group {args.id} is {data.get('status')}, not open — a decided/cancelled group takes no new members")
        return 1
    if len(data.get("members") or []) >= int(data.get("max_members") or DEFAULT_MAX_MEMBERS):
        print(f"FANOUT: FAIL — group {args.id} already has {len(data.get('members') or [])} member(s) "
              f"(max_members={data.get('max_members')})")
        return 1
    if any(isinstance(m, dict) and m.get("task_id") == args.task for m in (data.get("members") or [])):
        print(f"FANOUT: FAIL — task {args.task} is already a member of {args.id}")
        return 1
    # The claim path is the single code path: claim.py claim
    # --fanout-group validates the group (exists, open, room),
    # the conflict rules (same-file siblings need different
    # worktrees), quotas, and the roster — then records
    # fanout_group on the claim AND appends the member entry to
    # the group record. One code path means a direct
    # `claim.py claim --fanout-group` and `fanout.py add` can
    # never disagree about membership.
    import claim as claim_mod
    claim_args = types.SimpleNamespace(
        task=args.task, title=args.title, owner=args.owner,
        files=list(args.files or []), artifact=args.artifact, area=args.area,
        work_type=args.work_type, reserves_feature=args.reserves_feature,
        prd=args.prd, branch=args.branch, worktree=args.worktree,
        hours=args.hours, expected_duration=args.expected_duration,
        claims_dir=os.path.join(team_dir, "claims"),
        config=os.path.join(team_dir, "config.json"),
        roster=os.path.join(team_dir, "roster"),
        fanout_group=args.id, review="")
    rc = claim_mod.cmd_claim(claim_args)
    if rc != 0:
        return rc
    group_rec, _err = tc.load_json_file(fanout_path(fanout_dir, args.id))
    n = len(group_rec.get("members") or []) if isinstance(group_rec, dict) else "?"
    print(f"FANOUT: member {args.task} added to {args.id} ({n}/{group_rec.get('max_members')} members).")
    return 0


def cmd_compare(args):
    fanout_dir = _resolve_fanout_dir(args.fanout_dir)
    team_dir = tc.team_dir_of(fanout_dir)
    data, err = tc.load_json_file(fanout_path(fanout_dir, args.id))
    if err or not isinstance(data, dict):
        print(f"FANOUT: FAIL — no fan-out group with id {args.id}")
        return 1
    claims = member_claims(team_dir, data)
    root = tc.git_root()
    print(f"FANOUT COMPARE: {args.id} — \"{data.get('title')}\" [{data.get('status')}]")
    print("  (scorecard, not a merge tool — picking the winner is a human/coordinator judgment call)")
    for m in data.get("members") or []:
        if not isinstance(m, dict):
            continue
        tid = m.get("task_id") or ""
        claim = claims.get(tid)
        if claim is None:
            print(f"  - {tid}: NO CLAIM FILE — member without a claim")
            continue
        receipt = member_receipt(team_dir, tid)
        if receipt is not None:
            r_note = (f"receipt {receipt.get('outcome')} — {receipt.get('summary')} "
                      f"(files: {', '.join(receipt.get('files_modified') or []) or 'none listed'})")
        else:
            r_note = "no receipt yet"
        branch = claim.get("branch") or ""
        base = claim.get("base_commit") or ""
        if branch and base and root:
            r = tc.run_git(["diff", "--stat", "%s...%s" % (base, branch)], cwd=root)
            if r.returncode == 0 and r.stdout.strip():
                last = [ln for ln in r.stdout.splitlines() if "file" in ln and "changed" in ln]
                diff_note = last[-1].strip() if last else "no changes vs base"
            elif r.returncode == 0:
                diff_note = "no changes vs base"
            else:
                diff_note = "branch not on this machine"
        else:
            diff_note = "no branch recorded"
        print(f"  - {tid} [{claim.get('status')}] owner {claim.get('owner_id')} — {r_note} — diff: {diff_note}")
    return 0


def cmd_decide(args):
    guard = _guard_mutation(args.fanout_dir, "decide a fan-out group")
    if guard:
        print("FANOUT: FAIL — " + guard)
        return 1
    fanout_dir = args.fanout_dir
    team_dir = tc.team_dir_of(fanout_dir)
    claims_dir = os.path.join(team_dir, "claims")
    data, err = tc.load_json_file(fanout_path(fanout_dir, args.id))
    if err or not isinstance(data, dict):
        print(f"FANOUT: FAIL — no fan-out group with id {args.id}")
        return 1
    if data.get("status") != "open":
        print(f"FANOUT: FAIL — group {args.id} is {data.get('status')}, not open — a decided/cancelled group is terminal")
        return 1
    if not args.reason.strip():
        print("FANOUT: FAIL — --reason must not be empty (the decision is the record)")
        return 1
    members = [m for m in (data.get("members") or []) if isinstance(m, dict)]
    member_ids = {m.get("task_id") for m in members}
    if args.winner not in member_ids:
        print(f"FANOUT: FAIL — winner {args.winner} is not a member of {args.id}")
        return 1
    claims = member_claims(team_dir, data)
    active, rerr = load_roster_active(os.path.join(team_dir, "roster"))
    if rerr:
        print(f"FANOUT: warning — {rerr}; decider not verified against the roster.")
    elif args.by not in active:
        print(f"FANOUT: FAIL — decider {args.by} is not an active roster member")
        return 1
    winner_claim = claims.get(args.winner)
    if winner_claim is None:
        print(f"FANOUT: FAIL — winner {args.winner} has no claim file")
        return 1
    if (winner_claim.get("owner_id") or "") == args.by:
        print(f"FANOUT: FAIL — {args.by} owns the winning claim {args.winner} — nobody picks their own work as the winner")
        return 1
    # Every member must be settled first: done (receipt filed) or
    # released. An active member without a receipt blocks the
    # decision — wait for it, release it, or stall it first.
    for m in members:
        tid = m.get("task_id") or ""
        c = claims.get(tid)
        if c is None:
            print(f"FANOUT: FAIL — member {tid} has no claim file")
            return 1
        if c.get("status") == "active":
            print(f"FANOUT: FAIL — member {tid} is still ACTIVE without a receipt — "
                  "decide only when every member is settled (done with a receipt, or released)")
            return 1
    if winner_claim.get("status") == "released":
        print(f"FANOUT: FAIL — winner {args.winner} was released — a released attempt cannot win")
        return 1
    # The winner is judged on its receipt: done + succeeded. (An
    # active winner was already refused by the settled check above.)
    receipt = member_receipt(team_dir, args.winner)
    if receipt is None:
        print(f"FANOUT: FAIL — winner {args.winner} is done but has no receipt — "
              "a winner is judged on its receipt (file it with claim.py done)")
        return 1
    if receipt.get("outcome") == "failed":
        print(f"FANOUT: FAIL — winner {args.winner}'s receipt says failed — a failed attempt cannot win")
        return 1
    # Release the losers in the same call (same shape as feature
    # cancellation): the decision and the releases land together.
    losers = [m.get("task_id") for m in members if m.get("task_id") != args.winner]
    for tid in losers:
        c = claims.get(tid)
        if c is None or c.get("status") == "released":
            continue
        err = release_claim(team_dir, claims_dir, tid, args.by,
                            "fanout:" + args.winner,
                            "fan-out %s decided: %s won (%s)" % (args.id, args.winner, args.reason))
        if err:
            print(f"FANOUT: FAIL — could not release loser {tid}: {err}")
            return 1
    def upd(rec):
        rec["status"] = "decided"
        rec["winner_task_id"] = args.winner
        rec["decided_by"] = args.by
        rec["decided_at"] = tc.now_iso()
        rec["decision_reason"] = args.reason
    rec, err = mutate(fanout_dir, args.id, args.by, "fanout_decided", upd,
                      extra={"fanout_id": args.id, "winner": args.winner,
                             "reason": args.reason})
    if err:
        print("FANOUT: FAIL — " + err)
        return 1
    print(f"FANOUT: {args.id} decided — winner {args.winner} ({args.reason}). "
          f"{len(losers)} loser(s) released (released_by: fanout:{args.winner}).")
    print("Merge the winner's branch via PR, then remove the losers' worktrees.")
    return 0


def cmd_cancel(args):
    guard = _guard_mutation(args.fanout_dir, "cancel a fan-out group")
    if guard:
        print("FANOUT: FAIL — " + guard)
        return 1
    fanout_dir = args.fanout_dir
    team_dir = tc.team_dir_of(fanout_dir)
    claims_dir = os.path.join(team_dir, "claims")
    data, err = tc.load_json_file(fanout_path(fanout_dir, args.id))
    if err or not isinstance(data, dict):
        print(f"FANOUT: FAIL — no fan-out group with id {args.id}")
        return 1
    if data.get("status") != "open":
        print(f"FANOUT: FAIL — group {args.id} is {data.get('status')}, not open — a decided/cancelled group is terminal")
        return 1
    if not args.reason.strip():
        print("FANOUT: FAIL — --reason must not be empty")
        return 1
    active, rerr = load_roster_active(os.path.join(team_dir, "roster"))
    if rerr:
        print(f"FANOUT: warning — {rerr}; canceller not verified against the roster.")
    elif args.by not in active:
        print(f"FANOUT: FAIL — canceller {args.by} is not an active roster member")
        return 1
    claims = member_claims(team_dir, data)
    released = []
    for tid, c in claims.items():
        if c.get("status") != "active":
            continue
        err = release_claim(team_dir, claims_dir, tid, args.by,
                            "fanout-cancel:" + args.by,
                            "fan-out %s cancelled: %s" % (args.id, args.reason))
        if err:
            print(f"FANOUT: FAIL — could not release member {tid}: {err}")
            return 1
        released.append(tid)

    def upd(rec):
        rec["status"] = "cancelled"
        rec["cancelled_by"] = args.by
        rec["cancel_reason"] = args.reason
    rec, err = mutate(fanout_dir, args.id, args.by, "fanout_cancelled", upd,
                      extra={"fanout_id": args.id, "reason": args.reason})
    if err:
        print("FANOUT: FAIL — " + err)
        return 1
    print(f"FANOUT: {args.id} cancelled by {args.by} ({args.reason}). "
          f"{len(released)} active member claim(s) released.")
    return 0


def main():
    p = argparse.ArgumentParser(description="Fan-out for team-roster v2.6: one prompt across N agents, compare, merge the winner.")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("list", "check"):
        sp = sub.add_parser(name)
        sp.add_argument("path", nargs="?", default="team/fanout")
        if name == "check":
            sp.add_argument("--roster", default="team/roster")
    sp = sub.add_parser("create")
    sp.add_argument("--id", required=True)
    sp.add_argument("--title", required=True)
    sp.add_argument("--prompt", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--max-members", type=int, default=DEFAULT_MAX_MEMBERS)
    sp.add_argument("--fanout-dir", default="team/fanout")
    sp = sub.add_parser("add")
    sp.add_argument("--id", required=True)
    sp.add_argument("--task", required=True)
    sp.add_argument("--title", required=True)
    sp.add_argument("--owner", required=True)
    sp.add_argument("--files", nargs="*", default=[])
    sp.add_argument("--artifact", default="")
    sp.add_argument("--area", default="")
    sp.add_argument("--work-type", default="code")
    sp.add_argument("--reserves-feature", default="")
    sp.add_argument("--prd", default="")
    sp.add_argument("--branch", default="")
    sp.add_argument("--worktree", default="")
    sp.add_argument("--hours", type=float, default=None)
    sp.add_argument("--expected-duration", default="")
    sp.add_argument("--fanout-dir", default="team/fanout")
    sp = sub.add_parser("compare")
    sp.add_argument("--id", required=True)
    sp.add_argument("--fanout-dir", default="team/fanout")
    sp = sub.add_parser("decide")
    sp.add_argument("--id", required=True)
    sp.add_argument("--winner", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--fanout-dir", default="team/fanout")
    sp = sub.add_parser("cancel")
    sp.add_argument("--id", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--fanout-dir", default="team/fanout")

    args = p.parse_args()
    if args.cmd == "list":
        return cmd_list(args.path)
    if args.cmd == "check":
        return cmd_check(args.path, args.roster)
    if args.cmd == "create":
        return cmd_create(args)
    if args.cmd == "add":
        return cmd_add(args)
    if args.cmd == "compare":
        return cmd_compare(args)
    if args.cmd == "decide":
        return cmd_decide(args)
    if args.cmd == "cancel":
        return cmd_cancel(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
