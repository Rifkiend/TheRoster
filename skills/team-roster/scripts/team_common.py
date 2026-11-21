#!/usr/bin/env python3
"""Shared helpers for team-roster v2 scripts.

Stdlib only, Python 3.9+. Every script in this directory imports this
module (it lives next to them, so ``import team_common`` works when a
script is run as ``python3 scripts/<name>.py``).

Rules implemented here (team-roster v2.0 contract):
- strict id grammar ``^[a-z0-9][a-z0-9-]*$``, max 64 chars; NFKC +
  casefold before any comparison; non-ASCII ids rejected.
- all timestamps UTC ISO-8601 ending in ``Z``; timestamps without a
  zone are rejected (``parse_time`` returns None for them).
- claim expiry is advisory with a grace window (default 15 min): a
  claim is stealable only when ``now > expires_at + grace`` AND its
  heartbeat is stale (default 30 min without a heartbeat).
- Lamport: each record file carries ``lamport`` (int); on every write
  ``lamport = existing lamport + 1`` (new record: 1). Ties break by
  agent id.
- claim_seq: monotonic per claimed file = max existing claim_seq for
  that file across team/claims/ + 1.
- created_seq (v2.4): monotonic creation counter across ALL claims =
  max existing created_seq + 1, assigned once at claim time, never
  bumped by mutation. ``claim_order_key`` is (created_seq, owner_id)
  — "first" means created first; records without created_seq fall
  back to lamport. Honest limit: concurrent creators can compute the
  same number; the push fence adjudicates.
- max_lifetime_hours (v2.4, default 24): a claim dies this long after
  started_at no matter how fresh its heartbeat is; 0 disables the cap.
- paths are compared canonically (lowercased); exact file paths only.
- Signed identity (v2.4, opt-in via require_signatures): roster
  records may carry an SSH ``public_key``; ``verify_commit_signature``
  checks a commit against ONE key via a temp allowed-signers file.
- Hardening (v2.5): ghost claims (``is_blocking(..., roster_ids)``),
  clock-skew gate (``is_impossibly_future``), dependency cycles
  (``find_dependency_cycle``), mailbox signatures
  (``verify_mailbox_signature`` / ``check_mailbox``), last-known-main-SHA
  (``check_main_history`` / ``record_main_sha``).
- Orca features (v2.6): fan-out groups, decision gates, typed
  completion receipts, formal reviews (``receipt_sign_payload`` /
  ``verify_receipt_signature``, ``gate_blocks_claim`` /
  ``open_gate_for_claim`` / ``open_gate_for_prd``,
  ``latest_review_verdict`` / ``prd_blocked_by_review``).
"""
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime, timedelta, timezone

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
MAX_ID_LEN = 64
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

DEFAULT_CONFIG = {
    "canonical_remote": "origin",
    "canonical_branch": "main",
    "skill_version": "2.7.0",
    "claim_ttl_minutes": 120,
    "heartbeat_minutes": 30,
    "grace_minutes": 15,
    "max_claims_per_agent": 5,
    "max_files_per_claim": 10,
    "branch_freshness_commits": 20,
    "branch_freshness_days": 7,
    # v2.2 knobs (both default off — teams opt in to the strictness):
    # prd_required: every new claim must name a team/prd item.
    # strict_deps: a feature whose dependency bumped MAJOR since the
    # version it verified against FAILS validation (default: warn).
    "prd_required": False,
    "strict_deps": False,
    # Worktree-per-claim mode (v2.3). "off" (default) keeps v2.2 behaviour;
    # "per-claim" allows claim.py --worktree and the merge mode of sync_check.
    "worktree_mode": "off",
    # Dirs symlinked from the main checkout into a fresh worktree (only
    # when they exist there AND are gitignored — never symlink tracked dirs).
    "worktree_shared_dirs": ["node_modules", ".cache"],
    # Files copied (never symlinked) from the main checkout into a worktree.
    "worktree_copy_files": [".env", ".env.local"],
    # Signed identity (v2.4). Off by default: v2.4 changes nothing for
    # teams that don't opt in. On means validate.py verifies that every
    # commit in the push range touching team/ is signed by the SSH key
    # recorded on the roster for the record's owner.
    "require_signatures": False,
    # Hard claim lifetime (v2.4), in hours. A claim dies this long after
    # started_at no matter how fresh its heartbeat is. 0 disables the
    # cap (a team that genuinely wants immortal claims says so).
    "max_lifetime_hours": 24,
    # Last-known-main-SHA (v2.5). The origin/main SHA this team last
    # verified (recorded by sync_check after each successful fetch).
    # validate.py / sync_check FAIL when the recorded SHA is no longer
    # an ancestor of origin/main — history was rewritten (force-push).
    # "" means "never recorded yet" (warn only, first sight).
    "last_known_main_sha": "",
}


# ---------------------------------------------------------------- ids

def normalize_id(value):
    """NFKC + casefold, the canonical form used before any comparison."""
    return unicodedata.normalize("NFKC", str(value)).casefold()


def valid_id(value):
    """True iff the id is strict-grammar: lowercase ASCII, digits,
    hyphens, starts alnum, max 64 chars. Rejects non-ASCII ids."""
    if not isinstance(value, str):
        return False
    if len(value) > MAX_ID_LEN or not value:
        return False
    if value != normalize_id(value):
        return False
    if any(ord(c) > 127 for c in value):
        return False
    return bool(ID_RE.match(value))


def normalize_keyword(value):
    """Keywords: lowercase, NFKC, single spaces."""
    return re.sub(r"\s+", " ", normalize_id(value).strip())


# ---------------------------------------------------------------- time

def now():
    return datetime.now(timezone.utc)


def now_iso():
    """UTC ISO-8601 ending in Z, e.g. 2026-11-14T11:00:00Z."""
    return now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def to_utc_z(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value):
    """Parse an ISO-8601 timestamp. Returns an aware datetime, or None
    if unparseable OR if it carries no timezone (zone-less timestamps
    are rejected: they are ambiguous, see audit 1.3)."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt


def parse_date(value):
    """Strict YYYY-MM-DD date, or None."""
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


# Clock-skew gate (v2.5). A timestamp more than this many minutes in
# the future vs the local clock is impossible — no clock that is
# merely slow or fast by seconds writes a timestamp 15 minutes
# ahead. The tolerance is the grace_minutes window: the same
# disagreement the expiry rule already tolerates.
CLOCK_SKEW_FAIL_MINUTES = 15


def is_impossibly_future(dt, at=None, tolerance_minutes=CLOCK_SKEW_FAIL_MINUTES):
    """True when `dt` is more than `tolerance_minutes` ahead of `at`
    (default: now). A skewed clock can write a future timestamp;
    a correct one cannot. The AHEAD direction is detectable from the
    record alone; the BEHIND direction needs an external anchor — the
    base commit's timestamp — see check_claim_base_skew() (v2.7)."""
    if dt is None:
        return False
    at = at or now()
    return (dt - at) > timedelta(minutes=tolerance_minutes)


def git_commit_timestamp(repo_root, sha):
    """Committer date of a commit as an aware datetime, or None when
    the commit is unknown / git is unavailable / the date unparsable.

    Uses %cI (committer date, strict ISO-8601): the committer date is
    when the commit object came to exist; the author date (%aI) can be
    far older (cherry-pick, rebase, GIT_AUTHOR_DATE), so it must not
    be used as a "this commit existed by then" anchor."""
    if not sha or not repo_root:
        return None
    r = run_git(["show", "-s", "--format=%cI", sha], cwd=repo_root)
    if r.returncode != 0:
        return None
    return parse_time(r.stdout.strip())


