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
- ownership of team/ records, in the push range
  (git log <canonical_remote>/<canonical_branch>..HEAD, falling back
  to HEAD if no remote): all changed records must share one
  owner_id, UNLESS the change is a release/stalled event
  (released_by/stalled_by recorded) or a roster change (warn only —
  roster changes need human approval). Roster entries added in the
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
                    "status_reason")
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
TEAM_PREFIXES = ("team/roster", "team/claims", "team/features", "team/prd")


def load_all(team_dir):
    roster, claims, features, prd = {}, {}, {}, {}
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
    return roster, claims, features, prd


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
    roster, claims, features, prd = load_all(team_dir)
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
            errors.append(f"{label}: owner {c.get('owner_id')} is not an active roster member")
        for tf in ("started_at", "expires_at", "heartbeat_at"):
            if c.get(tf) and tc.parse_time(c[tf]) is None:
                errors.append(f"{label}: {tf} must be a UTC ISO-8601 timestamp ending in Z (got {c[tf]!r})")
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
        if c.get("reserves_feature") and not tc.valid_id(c["reserves_feature"]):
            errors.append(f"{label}: reserves_feature id fails the id grammar")
        if c.get("status") not in ("active", "done", "released", "stalled", "abandoned"):
            errors.append(f"{label}: unknown status {c.get('status')!r}")
    owner_counts = {}
    for tid, c in claims.items():
        if tc.is_blocking(c, cfg=cfg):
            owner_counts[c.get("owner_id")] = owner_counts.get(c.get("owner_id"), 0) + 1
    for owner, n in sorted(owner_counts.items()):
        if n > cfg["max_claims_per_agent"]:
            errors.append(f"{owner}: {n} active claims exceeds max_claims_per_agent={cfg['max_claims_per_agent']}")

    # ---- double claims: two blocking claims on the same file --------
    # "First" is decided by (lamport, owner_id), never by wall-clock.
    by_file = {}
    for tid, c in claims.items():
        if not tc.is_blocking(c, cfg=cfg):
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
                      f"first by (lamport, owner) is {first[0]} (lamport {first[1].get('lamport')}), "
                      f"so {', '.join(losers)} must yield")
    blocking_claims = [(t, c) for t, c in claims.items() if tc.is_blocking(c, cfg=cfg)]
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
                                      f"of the other; first by (lamport, owner) is {first[1]}, "
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
        elif tc.is_blocking(c, cfg=cfg) and prd[prd_item].get("status") in ("cancelled", "done"):
            errors.append(f"claim {tid}: works on PRD item {prd_item}, which is {prd[prd_item].get('status')} — "
                          "release this claim; there is nothing to work on")
    if cfg.get("prd_required"):
        for tid, c in sorted(claims.items()):
            if tc.is_blocking(c, cfg=cfg) and not (c.get("prd_item") or ""):
                errors.append(f"claim {tid}: team/config.json has prd_required=true but this claim names no PRD item")

    # ---- dependencies (v2.2): a recorded version may not be newer
    # than what exists, and a MAJOR bump upstream means re-verify ---
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
                if status in ("released", "stalled") and (released_by or stalled_by):
                    # Release/stalled events are exempt from the one-owner rule.
                    continue
                owner = rec.get("owner_id") or rec.get("id") or ""
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
    else:
        warnings.append("push-range checks skipped: not in a git repo")

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

    for w in warnings:
        print(f"  ! {w}")
    if errors:
        print("VALIDATE: FAIL — do not push until this is fixed:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"VALIDATE: OK — {len(roster)} roster member(s), {len(claims)} claim(s), "
          f"{len(features)} feature(s), {len(prd)} PRD item(s) cross-checked; {scanned} team/ file(s) scanned for secrets/PII.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
