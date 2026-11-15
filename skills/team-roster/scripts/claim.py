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
        [--reserves-feature login-api] [--prd prd-001] [--branch feat/checkout] \
        [--hours 2] [--expected-duration 6h] \
        [--claims-dir team/claims] [--config team/config.json]
    # Worktree-per-claim mode (v2.3, opt-in: worktree_mode="per-claim"
    # in team/config.json). The claim records where the work happens;
    # the claim itself is STILL pushed alone to main first.
    python3 claim.py claim --task t-201 --title "Kerjakan checkout" \
        --owner dev-1 --files src/checkout.py --worktree ../wt-t-201 \
        [--branch wt/t-201]
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
- Worktree-per-claim (v2.3, opt-in): with --worktree, the claim
  records `worktree` (path) + `branch` + `base_commit`. The claim
  itself is STILL pushed alone to main first — a claim board that
  lives only inside a worktree is invisible to the team
  (Dziatkovskii trap). Two worktree claims on DIFFERENT worktrees
  may cover the same file; any pair involving a shared-checkout
  claim still conflicts. Mutating team/ state from inside a linked
  worktree is refused — cd to the main checkout first. Reads from
  inside a worktree are redirected to the main checkout's team/.
  v2.3.1: worktree paths compare canonically — `../wt-x` and its
  absolute spelling are the same worktree, even before the directory
  exists; `claim.py check` fails a blocking claim whose worktree is
  gone but whose branch still exists here (deleted on this machine),
  and fails a branch mismatch between claim and worktree.

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


def load_prd_item(team_dir, pid):
    """One PRD item, or None (missing / not a JSON object)."""
    data, _err = tc.load_json_file(os.path.join(team_dir, "prd", pid + ".json"))
    return data if isinstance(data, dict) else None


def check_conflicts(claims, files, exclude_task=None, new_claim=None,
                     repo_root=None):
    """Returns [(claim, [overlapping files]), ...] against blocking claims,
    ordered by (lamport, owner_id) — the claim that claimed first comes
    first, so the caller can say who must yield.

    Worktree exemption (v2.3): a new worktree claim does NOT conflict
    with a blocking worktree claim on a DIFFERENT worktree — the two
    agents edit isolated checkouts, so there is nothing to overwrite.
    Any pair involving a shared-checkout claim still conflicts, and
    two claims naming the SAME worktree path conflict too. Worktree
    paths are compared canonically (v2.3.1): `../wt-x` and its
    absolute spelling are the same worktree, so `repo_root` (the main
    checkout root) must be passed for relative paths to resolve
    against the right base."""
    conflicts = []
    for task_id, c in sorted(claims.items(), key=lambda kv: tc.claim_order_key(kv[1])):
        if exclude_task and task_id == exclude_task:
            continue
        if not tc.is_blocking(c):
            continue
        if new_claim is not None and not tc.claims_share_checkout(new_claim, c, repo_root):
            continue
        overlap = sorted({f for f in files for g in (c.get("files") or [])
                          if tc.paths_overlap(f, g)})
        if overlap:
            conflicts.append((c, overlap))
    return conflicts


def resolve_claims_dir(claims_dir):
    """Redirect a claims dir inside a linked worktree to the main checkout.

    Returns (effective_claims_dir, note). Reads use the effective dir;
    the worktree copy of team/ is stale the moment it is branched."""
    team_dir = tc.team_dir_of(claims_dir)
    team_dir, note = tc.resolve_team_dir(team_dir)
    if note:
        print("CLAIM: note — " + note, file=sys.stderr)
        return os.path.join(team_dir, "claims"), note
    return claims_dir, None


