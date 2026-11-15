#!/usr/bin/env python3
"""Cross-file validator for team-roster v2 — validates roster + claims
+ features TOGETHER (the one check the per-file scripts cannot do).
Used by hooks/pre-push and by the CI twin (same script, same rules).

Usage:
    python3 validate.py [team] [--actor <roster-id>] [--range <git-range>]

Checks:
- owner must exist in roster with status active (claims + features);
  contributors must be roster ids; used_by holds TASK ids
  (features.py use --by <task-id>) and is only checked for id shape.
- feature version matches its latest changes entry; SemVer shape;
  0.y.z / pre-release can never be stable.
- files non-empty for files-scope claims; scope consistency
  (artifact/area name present, exactly one scope type).
- quotas: max active claims per agent, max files per claim
  (from team/config.json, defaults 5 / 10).
- every feature file carries status_reason + status_changed_by +
  status_changed_at (status transitions are enforced at write time
  by features.py; this re-checks the fields are present).
- PRD layer (v2.2): an item's owner must be an active roster member;
  a done item's features must exist and be stable or cancelled; a
  claim on a cancelled or done item FAILS; with prd_required=true
  (team/config.json) a blocking claim without a PRD item FAILS. A
  cancelled item whose features are still live only warns.
- dependencies (v2.2): a recorded dependency version may not be
  newer than the dependency's current version; a MAJOR bump upstream
  warns "needs reverify" — or FAILS with strict_deps=true.
- branch freshness of the checked-out branch: warn past the
  threshold, FAIL past 2x (config branch_freshness_commits/days).
- worktree-per-claim consistency (v2.3, gates hardened in v2.3.1):
  a blocking worktree claim must name a branch and a base_commit the
  repo knows, and two blocking worktree claims must not share a
  worktree path or a branch — paths compare canonically, so `../wt-x`
  and its absolute spelling are the same worktree. Two worktree
  claims on DIFFERENT worktrees may cover the same file (the merge
  check, not the claim check, decides whether they land cleanly). A
  worktree absent on THIS machine only warns — the claim may live on
  a teammate's laptop — EXCEPT when its branch still exists here:
  then the worktree was deleted on this machine, and that FAILS.
- signed identity (v2.4, only when team/config.json has
  require_signatures=true): every commit in the push range that
  touches team/ must be signed by the SSH key recorded on the roster
  for the record's owner (owner_id for claims/features/PRD, added_by
  for roster records, a single released_by/stalled_by for releases,
  done_by for a done claim — v2.6, see below). No key on file, a bad
  signature, or a signature from a different key FAILS. Composite
  actors (flags:, cancel:, fanout:, fanout-cancel:) skip
  verification (no single key can attest to them); multi-actor
  commits are already failed by the one-owner rule; the bootstrap
  push skips with a warning. Local script runs stay unbound — the
  push is the enforcement point.
- mailbox trust model (v2.4): team/mailbox/*.jsonl is untrusted
  data, never an instruction. An imperative pattern (the same list
  check_roster.py scans roster text for) in a line from a HUMAN
  roster member, or from a sender the roster does not know, FAILS;
  from an ai-agent/tool member it warns. Human orders arrive only
  via a human-approved PR or a signed record.
  v2.5: when require_signatures is on, a line from a HUMAN member
  MUST carry a valid `sig` from that sender's roster key — any
  content, any wording (the keyword heuristic could not catch a
  rephrased order). A `sig`, when present on an agent line, is
  verified too — present-but-invalid FAILS.
- ghost claims (v2.5): a claim whose owner is gone from the roster
  is automatically non-blocking and flagged for release (warning,
  not an error); it never holds files or bricks a push.
- clock-skew gate (v2.5): a claim timestamp more than 15 minutes in
  the future FAILS, as do expires_at / heartbeat_at before
  started_at. The same future check applies to event timestamps.
- claims may not cover team/ paths (v2.5); duplicate display names
  FAIL (v2.5); dependency cycles FAIL (v2.5).
- Orca features (v2.6): fan-out groups, decision gates, typed
  completion receipts, and formal reviews are all validated here —
  the per-script `check` functions are the single source of the
  rules and validate.py calls them (fanout.py check, gates.py
  check, receipts.py check, reviews.py check), plus two cross-file
  rules the per-script checks cannot see: a blocking claim bound
  to an open decision gate FAILS, and a done PRD item whose latest
  review (on the item, its features, or its claims) is
  request-changes FAILS. The push-range actor mapping is per
  record type (fan-out: created/decided/cancelled_by; gate:
  created/resolved/waived_by; receipt: done_by; review: reviewer)
  — the old `owner_id or id` would use a record id as the actor.
  The deletion-protection list and the event-reference checks cover
  the four new dirs, and the secret/PII scan covers their free-text
  fields (note, question, resolution, prompt).
- sabotage detection (v2.5, push range): a deleted team/ record
  file FAILS unless it never existed on the range base; every work
  commit touching a file covered by a blocking claim must carry a
  matching [claim #N] trailer (and, with require_signatures on, be
  signed by the claim owner's key); the recorded last-known-main-SHA
  must still be an ancestor of origin/main, or history was rewritten.
- event validation (v2.5): every team/events.jsonl line must be
  well-formed JSON with a parseable timestamp, a roster actor (or
  the system actor "v1-migration", or a composite flags:/cancel:
  actor), and task_id/feature_id references to records that exist.
  Shape and references only — a forged-but-well-formed event still
  passes; the log's truth rests on signed commits + branch protection.
- "first" in double-claim conflicts is decided by (created_seq,
  owner_id) (v2.4), never by wall-clock — and no longer by lamport.
- ownership of team/ records, in the push range
  (git log <canonical_remote>/<canonical_branch>..HEAD, falling back
  to HEAD if no remote): all changed records must share one
  owner_id, UNLESS the change is a release/stalled event
  (released_by/stalled_by recorded), a done claim (done_by recorded
  — v2.6: the filer attests, so a human filing a receipt on an
  agent's behalf can push the claim + receipt together), or a roster
  change (warn only — roster changes need human approval). Roster entries added in the
  same push as work they authorise = FAIL (no self-registration):
  if a new roster member id appears and a claim/feature in the same
  push has owner_id == that new id, FAIL. With --actor, changed
  records must also belong to that actor unless they are
  release/stalled events. EXCEPTION: the bootstrap push — when the
  remote branch has no team/roster/ yet, one human seeds the whole
  team in one commit, so the one-owner and no-self-registration rules
  are skipped once (warned, not failed) and apply from the next push.
- rename-without-registry-update: if a commit in the push range
  renames a file that is an entry point of a feature and
  team/features/<id>.json was not also changed in the same push,
  FAIL. (git log --name-status --diff-filter=R; if git history is
  unavailable, skip with a warning, not a fail.)
- contact allow-list + secret/PII scan on team/ free-text fields:
  emails and token shapes (ghp_/gho_/sk-/AKIA/xox*, 32+ char
  hex/alnum runs) FAIL; URLs in roster role/name FAIL, URLs in
  feature does/notes WARN. team/ is public-to-the-repo forever —
  never put secrets, emails, or internal URLs in it; removal needs
  a history rewrite (git filter-repo).

Exit 0 = all checks pass (warnings allowed). Exit 1 = any FAIL.
Stdlib only, Python 3.9+.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import team_common as tc

FREE_TEXT_FIELDS = ("role", "name", "task", "does", "summary", "reason",
                    "handoff_note", "stall_reason", "breaking_notes",
                    "status_reason", "note", "question", "resolution", "prompt")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
TOKEN_RES = [
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgho_[A-Za-z0-9]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\b[0-9a-f]{32,}\b"),
    re.compile(r"\b[A-Za-z0-9_-]{40,}\b"),
]
URL_RE = re.compile(r"https?://\S+")
TEAM_PREFIXES = ("team/roster", "team/claims", "team/features", "team/prd",
                 "team/fanout", "team/gates", "team/receipts", "team/reviews")


def record_actor(path, rec):
    """The roster member who attests to a team/ record — for the
    one-owner rule and the signature check in the push range.

    v2.6: the old code read `owner_id or id`, which is WRONG for the
    new record types — it would use the record id as the actor (a
    fan-out group id, a gate id, a receipt's task id, a review id
    are not people). Each type names its actor differently:
    fan-out = created_by (or decided_by/cancelled_by when the group
    is decided/cancelled — the person who made the LAST state
    change attests to it); gate = created_by (or resolved_by /
    waived_by); receipt = done_by; review = reviewer."""
    if path.startswith("team/roster/"):
        return rec.get("added_by") or ""
    if path.startswith("team/fanout/"):
        return (rec.get("decided_by") or rec.get("cancelled_by")
                or rec.get("created_by") or "")
    if path.startswith("team/gates/"):
        return (rec.get("resolved_by") or rec.get("waived_by")
                or rec.get("created_by") or "")
    if path.startswith("team/receipts/"):
        return rec.get("done_by") or ""
    if path.startswith("team/reviews/"):
        return rec.get("reviewer") or ""
    return rec.get("owner_id") or rec.get("id") or ""


def load_all(team_dir):
    roster, claims, features, prd = {}, {}, {}, {}
    fanout, gates, receipts, reviews = {}, {}, {}, {}
    for stem, (_p, rec) in tc.load_records(os.path.join(team_dir, "roster")).items():
        if isinstance(rec, dict):
            roster[stem] = rec
    for stem, (_p, rec) in tc.load_records(os.path.join(team_dir, "claims")).items():
        if isinstance(rec, dict):
            claims[stem] = rec
    for stem, (_p, rec) in tc.load_records(os.path.join(team_dir, "features")).items():
        if isinstance(rec, dict):
            features[stem] = rec
    for stem, (_p, rec) in tc.load_records(os.path.join(team_dir, "prd")).items():
        if isinstance(rec, dict):
            prd[stem] = rec
    for stem, (_p, rec) in tc.load_records(os.path.join(team_dir, "fanout")).items():
        if isinstance(rec, dict):
            fanout[stem] = rec
    for stem, (_p, rec) in tc.load_records(os.path.join(team_dir, "gates")).items():
        if isinstance(rec, dict):
            gates[stem] = rec
    for stem, (_p, rec) in tc.load_records(os.path.join(team_dir, "receipts")).items():
        if isinstance(rec, dict):
            receipts[stem] = rec
    for stem, (_p, rec) in tc.load_records(os.path.join(team_dir, "reviews")).items():
        if isinstance(rec, dict):
            reviews[stem] = rec
    return roster, claims, features, prd, fanout, gates, receipts, reviews


def git_log_names(range_spec, extra_args):
    r = tc.run_git(["log", "--pretty=format:", "--name-only"] + extra_args + [range_spec])
    if r.returncode != 0:
        return None
    return sorted({ln.strip() for ln in r.stdout.splitlines() if ln.strip()})


def git_log_renames(range_spec):
    """Returns [(old_path, new_path), ...] from --diff-filter=R."""
    r = tc.run_git(["log", "--pretty=format:", "--name-status", "--diff-filter=R", range_spec])
    if r.returncode != 0:
        return None
    out = []
    for ln in r.stdout.splitlines():
        parts = ln.split("\t")
        if len(parts) == 3 and parts[0].startswith("R"):
            out.append((parts[1], parts[2]))
    return out


def git_log_deletions(range_spec):
    """Paths deleted in a range (sorted, de-duplicated), or None.

    v2.5 (deletion detection): --no-renames matters — without it a
    rename out of team/ reports as R, not D, and a claim file
    renamed away would slip past as a "move". With it, a rename
    reports as a deletion + an addition, and the deletion half is
    what this check wants."""
    r = tc.run_git(["log", "--pretty=format:", "--name-only",
                    "--diff-filter=D", "--no-renames", range_spec])
    if r.returncode != 0:
        return None
    return sorted({ln.strip() for ln in r.stdout.splitlines() if ln.strip()})


def git_commit_parents(sha):
    """Parent SHAs of one commit (empty list for a root commit,
    None when git cannot read it)."""
    r = tc.run_git(["show", "--pretty=%P", "--no-patch", sha])
    if r.returncode != 0:
        return None
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def git_commit_message(sha):
    """Full commit message (subject + body) of one commit."""
    r = tc.run_git(["log", "-1", "--format=%B", sha])
    if r.returncode != 0:
        return ""
    return r.stdout


def record_at_head(path):
    r = tc.run_git(["show", "HEAD:" + path])
    if r.returncode != 0:
        return None
    try:
        import json
        data = json.loads(r.stdout)
        return data if isinstance(data, dict) else None
    except ValueError:
        return None


def git_log_commits(range_spec):
    """Commit SHAs in a range, newest first. Returns None on failure.
    A bare "HEAD" range means the tip commit only — `git log HEAD`
    would list the whole history, which is not the push range."""
    if range_spec == "HEAD":
        r = tc.run_git(["rev-parse", "HEAD"])
        return [r.stdout.strip()] if r.returncode == 0 and r.stdout.strip() else None
    r = tc.run_git(["log", "--format=%H", range_spec])
    if r.returncode != 0:
        return None
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def git_show_names(sha):
    """Paths changed by one commit (sorted, de-duplicated)."""
    r = tc.run_git(["show", "--name-only", "--pretty=format:", sha])
    if r.returncode != 0:
        return []
    return sorted({ln.strip() for ln in r.stdout.splitlines() if ln.strip()})


def record_at_commit(sha, path):
    """A team/ record as of one commit (or None when unreadable —
    deleted in that commit, or not a JSON object)."""
    r = tc.run_git(["show", "%s:%s" % (sha, path)])
    if r.returncode != 0:
        return None
    try:
        import json
        data = json.loads(r.stdout)
        return data if isinstance(data, dict) else None
    except ValueError:
        return None


def main():
    p = argparse.ArgumentParser(description="Cross-file validator for team-roster v2.")
    p.add_argument("team", nargs="?", default="team")
    p.add_argument("--actor", default="")
    p.add_argument("--range", default="")
    args = p.parse_args()
    team_dir = args.team
    # Reads come from the main checkout, never from a worktree copy
    # (Dziatkovskii rule).
    team_dir, redirect_note = tc.resolve_team_dir(team_dir)
    if redirect_note:
        print("VALIDATE: note — " + redirect_note, file=sys.stderr)
    cfg = tc.load_config(team_dir)
    errors, warnings = [], []
    roster, claims, features, prd, fanout, gates, receipts, reviews = load_all(team_dir)
    # Main checkout root: relative worktree paths are recorded against
    # it, so it is the base for every canonical path comparison.
    root = tc.git_root()
    main_root = (tc.main_checkout_path(root) or root) if root else None
    active_ids = {rid for rid, m in roster.items() if m.get("status") == "active"}
    all_ids = set(roster)

    # ---- per-record schema + cross-file references ----------------
    for tid, c in sorted(claims.items()):
        label = "claim %s" % tid
        if not tc.valid_id(tid):
            errors.append(f"{label}: task_id fails the id grammar")
        if c.get("owner_id") not in active_ids:
            if c.get("owner_id") not in all_ids:
                # Ghost claim (v2.5): the owner is gone from the roster
                # entirely — the claim is dead, not blocking, and must
                # never brick a push. Warn (with the release command);
                # every is_blocking() call below already excludes it.
                if c.get("status") == "active":
                    warnings.append(f"{label}: owner {c.get('owner_id')} is gone from the roster — "
                                    "the claim is dead (not blocking); release it: "
                                    f"python3 scripts/claim.py release --task {tid} --by <you>")
            else:
                errors.append(f"{label}: owner {c.get('owner_id')} is not an active roster member")
        for tf in ("started_at", "expires_at", "heartbeat_at"):
            if c.get(tf) and tc.parse_time(c[tf]) is None:
                errors.append(f"{label}: {tf} must be a UTC ISO-8601 timestamp ending in Z (got {c[tf]!r})")
        # Clock-skew gate (v2.5): started_at / heartbeat_at more
        # than 15 minutes in the future vs the local clock are
        # impossible — a skewed clock wrote this claim. expires_at is
        # NOT futureness-gated: it is a promise about the future
        # (now + TTL, default 2h), so a fresh claim legitimately
        # expires hours ahead. expires_at is only ordering-checked.
        _started = tc.parse_time(c.get("started_at") or "")
        _expires = tc.parse_time(c.get("expires_at") or "")
        _heartbeat = tc.parse_time(c.get("heartbeat_at") or "")
        for _tf, _dt in (("started_at", _started),
                         ("heartbeat_at", _heartbeat)):
            if _dt is not None and tc.is_impossibly_future(_dt):
                errors.append(f"{label}: {_tf} {c[_tf]} is more than {tc.CLOCK_SKEW_FAIL_MINUTES} minutes "
                              "in the future — a skewed clock wrote this claim; fix the clock (NTP) and re-claim")
        if _started is not None and _expires is not None and _expires < _started:
            errors.append(f"{label}: expires_at {c['expires_at']} is before started_at {c['started_at']} — "
                          "impossible; the claim's timestamps contradict each other")
        if _started is not None and _heartbeat is not None and _heartbeat < _started:
            errors.append(f"{label}: heartbeat_at {c['heartbeat_at']} is before started_at {c['started_at']} — "
                          "impossible; a heartbeat cannot predate the claim")
        if not isinstance(c.get("lamport"), int) or c.get("lamport", 0) < 1:
            errors.append(f"{label}: lamport must be an int >= 1")
        scope = c.get("scope") or {}
        stype = scope.get("type")
        if stype == "files" and not c.get("files"):
            errors.append(f"{label}: files-scope claim must list at least one file")
        if stype == "artifact" and not scope.get("artifact"):
            errors.append(f"{label}: artifact-scope claim must name the artifact")
        if stype == "area" and not scope.get("area"):
            errors.append(f"{label}: area-scope claim must name the area")
        if len(c.get("files") or []) > cfg["max_files_per_claim"]:
            errors.append(f"{label}: {len(c['files'])} files exceeds max_files_per_claim={cfg['max_files_per_claim']}")
        for f in c.get("files") or []:
            err = tc.path_syntax_error(f)
            if err:
                errors.append(f"{label}: {err}")
            # v2.5: the board cannot be claimed.
            if f == "team" or str(f).startswith("team/"):
                errors.append(f"{label}: claims may not cover team/ paths ({f!r}) — "
                              "the board cannot be claimed; claim the work files, not the roster/ledger itself")
        if c.get("reserves_feature") and not tc.valid_id(c["reserves_feature"]):
            errors.append(f"{label}: reserves_feature id fails the id grammar")
        if c.get("status") not in ("active", "done", "released", "stalled", "abandoned"):
            errors.append(f"{label}: unknown status {c.get('status')!r}")
    owner_counts = {}
    for tid, c in claims.items():
        if tc.is_blocking(c, cfg=cfg, roster_ids=all_ids):
            owner_counts[c.get("owner_id")] = owner_counts.get(c.get("owner_id"), 0) + 1
    for owner, n in sorted(owner_counts.items()):
        if n > cfg["max_claims_per_agent"]:
            errors.append(f"{owner}: {n} active claims exceeds max_claims_per_agent={cfg['max_claims_per_agent']}")

    # ---- double claims: two blocking claims on the same file --------
    # "First" is decided by (created_seq, owner_id), never by wall-clock
    # (and no longer by lamport — heartbeating bumps lamport).
    # Ghost claims (v2.5) are not blocking, so they never conflict.
    by_file = {}
    for tid, c in claims.items():
        if not tc.is_blocking(c, cfg=cfg, roster_ids=all_ids):
            continue
        for f in c.get("files") or []:
            by_file.setdefault(tc.canonical_path(f), []).append((tid, c, f))
    for low, holders in sorted(by_file.items()):
        if len(holders) < 2:
            continue
        ordered = sorted(holders, key=lambda h: tc.claim_order_key(h[1]))
        # Worktree exemption (v2.3): holders on different worktrees do
        # not conflict — only pairs sharing a checkout do. Paths are
        # compared canonically (v2.3.1).
        pairs = [(a, b) for i, a in enumerate(ordered)
                 for b in ordered[i + 1:]
                 if tc.claims_share_checkout(a[1], b[1], main_root)]
        if not pairs:
            continue
        first = min((a for a, _b in pairs),
                    key=lambda h: tc.claim_order_key(h[1]))
        losers = sorted({b[0] for _a, b in pairs})
        errors.append(f"CONFLICT: file {first[2]} claimed by both {first[0]} and {', '.join(losers)} — "
                      f"first by (created_seq, owner) is {first[0]} (created_seq {first[1].get('created_seq') or first[1].get('lamport')}), "
                      f"so {', '.join(losers)} must yield")
    blocking_claims = [(t, c) for t, c in claims.items()
                       if tc.is_blocking(c, cfg=cfg, roster_ids=all_ids)]
    for i in range(len(blocking_claims)):
        for j in range(i + 1, len(blocking_claims)):
            t1, c1 = blocking_claims[i]
            t2, c2 = blocking_claims[j]
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
                                      f"of the other; first by (created_seq, owner) is {first[1]}, "
                                      f"so {second[1]} must yield")
    # ---- worktree-per-claim consistency (v2.3) ----------------------
    # Only blocking claims are checked: a released/done claim's
    # worktree fields are history, not a live promise. (A claim that
    # is done/released and whose worktree is gone is the NORMAL
    # lifecycle — merged, then removed — so non-blocking claims are
    # not warned here at all.)
    seen_wt_path, seen_wt_branch = {}, {}
    root = tc.git_root()
    main_root = (tc.main_checkout_path(root) or root) if root else None
    for tid, c in blocking_claims:
        wt = c.get("worktree") or ""
        br = c.get("branch") or ""
        if not wt:
            continue
        label = "claim %s" % tid
        if cfg.get("worktree_mode") != "per-claim":
            errors.append(f"{label}: worktree claim but team/config.json worktree_mode is not 'per-claim'")
        if not br:
            errors.append(f"{label}: worktree claim without a branch")
        elif not tc.valid_branch_name(br):
            errors.append(f"{label}: branch {br!r} is not a legal git branch name")
        if not c.get("base_commit"):
            errors.append(f"{label}: worktree claim without a base_commit — re-claim so the base is recorded")
        # Canonical key (v2.3.1): `../wt-x` and its absolute spelling
        # are the same worktree.
        key = tc.canonical_worktree_path(wt, main_root)
        if key in seen_wt_path:
            errors.append(f"CONFLICT: worktree path {wt} is claimed by both {seen_wt_path[key]} and {tid} — one worktree per claim")
        else:
            seen_wt_path[key] = tid
        if br:
            if br in seen_wt_branch:
                errors.append(f"CONFLICT: branch {br} is claimed by both {seen_wt_branch[br]} and {tid} — one branch per worktree claim")
            else:
                seen_wt_branch[br] = tid
        if root and c.get("base_commit"):
            r = tc.run_git(["cat-file", "-e", c["base_commit"] + "^{commit}"], cwd=root)
            if r.returncode != 0:
                errors.append(f"{label}: worktree base_commit {c['base_commit']} is unknown to this repo")
        if root:
            # Presence (v2.3.1, E2): a blocking claim whose worktree is
            # gone but whose branch still exists HERE was deleted on
            # this machine — FAIL. A worktree absent entirely may live
            # on a teammate's laptop — warn only. A directory that
            # exists but is not a registered worktree is also a
            # warning: something removed the git metadata, or the
            # directory was never a worktree.
            presence = tc.worktree_presence(main_root, wt, br)
            if presence == "deleted-here":
                errors.append(f"{label}: worktree {wt} is gone but branch {br} still exists here — "
                              "the worktree was removed; release the claim or recreate the worktree")
            elif presence == "absent":
                abs_wt = tc.canonical_worktree_path(wt, main_root)
                if not os.path.isdir(abs_wt):
                    warnings.append(f"{label}: worktree {wt} is not present on this machine — the claim "
                                    "may live on a teammate's laptop; if it was deleted, release the claim")
                else:
                    warnings.append(f"{label}: worktree {wt} is not a registered worktree on this machine — "
                                    "the directory exists but git does not list it; if it was removed, release the claim")
            elif presence == "present":
                entry = tc.worktree_entry_for_path(main_root, wt)
                if entry is not None and (entry.get("branch") or "") != br:
                    errors.append(f"{label}: worktree at {wt} is on branch {entry.get('branch')}, but the claim says {br}")
    for fid, f in sorted(features.items()):
        label = "feature %s" % fid
        if f.get("owner_id") not in active_ids:
            errors.append(f"{label}: owner {f.get('owner_id')} is not an active roster member")
        # contributors are roster ids; used_by are TASK ids
        # (features.py use --by <task-id>) — only check they are id-shaped.
        for who in f.get("contributors") or []:
            if who not in all_ids:
                errors.append(f"{label}: {who} (contributors) is not a roster id")
        for who in f.get("used_by") or []:
            if not tc.valid_id(who):
                errors.append(f"{label}: {who} (used_by) is not a valid task id")
        version = f.get("version")
        if version and tc.parse_semver(version) is None:
            errors.append(f"{label}: version is not valid SemVer (got {version!r})")
        changes = f.get("changes") or []
        if changes and changes[-1].get("version") != version:
            errors.append(f"{label}: version {version} does not match the latest changes entry ({changes[-1].get('version')})")
        if str(version or "").startswith("0.") and f.get("status") == "stable":
            errors.append(f"{label}: 0.y.z is always experimental, but status is stable")
        if version and tc.is_prerelease(version) and f.get("status") == "stable":
            errors.append(f"{label}: pre-release version {version} can never be stable")
        for field in ("status_reason", "status_changed_by", "status_changed_at"):
            if field not in f or f[field] in (None, ""):
                errors.append(f"{label}: missing/empty {field} — every feature must record "
                              "who changed its status, when, and why")
        if f.get("status_changed_at") and tc.parse_time(f["status_changed_at"]) is None:
            errors.append(f"{label}: status_changed_at must be a UTC ISO-8601 timestamp ending in Z")

    # ---- PRD layer (v2.2): the item must be real, and its claims
    # must be workable ---------------------------------------------
    for pid, item in sorted(prd.items()):
        label = "PRD item %s" % pid
        if not tc.valid_id(pid):
            errors.append(f"{label}: id fails the id grammar")
        if item.get("owner_id") not in active_ids:
            errors.append(f"{label}: owner {item.get('owner_id')} is not an active roster member")
        if item.get("status") not in ("planned", "in_progress", "done", "cancelled"):
            errors.append(f"{label}: unknown status {item.get('status')!r}")
        for fid in item.get("features") or []:
            if not tc.valid_id(fid):
                errors.append(f"{label}: feature id {fid!r} fails the id grammar")
                continue
            if item.get("status") == "done":
                if fid not in features:
                    errors.append(f"{label}: is done but feature {fid} is not registered — "
                                  "a done item's features must exist")
                elif features[fid].get("status") not in ("stable", "cancelled"):
                    errors.append(f"{label}: is done but feature {fid} is {features[fid].get('status')} — "
                                  "a done item's features must be stable or cancelled")
        if item.get("status") == "cancelled":
            for fid in item.get("features") or []:
                if fid in features and features[fid].get("status") not in ("cancelled", "deprecated"):
                    warnings.append(f"{label}: is cancelled but feature {fid} is still "
                                    f"{features[fid].get('status')} — cancel it, or unlink it from this item")
    for tid, c in sorted(claims.items()):
        prd_item = c.get("prd_item") or ""
        if not prd_item:
            continue
        if prd_item not in prd:
            errors.append(f"claim {tid}: prd_item {prd_item} does not exist — create it with prd.py, or re-claim without --prd")
        elif tc.is_blocking(c, cfg=cfg, roster_ids=all_ids) and prd[prd_item].get("status") in ("cancelled", "done"):
            errors.append(f"claim {tid}: works on PRD item {prd_item}, which is {prd[prd_item].get('status')} — "
                          "release this claim; there is nothing to work on")
    if cfg.get("prd_required"):
        for tid, c in sorted(claims.items()):
            if tc.is_blocking(c, cfg=cfg, roster_ids=all_ids) and not (c.get("prd_item") or ""):
                errors.append(f"claim {tid}: team/config.json has prd_required=true but this claim names no PRD item")

    # ---- Orca features (v2.6): fan-out, decision gates, receipts,
    # reviews. Each per-script `check` is the single source of the
    # rules — validate.py calls the shared check functions (with the
    # roster passed, so actor checks run here too) and adds the two
    # cross-file rules the per-script checks cannot see: a blocking
    # claim bound to an open gate, and a done PRD item blocked by a
    # latest request-changes review.
    import fanout as _fo
    import gates as _ga
    import receipts as _rc
    import reviews as _rv
    _roster_dir = os.path.join(team_dir, "roster")
    _ferr, _fwarn = _fo.check_fanout(os.path.join(team_dir, "fanout"), _roster_dir, claims=claims)
    errors.extend("fanout group " + e for e in _ferr)
    warnings.extend("fanout group " + w for w in _fwarn)
    _gerr, _gwarn = _ga.check_gates(os.path.join(team_dir, "gates"), _roster_dir)
    errors.extend("gate " + e for e in _gerr)
    warnings.extend("gate " + w for w in _gwarn)
    _rerr, _rwarn = _rc.check_receipts(os.path.join(team_dir, "receipts"), _roster_dir,
                                       claims=claims, cfg=cfg)
    errors.extend("receipt " + e for e in _rerr)
    warnings.extend("receipt " + w for w in _rwarn)
    _verr, _vwarn = _rv.check_reviews(os.path.join(team_dir, "reviews"), _roster_dir)
    errors.extend("review " + e for e in _verr)
    warnings.extend("review " + w for w in _vwarn)
    for tid, c in sorted(claims.items()):
        if not tc.is_blocking(c, cfg=cfg, roster_ids=all_ids):
            continue
        gate = tc.open_gate_for_claim(c, gates, features)
        if gate:
            errors.append(f"claim {tid}: decision gate {gate.get('id')} is OPEN on this work "
                          f"({gate.get('blocks_kind')} {gate.get('blocks_id')}): \"{gate.get('question')}\" — "
                          "resolve or waive the gate before pushing")
    for pid, item in sorted(prd.items()):
        if item.get("status") != "done":
            continue
        blocked = tc.prd_blocked_by_review(reviews, pid,
                                           (item.get("features") or []), claims)
        if blocked:
            review, label = blocked
            errors.append(f"PRD item {pid}: is done but the latest review {review.get('id')} on "
                          f"{label} is request-changes (reviewer {review.get('reviewer')}): "
                          f"\"{review.get('note')}\" — a done item cannot carry an open request-changes")

    # ---- dependencies (v2.2): a recorded version may not be newer
    # than what exists, and a MAJOR bump upstream means re-verify ---
    # Dependency cycles (v2.5): a loop can never be topologically
    # sorted, so it FAILS here (and at write time in features.py).
    cycle = tc.find_dependency_cycle(features)
    if cycle:
        errors.append("dependency cycle: %s — break the loop (remove one depends_on edge); "
                      "a cycle can never be topologically sorted" % " -> ".join(cycle))
    for fid, f in sorted(features.items()):
        label = "feature %s" % fid
        for dep in f.get("depends_on") or []:
            if not isinstance(dep, dict):
                continue
            did, ver = dep.get("id") or "", dep.get("version") or ""
            if did not in features:
                errors.append(f"{label}: depends on {did}, which is not registered — register the dependency first")
                continue
            cur = features[did].get("version")
            if tc.parse_semver(ver) is not None and tc.parse_semver(cur) is not None:
                if tc.parse_semver(ver) > tc.parse_semver(cur):
                    errors.append(f"{label}: depends on {did} v{ver} but {did} is only v{cur}")
                elif tc.parse_semver(cur)[0] > tc.parse_semver(ver)[0]:
                    msg = (f"{label}: needs reverify — dependency {did} is now v{cur} (was v{ver}, MAJOR bump); "
                           f"run features.py reverify --id {fid} --by <you> after reading the new code")
                    if cfg.get("strict_deps"):
                        errors.append(msg)
                    else:
                        warnings.append(msg)

    # ---- duplicate display names (v2.5): ids are unique, but names
    # are what humans read — two members named "Rian" FAIL, whatever
    # their ids say. check_roster.py enforces the same rule; this is
    # defense in depth for runs that only call validate.py.
    import check_roster as _cr
    seen_names = {}
    for rid, m in sorted(roster.items()):
        name = m.get("name", "")
        if not isinstance(name, str) or not name.strip():
            continue
        norm = _cr.normalize_display_name(name)
        if norm in seen_names:
            errors.append(f"roster: duplicate display name {name!r} — {seen_names[norm]} and {rid} share it; "
                          "display names must be unique; ids are unique, but names are what humans read")
        else:
            seen_names[norm] = rid

    # ---- branch freshness (current branch) -------------------------
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

    # ---- push-range rules (ownership, no self-registration, renames)
    if root:
        ref = "%s/%s" % (cfg["canonical_remote"], cfg["canonical_branch"])
        if args.range:
            range_spec = args.range
        elif tc.run_git(["rev-parse", "--verify", "--quiet", ref]).returncode == 0:
            range_spec = "%s..HEAD" % ref
        else:
            # No remote branch yet: there is no "pushed" baseline, so
            # the push range is the tip commit only. (The contract
            # says "fallback to HEAD"; using the whole history would
            # flag every repo whose roster was ever added alongside
            # work, including a roster landed 100 commits ago — the
            # push range must mean the unpushed work, and the tip
            # commit is the only approximation available without a
            # remote. Use --range to check a wider range explicitly.)
            if tc.run_git(["rev-parse", "--verify", "--quiet", "HEAD~1"]).returncode == 0:
                range_spec = "HEAD~1..HEAD"
            else:
                range_spec = "HEAD"
        # Bootstrap: the remote branch has no team/roster/ yet, so this
        # push is the FIRST team setup — one human seeds the roster and
        # the first claims/features in one commit. The one-owner and
        # no-self-registration rules do not apply to that first commit;
        # they apply from the second push on.
        ls = tc.run_git(["ls-tree", ref, "team/roster/"])
        bootstrap = not (ls.returncode == 0 and ls.stdout.strip())
        if bootstrap:
            warnings.append("bootstrap push — team/roster/ does not exist on the remote yet; "
                            "one-owner and no-self-registration rules apply from the next push")
        changed = git_log_names(range_spec, [])
        if changed is None:
            warnings.append("push-range checks skipped: git log failed for range %s" % range_spec)
            changed = []
        team_changed = [p for p in changed
                        if any(p.startswith(pref + "/") for pref in TEAM_PREFIXES)]
        # Deletion detection (v2.5): a team/ record deleted in the
        # push range FAILS — the record IS the audit trail, and the
        # correct lifecycle is release/cancel/retire, never delete.
        # (The ownership block below silently skips deleted files —
        # record_at_head returns None for them — so this check has to
        # live here, outside `if team_changed:`.) Exception: a file
        # added AND deleted inside the same range never existed on
        # the shared history, so nothing was erased — that warns.
        deletions = git_log_deletions(range_spec)
        if deletions is None:
            warnings.append("deletion check skipped: git log failed for range %s" % range_spec)
        else:
            if ".." in range_spec:
                del_base = range_spec.split("..")[0]
            elif tc.run_git(["rev-parse", "--verify", "--quiet", "HEAD~1"]).returncode == 0:
                del_base = "HEAD~1"
            else:
                del_base = None
            del_protected = ("team/roster/", "team/claims/", "team/features/",
                             "team/prd/", "team/handoffs/", "team/fanout/",
                             "team/gates/", "team/receipts/", "team/reviews/")
            for path in deletions:
                if path in ("team/events.jsonl", "team/config.json"):
                    protected_hit = True
                elif any(path.startswith(p) for p in del_protected):
                    protected_hit = True
                else:
                    protected_hit = False
                if not protected_hit:
                    continue
                if del_base is None:
                    warnings.append(f"deletion: {path} was deleted in this push, but there is no range base "
                                    "to check it against — verify by hand that the record was not shared history")
                    continue
                r = tc.run_git(["show", "%s:%s" % (del_base, path)])
                if r.returncode == 0:
                    errors.append(f"deletion: {path} existed on {del_base} and was deleted in this push — "
                                  "team/ records are never deleted; release the claim, cancel the feature, "
                                  "or retire the member instead (deletion erases the audit trail)")
                else:
                    warnings.append(f"deletion: {path} was added and deleted inside this push — it never "
                                    "existed on the shared history, so nothing was erased")
        if team_changed:
            added = git_log_names(range_spec, ["--diff-filter=A"]) or []
            new_roster_ids = {os.path.basename(p)[:-5] for p in added
                              if p.startswith("team/roster/") and p.endswith(".json")}
            actors = set()
            roster_touched = False
            self_registration = []
            for path in team_changed:
                rec = record_at_head(path)
                if rec is None:
                    # Deleted file, or unreadable at HEAD — load from disk as fallback.
                    try:
                        import json
                        with open(os.path.join(root, path), encoding="utf-8") as fh:
                            rec = json.load(fh)
                    except (OSError, ValueError):
                        rec = None
                if not isinstance(rec, dict):
                    continue
                if path.startswith("team/roster/"):
                    roster_touched = True
                    continue
                status = rec.get("status")
                released_by = rec.get("released_by") or ""
                stalled_by = rec.get("stalled_by") or ""
                done_by = rec.get("done_by") or ""
                if status in ("released", "stalled") and (released_by or stalled_by):
                    # Release/stalled events are exempt from the one-owner rule.
                    continue
                if status == "done" and done_by:
                    # v2.6: a done claim is attributed to its filer
                    # (done_by), not its owner — a human filing a
                    # receipt on an agent's behalf must be able to
                    # push the claim + receipt together (the receipt's
                    # actor is done_by too, so the commit has one
                    # actor). Same shape as released_by/stalled_by.
                    continue
                # v2.6: record_actor() maps each record type to the
                # person who attests to it — the old `owner_id or id`
                # would use a fan-out group id / gate id / review id
                # as the actor.
                owner = record_actor(path, rec)
                actors.add(owner)
                if owner and owner in new_roster_ids:
                    self_registration.append((path, owner))
                if args.actor and owner and owner != args.actor:
                    errors.append(f"ownership: {path} is owned by {owner}, not by --actor {args.actor}")
            if len(actors) > 1 and not bootstrap:
                errors.append(f"ownership: changed team/ records in this push belong to several owners "
                              f"({', '.join(sorted(actors))}) — one push, one owner, unless the change "
                              "is a release/stalled event (released_by/stalled_by recorded)")
            if roster_touched:
                warnings.append("roster change in this push — needs HUMAN approval before merge "
                                "(roster changes are warn-only here; branch protection / CODEOWNERS is the gate)")
            for path, owner in self_registration:
                if bootstrap:
                    continue
                errors.append(f"no self-registration: new roster member {owner} appears in the same push as "
                              f"{path} owned by {owner} — a roster entry cannot authorise its own work; "
                              "land the roster change (human-approved) first, then the work")
        # Rename rule — deliberately OUTSIDE `if team_changed:`: the
        # violation is exactly a rename with NO team/ file changed.
        renames = git_log_renames(range_spec)
        if renames is None:
            warnings.append("rename check skipped: git history unavailable")
        else:
            changed_features = {p for p in team_changed if p.startswith("team/features/")}
            entry_files = {}
            for fid, f in features.items():
                kind = f.get("kind") or "code"
                if kind == "code" and f.get("entry"):
                    entry_files[f["entry"].rsplit(":", 1)[0]] = fid
                elif f.get("entry"):
                    entry_files[f["entry"]] = fid
            for old, _new in renames:
                fid = entry_files.get(old)
                if fid and ("team/features/%s.json" % fid) not in changed_features:
                    errors.append(f"rename: {old} is the entry point of feature {fid}, but "
                                  f"team/features/{fid}.json was not updated in the same push — "
                                  "a rename must update the registry in the same push")
        # Signed identity (v2.4, only when require_signatures is on):
        # every commit in the push range that touches team/ must be
        # signed by the SSH key recorded on the roster for the
        # record's owner. Local script runs stay unbound — the push
        # is the enforcement point, because the pushed record is the
        # only thing other agents ever see.
        if cfg.get("require_signatures"):
            if bootstrap:
                warnings.append("signature checks skipped on the bootstrap push — "
                                "team/roster/ does not exist on the remote yet; the first keys enter through this push")
            else:
                commits = git_log_commits(range_spec)
                if commits is None:
                    warnings.append("signature checks skipped: git log failed for range %s" % range_spec)
                else:
                    for sha in commits:
                        changed_here = [p for p in git_show_names(sha)
                                        if any(p.startswith(pref + "/") for pref in TEAM_PREFIXES)]
                        if not changed_here:
                            continue
                        actors = set()
                        unreadable = False
                        for path in changed_here:
                            rec = record_at_commit(sha, path)
                            if not isinstance(rec, dict):
                                unreadable = True
                                continue
                            status = rec.get("status")
                            rel_by = rec.get("released_by") or ""
                            st_by = rec.get("stalled_by") or ""
                            done_by = rec.get("done_by") or ""
                            if status in ("released", "stalled") and (rel_by or st_by):
                                actor = rel_by or st_by
                                # Composite actors (flags:a,b / cancel:x /
                                # fanout:<winner> / fanout-cancel:<by>)
                                # cannot be attributed to one key — the
                                # release is still recorded and visible,
                                # just not signed. (The decide/cancel
                                # commit is still bound to the decider
                                # through the fan-out group record's
                                # decided_by/cancelled_by in the same
                                # commit.)
                                if actor.startswith(("flags:", "cancel:", "fanout:")) or "," in actor:
                                    continue
                                actors.add(actor)
                                continue
                            if status == "done" and done_by:
                                # v2.6: a done claim is signed by its
                                # filer (done_by), not its owner — the
                                # receipt's actor is done_by too, so a
                                # human filing on an agent's behalf
                                # signs one commit with one key.
                                actors.add(done_by)
                                continue
                            # v2.6: record_actor() maps each record
                            # type to its attesting person (see above
                            # — `owner_id or id` is wrong for fan-out
                            # / gates / receipts / reviews).
                            actor = record_actor(path, rec)
                            if actor:
                                actors.add(actor)
                        if len(actors) > 1:
                            # Already failed by the one-owner rule;
                            # nothing more to attribute here.
                            continue
                        if not actors:
                            if unreadable:
                                warnings.append(f"signature: commit {sha[:8]} touches team/ records that "
                                                "cannot be read as of that commit — signature not checked "
                                                "(deletion detection is a separate item)")
                            continue
                        actor = next(iter(actors))
                        member = roster.get(actor)
                        if member is None:
                            errors.append(f"signature: commit {sha[:8]} touches team/ records owned by "
                                          f"{actor}, who is not in the roster")
                            continue
                        key = (member or {}).get("public_key") or ""
                        if not key:
                            errors.append(f"signature: commit {sha[:8]} touches team/ records owned by "
                                          f"{actor}, who has no public_key on the roster — add a key "
                                          "(human-approved roster change) or turn require_signatures off")
                            continue
                        ok, err = tc.verify_commit_signature(root, sha, key, principal=actor)
                        if not ok:
                            errors.append(f"signature: commit {sha[:8]} is not signed by {actor}'s key — "
                                          f"{err or 'unsigned or signed by a different key'}")
        # Last-known-main-SHA (v2.5): the recorded main SHA must still
        # be an ancestor of origin/main — otherwise the remote history
        # was rewritten (force-push). No recorded SHA yet warns only.
        _hstatus, _hmsg = tc.check_main_history(
            root, cfg["canonical_remote"], cfg["canonical_branch"],
            cfg.get("last_known_main_sha") or "")
        if _hstatus == "fail":
            errors.append("history: " + _hmsg)
        elif _hstatus in ("unrecorded", "unknown"):
            warnings.append("history: " + _hmsg)
        # Trailer enforcement (v2.5): the [claim #N] fence is now
        # read, not just documented. A work commit touching a file
        # covered by a blocking claim must carry a trailer matching
        # that claim's claim_seq — and, when require_signatures is
        # on, the commit must also verify against the claim owner's
        # key (a trailer alone is a claim on a number; only a
        # signature binds the number to a person). Merge commits are
        # skipped (their content is the union of commits checked
        # individually); commits touching only team/ are record
        # commits, not work; the bootstrap push skips with a warning.
        if bootstrap:
            warnings.append("trailer checks skipped on the bootstrap push — the first team setup "
                            "lands claim files and work files together; the fence applies from the next push")
        else:
            _tcommits = git_log_commits(range_spec)
            if _tcommits is None:
                warnings.append("trailer checks skipped: git log failed for range %s" % range_spec)
            else:
                for _sha in _tcommits:
                    _parents = git_commit_parents(_sha)
                    if _parents is not None and len(_parents) > 1:
                        continue
                    _msg = git_commit_message(_sha)
                    _trailers = {int(n) for n in re.findall(r"\[claim #(\d+)\]", _msg or "")}
                    _work = [p for p in git_show_names(_sha)
                             if not any(p.startswith(pref + "/") for pref in TEAM_PREFIXES)]
                    if not _work:
                        continue
                    for _wp in _work:
                        _covering = [(t, c) for t, c in blocking_claims
                                     if any(tc.paths_overlap(_wp, f) for f in (c.get("files") or []))]
                        if not _covering:
                            continue
                        _ok = False
                        for _t, _c in _covering:
                            if _c.get("claim_seq") not in _trailers:
                                continue
                            if not cfg.get("require_signatures"):
                                _ok = True
                                break
                            _owner = _c.get("owner_id") or ""
                            _key = (roster.get(_owner) or {}).get("public_key") or ""
                            if _key:
                                _sok, _serr = tc.verify_commit_signature(
                                    root, _sha, _key, principal=_owner)
                                if _sok:
                                    _ok = True
                                    break
                        if not _ok:
                            _seqs = sorted({_c.get("claim_seq") for _t, _c in _covering
                                            if _c.get("claim_seq")})
                            _owners = ", ".join(sorted(t for t, _c in _covering))
                            _extra = (" and signed by the claim owner's key"
                                      if cfg.get("require_signatures") else "")
                            errors.append(f"trailer: commit {_sha[:8]} touches {_wp}, claimed by {_owners} "
                                          f"(claim #{', #'.join(str(s) for s in _seqs)}) — the commit carries no "
                                          f"matching [claim #N] trailer{_extra}; add the trailer (claim.py prints it) "
                                          "and re-commit")
    else:
        warnings.append("push-range checks skipped: not in a git repo")

    # ---- event validation (v2.5): team/events.jsonl is the audit
    # log, and until now no script ever read it. Every line must be
    # well-formed JSON with a parseable timestamp. NEW lines (beyond
    # the push-range base — i.e. not yet on the shared history) must
    # also name a roster actor and reference records that exist.
    # OLD lines (already on the shared history) get shape checks
    # only: they were validated when pushed, and the world has moved
    # on since — a member named in an old line may have left, a
    # claim named in one may have been released long ago. Checking
    # old lines against the CURRENT roster would make every ghost
    # fail validation, defeating the ghost gate (warn, never fail).
    # Same reason member_id is NOT validated at all.
    # Allowed actors: roster ids, composite actors (flags:a,b /
    # cancel:x, parts must be roster ids), and the system actor
    # "v1-migration" (written once by migrate_v1_to_v2.py).
    # What this does NOT do is semantic cross-checks (a
    # forged-but-well-formed event that references real records
    # still passes shape checks) — the log's truth rests on signed
    # commits (v2.4) + branch protection. Say so plainly; a shape
    # check is not a truth check. No range base (first push / no
    # git) → every line is new.
    events_path = os.path.join(team_dir, "events.jsonl")
    events_base_count = 0
    if os.path.isfile(events_path):
        try:
            with open(events_path, encoding="utf-8") as fh:
                elines = fh.read().splitlines()
        except OSError as e:
            warnings.append(f"events: cannot read {events_path} ({e})")
            elines = []
        # How many leading lines are already on the shared history?
        if root:
            _ref = "%s/%s" % (cfg["canonical_remote"], cfg["canonical_branch"])
            if args.range:
                _ebase = args.range.split("..")[0] if ".." in args.range else None
            elif tc.run_git(["rev-parse", "--verify", "--quiet", _ref]).returncode == 0:
                _ebase = _ref
            elif tc.run_git(["rev-parse", "--verify", "--quiet", "HEAD~1"]).returncode == 0:
                _ebase = "HEAD~1"
            else:
                _ebase = None
            if _ebase:
                _r = tc.run_git(["show", "%s:team/events.jsonl" % _ebase])
                if _r.returncode == 0:
                    events_base_count = len(_r.stdout.splitlines())
        for lineno, line in enumerate(elines, 1):
            if not line.strip():
                continue
            try:
                import json
                ev = json.loads(line)
            except ValueError:
                errors.append(f"events: line {lineno} is not valid JSON — the audit log is corrupt; "
                              "fix the line, do not delete it")
                continue
            if not isinstance(ev, dict):
                errors.append(f"events: line {lineno} is not a JSON object")
                continue
            elabel = f"events: line {lineno}"
            if not isinstance(ev.get("event"), str) or not ev["event"].strip():
                errors.append(f"{elabel}: missing/empty 'event'")
            ts_raw = ev.get("at") or ev.get("ts") or ""
            ts = tc.parse_time(ts_raw) if isinstance(ts_raw, str) else None
            if not ts_raw or ts is None:
                errors.append(f"{elabel}: missing/unparseable timestamp ('at' or legacy 'ts', "
                              f"UTC ISO-8601 ending in Z, got {ts_raw!r})")
            is_new = lineno > events_base_count
            if is_new and ts is not None and tc.is_impossibly_future(ts):
                errors.append(f"{elabel}: timestamp {ts_raw} is more than {tc.CLOCK_SKEW_FAIL_MINUTES} minutes "
                              "in the future — a skewed clock wrote this event")
            if not isinstance(ev.get("lamport"), int) or ev.get("lamport", 0) < 1:
                errors.append(f"{elabel}: lamport must be an int >= 1")
            if not is_new:
                # Old line, already on the shared history: shape only
                # (see the header comment — its actor/records may
                # legitimately be gone by now).
                continue
            actor = ev.get("actor") or ""
            if not isinstance(actor, str) or not actor.strip():
                errors.append(f"{elabel}: missing/empty 'actor'")
            elif actor == "v1-migration":
                pass
            elif actor.startswith(("flags:", "cancel:")):
                for part in actor.split(":", 1)[1].split(","):
                    if part.strip() not in all_ids:
                        errors.append(f"{elabel}: composite actor {actor!r} names {part.strip()!r}, "
                                      "who is not in the roster")
            elif actor not in all_ids:
                errors.append(f"{elabel}: actor {actor!r} is not a roster member")
            etid = ev.get("task_id") or ""
            if etid and etid not in claims:
                errors.append(f"{elabel}: task_id {etid!r} does not exist — the referenced claim is gone")
            efid = ev.get("feature_id") or ""
            if efid and efid not in features:
                errors.append(f"{elabel}: feature_id {efid!r} does not exist — the referenced feature is gone")
            # v2.6: the new record types are referenced the same way.
            egid = ev.get("fanout_id") or ""
            if egid and egid not in fanout:
                errors.append(f"{elabel}: fanout_id {egid!r} does not exist — the referenced fan-out group is gone")
            egate = ev.get("gate_id") or ""
            if egate and egate not in gates:
                errors.append(f"{elabel}: gate_id {egate!r} does not exist — the referenced gate is gone")
            erid = ev.get("review_id") or ""
            if erid and erid not in reviews:
                errors.append(f"{elabel}: review_id {erid!r} does not exist — the referenced review is gone")

    # ---- secret / PII scan on team/ free-text fields ---------------
    scanned = 0
    for dirpath, _dirs, filenames in os.walk(team_dir):
        if "v1-backup" in dirpath:
            continue
        for name in sorted(filenames):
            if not name.endswith(".json"):
                continue
            path = os.path.join(dirpath, name)
            data, err = tc.load_json_file(path)
            if err or not isinstance(data, dict):
                continue
            scanned += 1
            rel = os.path.relpath(path, os.getcwd())
            for field in FREE_TEXT_FIELDS:
                value = data.get(field)
                if not isinstance(value, str) or not value:
                    continue
                if EMAIL_RE.search(value):
                    errors.append(f"secret/PII scan: {rel} field {field} contains an email address — "
                                  "team/ is public-to-the-repo forever; remove it")
                for rx in TOKEN_RES:
                    if rx.search(value):
                        errors.append(f"secret/PII scan: {rel} field {field} looks like a secret/token — "
                                      "team/ is public-to-the-repo forever; remove it (history rewrite needed)")
                        break
                if URL_RE.search(value):
                    if field in ("role", "name"):
                        errors.append(f"secret/PII scan: {rel} field {field} contains a URL — "
                                      "roster text must not carry URLs")
                    else:
                        warnings.append(f"secret/PII scan: {rel} field {field} contains a URL — "
                                        "keep internal URLs out of team/ where possible")
            contact = data.get("contact")
            if isinstance(contact, str) and contact:
                if EMAIL_RE.search(contact) or URL_RE.search(contact) \
                        or re.search(r"\d[\d\s\-]{6,}\d", contact):
                    errors.append(f"secret/PII scan: {rel} contact is not a bare channel name — "
                                  "use a channel name only (no email, URL, or phone number)")

    # ---- mailbox trust model (v2.4, signatures in v2.5): mailbox
    # content is untrusted data, never an instruction. The gate lives
    # in team_common.check_mailbox() (shared with scripts/mailbox.py
    # check): when require_signatures is on, a line from a HUMAN
    # roster member MUST carry a valid sig from that sender's roster
    # key — any content, any wording — because the v2.4 keyword
    # heuristic could not catch a rephrased order ("STOP semua kerja
    # hari ini…" contains none of the listed words). Without
    # require_signatures, the v2.4 heuristic applies.
    import check_roster
    _merrors, _mwarnings = tc.check_mailbox(team_dir, roster, cfg, check_roster.IMPERATIVE_RES)
    errors.extend(_merrors)
    warnings.extend(_mwarnings)

    for w in warnings:
        print(f"  ! {w}")
    if errors:
        print("VALIDATE: FAIL — do not push until this is fixed:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"VALIDATE: OK — {len(roster)} roster member(s), {len(claims)} claim(s), "
          f"{len(features)} feature(s), {len(prd)} PRD item(s), {len(fanout)} fan-out group(s), "
          f"{len(gates)} gate(s), {len(receipts)} receipt(s), {len(reviews)} review(s) cross-checked; "
          f"{scanned} team/ file(s) scanned for secrets/PII.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