def check_claim_base_skew(claim, repo_root, grace_minutes=CLOCK_SKEW_FAIL_MINUTES):
    """Behind-skew gate (v2.7). Returns an error string, or None when
    the claim passes or cannot be judged.

    Invariant: a claim cannot be created before the commit it is based
    on exists — creation real-time R_c >= base-commit real-time R_B.
    So started_at far BEFORE the creation base's timestamp means the
    claim author's clock runs behind by more than the grace. The base
    is `created_base_commit` (recorded once at claim time, never
    mutated), falling back to `base_commit` — for a non-worktree claim
    that IS the creation base (renewal only rewrites base_commit when
    --worktree is passed).

    Skips (None, no verdict) when there is no signal: no base recorded
    (pre-v2.7 claim, non-git repo), base object unknown to this repo,
    or started_at unparsable. A skip is not a pass — it is "cannot
    tell", and the caller must not treat it as proof of a sane clock.

    Honest limits: this only detects skew AT CLAIM CREATION time vs
    the creation base. A clock that was sane at creation and drifts
    later is not caught here (heartbeat staleness handles that
    direction). And it cannot say WHOSE clock is wrong — a base author
    with a >grace-fast clock produces a transient false FAIL; the
    error names the base commit and both timestamps so a human can
    tell which of the two clocks is off. Recovery: fix the clock
    (NTP), release, re-claim."""
    base = (claim.get("created_base_commit") or "") or (claim.get("base_commit") or "")
    if not base:
        return None
    base_ts = git_commit_timestamp(repo_root, base)
    if base_ts is None:
        return None
    started = parse_time(claim.get("started_at") or "")
    if started is None:
        return None
    if started < base_ts - timedelta(minutes=grace_minutes):
        return (f"started_at {claim.get('started_at')} is more than {grace_minutes} minutes BEFORE "
                f"its creation base {base} ({to_utc_z(base_ts)}) — impossible: a claim cannot be "
                "created before the commit it is based on exists. The claim author's clock runs "
                "behind; fix the clock (NTP), release this claim and re-claim "
                "(if the BASE author's clock was fast instead, this is a transient false FAIL — "
                "release + re-claim once time has passed the base timestamp)")
    return None


def find_duplicate_created_seq(claims):
    """Duplicate created_seq (v2.7). Returns a list of (seq, [task_ids])
    for every seq > 0 shared by two or more DISTINCT claims.

    created_seq is max-over-all-claims + 1, so a duplicate can only
    arise from true concurrency (two agents reading the same max at
    the same time) or hand-tampering. Seq <= 0 is ignored: pre-v2.4
    claims carry no created_seq. The caller FAILs only when at least
    TWO of the duplicates are active — a single active holder plus
    released/done ones is unambiguous, and the released ones are dead
    history that cannot be fixed without rewriting history."""
    by_seq = {}
    for tid, c in claims.items():
        if not isinstance(c, dict):
            continue
        try:
            seq = int(c.get("created_seq") or 0)
        except (TypeError, ValueError):
            seq = 0
        if seq <= 0:
            continue
        by_seq.setdefault(seq, []).append(tid)
    return sorted((seq, sorted(tids)) for seq, tids in by_seq.items()
                  if len(set(tids)) > 1)


# ---------------------------------------------------------------- json io

def load_json_file(path):
    """Returns (data, error). error is None on success."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, f"not found: {path}"
    except json.JSONDecodeError as e:
        return None, f"not valid JSON: {path}: {e}"
    except OSError as e:
        return None, f"cannot read {path}: {e}"


def save_json(path, data):
    """Write JSON deterministically (sorted keys, 2-space indent) via a
    temp file + rename, so a half-written record never lands."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def load_records(directory):
    """Load every ``*.json`` in a sharded dir. Returns
    ``{stem: (path, record)}`` sorted by stem. Files whose content is
    not a JSON object are returned with record=None so callers can
    report them instead of crashing."""
    out = {}
    if not os.path.isdir(directory):
        return out
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(directory, name)
        data, _err = load_json_file(path)
        out[name[:-5]] = (path, data if isinstance(data, dict) else None)
    return out


def load_config(team_dir):
    """team/config.json merged over DEFAULT_CONFIG. Missing file or bad
    JSON -> defaults (with a note on stderr for bad JSON)."""
    cfg = dict(DEFAULT_CONFIG)
    path = os.path.join(team_dir, "config.json")
    if os.path.isfile(path):
        data, err = load_json_file(path)
        if err:
            print(f"team_common: warning — {err}; using default config.", file=sys.stderr)
        elif isinstance(data, dict):
            for key, value in data.items():
                if key in cfg:
                    cfg[key] = value
    return cfg


# ---------------------------------------------------------------- git

def run_git(args, cwd=None):
    return subprocess.run(["git"] + args, capture_output=True, text=True, cwd=cwd)


def git_root(cwd=None):
    r = run_git(["rev-parse", "--show-toplevel"], cwd=cwd)
    return r.stdout.strip() if r.returncode == 0 else None


def git_head_commit(cwd=None):
    r = run_git(["rev-parse", "HEAD"], cwd=cwd)
    return r.stdout.strip() if r.returncode == 0 else ""


def git_blob_sha(path, cwd=None):
    """Git blob sha of a file's bytes (``git hash-object`` semantics,
    computed in pure Python so it works with no repo at all)."""
    try:
        with open(path, "rb") as f:
            content = f.read()
    except OSError:
        return None
    header = ("blob %d\0" % len(content)).encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def branch_freshness(repo_root, remote, branch, ref="HEAD"):
    """How far `ref` is behind <remote>/<branch>.

    Returns (behind_commits, age_days, note). behind/age are None when
    the remote branch cannot be resolved (no remote = skip with note).
    Age = days since the oldest commit on `ref` that the remote does
    not have; 0 when there is no such commit. `ref` defaults to HEAD
    so existing callers are unchanged; worktree mode passes the
    worktree branch name to measure that branch instead."""
    ref_remote = "%s/%s" % (remote, branch)
    r = run_git(["rev-parse", "--verify", "--quiet", ref_remote], cwd=repo_root)
    if r.returncode != 0:
        return None, None, "no remote branch %s to compare against" % ref_remote
    r = run_git(["rev-list", "--count", "%s..%s" % (ref, ref_remote)], cwd=repo_root)
    try:
        behind = int(r.stdout.strip())
    except ValueError:
        return None, None, "could not count commits behind %s" % ref_remote
    r = run_git(["log", "%s..%s" % (ref_remote, ref), "--reverse", "--format=%aI", "-n", "1"],
                cwd=repo_root)
    first = r.stdout.strip().splitlines()
    if not first:
        return behind, 0.0, ""
    dt = parse_time(first[0])
    if dt is None:
        return behind, 0.0, ""
    age_days = (now() - dt).total_seconds() / 86400.0
    return behind, age_days, ""


# ---------------------------------------------------------------------------
# Worktree helpers (v2.3)
# ---------------------------------------------------------------------------

def git_available():
    """True when a `git` binary runs."""
    try:
        r = run_git(["--version"])
    except (OSError, ValueError):
        return False
    return r.returncode == 0


def valid_branch_name(name):
    """True when `name` is a legal git branch name.

    Uses `git check-ref-format --branch` when git is available (the
    authoritative rule); falls back to the main grammar rules."""
    if not name or not isinstance(name, str):
        return False
    if git_available():
        try:
            r = run_git(["check-ref-format", "--branch", name])
        except (OSError, ValueError):
            r = None
        if r is not None:
            return r.returncode == 0
    # Fallback grammar (subset of git-check-ref-format rules).
    if name.startswith(("-", ".", "/")) or name.endswith(("/", ".lock", ".")):
        return False
    if any(bad in name for bad in ("..", "~", "^", ":", "?", "*", "[", "\\", " ")):
        return False
    if "@{" in name or "//" in name:
        return False
    return True


