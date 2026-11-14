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
- paths are compared canonically (lowercased); exact file paths only.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
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
    "skill_version": "2.0.0",
    "claim_ttl_minutes": 120,
    "heartbeat_minutes": 30,
    "grace_minutes": 15,
    "max_claims_per_agent": 5,
    "max_files_per_claim": 10,
    "branch_freshness_commits": 20,
    "branch_freshness_days": 7,
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


def branch_freshness(repo_root, remote, branch):
    """How far the checked-out branch is behind <remote>/<branch>.

    Returns (behind_commits, age_days, note). behind/age are None when
    the remote branch cannot be resolved (no remote = skip with note).
    Age = days since the oldest commit on this branch that the remote
    does not have; 0 when there is no such commit."""
    ref = "%s/%s" % (remote, branch)
    r = run_git(["rev-parse", "--verify", "--quiet", ref], cwd=repo_root)
    if r.returncode != 0:
        return None, None, "no remote branch %s to compare against" % ref
    r = run_git(["rev-list", "--count", "HEAD..%s" % ref], cwd=repo_root)
    try:
        behind = int(r.stdout.strip())
    except ValueError:
        return None, None, "could not count commits behind %s" % ref
    r = run_git(["log", "%s..HEAD" % ref, "--reverse", "--format=%aI", "-n", "1"],
                cwd=repo_root)
    first = r.stdout.strip().splitlines()
    if not first:
        return behind, 0.0, ""
    dt = parse_time(first[0])
    if dt is None:
        return behind, 0.0, ""
    age_days = (now() - dt).total_seconds() / 86400.0
    return behind, age_days, ""


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
    still heartbeating."""
    cfg = cfg or DEFAULT_CONFIG
    at = at or now()
    if claim.get("status") != "active":
        return False
    exp = parse_time(claim.get("expires_at") or "")
    if exp is None:
        return False
    grace = timedelta(minutes=cfg.get("grace_minutes", 15))
    if at <= exp + grace:
        return False
    return is_heartbeat_stale(claim, at, cfg.get("heartbeat_minutes", 30))


def is_blocking(claim, at=None, cfg=None):
    """A claim blocks others while it is active and not stealable."""
    return claim.get("status") == "active" and not is_stealable(claim, at, cfg)


def claim_order_key(claim):
    """Causal order of two claims: (lamport, owner_id). Wall-clock
    timestamps NEVER decide "who claimed first" — a skewed clock can
    be 10 minutes in the future. The claim with the higher lamport
    came later; on a lamport tie, the higher owner_id (lexicographic)
    is the later one, so the order is deterministic."""
    try:
        lamport = int(claim.get("lamport") or 0)
    except (TypeError, ValueError):
        lamport = 0
    return (lamport, str(claim.get("owner_id") or ""))


def claim_status_note(claim, at=None, cfg=None):
    """One-line human note about a claim's liveness (for list output)."""
    cfg = cfg or DEFAULT_CONFIG
    at = at or now()
    if claim.get("status") != "active":
        return "status=%s" % claim.get("status")
    if is_stealable(claim, at, cfg):
        return "STEALABLE (expired + heartbeat stale)"
    exp = parse_time(claim.get("expires_at") or "")
    if exp is not None and at > exp:
        return "expired but heartbeat fresh — still blocking until heartbeat goes stale"
    return "active"


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
    try:
        os.makedirs(team_dir, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as e:
        print(f"team_common: warning — could not append to {path}: {e}", file=sys.stderr)


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


def team_dir_of(subdir):
    """team/claims -> team ; team -> team."""
    subdir = os.path.normpath(subdir)
    base = os.path.basename(subdir)
    if base in ("roster", "claims", "features", "mailbox", "handoffs"):
        return os.path.dirname(subdir) or "."
    return subdir
