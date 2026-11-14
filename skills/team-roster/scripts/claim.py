#!/usr/bin/env python3
"""Work ledger for team-roster v2: claim work before you do it, so two
disconnected AIs / humans don't edit the same file or task at once.

Sharded layout (v2, BREAKING vs v1): one file per claim in
team/claims/<task-id>.json — creating the file IS the claim, so two
agents claiming different tasks never touch the same file and never
merge-conflict. team/index.json is GENERATED (scripts/rebuild_index.py),
never hand-edited. team/events.jsonl is the append-only audit log.

Usage:
    python3 claim.py list   [team/claims] [--roster team/roster]
    python3 claim.py check  [team/claims] [--roster team/roster]
                            [--config team/config.json]
    python3 claim.py claim  --task t-002 --title "Kerjakan checkout" \
        --owner dev-1 --files src/checkout.py tests/test_checkout.py \
        [--artifact NAME | --area NAME] [--work-type code] \
        [--reserves-feature login-api] [--branch feat/checkout] \
        [--hours 2] [--expected-duration 6h] \
        [--claims-dir team/claims] [--config team/config.json]
    python3 claim.py heartbeat --task t-002 [--claims-dir team/claims]
    python3 claim.py stall     --task t-002 --by dev-2 --reason "..." [--claims-dir team/claims]
    python3 claim.py flag      --task t-002 --by dev-2 --reason "..." [--claims-dir team/claims]
    python3 claim.py release   --task t-002 --by dev-1 [--claims-dir team/claims]
    python3 claim.py done      --task t-002 [--claims-dir team/claims]
    python3 claim.py handoff   --task t-002 --to dev-2 --reason "..." \
        --summary "done X, left Y" [--claims-dir team/claims]

Rules:
- Claims are EXACT file paths only. Directory paths (trailing /) and
  globs (* ? [) are rejected. A claim whose path is a prefix of, or
  has as a prefix, an existing claimed path is rejected. Paths are
  compared lowercased, so src/Auth.py and src/auth.py collide.
  Newly created files inside a claimed directory are UNCLAIMED until
  claimed — a claim covers exactly the files it lists, nothing more.
- Claim expiry is ADVISORY with a 15-min grace window (config
  grace_minutes): a claim is stealable only when now > expires_at +
  grace AND its heartbeat is stale (30+ min without a heartbeat,
  config heartbeat_minutes). An expired claim whose owner is still
  heartbeating still blocks — the owner is alive, just overdue.
- Every mutating command bumps the claim's lamport (+1), appends one
  line to team/events.jsonl, and rebuilds team/index.json.
- claim_seq is monotonic per claimed file (max existing claim_seq for
  that file + 1). Copy it into commit trailers as "[claim #N]" — the
  hook/CI rejects a push whose commits touch a file whose current
  claim_seq on origin/main is higher than the trailer's (someone
  re-claimed it after you). This is a fencing approximation, not a
  real lock: a human can forge a trailer.
- Quotas (from team/config.json, defaults 5 claims/agent, 10
  files/claim): claiming past them FAILS.
- handoff is the ONLY way a claim's owner changes: it writes
  team/handoffs/<task>-<to>.json AND updates the claim owner_id +
  handoff_note. Never edit owner_id by hand.
- flag: 3 flags from distinct agents, or 1 flag from a human roster
  member, releases the claim (recorded, auditable).

Exit 0 = ok. Exit 1 = conflict / invalid / unknown owner.
Stdlib only, Python 3.9+.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import team_common as tc

DEFAULT_HOURS = 2
STATUSES = {"active", "done", "released", "stalled", "abandoned"}
WORK_TYPES = {"code", "design", "writing", "data", "ops"}


def claim_path(claims_dir, task_id):
    return os.path.join(claims_dir, task_id + ".json")


def load_roster_active(roster_dir):
    """(active_ids, human_ids, error). error is None on success."""
    if not roster_dir or not os.path.isdir(roster_dir):
        return None, None, "roster dir not found: %s" % roster_dir
    active, humans = set(), set()
    for _stem, (_path, rec) in tc.load_records(roster_dir).items():
        if not isinstance(rec, dict):
            continue
        rid = rec.get("id")
        if not rid:
            continue
        if rec.get("status") == "active":
            active.add(rid)
        if rec.get("type") == "human":
            humans.add(rid)
    return active, humans, None


def load_claims(claims_dir):
    recs = {}
    for stem, (_path, rec) in tc.load_records(claims_dir).items():
        if isinstance(rec, dict):
            recs[stem] = rec
    return recs


def check_conflicts(claims, files, exclude_task=None):
    """Returns [(claim, [overlapping files]), ...] against blocking claims,
    ordered by (lamport, owner_id) — the claim that claimed first comes
    first, so the caller can say who must yield."""
    conflicts = []
    for task_id, c in sorted(claims.items(), key=lambda kv: tc.claim_order_key(kv[1])):
        if exclude_task and task_id == exclude_task:
            continue
        if not tc.is_blocking(c):
            continue
        overlap = sorted({f for f in files for g in (c.get("files") or [])
                          if tc.paths_overlap(f, g)})
        if overlap:
            conflicts.append((c, overlap))
    return conflicts


def rebuild(team_dir):
    try:
        import rebuild_index
        rebuild_index.rebuild(team_dir)
    except Exception as e:  # index rebuild must never fail a mutation
        print(f"CLAIM: warning — could not rebuild team/index.json: {e}", file=sys.stderr)


def mutate(claims_dir, task_id, actor, event, updater, extra=None):
    """Load, update (bumps lamport), save, log event, rebuild index.
    Returns (record, None) or (None, error)."""
    path = claim_path(claims_dir, task_id)
    data, err = tc.load_json_file(path)
    if err:
        return None, "no claim with task_id %s (%s)" % (task_id, err)
    if not isinstance(data, dict):
        return None, "claim %s is not a JSON object" % task_id
    updater(data)
    data["lamport"] = int(data.get("lamport") or 0) + 1
    tc.save_json(path, data)
    team_dir = tc.team_dir_of(claims_dir)
    tc.append_event(team_dir, event, data["lamport"], actor,
                    base_commit=tc.git_head_commit(), task_id=task_id, extra=extra)
    rebuild(team_dir)
    return data, None


# ---------------------------------------------------------------- commands

def cmd_list(claims_dir):
    claims = load_claims(claims_dir)
    if not claims:
        print("CLAIM: no claims recorded yet.")
        return 0
    blocking = {t: c for t, c in claims.items() if tc.is_blocking(c)}
    print(f"CLAIM: {len(claims)} claim(s) recorded, {len(blocking)} blocking:")
    for task_id in sorted(claims):
        c = claims[task_id]
        files = ", ".join(c.get("files") or []) or "(no files — %s scope)" % (c.get("scope") or {}).get("type", "?")
        print(f"  - {task_id}: {c.get('task')} — {c.get('owner_id')} "
              f"[claim #{c.get('claim_seq')}] — {tc.claim_status_note(c)} — files: {files}")
    return 0


def cmd_check(claims_dir, roster_path, config_path):
    team_dir = tc.team_dir_of(claims_dir)
    cfg = tc.load_config(team_dir if not config_path else os.path.dirname(config_path) or ".")
    if config_path and os.path.isfile(config_path):
        cfg = tc.load_config(os.path.dirname(config_path) or ".")
    errors, warnings = [], []
    claims = load_claims(claims_dir)
    roster_ids = None
    if roster_path:
        roster_ids, _humans, err = load_roster_active(roster_path)
        if err:
            errors.append("cannot read roster %s: %s" % (roster_path, err))
            roster_ids = None
    seen_lower = {}
    owner_active_count = {}
    for task_id, c in sorted(claims.items()):
        if not tc.valid_id(task_id):
            errors.append(f"{task_id}: task_id fails the id grammar "
                          "^[a-z0-9][a-z0-9-]*$ (max 64 chars, ASCII only)")
        for field in ("task_id", "task", "owner_id", "status", "started_at",
                      "expires_at", "heartbeat_at", "lamport", "claim_seq", "scope"):
            if field not in c or c[field] in (None, "", []):
                if field in ("lamport", "claim_seq") and c.get(field) == 0:
                    continue
                errors.append(f"{task_id}: missing/empty field: {field}")
        if c.get("status") not in STATUSES:
            errors.append(f"{task_id}: status must be one of {sorted(STATUSES)} (got {c.get('status')})")
        if c.get("work_type") and c["work_type"] not in WORK_TYPES:
            errors.append(f"{task_id}: work_type must be one of {sorted(WORK_TYPES)}")
        for tf in ("started_at", "expires_at", "heartbeat_at"):
            if c.get(tf) and tc.parse_time(c[tf]) is None:
                errors.append(f"{task_id}: {tf} must be a UTC ISO-8601 timestamp ending in Z (got {c[tf]!r}) — zone-less timestamps are rejected")
        if roster_ids is not None and c.get("owner_id") not in roster_ids:
            errors.append(f"{task_id}: owner {c.get('owner_id')} is not an active roster member")
        scope = c.get("scope") or {}
        stype = scope.get("type")
        if stype not in ("files", "artifact", "area"):
            errors.append(f"{task_id}: scope.type must be files|artifact|area (got {stype!r})")
        elif stype == "files" and not c.get("files"):
            errors.append(f"{task_id}: files-scope claim must list at least one file")
        elif stype == "artifact" and not scope.get("artifact"):
            errors.append(f"{task_id}: artifact-scope claim must name the artifact")
        elif stype == "area" and not scope.get("area"):
            errors.append(f"{task_id}: area-scope claim must name the area")
        if len(c.get("files") or []) > cfg["max_files_per_claim"]:
            errors.append(f"{task_id}: {len(c['files'])} files exceeds max_files_per_claim={cfg['max_files_per_claim']}")
        for f in c.get("files") or []:
            err = tc.path_syntax_error(f)
            if err:
                errors.append(f"{task_id}: {err}")
            low = tc.canonical_path(f)
            if low in seen_lower and tc.is_blocking(c):
                other_id, other_claim = seen_lower[low]
                if other_id != task_id:
                    # "First" is decided by (lamport, owner_id), never
                    # by wall-clock — a skewed clock can be minutes in
                    # the future. The later claim must yield.
                    first, second = sorted(
                        [(other_claim, other_id), (c, task_id)],
                        key=lambda pair: tc.claim_order_key(pair[0]))
                    errors.append(
                        f"CONFLICT: file {f} claimed by both {other_id} and {task_id} — "
                        f"first by (lamport, owner) is {first[1]} (lamport {first[0].get('lamport')}), "
                        f"so {second[1]} must yield")
            elif tc.is_blocking(c):
                seen_lower[low] = (task_id, c)
        if tc.is_blocking(c):
            owner_active_count[c.get("owner_id")] = owner_active_count.get(c.get("owner_id"), 0) + 1
        if c.get("status") == "active" and tc.parse_time(c.get("expires_at") or ""):
            if tc.is_stealable(c, cfg=cfg):
                warnings.append(f"{task_id}: stealable — expired + heartbeat stale for "
                                f"{cfg['heartbeat_minutes']}+ min; anyone may re-claim these files")
    for owner, n in sorted(owner_active_count.items()):
        if n > cfg["max_claims_per_agent"]:
            errors.append(f"{owner}: {n} active claims exceeds max_claims_per_agent={cfg['max_claims_per_agent']}")
    # Prefix-overlap between blocking claims (exact-string check above
    # only catches identical lowercased paths).
    blocking = [(t, c) for t, c in claims.items() if tc.is_blocking(c)]
    for i in range(len(blocking)):
        for j in range(i + 1, len(blocking)):
            t1, c1 = blocking[i]
            t2, c2 = blocking[j]
            for f in c1.get("files") or []:
                for g in c2.get("files") or []:
                    if tc.paths_overlap(f, g) and tc.canonical_path(f) != tc.canonical_path(g):
                        first, second = sorted(
                            [(c1, t1), (c2, t2)],
                            key=lambda pair: tc.claim_order_key(pair[0]))
                        errors.append(f"CONFLICT: {f} ({t1}) overlaps {g} ({t2}) — one path is a prefix "
                                      f"of the other; first by (lamport, owner) is {first[1]}, "
                                      f"so {second[1]} must yield")
    # Branch freshness of the checked-out branch: warn past the
    # threshold, FAIL past 2x, until rebase + re-search.
    root = tc.git_root()
    if root:
        behind, age, note = tc.branch_freshness(root, cfg["canonical_remote"], cfg["canonical_branch"])
        if behind is None:
            warnings.append("branch freshness skipped: " + note)
        else:
            lim_c, lim_d = cfg["branch_freshness_commits"], cfg["branch_freshness_days"]
            if behind > 2 * lim_c or age > 2 * lim_d:
                errors.append(f"branch is {behind} commits behind {cfg['canonical_remote']}/{cfg['canonical_branch']} "
                              f"and {age:.1f} days old (limit {lim_c} commits / {lim_d} days) — "
                              "rebase onto fresh main and re-search the registry before pushing")
            elif behind > lim_c or age > lim_d:
                warnings.append(f"branch is {behind} commits behind {cfg['canonical_remote']}/{cfg['canonical_branch']} "
                                f"and {age:.1f} days old (limit {lim_c} commits / {lim_d} days) — rebase soon")
    else:
        warnings.append("branch freshness skipped: not in a git repo")
    for w in warnings:
        print(f"  ! {w}")
    if errors:
        print("CLAIM CHECK: FAIL — do not push until this is fixed:")
        for e in errors:
            print(f"  - {e}")
        return 1
    n_blocking = sum(1 for c in claims.values() if tc.is_blocking(c))
    print(f"CLAIM CHECK: OK — {len(claims)} claim(s), {n_blocking} blocking, no conflicts.")
    return 0


def cmd_claim(args):
    claims_dir = args.claims_dir
    os.makedirs(claims_dir, exist_ok=True)
    team_dir = tc.team_dir_of(claims_dir)
    cfg = tc.load_config(team_dir if not args.config else os.path.dirname(args.config) or ".")
    if args.config and os.path.isfile(args.config):
        cfg = tc.load_config(os.path.dirname(args.config) or ".")
    if not tc.valid_id(args.task):
        print(f"CLAIM: FAIL — task id {args.task!r} fails the id grammar "
              "^[a-z0-9][a-z0-9-]*$ (max 64 chars, ASCII only)")
        return 1
    if not tc.valid_id(args.owner):
        print(f"CLAIM: FAIL — owner id {args.owner!r} fails the id grammar")
        return 1
    if args.artifact and args.area:
        print("CLAIM: FAIL — give at most one of --artifact / --area (only one scope type)")
        return 1
    files = sorted(set(args.files or []))
    if args.artifact:
        scope = {"type": "artifact", "artifact": args.artifact}
    elif args.area:
        scope = {"type": "area", "area": args.area}
    else:
        scope = {"type": "files"}
        if not files:
            print("CLAIM: FAIL — give at least one --files entry, or --artifact / --area for non-file work")
            return 1
    for f in files:
        err = tc.path_syntax_error(f)
        if err:
            print(f"CLAIM: FAIL — {err}")
            return 1
    if len(files) > cfg["max_files_per_claim"]:
        print(f"CLAIM: FAIL — {len(files)} files exceeds max_files_per_claim={cfg['max_files_per_claim']}")
        return 1
    if args.work_type not in WORK_TYPES:
        print(f"CLAIM: FAIL — --work-type must be one of {sorted(WORK_TYPES)}")
        return 1
    if args.reserves_feature and not tc.valid_id(args.reserves_feature):
        print(f"CLAIM: FAIL — reserves-feature id {args.reserves_feature!r} fails the id grammar")
        return 1
    roster_dir = args.roster or os.path.join(team_dir, "roster")
    active, _humans, rerr = load_roster_active(roster_dir)
    if rerr:
        print(f"CLAIM: warning — {rerr}; owner not verified against the roster.")
    elif args.owner not in active:
        print(f"CLAIM: FAIL — owner {args.owner} is not an active roster member")
        return 1
    claims = load_claims(claims_dir)
    active_count = sum(1 for c in claims.values()
                       if c.get("owner_id") == args.owner and tc.is_blocking(c))
    path = claim_path(claims_dir, args.task)
    existing, _err = tc.load_json_file(path)
    if isinstance(existing, dict) and existing.get("status") == "active":
        if existing.get("owner_id") != args.owner:
            print(f"CLAIM: FAIL — {args.task} is already actively claimed by {existing.get('owner_id')}")
            return 1
        # Renewal: same task re-claims (extend expiry, update files).
        conflicts = check_conflicts(claims, files, exclude_task=args.task)
        if conflicts:
            print("CLAIM: FAIL — someone else is already working on this:")
            for c, overlap in conflicts:
                print(f"  - {c.get('task_id')} ({c.get('owner_id')}): {', '.join(overlap)}")
            return 1
        def renew(c):
            c["files"] = files
            c["scope"] = scope
            c["work_type"] = args.work_type
            c["branch"] = args.branch or c.get("branch", "")
            c["heartbeat_at"] = tc.now_iso()
            c["expires_at"] = tc.to_utc_z(tc.now() + tc.timedelta(minutes=int(cfg["claim_ttl_minutes"] if args.hours is None else args.hours * 60)))
            if args.expected_duration:
                c["expected_duration"] = args.expected_duration
        rec, err = mutate(claims_dir, args.task, args.owner, "claim_renewed", renew)
        if err:
            print("CLAIM: FAIL — " + err)
            return 1
        print(f"CLAIM: renewed {args.task} for {args.hours}h — files: {', '.join(files) or '(none — ' + scope['type'] + ' scope)'}")
        print(f"Commit trailer to use: [claim #{rec['claim_seq']}]")
        print("Remember: commit + push the claim file ALONE first, before any work commits.")
        return 0
    if active_count >= cfg["max_claims_per_agent"]:
        print(f"CLAIM: FAIL — {args.owner} already has {active_count} active claims "
              f"(max_claims_per_agent={cfg['max_claims_per_agent']})")
        return 1
    conflicts = check_conflicts(claims, files)
    if conflicts:
        print("CLAIM: FAIL — someone else is already working on this:")
        for c, overlap in conflicts:
            print(f"  - {c.get('task_id')} ({c.get('owner_id')}, until {c.get('expires_at')}): {', '.join(overlap)}")
        print("Pick other work, or wait until their claim expires / is released.")
        return 1
    if args.reserves_feature:
        features_dir = os.path.join(team_dir, "features")
        if os.path.isdir(features_dir) and args.reserves_feature + ".json" in os.listdir(features_dir):
            print(f"CLAIM: warning — feature id {args.reserves_feature} is already registered; "
                  "reserving it has no effect — reuse that feature instead.")
    start = tc.now()
    hours = DEFAULT_HOURS if args.hours is None else args.hours
    seq = tc.next_claim_seq(claims_dir, files)
    record = {
        "task_id": args.task,
        "task": args.title,
        "owner_id": args.owner,
        "status": "active",
        "files": files,
        "scope": scope,
        "work_type": args.work_type,
        "branch": args.branch or "",
        "base_commit": tc.git_head_commit(),
        "started_at": tc.to_utc_z(start),
        "expires_at": tc.to_utc_z(start + tc.timedelta(hours=hours)),
        "heartbeat_at": tc.to_utc_z(start),
        "lamport": 1,
        "claim_seq": seq,
        "reserves_feature": args.reserves_feature or "",
        "expected_duration": args.expected_duration or "",
        "handoff_note": "",
        "released_by": "",
        "stalled_by": "",
        "stall_reason": "",
    }
    tc.save_json(path, record)
    tc.append_event(team_dir, "claim_created", 1, args.owner,
                    base_commit=tc.git_head_commit(), task_id=args.task)
    rebuild(team_dir)
    print(f"CLAIM: OK — {args.task} claimed by {args.owner} for {hours}h — files: {', '.join(files) or '(none — ' + scope['type'] + ' scope)'}")
    print(f"Commit trailer to use on every commit touching these files: [claim #{seq}]")
    print("Remember: commit + push the claim file ALONE first, before any work commits.")
    return 0


def cmd_heartbeat(args):
    def upd(c):
        c["heartbeat_at"] = tc.now_iso()
    rec, err = mutate(args.claims_dir, args.task, args.by or (tc.load_json_file(claim_path(args.claims_dir, args.task))[0] or {}).get("owner_id", "?"),
                      "claim_heartbeat", upd)
    if err:
        print("CLAIM: FAIL — " + err)
        return 1
    print(f"CLAIM: heartbeat {args.task} at {rec['heartbeat_at']} (claim #{rec['claim_seq']}). Commit + push the claim file.")
    return 0


def cmd_stall(args):
    path = claim_path(args.claims_dir, args.task)
    data, err = tc.load_json_file(path)
    if err or not isinstance(data, dict):
        print(f"CLAIM: FAIL — no claim with task_id {args.task}")
        return 1
    if data.get("status") != "active":
        print(f"CLAIM: FAIL — {args.task} is {data.get('status')}, not active; nothing to stall")
        return 1
    team_dir = tc.team_dir_of(args.claims_dir)
    cfg = tc.load_config(team_dir)
    roster_dir = args.roster or os.path.join(team_dir, "roster")
    _active, humans, _rerr = load_roster_active(roster_dir)
    stale = tc.is_heartbeat_stale(data, heartbeat_minutes=cfg["heartbeat_minutes"])
    if not stale and args.by != data.get("owner_id") and args.by not in (humans or set()):
        print(f"CLAIM: FAIL — {args.task} has a fresh heartbeat (owner is alive); "
              "only the owner or a human roster member may stall a live claim")
        return 1

    def upd(c):
        c["status"] = "stalled"
        c["stalled_by"] = args.by
        c["stall_reason"] = args.reason
    rec, err = mutate(args.claims_dir, args.task, args.by, "claim_stalled", upd,
                      extra={"reason": args.reason})
    if err:
        print("CLAIM: FAIL — " + err)
        return 1
    print(f"CLAIM: {args.task} -> stalled by {args.by}: {args.reason}. Commit + push the claim file.")
    return 0


def cmd_flag(args):
    path = claim_path(args.claims_dir, args.task)
    data, err = tc.load_json_file(path)
    if err or not isinstance(data, dict):
        print(f"CLAIM: FAIL — no claim with task_id {args.task}")
        return 1
    if data.get("status") != "active":
        print(f"CLAIM: FAIL — {args.task} is {data.get('status')}, not active; nothing to flag")
        return 1
    team_dir = tc.team_dir_of(args.claims_dir)
    cfg = tc.load_config(team_dir)
    roster_dir = args.roster or os.path.join(team_dir, "roster")
    _active, humans, _rerr = load_roster_active(roster_dir)
    flags = data.get("flags") or []
    if any(f.get("by") == args.by for f in flags):
        print(f"CLAIM: FAIL — {args.by} already flagged {args.task}")
        return 1
    flags.append({"by": args.by, "reason": args.reason, "at": tc.now_iso()})
    distinct = {f.get("by") for f in flags}
    is_human = args.by in (humans or set())
    released = len(distinct) >= 3 or is_human

    def upd(c):
        c["flags"] = flags
        c["flagged_by"] = sorted(distinct)
        c["flag_reason"] = args.reason
        if released:
            c["status"] = "released"
            c["released_by"] = "flags:" + ",".join(sorted(distinct))
    rec, err = mutate(args.claims_dir, args.task, args.by,
                      "claim_released" if released else "claim_flagged", upd,
                      extra={"reason": args.reason})
    if err:
        print("CLAIM: FAIL — " + err)
        return 1
    if released:
        print(f"CLAIM: {args.task} RELEASED by flags ({len(distinct)} distinct flagger(s)"
              + ("; a human flagged it" if is_human else "")
              + "). Commit + push the claim file.")
    else:
        print(f"CLAIM: {args.task} flagged by {args.by} ({len(distinct)}/3 distinct flags; "
              "a human flag releases immediately). Commit + push the claim file.")
    return 0


def cmd_set_status(args, status, event):
    def upd(c):
        c["status"] = status
        if status == "released":
            c["released_by"] = args.by
    rec, err = mutate(args.claims_dir, args.task, getattr(args, "by", "") or "", event, upd)
    if err:
        print("CLAIM: FAIL — " + err)
        return 1
    print(f"CLAIM: {args.task} -> {status}. Commit + push the claim file.")
    return 0


def cmd_handoff(args):
    claims_dir = args.claims_dir
    team_dir = tc.team_dir_of(claims_dir)
    if not tc.valid_id(args.to):
        print(f"CLAIM: FAIL — new owner id {args.to!r} fails the id grammar")
        return 1
    roster_dir = args.roster or os.path.join(team_dir, "roster")
    active, _humans, rerr = load_roster_active(roster_dir)
    if rerr:
        print(f"CLAIM: warning — {rerr}; new owner not verified against the roster.")
    elif args.to not in active:
        print(f"CLAIM: FAIL — {args.to} is not an active roster member")
        return 1
    path = claim_path(claims_dir, args.task)
    data, err = tc.load_json_file(path)
    if err or not isinstance(data, dict):
        print(f"CLAIM: FAIL — no claim with task_id {args.task}")
        return 1
    if data.get("status") != "active":
        print(f"CLAIM: FAIL — {args.task} is {data.get('status')}, not active; only an active claim can be handed off")
        return 1
    from_id = data.get("owner_id", "")
    note = "Handoff %s -> %s: %s. %s" % (from_id, args.to, args.reason, args.summary)

    def upd(c):
        c["owner_id"] = args.to
        c["handoff_note"] = note
        c["heartbeat_at"] = tc.now_iso()
    rec, err = mutate(claims_dir, args.task, from_id, "claim_handed_off", upd,
                      extra={"to": args.to, "reason": args.reason})
    if err:
        print("CLAIM: FAIL — " + err)
        return 1
    handoffs_dir = os.path.join(team_dir, "handoffs")
    os.makedirs(handoffs_dir, exist_ok=True)
    handoff = {
        "id": "%s-%s" % (args.task, args.to),
        "task_id": args.task,
        "from_id": from_id,
        "to_id": args.to,
        "reason": args.reason,
        "summary": args.summary,
        "at": tc.now_iso(),
        "lamport": rec["lamport"],
    }
    tc.save_json(os.path.join(handoffs_dir, "%s-%s.json" % (args.task, args.to)), handoff)
    print(f"CLAIM: handoff {args.task}: {from_id} -> {args.to}. Wrote team/handoffs/{args.task}-{args.to}.json.")
    print("Commit + push the claim file AND the handoff file.")
    return 0


def main():
    p = argparse.ArgumentParser(description="Claim work so teammates don't collide (team-roster v2, sharded claims).")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("list", "check"):
        sp = sub.add_parser(name)
        sp.add_argument("path", nargs="?", default="team/claims")
        if name == "check":
            sp.add_argument("--roster", default="team/roster")
            sp.add_argument("--config", default="")
    sp = sub.add_parser("claim")
    sp.add_argument("--task", required=True)
    sp.add_argument("--title", required=True)
    sp.add_argument("--owner", required=True)
    sp.add_argument("--files", nargs="*", default=[])
    sp.add_argument("--artifact", default="")
    sp.add_argument("--area", default="")
    sp.add_argument("--work-type", default="code", choices=sorted(WORK_TYPES))
    sp.add_argument("--reserves-feature", default="")
    sp.add_argument("--branch", default="")
    sp.add_argument("--hours", type=float, default=None)
    sp.add_argument("--expected-duration", default="")
    sp.add_argument("--claims-dir", default="team/claims")
    sp.add_argument("--config", default="")
    sp.add_argument("--roster", default="")
    sp = sub.add_parser("heartbeat")
    sp.add_argument("--task", required=True)
    sp.add_argument("--by", default="")
    sp.add_argument("--claims-dir", default="team/claims")
    sp = sub.add_parser("stall")
    sp.add_argument("--task", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--claims-dir", default="team/claims")
    sp.add_argument("--roster", default="")
    sp = sub.add_parser("flag")
    sp.add_argument("--task", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--claims-dir", default="team/claims")
    sp.add_argument("--roster", default="")
    sp = sub.add_parser("release")
    sp.add_argument("--task", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--claims-dir", default="team/claims")
    sp = sub.add_parser("done")
    sp.add_argument("--task", required=True)
    sp.add_argument("--by", default="")
    sp.add_argument("--claims-dir", default="team/claims")
    sp = sub.add_parser("handoff")
    sp.add_argument("--task", required=True)
    sp.add_argument("--to", required=True)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--summary", required=True)
    sp.add_argument("--claims-dir", default="team/claims")
    sp.add_argument("--roster", default="")

    args = p.parse_args()
    if args.cmd == "list":
        return cmd_list(args.path)
    if args.cmd == "check":
        return cmd_check(args.path, args.roster, args.config)
    if args.cmd == "claim":
        return cmd_claim(args)
    if args.cmd == "heartbeat":
        return cmd_heartbeat(args)
    if args.cmd == "stall":
        return cmd_stall(args)
    if args.cmd == "flag":
        return cmd_flag(args)
    if args.cmd == "release":
        return cmd_set_status(args, "released", "claim_released")
    if args.cmd == "done":
        return cmd_set_status(args, "done", "claim_done")
    if args.cmd == "handoff":
        return cmd_handoff(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