def canonical_worktree_path(path, repo_root=None):
    """Canonical form of a worktree path, for comparison.

    Two spellings of the SAME directory — relative vs absolute,
    `../wt-x` vs `/abs/path/wt-x`, a symlinked parent — must compare
    equal, or two claims can silently land in the same worktree
    (v2.3.0 defect E3c: `../wt-alias` vs its absolute spelling were
    accepted as two different worktrees). `realpath` resolves
    symlinks in the part of the path that exists and normalises the
    rest lexically, so it also works in the claim-before-setup window
    when the directory does not exist yet. Relative paths are
    recorded against the main checkout root, so that is the base —
    never the caller's cwd (a script run from inside a linked
    worktree would resolve them against the wrong checkout).
    Returns "" for an empty path."""
    p = str(path or "")
    if not p:
        return ""
    if not os.path.isabs(p):
        base = repo_root or os.getcwd()
        p = os.path.join(base, p)
    return os.path.realpath(p)


def worktree_branch_exists(repo_root, branch):
    """True when `branch` exists as a LOCAL branch in this repo."""
    if not branch or not repo_root:
        return False
    r = run_git(["show-ref", "--verify", "--quiet",
                 "refs/heads/" + branch], cwd=repo_root)
    return r.returncode == 0


def worktree_presence(repo_root, worktree, branch):
    """Where is a worktree claim's worktree? Returns one of:

    - "present": registered in `git worktree list` on this machine.
    - "deleted-here": no entry, but the claim's branch still exists
      as a LOCAL branch — the worktree lived on this machine and was
      removed (a worktree on a teammate's machine never creates a
      local branch here, so this is not them).
    - "absent": no entry and no local branch — the worktree may live
      on a teammate's machine, or was never set up.
    - "unknown": repo_root is None (cannot tell — caller must skip).
    """
    if not repo_root:
        return "unknown"
    if worktree_entry_for_path(repo_root, worktree) is not None:
        return "present"
    if branch and worktree_branch_exists(repo_root, branch):
        return "deleted-here"
    return "absent"


def worktree_dirty_status(worktree_path):
    """Uncommitted changes in a worktree, via `git status --porcelain`.

    Returns (dirty, lines, error). `dirty` is True when there are
    uncommitted changes — tracked modifications, staged changes, or
    untracked files. `error` is set when git could not run there.
    A merge check only looks at commits, so uncommitted work is
    invisible to it: callers must fail before claiming a merge is
    clean (v2.3.0 gap E5)."""
    r = run_git(["status", "--porcelain"], cwd=worktree_path)
    if r.returncode != 0:
        return (False, [], "could not run git status in %s: %s"
                % (worktree_path, (r.stderr or "").strip() or "unknown error"))
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    return (bool(lines), lines, None)


def git_worktree_list(repo_root):
    """Parse `git worktree list --porcelain`.

    Returns a list of dicts: {"path", "head", "branch" (None when
    detached), "bare": bool}. The first entry is the main worktree
    (unless the repo is bare). Returns [] when git or the repo is
    unavailable."""
    try:
        r = run_git(["worktree", "list", "--porcelain"], cwd=repo_root)
    except (OSError, ValueError):
        return []
    if r.returncode != 0:
        return []
    entries = []
    current = None
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            if current:
                entries.append(current)
                current = None
            continue
        if line.startswith("worktree "):
            current = {"path": line[len("worktree "):], "head": "",
                       "branch": None, "bare": False}
        elif current is not None:
            if line.startswith("HEAD "):
                current["head"] = line[len("HEAD "):]
            elif line.startswith("branch "):
                ref = line[len("branch "):]
                current["branch"] = (ref[len("refs/heads/"):]
                                     if ref.startswith("refs/heads/") else ref)
            elif line == "bare":
                current["bare"] = True
            elif line == "detached":
                current["branch"] = None
    if current:
        entries.append(current)
    return entries


def main_checkout_path(repo_root):
    """Absolute path of the main checkout, or None.

    The main checkout is the first non-bare entry of `git worktree list`.
    """
    for entry in git_worktree_list(repo_root):
        if not entry.get("bare"):
            return os.path.normpath(entry["path"])
    return None


def worktree_entry_for_path(repo_root, path):
    """The worktree-list entry for `path`, or None when not a worktree.

    Compared on canonical paths (v2.3.1): a registered worktree
    spelled differently from the claim (`../wt-x` vs its absolute
    path) is still the same worktree."""
    norm = canonical_worktree_path(path, repo_root)
    for entry in git_worktree_list(repo_root):
        if canonical_worktree_path(entry["path"], repo_root) == norm:
            return entry
    return None


def _team_dir_context(team_dir):
    """(toplevel, main_path) for a team dir.

    toplevel is the checkout that contains team_dir; main_path is the
    main checkout of that repo. (None, None) when team_dir is not
    inside a git repo."""
    abs_dir = os.path.abspath(team_dir)
    # team_dir may not exist yet (fresh repo) — probe the nearest
    # existing ancestor so git_root still finds the repo.
    probe = abs_dir
    while not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            return (None, None)
        probe = parent
    try:
        toplevel = git_root(cwd=probe)
    except OSError:
        return (None, None)
    if not toplevel:
        return (None, None)
    return (toplevel, main_checkout_path(toplevel))


def team_dir_in_linked_worktree(team_dir):
    """True when team_dir lives inside a LINKED worktree (not the main one).

    This is the Dziatkovskii trap detector: team/ state read from inside
    a worktree is a private copy, not the shared board."""
    toplevel, main_path = _team_dir_context(team_dir)
    if not toplevel or not main_path:
        return False
    return os.path.normpath(toplevel) != os.path.normpath(main_path)


def resolve_team_dir(team_dir):
    """Redirect a team dir inside a linked worktree to the main checkout.

    Returns (effective_team_dir, note). `note` is None when no redirect
    happened. Reads MUST use the effective dir — the worktree copy of
    team/ is stale the moment it is branched."""
    toplevel, main_path = _team_dir_context(team_dir)
    if not toplevel or not main_path:
        return (team_dir, None)
    if os.path.normpath(toplevel) == os.path.normpath(main_path):
        return (team_dir, None)
    suffix = os.path.relpath(os.path.abspath(team_dir), toplevel)
    effective = (os.path.join(main_path, suffix)
                 if suffix != "." else main_path)
    note = ("team/ state lives on the main checkout (%s) — reading from "
            "there, not from the worktree copy at %s"
            % (main_path, toplevel))
    return (effective, note)


def guard_team_mutation(team_dir, action="mutate team/ state"):
    """Error message when team_dir is inside a linked worktree, else None.

    Call at the top of every command that WRITES team/ state. A mutation
    from inside a worktree lands on the worktree branch and is invisible
    to the rest of the team until (and unless) that branch merges — the
    exact failure Dziatkovskii documented with 94 worktrees."""
    toplevel, main_path = _team_dir_context(team_dir)
    if not toplevel or not main_path:
        return None
    if os.path.normpath(toplevel) == os.path.normpath(main_path):
        return None
    return ("refusing to %s from inside a linked worktree (%s): team/ "
            "state is only written from the main checkout (%s) — "
            "cd there first" % (action, toplevel, main_path))