def validate_worktree_args(worktree, branch, task_id, cfg, repo_root):
    """Validate a --worktree claim. Returns (worktree, branch, error).

    `worktree` is the path as given (recorded verbatim); `branch`
    defaults to wt/<task-id> when --worktree is given without --branch.
    Error is None on success."""
    if not worktree:
        return ("", branch or "", None)
    if cfg.get("worktree_mode") != "per-claim":
        return ("", "", "worktree claims need worktree_mode=\"per-claim\" "
                "in team/config.json — set it first, or claim without "
                "--worktree (shared-checkout mode)")
    if not repo_root:
        return ("", "", "--worktree needs a git repo (could not find the "
                "repo root from the current directory)")
    branch = branch or ("wt/" + task_id)
    if not tc.valid_branch_name(branch):
        return ("", "", "branch %r is not a legal git branch name" % branch)
    abs_wt = (worktree if os.path.isabs(worktree)
              else os.path.join(repo_root, worktree))
    abs_wt = os.path.normpath(abs_wt)
    root_norm = os.path.normpath(repo_root)
    if abs_wt == root_norm or abs_wt.startswith(root_norm + os.sep):
        return ("", "", "worktree path %r is inside the repo (%s) — a "
                "worktree must live OUTSIDE the main checkout" % (worktree, root_norm))
    if tc.worktree_entry_for_path(repo_root, abs_wt) is not None:
        return ("", "", "worktree path %r is already a registered "
                "worktree — use a fresh path per claim" % worktree)
    if os.path.isdir(abs_wt) and os.listdir(abs_wt):
        return ("", "", "worktree path %r already exists and is not empty"
                % worktree)
    r = tc.run_git(["show-ref", "--verify", "--quiet",
                    "refs/heads/" + branch], cwd=repo_root)
    if r.returncode == 0:
        return ("", "", "branch %r already exists — a worktree claim "
                "creates a fresh branch" % branch)
    return (worktree, branch, None)


def rebuild(team_dir):
    try:
        import rebuild_index
        rebuild_index.rebuild(team_dir)
    except Exception as e:  # index rebuild must never fail a mutation
        print(f"CLAIM: warning — could not rebuild team/index.json: {e}", file=sys.stderr)


def mutate(claims_dir, task_id, actor, event, updater, extra=None):
    """Load, update (bumps lamport), save, log event, rebuild index.
    Returns (record, None) or (None, error)."""
    team_dir = tc.team_dir_of(claims_dir)
    guard = tc.guard_team_mutation(team_dir, "mutate team/ state")
    if guard:
        return None, guard
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
    claims_dir, _note = resolve_claims_dir(claims_dir)
    claims = load_claims(claims_dir)
    if not claims:
        print("CLAIM: no claims recorded yet.")
        return 0
    blocking = {t: c for t, c in claims.items() if tc.is_blocking(c)}
    print(f"CLAIM: {len(claims)} claim(s) recorded, {len(blocking)} blocking:")
    for task_id in sorted(claims):
        c = claims[task_id]
        files = ", ".join(c.get("files") or []) or "(no files — %s scope)" % (c.get("scope") or {}).get("type", "?")
        wt = c.get("worktree") or ""
        wt_note = f" — worktree {wt} [{c.get('branch')}]" if wt else ""
        print(f"  - {task_id}: {c.get('task')} — {c.get('owner_id')} "
              f"[claim #{c.get('claim_seq')}] — {tc.claim_status_note(c)} — files: {files}{wt_note}")
    return 0