def claims_share_checkout(claim_a, claim_b, repo_root=None):
    """True when two claims edit files on the SAME checkout.

    Two worktree claims on DIFFERENT worktrees never share a checkout,
    so they may cover the same file. Any pair involving a shared-checkout
    (non-worktree) claim shares the main checkout. Two claims naming the
    SAME worktree path also share a checkout (misconfiguration — treat
    as a conflict). Paths are compared canonically (v2.3.1): `../wt-x`
    and its absolute spelling are the same worktree, even when the
    directory does not exist yet — relative paths resolve against
    repo_root (the main checkout root), never the caller's cwd."""
    wa = (claim_a or {}).get("worktree") or ""
    wb = (claim_b or {}).get("worktree") or ""
    if not wa or not wb:
        return True
    return (canonical_worktree_path(wa, repo_root)
            == canonical_worktree_path(wb, repo_root))


def merge_check(repo_root, base_ref, branch):
    """Does `branch` merge cleanly onto `base_ref`?

    Returns (clean, conflicts, tree_sha, error). `clean` is True when
    the merge is conflict-free, False when it conflicts, None when the
    check could not run at all (error explains why). `conflicts` is the
    list of conflicted paths. Uses `git merge-tree --write-tree`
    (git >= 2.38); falls back to a trial merge in a throwaway worktree
    when merge-tree is unavailable. Never touches the real worktree."""
    import shutil
    import tempfile
    if not git_available():
        return (None, [], None, "git not available")
    r = run_git(["rev-parse", "--verify", "--quiet", base_ref], cwd=repo_root)
    if r.returncode != 0:
        return (None, [], None, "unknown base ref: %s" % base_ref)
    r = run_git(["rev-parse", "--verify", "--quiet", branch], cwd=repo_root)
    if r.returncode != 0:
        return (None, [], None, "unknown branch: %s" % branch)
    # Preferred: merge-tree (no working tree touched, no checkout needed).
    r = run_git(["merge-tree", "--write-tree", "--name-only",
                 base_ref, branch], cwd=repo_root)
    if r.returncode in (0, 1):
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        tree_sha = lines[0] if lines else None
        conflicts = lines[1:] if r.returncode == 1 else []
        return (r.returncode == 0, conflicts, tree_sha, None)
    # Fallback: trial merge in a throwaway worktree of the base ref.
    tmp = tempfile.mkdtemp(prefix="theroster-merge-check-")
    try:
        r2 = run_git(["worktree", "add", "--detach", tmp, base_ref],
                     cwd=repo_root)
        if r2.returncode != 0:
            return (None, [], None,
                    "merge-tree unavailable and trial worktree failed: %s"
                    % ((r2.stderr or "").strip() or "unknown error"))
        r3 = run_git(["merge", "--no-commit", "--no-ff", branch], cwd=tmp)
        if r3.returncode == 0:
            run_git(["merge", "--abort"], cwd=tmp)
            return (True, [], None, None)
        r4 = run_git(["diff", "--name-only", "--diff-filter=U"], cwd=tmp)
        run_git(["merge", "--abort"], cwd=tmp)
        conflicts = [ln for ln in r4.stdout.splitlines() if ln.strip()]
        return (False, conflicts, None, None)
    finally:
        run_git(["worktree", "remove", "--force", tmp], cwd=repo_root)
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- claims

def is_heartbeat_stale(claim, at=None, heartbeat_minutes=30):
    at = at or now()
    hb = parse_time(claim.get("heartbeat_at") or claim.get("started_at") or "")
    if hb is None:
        return True  # no heartbeat recorded at all counts as stale
    return (at - hb) > timedelta(minutes=heartbeat_minutes)


def is_stealable(claim, at=None, cfg=None):
    """Stealable only when now > expires_at + grace AND the heartbeat
    is stale. Expiry alone never frees a claim while its owner is
    still heartbeating.

    v2.4: a claim is ALSO stealable past its hard lifetime —
    now > started_at + max_lifetime_hours (default 24h) — no matter
    how fresh the heartbeat is. A heartbeat can keep a claim alive
    for hours; it cannot keep it alive forever. Set
    max_lifetime_hours to 0 to disable the cap."""
    cfg = cfg or DEFAULT_CONFIG
    at = at or now()
    if claim.get("status") != "active":
        return False
    try:
        max_life = float(cfg.get("max_lifetime_hours", 24) or 0)
    except (TypeError, ValueError):
        max_life = 24
    if max_life > 0:
        started = parse_time(claim.get("started_at") or "")
        if started is not None and at > started + timedelta(hours=max_life):
            return True
    exp = parse_time(claim.get("expires_at") or "")
    if exp is None:
        return False
    grace = timedelta(minutes=cfg.get("grace_minutes", 15))
    if at <= exp + grace:
        return False
    return is_heartbeat_stale(claim, at, cfg.get("heartbeat_minutes", 30))


def is_blocking(claim, at=None, cfg=None, roster_ids=None):
    """A claim blocks others while it is active and not stealable.

    v2.5 (ghost claims): when `roster_ids` is given (the set of ALL
    roster ids, any status), a claim whose owner is not in the
    roster at all is NOT blocking — the member is gone, the claim
    is dead, and it must never hold files or brick a push while it
    waits to be released. Callers without a roster (e.g.
    sync_check §2, where the remote roster is not the local one)
    pass nothing and keep the time-based behaviour."""
    if roster_ids is not None and (claim.get("owner_id") or "") not in roster_ids:
        return False
    return claim.get("status") == "active" and not is_stealable(claim, at, cfg)


def claim_order_key(claim):
    """Causal order of two claims: (created_seq, owner_id). Wall-clock
    timestamps NEVER decide "who claimed first" — a skewed clock can
    be 10 minutes in the future.

    v2.4: `created_seq` is a monotonic creation counter, assigned at
    claim time and never bumped by mutation — so "first" means
    created first. (v2.3 and earlier used lamport here, but lamport
    counts *mutations*: every new claim starts at lamport 1, so for
    fresh claims "first" was alphabetical, and heartbeating a claim
    made it *later*. Records written before v2.4 have no
    created_seq; for them the key falls back to lamport.)
    The claim with the higher created_seq came later; on a tie, the
    higher owner_id (lexicographic) is the later one, so the order
    is deterministic. Honest limit: two agents creating claims
    concurrently can compute the same created_seq — a file cannot
    be an atomic counter; the push fence adjudicates that race."""
    try:
        seq = int(claim.get("created_seq") or 0)
    except (TypeError, ValueError):
        seq = 0
    if seq <= 0:
        try:
            seq = int(claim.get("lamport") or 0)
        except (TypeError, ValueError):
            seq = 0
    return (seq, str(claim.get("owner_id") or ""))


def claim_status_note(claim, at=None, cfg=None):
    """One-line human note about a claim's liveness (for list output)."""
    cfg = cfg or DEFAULT_CONFIG
    at = at or now()
    if claim.get("status") != "active":
        return "status=%s" % claim.get("status")
    if is_stealable(claim, at, cfg):
        try:
            max_life = float(cfg.get("max_lifetime_hours", 24) or 0)
        except (TypeError, ValueError):
            max_life = 24
        started = parse_time(claim.get("started_at") or "")
        if (max_life > 0 and started is not None
                and at > started + timedelta(hours=max_life)):
            return "STEALABLE (past max lifetime of %gh — release it and re-claim as a new task)" % max_life
        return "STEALABLE (expired + heartbeat stale)"
    exp = parse_time(claim.get("expires_at") or "")
    if exp is not None and at > exp:
        return "expired but heartbeat fresh — still blocking until heartbeat goes stale"
    return "active"


# ---------------------------------------------------------------------------
# Dependency cycles (v2.5)
# ---------------------------------------------------------------------------

def find_dependency_cycle(features):
    """Find a dependency cycle in a feature map, or None.

    `features` maps id -> record (only `depends_on` is read).
    Edges to unregistered features are ignored here — they are
    already an error elsewhere. Returns the cycle as a list that
    starts and ends at the same id, e.g. ["feat-a", "feat-b",
    "feat-a"], or None when the graph is acyclic. Iterative DFS
    with a grey stack, so a long chain never recurses.

    A cycle can never be topologically sorted — any future tool
    that tries loops forever — so it must fail at write time
    (register/improve) and at check time (check/validate/reverify)."""
    grey = []          # current DFS path (ordered)
    grey_set = set()
    black = set()

    def neighbours(fid):
        rec = features.get(fid) or {}
        out = []
        for dep in rec.get("depends_on") or []:
            if isinstance(dep, dict) and (dep.get("id") or "") in features:
                out.append(dep["id"])
        return sorted(out)

    for start in sorted(features):
        if start in black or start in grey_set:
            continue
        # (node, neighbour-index) stack; a node is grey while on it.
        stack = [(start, 0)]
        grey.append(start)
        grey_set.add(start)
        while stack:
            node, idx = stack[-1]
            nbrs = neighbours(node)
            if idx >= len(nbrs):
                stack.pop()
                grey_set.discard(node)
                grey.pop()
                black.add(node)
                continue
            stack[-1] = (node, idx + 1)
            nxt = nbrs[idx]
            if nxt in grey_set:
                # Cycle: the grey path from nxt back to nxt.
                at = grey.index(nxt)
                return grey[at:] + [nxt]
            if nxt in black:
                continue
            grey.append(nxt)
            grey_set.add(nxt)
            stack.append((nxt, 0))
    return None


# ---------------------------------------------------------------- paths

def canonical_path(path):
    """Lowercased comparison form. On-disk spelling is preserved in
    the record; conflicts are decided on this form (audit 1.9)."""
    return str(path).replace("\\", "/").casefold()


def path_syntax_error(path):
    """Exact-file-path rule (audit 1.5): no directories (trailing /),
    no globs (* ? [), no absolute paths, no parent escapes."""
    p = str(path)
    if not p:
        return "empty path"
    if p.endswith("/"):
        return "directory path %r — claims are exact file paths only" % p
    if any(ch in p for ch in "*?["):
        return "glob %r — claims are exact file paths only" % p
    if p.startswith("/") or (len(p) > 1 and p[1] == ":"):
        return "absolute path %r — use a repo-relative path" % p
    parts = p.replace("\\", "/").split("/")
    if ".." in parts:
        return "path %r escapes the repo (..)" % p
    if p.startswith("./"):
        return "path %r — drop the leading ./" % p
    return None


def paths_overlap(a, b):
    """True if canonical path a is b, a prefix of b, or has b as a
    prefix (covers file-under-claimed-path and vice versa)."""
    ca, cb = canonical_path(a), canonical_path(b)
    if ca == cb:
        return True
    return ca.startswith(cb + "/") or cb.startswith(ca + "/")


# ---------------------------------------------------------------- semver

def parse_semver(version):
    """Returns a comparable key, or None if not valid SemVer
    (MAJOR.MINOR.PATCH, no leading zeros, optional pre-release/build).
    Pre-release sorts before its release, per the SemVer spec."""
    m = SEMVER_RE.match(str(version or ""))
    if not m:
        return None
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    pre = m.group(4)
    if pre is None:
        pre_key = (1,)
    else:
        parts = []
        for part in pre.split("."):
            if part.isdigit():
                parts.append((0, int(part), ""))
            else:
                parts.append((1, 0, part))
        pre_key = (0,) + tuple(parts)
    return (major, minor, patch, pre_key)


def is_prerelease(version):
    m = SEMVER_RE.match(str(version or ""))
    return bool(m and m.group(4))


def bump_version(version, bump):
    """Mechanical bump with reset-to-zero. Returns the new version
    string, or None if the input is not valid SemVer."""
    key = parse_semver(version)
    if key is None:
        return None
    major, minor, patch = key[0], key[1], key[2]
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
    return "%d.%d.%d" % (major, minor, patch)


# ---------------------------------------------------------------- events

def append_jsonl(path, line):
    """Append one JSON object as one line to a .jsonl file.

    Newline-safe: if the file exists, is non-empty, and does not
    end with a newline, a newline is written first — otherwise the
    new line concatenates onto the previous one and BOTH become
    unparseable. (Caught in v2.6 smoke testing: the starter
    team/mailbox/assistant.jsonl ended without a trailing newline,
    so the first appended review delivery corrupted line 1.)
    Returns True on success, False on OSError."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        needs_sep = False
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            with open(path, "rb") as f:
                f.seek(-1, os.SEEK_END)
                needs_sep = f.read(1) != b"\n"
        with open(path, "a", encoding="utf-8") as f:
            if needs_sep:
                f.write("\n")
            f.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
        return True
    except OSError:
        return False


def append_text_lines(path, lines):
    """Append raw text lines to a file, newline-safe like
    append_jsonl (a separator newline first when the existing file
    does not end with one). Each line is written as-is plus one
    trailing newline. Returns True on success, False on OSError."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        needs_sep = False
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            with open(path, "rb") as f:
                f.seek(-1, os.SEEK_END)
                needs_sep = f.read(1) != b"\n"
        with open(path, "a", encoding="utf-8") as f:
            if needs_sep:
                f.write("\n")
            for line in lines:
                f.write(line.rstrip("\n") + "\n")
        return True
    except OSError:
        return False


def append_event(team_dir, event, lamport, actor, base_commit="", task_id=None,
                 feature_id=None, extra=None):
    """Append one line to team/events.jsonl (append-only audit log).
    Never fails the caller: a missing/unwritable log is reported on
    stderr and skipped."""
    line = {
        "event": event,
        "at": now_iso(),
        "lamport": lamport,
        "actor": actor,
        "base_commit": base_commit or "",
    }
    if task_id:
        line["task_id"] = task_id
    if feature_id:
        line["feature_id"] = feature_id
    if extra:
        line.update(extra)
    path = os.path.join(team_dir, "events.jsonl")
    if not append_jsonl(path, line):
        print(f"team_common: warning — could not append to {path}", file=sys.stderr)


def next_claim_seq(claims_dir, files):
    """claim_seq for a new claim: per file, max existing claim_seq for
    that file across team/claims/ + 1; the claim carries the highest of
    its files' next numbers (copied into commit trailers as
    ``[claim #N]``)."""
    best = 0
    wanted = {canonical_path(f) for f in files}
    for _stem, (_path, rec) in load_records(claims_dir).items():
        if not isinstance(rec, dict):
            continue
        try:
            seq = int(rec.get("claim_seq") or 0)
        except (TypeError, ValueError):
            seq = 0
        if seq <= 0:
            continue
        for f in rec.get("files", []) or []:
            if canonical_path(f) in wanted and seq >= best:
                best = seq
    return best + 1


def next_created_seq(claims_dir):
    """created_seq for a new claim (v2.4): max existing created_seq
    across ALL team/claims/ + 1. Unlike claim_seq it is per-claim,
    not per-file, and it is never bumped by mutation — heartbeat,
    renewal, flag, and stall all move lamport only.

    Honest limit: two agents creating claims concurrently can
    compute the same number (both read max N, both write N+1). A
    file cannot be an atomic counter; the push fence (first push
    wins + claim_seq per file + claim check on merge) adjudicates
    that race — and since v2.7 the duplicate-created_seq gate
    (find_duplicate_created_seq, enforced by claim.py check and
    validate.py) FAILs the push until the active duplicate is
    released and re-claimed. created_seq orders creation within one
    repo's visible history — it is not a distributed counter."""
    best = 0
    for _stem, (_path, rec) in load_records(claims_dir).items():
        if not isinstance(rec, dict):
            continue
        try:
            seq = int(rec.get("created_seq") or 0)
        except (TypeError, ValueError):
            seq = 0
        if seq > best:
            best = seq
    return best + 1