def cmd_check(claims_dir, roster_path, config_path):
    claims_dir, _note = resolve_claims_dir(claims_dir)
    team_dir = tc.team_dir_of(claims_dir)
    cfg = tc.load_config(team_dir if not config_path else os.path.dirname(config_path) or ".")
    if config_path and os.path.isfile(config_path):
        cfg = tc.load_config(os.path.dirname(config_path) or ".")
    # Main checkout root: relative worktree paths are recorded against
    # it, so it is the base for every canonical path comparison below.
    # (When run from inside a linked worktree, git_root() is the
    # worktree — main_checkout_path() recovers the main checkout.)
    root = tc.git_root()
    main_root = (tc.main_checkout_path(root) or root) if root else None
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
        prd_item = c.get("prd_item") or ""
        if prd_item:
            if not tc.valid_id(prd_item):
                errors.append(f"{task_id}: prd_item id fails the id grammar")
            else:
                prd = load_prd_item(team_dir, prd_item)
                if prd is None:
                    errors.append(f"{task_id}: prd_item {prd_item} does not exist — create it with prd.py, or re-claim without --prd")
                elif tc.is_blocking(c) and prd.get("status") in ("cancelled", "done"):
                    errors.append(f"{task_id}: works on PRD item {prd_item}, which is {prd.get('status')} — "
                                  "release this claim; there is nothing to work on")
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
        # Worktree-per-claim consistency (v2.3).
        wt = c.get("worktree") or ""
        if wt:
            if cfg.get("worktree_mode") != "per-claim":
                errors.append(f"{task_id}: worktree claim but team/config.json worktree_mode is not "
                              "'per-claim' — enable it, or re-claim without --worktree")
            if not c.get("branch"):
                errors.append(f"{task_id}: worktree claim without a branch")
            if not c.get("base_commit"):
                errors.append(f"{task_id}: worktree claim without a base_commit — re-claim so the base is recorded")
            if c.get("branch") and not tc.valid_branch_name(c["branch"]):
                errors.append(f"{task_id}: branch {c['branch']!r} is not a legal git branch name")
        for f in c.get("files") or []:
            err = tc.path_syntax_error(f)
            if err:
                errors.append(f"{task_id}: {err}")
            low = tc.canonical_path(f)
            if low in seen_lower and tc.is_blocking(c):
                other_id, other_claim = seen_lower[low]
                # Worktree exemption (v2.3): two worktree claims on
                # different worktrees may cover the same file.
                if other_id != task_id and tc.claims_share_checkout(c, other_claim, main_root):
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
            # Worktree exemption (v2.3): different worktrees never share
            # a checkout, so prefix overlap between them is not a conflict.
            if not tc.claims_share_checkout(c1, c2, main_root):
                continue
            for f in c1.get("files") or []:
                for g in c2.get("files") or []:
                    if tc.paths_overlap(f, g) and tc.canonical_path(f) != tc.canonical_path(g):
                        first, second = sorted(
                            [(c1, t1), (c2, t2)],
                            key=lambda pair: tc.claim_order_key(pair[0]))
                        errors.append(f"CONFLICT: {f} ({t1}) overlaps {g} ({t2}) — one path is a prefix "
                                      f"of the other; first by (lamport, owner) is {first[1]}, "
                                      f"so {second[1]} must yield")
    # Two blocking worktree claims must not share a worktree path or a branch.
    seen_wt_path, seen_wt_branch = {}, {}
    for t, c in blocking:
        wt = c.get("worktree") or ""
        br = c.get("branch") or ""
        if wt:
            # Canonical key (v2.3.1): `../wt-x` and its absolute
            # spelling are the same worktree.
            key = tc.canonical_worktree_path(wt, main_root)
            if key in seen_wt_path:
                errors.append(f"CONFLICT: worktree path {wt} is claimed by both {seen_wt_path[key]} and {t} — one worktree per claim")
            else:
                seen_wt_path[key] = t
        if wt and br:
            if br in seen_wt_branch:
                errors.append(f"CONFLICT: branch {br} is claimed by both {seen_wt_branch[br]} and {t} — one branch per worktree claim")
            else:
                seen_wt_branch[br] = t
    # A worktree claim's base_commit must be a commit this repo knows,
    # its worktree must be present or accounted for, and its branch
    # must match the worktree on disk (v2.3.1: E2 + E8b).
    root = tc.git_root()
    if root:
        main_root = tc.main_checkout_path(root) or root
        for t, c in blocking:
            wt = c.get("worktree") or ""
            br = c.get("branch") or ""
            if wt and c.get("base_commit"):
                r = tc.run_git(["cat-file", "-e", c["base_commit"] + "^{commit}"], cwd=root)
                if r.returncode != 0:
                    errors.append(f"{t}: worktree claim base_commit {c['base_commit']} is unknown to this "
                                  "repo — rebase the worktree onto a known base and re-claim")
            if not wt:
                continue
            presence = tc.worktree_presence(main_root, wt, br)
            if presence == "deleted-here":
                errors.append(f"{t}: worktree {wt} is gone but branch {br} still exists here — "
                              "the worktree was removed; release the claim or recreate the worktree")
            elif presence == "absent":
                warnings.append(f"{t}: worktree {wt} is not present on this machine — the claim "
                                "may live on a teammate's laptop; if it was deleted, release the claim")
            elif presence == "present":
                entry = tc.worktree_entry_for_path(main_root, wt)
                if entry is not None and (entry.get("branch") or "") != br:
                    errors.append(f"{t}: worktree at {wt} is on branch {entry.get('branch')}, but the claim "
                                  f"says {br} — fix the claim or the worktree")
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
    team_dir = tc.team_dir_of(claims_dir)
    guard = tc.guard_team_mutation(team_dir, "create a claim")
    if guard:
        print("CLAIM: FAIL — " + guard)
        return 1
    os.makedirs(claims_dir, exist_ok=True)
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
    prd_item = args.prd or ""
    if prd_item and not tc.valid_id(prd_item):
        print(f"CLAIM: FAIL — prd id {prd_item!r} fails the id grammar")
        return 1
    if prd_item:
        prd = load_prd_item(team_dir, prd_item)
        if prd is None:
            print(f"CLAIM: FAIL — no PRD item {prd_item} (create it first: "
                  f"python3 prd.py create --id {prd_item} --title ... --owner <you>)")
            return 1
        if prd.get("status") in ("cancelled", "done"):
            print(f"CLAIM: FAIL — PRD item {prd_item} is {prd.get('status')} — there is nothing to work on")
            return 1
    elif cfg.get("prd_required"):
        print("CLAIM: FAIL — team/config.json has prd_required=true: give --prd <item-id> "
              "(create the item first with prd.py)")
        return 1
    # Worktree-per-claim (v2.3): validates the mode, the branch name,
    # and the path (outside the repo, fresh branch, unused path).
    repo_root = tc.git_root()
    worktree, branch, werr = validate_worktree_args(
        args.worktree, args.branch, args.task, cfg, repo_root)
    if werr:
        print("CLAIM: FAIL — " + werr)
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
        # The effective worktree is the new one when --worktree is
        # given, else the claim's existing worktree (if any).
        renew_wt = worktree if args.worktree else (existing.get("worktree") or "")
        conflicts = check_conflicts(claims, files, exclude_task=args.task,
                                    new_claim={"worktree": renew_wt},
                                    repo_root=repo_root)
        if conflicts:
            print("CLAIM: FAIL — someone else is already working on this:")
            for c, overlap in conflicts:
                print(f"  - {c.get('task_id')} ({c.get('owner_id')}): {', '.join(overlap)}")
            return 1
        def renew(c):
            c["files"] = files
            c["scope"] = scope
            c["work_type"] = args.work_type
            c["branch"] = branch or c.get("branch", "")
            if args.worktree:
                c["worktree"] = worktree
                c["base_commit"] = tc.git_head_commit()
            c["heartbeat_at"] = tc.now_iso()
            c["expires_at"] = tc.to_utc_z(tc.now() + tc.timedelta(minutes=int(cfg["claim_ttl_minutes"] if args.hours is None else args.hours * 60)))
            if args.expected_duration:
                c["expected_duration"] = args.expected_duration
            if args.prd:
                c["prd_item"] = args.prd
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
    conflicts = check_conflicts(claims, files, new_claim={"worktree": worktree},
                              repo_root=repo_root)
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
        "branch": branch,
        "worktree": worktree,
        "base_commit": tc.git_head_commit(),
        "started_at": tc.to_utc_z(start),
        "expires_at": tc.to_utc_z(start + tc.timedelta(hours=hours)),
        "heartbeat_at": tc.to_utc_z(start),
        "lamport": 1,
        "claim_seq": seq,
        "reserves_feature": args.reserves_feature or "",
        "prd_item": prd_item,
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
    wt_note = f" — worktree {worktree} [{branch}]" if worktree else ""
    print(f"CLAIM: OK — {args.task} claimed by {args.owner} for {hours}h — files: {', '.join(files) or '(none — ' + scope['type'] + ' scope)'}{wt_note}")
    print(f"Commit trailer to use on every commit touching these files: [claim #{seq}]")
    print("Remember: commit + push the claim file ALONE first, before any work commits.")
    if worktree:
        print(f"Then create the worktree: bash scripts/worktree_setup.sh {worktree} {branch} {record['base_commit']}")
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
    sp.add_argument("--prd", default="")
    sp.add_argument("--branch", default="")
    sp.add_argument("--worktree", default="",
                    help="worktree-per-claim (v2.3, needs worktree_mode='per-claim'): "
                         "path of the worktree this claim works in, outside the repo; "
                         "branch defaults to wt/<task-id>")
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