# ---------------------------------------------------------------------------
# Signed identity (v2.4)
# ---------------------------------------------------------------------------

SSH_KEY_TYPES = {
    "ssh-ed25519", "ssh-rsa",
    "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com",
}


def valid_public_key(value):
    """True iff `value` looks like one SSH public key line:
    `<type> <base64 blob> [comment]`. Format only — it does not
    prove anyone holds the private key (that is the roster's
    human-approval step, not this check)."""
    if not isinstance(value, str):
        return False
    parts = value.strip().split()
    if len(parts) < 2:
        return False
    if parts[0] not in SSH_KEY_TYPES:
        return False
    try:
        raw = base64.b64decode(parts[1], validate=True)
    except Exception:
        return False
    return len(raw) >= 16


def verify_commit_signature(repo_root, commit_sha, public_key, principal="theroster"):
    """Was `commit_sha` signed by `public_key`?

    Returns (ok, error). `ok` is True only when git verifies the
    commit's signature against a temp allowed-signers file holding
    ONLY that key — so a signature from any other key fails, and an
    unsigned commit fails. `error` explains the failure (last line
    of git's stderr) or is None on success.

    Mechanism: `git -c gpg.format=ssh -c
    gpg.ssh.allowedSignersFile=<tmp> verify-commit <sha>`. The -c
    overrides mean the check works regardless of the repo's own
    git config. git shells out to ssh-keygen for SSH verification;
    if ssh-keygen is missing, verification fails with an error
    that says so."""
    if not repo_root or not commit_sha or not public_key:
        return (False, "missing repo, commit, or key")
    if not valid_public_key(public_key):
        return (False, "public key is not a valid SSH public key")
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix="theroster-allowed-signers-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("%s %s\n" % (principal, public_key.strip()))
        r = run_git(["-c", "gpg.format=ssh",
                     "-c", "gpg.ssh.allowedSignersFile=" + tmp,
                     "verify-commit", commit_sha], cwd=repo_root)
        if r.returncode == 0:
            return (True, None)
        lines = [ln for ln in (r.stderr or "").strip().splitlines() if ln.strip()]
        return (False, lines[-1] if lines else "signature verification failed")
    except OSError as e:
        return (False, "could not verify signature: %s" % e)
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def team_dir_of(subdir):
    """team/claims -> team ; team -> team."""
    subdir = os.path.normpath(subdir)
    base = os.path.basename(subdir)
    if base in ("roster", "claims", "features", "mailbox", "handoffs", "prd",
                "fanout", "gates", "receipts", "reviews"):
        return os.path.dirname(subdir) or "."
    return subdir


# ---------------------------------------------------------------------------
# Last-known-main-SHA (v2.5) — history-rewrite detection
# ---------------------------------------------------------------------------

def check_main_history(repo_root, remote, branch, recorded_sha):
    """Is the recorded main SHA still an ancestor of <remote>/<branch>?

    Returns (status, message) with status one of:
    - "ok": recorded SHA is an ancestor — history was not rewritten.
    - "fail": recorded SHA is NOT an ancestor (or is an unknown
      object) — the remote's history was rewritten (force-push).
    - "unrecorded": no SHA recorded yet ("" / None) — first sight,
      nothing to compare against; caller should warn and record.
    - "unknown": the remote branch cannot be resolved — caller
      should warn and skip (same posture as branch_freshness).
    The recorded SHA is only ever advanced after a PASS — a failing
    run must NOT overwrite it, or the evidence is erased."""
    if not recorded_sha:
        return ("unrecorded",
                "no last-known-main-SHA recorded yet — run sync_check to record one")
    if not repo_root:
        return ("unknown", "not in a git repo")
    ref = "%s/%s" % (remote, branch)
    r = run_git(["rev-parse", "--verify", "--quiet", ref], cwd=repo_root)
    if r.returncode != 0:
        return ("unknown", "no remote branch %s to compare against" % ref)
    r = run_git(["merge-base", "--is-ancestor", recorded_sha, ref], cwd=repo_root)
    if r.returncode == 0:
        return ("ok", "recorded main SHA %s is an ancestor of %s" % (recorded_sha[:8], ref))
    if r.returncode == 1:
        return ("fail",
                "recorded main SHA %s is NOT an ancestor of %s — the remote history was "
                "rewritten (force-push); the audit log and claim_seq numbers on the old line "
                "are gone; verify the new history by hand before trusting it"
                % (recorded_sha, ref))
    return ("fail",
            "recorded main SHA %s is unknown to this repo — it is not in the remote history "
            "and not in the local object store; the remote history was rewritten or the "
            "object was pruned" % recorded_sha)


def record_main_sha(team_dir, repo_root, remote, branch):
    """Record the current <remote>/<branch> SHA into team/config.json.

    Returns the SHA recorded, or None when it could not be read.
    Only call after the ancestry check PASSED — recording the tip
    of a rewritten history would erase the evidence. The write is
    a working-tree change that lands in the next commit; that lag
    is by design (the recorded SHA is always an ancestor of the
    real tip, which is all the check needs)."""
    if not repo_root:
        return None
    ref = "%s/%s" % (remote, branch)
    r = run_git(["rev-parse", "--verify", "--quiet", ref], cwd=repo_root)
    if r.returncode != 0:
        return None
    sha = r.stdout.strip()
    if not sha:
        return None
    path = os.path.join(team_dir, "config.json")
    data, err = load_json_file(path)
    if err or not isinstance(data, dict):
        data = {}
    if data.get("last_known_main_sha") == sha:
        return sha
    data["last_known_main_sha"] = sha
    try:
        save_json(path, data)
    except OSError as e:
        print("team_common: warning — could not record last_known_main_sha: %s" % e,
              file=sys.stderr)
        return None
    return sha


# ---------------------------------------------------------------------------
# Mailbox signatures (v2.5) — rides on v2.4 signed identity
# ---------------------------------------------------------------------------

# Namespace ssh-keygen -Y sign/verify uses. Binding the signature to
# this namespace means a git-commit signature (namespace "git") or a
# signature for any other purpose cannot be replayed as mailbox mail.
MAILBOX_SIG_NAMESPACE = "theroster-mailbox"
# First line of the signed payload. Versioned so a future payload
# format does not silently verify against this one.
MAILBOX_SIG_VERSION = "theroster-mailbox-v1"


def mailbox_sign_payload(sender, ts, message):
    """The exact bytes a mailbox signature covers: version, sender,
    timestamp, message, one per line. A signature is bound to all
    three — it cannot be replayed onto a different message, a
    different timestamp, or a different sender."""
    return ("%s\n%s\n%s\n%s" % (MAILBOX_SIG_VERSION, sender, ts, message)).encode("utf-8")


def _verify_ssh_signature(public_key, principal, namespace, payload, sig):
    """Verify an armored SSH signature over `payload` bytes.

    Shared core for the mailbox (v2.5) and receipt (v2.6) schemes:
    `ssh-keygen -Y verify` against a temp allowed-signers file
    holding ONLY `public_key`, with the namespace binding the
    signature to its purpose — a mailbox signature cannot be
    replayed as a receipt signature or vice versa. Returns
    (ok, error)."""
    if not public_key or not valid_public_key(public_key):
        return (False, "sender has no valid public_key on the roster")
    if not isinstance(sig, str) or "BEGIN SSH SIGNATURE" not in sig:
        return (False, "sig is not an SSH signature block")
    tmp_signers = tmp_sig = None
    try:
        fd, tmp_signers = tempfile.mkstemp(prefix="theroster-ssh-signers-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("%s %s\n" % (principal, public_key.strip()))
        fd, tmp_sig = tempfile.mkstemp(prefix="theroster-ssh-sig-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(sig if sig.endswith("\n") else sig + "\n")
        r = subprocess.run(
            ["ssh-keygen", "-Y", "verify",
             "-f", tmp_signers, "-I", principal,
             "-n", namespace, "-s", tmp_sig],
            input=payload, capture_output=True)
        if r.returncode == 0:
            return (True, None)
        err = (r.stderr or b"").decode("utf-8", "replace").strip()
        lines = [ln for ln in err.splitlines() if ln.strip()]
        return (False, lines[-1] if lines else "signature verification failed")
    except OSError as e:
        return (False, "could not verify signature: %s" % e)
    finally:
        for tmp in (tmp_signers, tmp_sig):
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


def verify_mailbox_signature(public_key, sender, ts, message, sig):
    """Was this mailbox line signed by `public_key`?

    Returns (ok, error). `ok` is True only when ssh-keygen verifies
    the armored signature in `sig` against a temp allowed-signers
    file holding ONLY that key, with the payload built by
    mailbox_sign_payload() fed on stdin. A missing ssh-keygen, a
    malformed sig, or a signature from any other key fails."""
    payload = mailbox_sign_payload(sender, ts or "", message or "")
    return _verify_ssh_signature(public_key, sender,
                                 MAILBOX_SIG_NAMESPACE, payload, sig)


# ---------------------------------------------------------------------------
# Receipt signatures (v2.6) — rides on v2.4 signed identity, same shape
# as the mailbox scheme (v2.5) but a different namespace + payload,
# so a mailbox signature can never be replayed as a receipt.
# ---------------------------------------------------------------------------

# Namespace ssh-keygen -Y sign/verify uses for receipts.
RECEIPT_SIG_NAMESPACE = "theroster-receipt"
# First line of the signed payload. Versioned so a future payload
# format does not silently verify against this one.
RECEIPT_SIG_VERSION = "theroster-receipt-v1"


def receipt_sign_payload(done_by, done_at, task_id, claim_seq, outcome, summary):
    """The exact bytes a receipt signature covers: version, filer,
    timestamp, task, claim_seq, outcome, summary, one per line. A
    signature is bound to all six — it cannot be replayed onto a
    different task, a different attempt (claim_seq), a different
    outcome, or a different summary."""
    return ("%s\n%s\n%s\n%s\n%s\n%s\n%s"
            % (RECEIPT_SIG_VERSION, done_by, done_at, task_id,
               claim_seq, outcome, summary)).encode("utf-8")


def verify_receipt_signature(public_key, done_by, done_at, task_id,
                             claim_seq, outcome, summary, sig):
    """Was this receipt signed by `public_key`?

    Returns (ok, error). `ok` is True only when ssh-keygen verifies
    the armored signature in `sig` against a temp allowed-signers
    file holding ONLY that key, with the payload built by
    receipt_sign_payload() fed on stdin. A missing ssh-keygen, a
    malformed sig, or a signature from any other key fails."""
    payload = receipt_sign_payload(done_by, done_at or "", task_id,
                                   claim_seq, outcome, summary or "")
    return _verify_ssh_signature(public_key, done_by,
                                 RECEIPT_SIG_NAMESPACE, payload, sig)


def check_mailbox(team_dir, roster, cfg, imperative_res):
    """Validate team/mailbox/*.jsonl. Returns (errors, warnings).

    `roster` maps id -> record; `cfg` is the loaded team config;
    `imperative_res` is the regex list (check_roster.IMPERATIVE_RES)
    — passed in so this module stays free of the check_roster import.

    Rules (v2.5):
    - A line from a HUMAN roster member, when require_signatures is
      on, MUST carry a valid `sig` from that sender's roster key
      (any content, any wording — the signature IS the human
      speaking). No sig, no ts, no key on file, or a bad sig FAILS.
      When require_signatures is off, the v2.4 heuristic applies
      (imperative content FAILS).
    - A line from an UNKNOWN sender: the v2.4 heuristic (imperative
      FAILS; benign passes) — there is no roster key to verify.
    - A line from an ai-agent/tool member: a `sig`, when present, is
      verified against their key if they have one — present-but-
      invalid FAILS. No sig: the v2.4 heuristic (imperative warns).
    Malformed JSON lines warn, not fail (same as v2.4)."""
    import json as _json

    errors, warnings = [], []
    mailbox_dir = os.path.join(team_dir, "mailbox")
    if not os.path.isdir(mailbox_dir):
        return errors, warnings
    human_ids = {rid for rid, m in roster.items() if m.get("type") == "human"}
    all_ids = set(roster)
    require_sigs = bool((cfg or {}).get("require_signatures"))
    for name in sorted(os.listdir(mailbox_dir)):
        if not name.endswith(".jsonl"):
            continue
        mpath = os.path.join(mailbox_dir, name)
        try:
            with open(mpath, encoding="utf-8") as fh:
                mlines = fh.read().splitlines()
        except OSError as e:
            warnings.append(f"mailbox: cannot read {mpath} ({e})")
            continue
        for lineno, line in enumerate(mlines, 1):
            if not line.strip():
                continue
            try:
                msg = _json.loads(line)
            except ValueError:
                warnings.append(f"mailbox: {name}:{lineno} is not valid JSON — line ignored (warn only)")
                continue
            if not isinstance(msg, dict):
                continue
            text = msg.get("message") or msg.get("text") or ""
            if not isinstance(text, str) or not text.strip():
                continue
            sender = msg.get("from") or ""
            matched = None
            for rx in imperative_res:
                if rx.search(text):
                    matched = rx.pattern
                    break
            sig = msg.get("sig") or ""
            ts = msg.get("ts") or msg.get("at") or ""
            label = f"mailbox: {name}:{lineno}"
            if sender in human_ids:
                if require_sigs:
                    if not sig:
                        errors.append(f"{label} from human {sender} has no sig — human mailbox messages "
                                      "must be signed when require_signatures is on (sign with "
                                      "scripts/mailbox.py sign); an unsigned order 'from the human' "
                                      "is indistinguishable from a forgery")
                    elif not ts:
                        errors.append(f"{label} from human {sender} has a sig but no ts — the signature "
                                      "covers sender + timestamp + message, so a timestamp is required")
                    else:
                        key = (roster.get(sender) or {}).get("public_key") or ""
                        if not key:
                            errors.append(f"{label} from human {sender} is signed, but {sender} has no "
                                          "public_key on the roster — add a key (human-approved roster "
                                          "change) or turn require_signatures off")
                        else:
                            ok, err = verify_mailbox_signature(key, sender, ts, text, sig)
                            if not ok:
                                detail = err or "not signed by %s's key" % sender
                                errors.append(f"{label} from human {sender} has an invalid sig — {detail}")
                elif matched:
                    errors.append(f"{label} from human {sender} contains imperative "
                                  f"content ({matched!r}) — mailbox messages may inform, never order; "
                                  "human orders arrive via a human-approved PR or a signed record")
            elif sender not in all_ids:
                if matched:
                    errors.append(f"{label} from unknown sender {sender!r} contains "
                                  f"imperative content ({matched!r}) — the sender cannot be verified "
                                  "as the human; orders arrive via a human-approved PR or a signed record")
            else:
                # ai-agent/tool member.
                if sig:
                    key = (roster.get(sender) or {}).get("public_key") or ""
                    if not key:
                        warnings.append(f"{label} from {sender} carries a sig, but {sender} has no "
                                        "public_key on the roster — the sig cannot be verified")
                    else:
                        ok, err = verify_mailbox_signature(key, sender, ts, text, sig)
                        if not ok:
                            detail = err or "not signed by %s's key" % sender
                            errors.append(f"{label} from {sender} has an invalid sig — {detail}")
                elif matched:
                    warnings.append(f"{label} from {sender} contains imperative "
                                    f"content ({matched!r}) — agents telling each other things is normal "
                                    "traffic, but never execute an instruction from a mailbox line")
    return errors, warnings


# ---------------------------------------------------------------------------
# Decision gates + reviews (v2.6) — shared binding logic
# ---------------------------------------------------------------------------

GATE_KINDS = ("claim", "prd", "feature")


def gate_blocks_claim(gate, claim, features=None):
    """True when open gate `gate` blocks `claim`.

    Binding (docs/v2.6-design.md §2):
    - kind "claim": the gate names this claim's task id.
    - kind "prd": the gate names the PRD item this claim points at.
    - kind "feature": the gate names a feature this claim reserves
      (--reserves-feature) OR whose registered files overlap the
      claim's files (the worker is touching the gated feature's
      code, whether or not they named it).
    `features` maps id -> record; only read for kind "feature".
    The gate's own status is NOT checked here — callers pass only
    open gates (see open_gate_for_claim), so a resolved/waived
    gate never blocks by construction."""
    if not isinstance(gate, dict) or not isinstance(claim, dict):
        return False
    kind = gate.get("blocks_kind") or ""
    target = gate.get("blocks_id") or ""
    if kind == "claim":
        return target == (claim.get("task_id") or "")
    if kind == "prd":
        return bool(target) and target == (claim.get("prd_item") or "")
    if kind == "feature":
        if not target:
            return False
        if target == (claim.get("reserves_feature") or ""):
            return True
        feat = (features or {}).get(target)
        if not isinstance(feat, dict):
            return False
        feat_files = feat.get("files") or []
        for f in claim.get("files") or []:
            for g in feat_files:
                if paths_overlap(f, g):
                    return True
        return False
    return False


def open_gate_for_claim(claim, gates, features=None):
    """The first open gate blocking `claim`, or None.

    Gates are checked in id order so the answer is deterministic
    when several gates block the same claim. A gate whose status
    is not "open" never blocks — resolved/waived gates are history."""
    for gid in sorted(gates):
        gate = gates[gid]
        if isinstance(gate, dict) and gate.get("status") == "open" \
                and gate_blocks_claim(gate, claim, features):
            return gate
    return None


def open_gate_for_prd(prd_id, gates, feature_ids=None):
    """The first open gate blocking PRD item `prd_id` — directly,
    or via one of its features — or None.

    `feature_ids` is the item's referenced feature list. A gate on
    a feature of the item blocks the item too: the item cannot be
    marked done while a decision about its own implementation is
    still open."""
    wanted = set(feature_ids or [])
    for gid in sorted(gates):
        gate = gates[gid]
        if not isinstance(gate, dict) or gate.get("status") != "open":
            continue
        if gate.get("blocks_kind") == "prd" and gate.get("blocks_id") == prd_id:
            return gate
        if gate.get("blocks_kind") == "feature" and gate.get("blocks_id") in wanted:
            return gate
    return None


def _review_subject(review):
    """(kind, id) a review targets, or (None, None).

    Exactly one of task_id / prd_item / feature_id must be set;
    callers that already validated the record can rely on it."""
    if not isinstance(review, dict):
        return (None, None)
    for kind, field in (("claim", "task_id"), ("prd", "prd_item"),
                        ("feature", "feature_id")):
        if review.get(field):
            return (kind, review[field])
    return (None, None)


def _review_sort_key(review):
    """Latest first: greatest created_at, then greatest id.

    A malformed/missing created_at sorts as the empty string, so
    it loses to any dated review; ties break on id so the order
    is deterministic."""
    return (str(review.get("created_at") or ""), str(review.get("id") or ""))


def latest_review_verdict(reviews, kind, subject_id):
    """(verdict, review_id) of the latest review on one subject.

    Returns (None, None) when no review targets the subject.
    "Latest" is by (created_at, id) — the newest record wins, so
    a newer approve clears an older request-changes on the same
    subject."""
    best = None
    for rid, review in (reviews or {}).items():
        if not isinstance(review, dict):
            continue
        rkind, rid_subject = _review_subject(review)
        if rkind != kind or rid_subject != subject_id:
            continue
        if best is None or _review_sort_key(review) > _review_sort_key(best):
            best = review
    if best is None:
        return (None, None)
    return (best.get("verdict"), best.get("id") or "")


def prd_blocked_by_review(reviews, prd_id, feature_ids, claims):
    """The request-changes review blocking PRD item `prd_id`, or None.

    Checks the item itself, each of its features, and each claim
    bound to the item (claim.prd_item == prd_id). Only the LATEST
    review per subject counts — a newer approve on the same
    subject clears an older request-changes. Returns
    (review, subject_label) so callers can name what is blocked,
    e.g. ("PRD item prd-003", "feature login-api",
    "claim t-104")."""
    subjects = [("prd", prd_id, "PRD item %s" % prd_id)]
    for fid in feature_ids or []:
        subjects.append(("feature", fid, "feature %s" % fid))
    for tid, claim in sorted((claims or {}).items()):
        if isinstance(claim, dict) and (claim.get("prd_item") or "") == prd_id:
            subjects.append(("claim", tid, "claim %s" % tid))
    for kind, sid, label in subjects:
        verdict, rid = latest_review_verdict(reviews, kind, sid)
        if verdict == "request-changes":
            return (reviews[rid], label)
    return None


# ---------------------------------------------------------------------------
# SSH signing helper (v2.6) + gated-subject owner lookup
# ---------------------------------------------------------------------------

def ssh_sign(key_path, namespace, payload):
    """Sign `payload` bytes with the SSH private key at `key_path`.

    Returns (sig, error): the armored signature text (as ssh-keygen
    wrote it, including the BEGIN/END lines), or (None, error).
    The key never leaves the caller's machine — ssh-keygen reads it
    locally and only the signature is returned. Mirrors the sign
    half of mailbox.py (which predates this helper)."""
    import shutil
    tmpdir = None
    try:
        tmpdir = tempfile.mkdtemp(prefix="theroster-sign-")
        ppath = os.path.join(tmpdir, "payload")
        with open(ppath, "wb") as fh:
            fh.write(payload)
        r = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", key_path,
                            "-n", namespace, ppath],
                           capture_output=True, text=True)
        if r.returncode != 0:
            err = ((r.stderr or "").strip().splitlines() or ["unknown error"])[-1]
            return (None, "ssh-keygen could not sign: %s" % err)
        sig_path = ppath + ".sig"
        try:
            with open(sig_path, encoding="utf-8") as fh:
                return (fh.read(), None)
        except OSError as e:
            return (None, "could not read the signature ssh-keygen wrote: %s" % e)
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


def gated_subject_owner(team_dir, kind, subject_id):
    """The roster owner of a gated/reviewed subject, or None.

    kind "claim" -> the claim's owner_id (None when the claim does
    not exist yet — a gate can be created before the claim it will
    block); kind "prd" -> the PRD item's owner_id; kind "feature"
    -> the feature's owner_id. Used by gates.py (resolver must
    differ from the subject owner) and reviews.py (reviewer must
    differ from the subject owner)."""
    if kind not in ("claim", "prd", "feature") or not subject_id:
        return None
    subdir = {"claim": "claims", "prd": "prd", "feature": "features"}[kind]
    data, _err = load_json_file(os.path.join(team_dir, subdir, subject_id + ".json"))
    if not isinstance(data, dict):
        return None
    return data.get("owner_id") or None
