#!/usr/bin/env python3
"""Feature registry for team-roster v2: search before you build, reuse what
exists, register what you build, never delete what others use.

Sharded layout (v2, BREAKING vs v1): one file per feature in
team/features/<id>.json. team/index.json is GENERATED
(scripts/rebuild_index.py), never hand-edited. team/events.jsonl is
the append-only audit log.

Usage:
    python3 features.py list   [team/features]
    python3 features.py search <query> [team/features]
    python3 features.py check  [team/features] [--roster team/roster]
                               [--repo-root .]
    python3 features.py register --id login-api --name "Login API" \
        --does "Login with email + password, returns a session token" \
        --entry src/auth/login.py:login \
        --files src/auth/login.py tests/test_login.py \
        --owner rian --keywords login auth sesi \
        [--kind code] [--status experimental] [--version 1.0.0] \
        [--breaking false] [--breaking-notes ""] [--canonical] \
        [--contract] [--depends-on login-api@1.0.0] \
        [--reviewer human-1] [--features-dir team/features]
    python3 features.py use --id login-api --by t-002 [--features-dir team/features]
    python3 features.py improve --id login-api --by rean --bump minor \
        --note "Tambah login Google" --breaking false \
        [--does "..."] [--entry ...] [--files ...] [--keywords ...] \
        [--breaking-notes "..."] [--features-dir team/features]
    python3 features.py deprecate --id old-login --by rian --reason "..." \
        [--replacement login-api] [--features-dir team/features]
    python3 features.py cancel --id old-login --by rian --reason "PRD dropped this" \
        [--features-dir team/features]
    python3 features.py reverify --id login-api --by rean [--features-dir team/features]
    python3 features.py promote --id login-api --by rean --reviewer rian \
        --reason "reviewed, tested" [--features-dir team/features]

Rules:
- New registrations default to experimental, NOT stable. A stable
  registration needs --status stable TOGETHER WITH --reviewer, and the
  reviewer must differ from the owner (same rule as promote).
- Status transitions allowed: experimental->stable, stable->deprecated,
  experimental->deprecated, experimental->cancelled, stable->cancelled.
  deprecated->stable is FORBIDDEN — the script rejects it and says it
  needs a human-approved PR. done and cancelled are TERMINAL for PRD
  items (prd.py) and the same idea holds here: a cancelled feature
  never comes back; build a replacement instead. Every transition
  records status_reason + status_changed_by + status_changed_at in
  the same write. Cancelling (v2.2) also releases every ACTIVE claim
  touching the feature's files in the same push — a cancellation
  stops the work, it does not leave the worker to discover it.
- Contracts and dependencies (v2.2): a feature with --contract is a
  shared interface. Its entry file should carry a
  "# roster-contract: <id>" comment (warned, not failed, when
  missing). Changing a contract feature's entry needs BOTH --bump
  major AND --contract-change "<what breaks and who must adapt>".
  --depends-on id@version records the version the feature was
  verified against; when a dependency bumps MAJOR, validate.py warns
  (or FAILs with strict_deps=true in team/config.json) until the
  dependent is re-verified (features.py reverify refreshes the
  recorded versions). Old records without contract/depends_on are
  read as contract=false with no dependencies.
- Dependency cycles (v2.5): a cycle (A depends on B depends on A,
  or any longer loop) FAILS every check — register/improve refuse
  the edge that would close the loop, check/validate/reverify fail
  on a loop already in the files. A cycle can never be
  topologically sorted, so any tool that tries loops forever.
- SemVer, enforced mechanically: valid MAJOR.MINOR.PATCH, no leading
  zeros, reset-to-zero on bump, monotonic increase. 0.y.z is always
  experimental (forced on register/improve). A pre-release version
  (e.g. 1.6.0-branch.1) can never be stable. --breaking true/false +
  --breaking-notes are mandatory on register and improve.
- Entry validation (check): for kind=code, entry is file:function —
  the file must exist in the tree, the named symbol must exist
  (ast parse for def/class, regex fallback — a HEURISTIC, not a
  proof the function does what the description says), and
  entry_blob_sha must match the file's current git blob sha
  (sha1 of "blob <len>\\0" + bytes). Mismatch = FAIL "suspect — run
  features.py reverify --id <id> to re-verify after reading the new
  code". For non-code kinds, entry is a pointer (doc URL or repo
  path); a path pointer must exist, a URL is accepted as-is.
- Canonical rule: two stable features sharing >=1 keyword FAIL unless
  exactly one of them is canonical.
- Search is deterministic, top-5: score (id match 3, name/keyword
  match 2, description/keyword-substring match 1), then status rank
  stable>experimental>deprecated, then version desc, then len(used_by)
  desc, then id asc. The printed rank + score are the decision
  record — reuse the top-ranked stable match.
- Every mutating command bumps lamport (+1), appends one line to
  team/events.jsonl, and rebuilds team/index.json.
- claim.py reserves a feature id BEFORE any code is written
  (--reserves-feature); register in the same push as the claim.

Exit 0 = ok / found. Exit 1 = invalid, duplicate, or not found.
Search exit 0 even with no matches — "not found" is a valid answer
that means "you may build it".
Stdlib only, Python 3.9+.
"""
import argparse
import ast
import json
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import team_common as tc

STATUSES = {"stable", "experimental", "deprecated", "cancelled"}
KINDS = {"code", "doc", "design", "process"}
STATUS_RANK = {"stable": 0, "experimental": 1, "deprecated": 2, "cancelled": 3}
ALLOWED_TRANSITIONS = {
    ("experimental", "stable"),
    ("stable", "deprecated"),
    ("experimental", "deprecated"),
    ("experimental", "cancelled"),
    ("stable", "cancelled"),
}


def feature_path(features_dir, fid):
    return os.path.join(features_dir, fid + ".json")


def _guard_mutation(features_dir, action):
    """Error string when features_dir is inside a linked worktree, else None.

    team/ state is only written from the main checkout (v2.3,
    Dziatkovskii rule) — a mutation from inside a worktree lands on
    the worktree branch and is invisible to the rest of the team."""
    return tc.guard_team_mutation(tc.team_dir_of(features_dir), action)


def _resolve_features_dir(features_dir):
    """Redirect a features dir inside a linked worktree to the main
    checkout's features dir (reads only; prints a note on stderr)."""
    team_dir = tc.team_dir_of(features_dir)
    team_dir, note = tc.resolve_team_dir(team_dir)
    if note:
        print("FEATURES: note — " + note, file=sys.stderr)
        return os.path.join(team_dir, "features")
    return features_dir


def load_roster_active(roster_dir):
    if not roster_dir or not os.path.isdir(roster_dir):
        return None, "roster dir not found: %s" % roster_dir
    active = set()
    for _stem, (_path, rec) in tc.load_records(roster_dir).items():
        if isinstance(rec, dict) and rec.get("status") == "active" and rec.get("id"):
            active.add(rec["id"])
    return active, None


def load_features(features_dir):
    recs = {}
    for stem, (_path, rec) in tc.load_records(features_dir).items():
        if isinstance(rec, dict):
            recs[stem] = rec
    return recs


def rebuild(team_dir):
    try:
        import rebuild_index
        rebuild_index.rebuild(team_dir)
    except Exception as e:
        print(f"FEATURES: warning — could not rebuild team/index.json: {e}", file=sys.stderr)


def mutate(features_dir, fid, actor, event, updater, extra=None):
    """Load, update (bumps lamport), save, log event, rebuild index.
    Returns (record, None) or (None, error)."""
    path = feature_path(features_dir, fid)
    data, err = tc.load_json_file(path)
    if err:
        return None, "no feature with id %s (%s)" % (fid, err)
    if not isinstance(data, dict):
        return None, "feature %s is not a JSON object" % fid
    updater(data)
    data["lamport"] = int(data.get("lamport") or 0) + 1
    tc.save_json(path, data)
    team_dir = tc.team_dir_of(features_dir)
    tc.append_event(team_dir, event, data["lamport"], actor,
                    base_commit=tc.git_head_commit(), feature_id=fid, extra=extra)
    rebuild(team_dir)
    return data, None


def check_entry(entry, kind, repo_root):
    """Validate an entry pointer. Returns an error string, or None.

    kind=code: entry must be file:function; file must exist; symbol
    must exist (ast, regex fallback — heuristic); blob sha is checked
    separately by the caller when a record carries entry_blob_sha.
    Other kinds: entry is a pointer — an http(s) URL is accepted
    as-is; anything else is treated as a repo path and must exist.
    """
    if not entry:
        return "entry is empty"
    if kind == "code":
        if ":" not in entry:
            return "code entry must be file:function (got %r)" % entry
        fpath, symbol = entry.rsplit(":", 1)
        if not fpath or not symbol:
            return "code entry must be file:function (got %r)" % entry
        full = os.path.join(repo_root, fpath)
        if not os.path.isfile(full):
            return "entry file does not exist: %s" % fpath
        sym_err = check_symbol(full, symbol)
        if sym_err:
            return sym_err
        return None
    if re.match(r"^https?://", entry):
        return None
    full = os.path.join(repo_root, entry)
    if not os.path.isfile(full) and not os.path.isdir(full):
        return "entry pointer does not exist as a repo path: %s" % entry
    return None


def check_symbol(path, symbol):
    """Heuristic: symbol defined as a def/class in the file. ast first,
    regex fallback for files ast cannot parse. Returns error or None."""
    try:
        with open(path, encoding="utf-8") as f:
            source = f.read()
    except OSError as e:
        return "cannot read entry file %s: %s" % (path, e)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                    and node.name == symbol:
                return None
        return ("symbol %r is not defined (def/class) in %s — ast parse found no match "
                "(heuristic check; confirm by reading the file)" % (symbol, path))
    if re.search(r"^\s*(def|class)\s+" + re.escape(symbol) + r"\b", source, re.M):
        return None
    return ("symbol %r not found in %s by regex fallback "
            "(heuristic check; confirm by reading the file)" % (symbol, path))


def _set_status_fields(rec, by, reason):
    rec["status_reason"] = reason
    rec["status_changed_by"] = by
    rec["status_changed_at"] = tc.now_iso()


# ---------------------------------------------------------------- commands

def parse_depends_on(raw_list, features_dir, self_id):
    """Parse --depends-on id@version entries. Returns (deps, error).
    A dependency must be registered already, and the recorded version
    may not be newer than the dependency's current version — you
    cannot depend on a version that does not exist yet."""
    deps, seen = [], set()
    feats = load_features(features_dir)
    for raw in raw_list or []:
        if "@" not in raw:
            return None, f"depends-on entry {raw!r} must be id@version (e.g. login-api@1.0.0)"
        did, _, ver = raw.rpartition("@")
        if not tc.valid_id(did):
            return None, f"depends-on id {did!r} fails the id grammar"
        if tc.parse_semver(ver) is None:
            return None, f"depends-on version {ver!r} is not SemVer MAJOR.MINOR.PATCH"
        if did == self_id:
            return None, "a feature cannot depend on itself"
        if did not in feats:
            return None, f"depends on {did}, which is not registered — register the dependency first"
        cur = tc.parse_semver(feats[did].get("version"))
        if cur is not None and tc.parse_semver(ver) > cur:
            return None, (f"depends on {did} v{ver} but {did} is only v{feats[did].get('version')} — "
                          "you cannot depend on a version that does not exist yet")
        if did not in seen:
            seen.add(did)
            deps.append({"id": did, "version": ver})
    deps.sort(key=lambda d: d["id"])
    return deps, None


def merge_depends_on(existing, new):
    """Union of old and new dependencies; a repeated id takes the new
    (recorded) version."""
    by_id = {d["id"]: d["version"] for d in (existing or [])
             if isinstance(d, dict) and d.get("id")}
    for d in new or []:
        by_id[d["id"]] = d["version"]
    return [{"id": i, "version": by_id[i]} for i in sorted(by_id)]


def cmd_list(features_dir):
    features_dir = _resolve_features_dir(features_dir)
    feats = load_features(features_dir)
    if not feats:
        print("FEATURES: registry is empty — nothing built yet. Build, then register it.")
        return 0
    print(f"FEATURES: {len(feats)} feature(s):")
    for fid in sorted(feats):
        f = feats[fid]
        tag = " [contract]" if f.get("contract") is True else ""
        print(f"  - {fid} [{f.get('status')}] v{f.get('version')}{tag} — {f.get('does')} "
              f"(entry: {f.get('entry')}, used by: {', '.join(f.get('used_by', [])) or 'nobody yet'})")
    return 0


def match_score(feat, query):
    q = tc.normalize_keyword(query)
    if not q:
        return 0
    if q in tc.normalize_keyword(feat.get("id", "")):
        return 3
    if q in tc.normalize_keyword(feat.get("name", "")):
        return 2
    if any(q == tc.normalize_keyword(k) for k in feat.get("keywords", [])):
        return 2
    text = " ".join([feat.get("does", ""), " ".join(feat.get("keywords", []))])
    if q in tc.normalize_keyword(text):
        return 1
    return 0


def rank_key(item):
    score, feat = item
    version = tc.parse_semver(feat.get("version")) or (0, 0, 0, (1,))
    return (-score, STATUS_RANK.get(feat.get("status"), 9), _neg_version(version),
            -len(feat.get("used_by") or []), feat.get("id", ""))


def _neg_version(key):
    """Negate a SemVer key for descending sort. Only the numeric
    (major, minor, patch) parts are negated; pre-release order is kept
    as-is (a release still sorts above its pre-release)."""
    return (-key[0], -key[1], -key[2], key[3])


def cmd_search(features_dir, query):
    feats = load_features(features_dir)
    scored = [(match_score(f, query), f) for f in feats.values()]
    hits = sorted([s for s in scored if s[0] > 0], key=rank_key)[:5]
    if not hits:
        print(f"FEATURES: no match for '{query}' — you may build it, then register it.")
        print("Try 2-3 different keywords first (e.g. login, then auth, then sesi) — one empty search does not prove it does not exist.")
        return 0
    print(f"FEATURES: {len(hits)} match(es) for '{query}' (top-5, deterministic rank) — REUSE, do not rebuild:")
    for rank, (score, f) in enumerate(hits, 1):
        print(f"  #{rank} score={score} {f.get('id')} [{f.get('status')}] v{f.get('version')} — {f.get('does')}")
        print(f"      entry: {f.get('entry')} | owner: {f.get('owner_id')} | used by: {', '.join(f.get('used_by', [])) or 'nobody yet'}")
        if f.get("status") == "deprecated":
            print("      WARNING: deprecated — do not use for new work.")
        elif f.get("status") == "cancelled":
            print(f"      WARNING: cancelled ({f.get('status_reason')}) — do not rebuild it; it was deliberately killed.")
        elif f.get("status") == "experimental":
            print("      NOTE: experimental — use with care, or improve it in place instead of copying it.")
        if f.get("contract") is True:
            print("      CONTRACT: shared interface — changing its entry needs --contract-change plus --bump major.")
        if f.get("depends_on"):
            print("      depends on: " + ", ".join("%s@%s" % (d.get("id"), d.get("version")) for d in f["depends_on"]))
    return 0


def cmd_check(features_dir, roster_path, repo_root):
    team_dir = tc.team_dir_of(features_dir)
    eff_team, note = tc.resolve_team_dir(team_dir)
    if note:
        print("FEATURES: note — " + note, file=sys.stderr)
        features_dir = os.path.join(eff_team, "features")
        if roster_path and os.path.abspath(roster_path).startswith(os.path.abspath(team_dir) + os.sep):
            roster_path = os.path.join(eff_team, "roster")
    feats = load_features(features_dir)
    errors, warnings = [], []
    roster_ids = None
    if roster_path:
        roster_ids, err = load_roster_active(roster_path)
        if err:
            errors.append("cannot read roster %s: %s" % (roster_path, err))
            roster_ids = None
    seen_entries = {}
    for fid, f in sorted(feats.items()):
        label = fid
        if not tc.valid_id(fid):
            errors.append(f"{label}: id fails the id grammar ^[a-z0-9][a-z0-9-]*$ (max 64 chars, ASCII only)")
        if f.get("id") != fid:
            errors.append(f"{label}: id inside the file ({f.get('id')!r}) does not match the filename — one feature per file, named <id>.json")
        for field in ("id", "name", "does", "entry", "keywords", "owner_id",
                      "status", "version", "last_verified", "lamport",
                      "status_reason", "status_changed_by", "status_changed_at",
                      "breaking"):
            # NOTE: "breaking_notes" is intentionally NOT in this list —
            # it is legitimately empty when breaking=false.
            if field not in f or f[field] in (None, ""):
                errors.append(f"{label}: missing/empty field: {field}")
        if f.get("kind") not in KINDS:
            errors.append(f"{label}: kind must be one of {sorted(KINDS)} (got {f.get('kind')!r})")
        if f.get("status") not in STATUSES:
            errors.append(f"{label}: status must be stable, experimental, deprecated, or cancelled (got {f.get('status')!r})")
        if "contract" in f and not isinstance(f.get("contract"), bool):
            errors.append(f"{label}: contract must be true or false")
        for dep in f.get("depends_on") or []:
            if (not isinstance(dep, dict) or not tc.valid_id(dep.get("id") or "")
                    or tc.parse_semver(dep.get("version") or "") is None):
                errors.append(f"{label}: depends_on entries must be {{\"id\": <id>, \"version\": <SemVer>}} (got {dep!r})")
        version = f.get("version")
        if version and tc.parse_semver(version) is None:
            errors.append(f"{label}: version must be SemVer MAJOR.MINOR.PATCH, no leading zeros (got {version!r})")
        if version and str(version).startswith("0.") and f.get("status") == "stable":
            errors.append(f"{label}: 0.y.z is always experimental, but status is stable")
        if version and tc.is_prerelease(version) and f.get("status") == "stable":
            errors.append(f"{label}: pre-release version {version} can never be stable")
        if f.get("breaking") not in (True, False):
            errors.append(f"{label}: breaking must be true or false")
        if f.get("breaking") is True and not f.get("breaking_notes"):
            errors.append(f"{label}: breaking=true needs non-empty breaking_notes")
        changes = f.get("changes") or []
        if not isinstance(changes, list) or not changes:
            errors.append(f"{label}: changes must be a non-empty list")
        elif changes[-1].get("version") != version:
            errors.append(f"{label}: version {version} does not match the latest changes entry ({changes[-1].get('version')})")
        if roster_ids is not None:
            if f.get("owner_id") not in roster_ids:
                errors.append(f"{label}: owner {f.get('owner_id')} is not an active roster member")
            for who in f.get("contributors") or []:
                if who not in roster_ids:
                    errors.append(f"{label}: {who} (contributors) is not an active roster member")
            # used_by holds TASK ids (features.py use --by <task-id>),
            # not roster ids — only check they are id-shaped.
            for who in f.get("used_by") or []:
                if not tc.valid_id(who):
                    errors.append(f"{label}: {who} (used_by) is not a valid task id")
        entry = f.get("entry", "")
        if entry in seen_entries:
            errors.append(f"{label}: duplicate entry {entry} — already used by {seen_entries[entry]}. Reuse that feature instead of registering a second one.")
        else:
            seen_entries[entry] = fid
        # Entry validation: file + symbol + blob sha (code kind).
        # Cancelled features are dead history — their code may be
        # gone — so they are exempt from live entry verification.
        # The entry stays registered, so nobody rebuilds the same
        # thing by accident. The duplicate-entry check above still runs.
        if f.get("status") != "cancelled":
            kind = f.get("kind") or "code"
            err = check_entry(entry, kind, repo_root)
            if err:
                errors.append(f"{label}: entry invalid: {err}")
            elif kind == "code":
                fpath = entry.rsplit(":", 1)[0]
                full = os.path.join(repo_root, fpath)
                want = f.get("entry_blob_sha")
                if not want:
                    errors.append(f"{label}: missing entry_blob_sha — run: python3 features.py reverify --id {fid} --by <you>")
                else:
                    have = tc.git_blob_sha(full)
                    if have is None:
                        errors.append(f"{label}: cannot read entry file {fpath} to verify its blob sha")
                    elif have != want:
                        errors.append(f"{label}: entry file changed since registration (blob sha mismatch) — "
                                      f"suspect — run features.py reverify --id {fid} to re-verify after reading the new code")
            # Contract marker: the entry file should point back at the
            # registry, so a reader of the code finds its contract.
            # Warn-only — the contract flag itself is the control.
            # Skipped when the entry is already invalid (that error
            # is reported above; the marker check needs a read file).
            if f.get("contract") is True and kind == "code" and not err:
                fpath = entry.rsplit(":", 1)[0]
                full = os.path.join(repo_root, fpath)
                try:
                    with open(full, encoding="utf-8", errors="ignore") as fh:
                        text = fh.read()
                except OSError:
                    text = ""
                if not re.search(r"roster-contract:\s*" + re.escape(fid) + r"\b", text):
                    warnings.append(f"{label}: contract feature but the entry file has no "
                                    f"'roster-contract: {fid}' marker — add it as a comment so the code points back at the registry")
        if f.get("status") == "deprecated" and f.get("used_by"):
            warnings.append(f"{label}: deprecated but still used by {', '.join(f['used_by'])} — do not delete it.")
        lv = f.get("last_verified", "")
        if lv and tc.parse_date(lv) is None:
            errors.append(f"{label}: last_verified must be YYYY-MM-DD (got {lv!r})")
    # Canonical rule: two stable features sharing >=1 keyword FAIL
    # unless exactly one of them is canonical.
    by_keyword = {}
    for fid, f in feats.items():
        if f.get("status") != "stable":
            continue
        for kw in f.get("keywords") or []:
            by_keyword.setdefault(tc.normalize_keyword(kw), []).append(fid)
    for kw, ids in sorted(by_keyword.items()):
        if len(ids) > 1:
            canon = [i for i in ids if feats[i].get("canonical") is True]
            if len(canon) != 1:
                errors.append(f"canonical rule: stable features {', '.join(sorted(ids))} share keyword {kw!r} "
                              f"but {len(canon)} of them are canonical (exactly one must be) — "
                              "mark one canonical, or deprecate the others")
    # Dependency cycles (v2.5): a cycle can never be topologically
    # sorted, so any future tool that tries loops forever. The loop
    # is named so the fixer knows which edge to cut.
    cycle = tc.find_dependency_cycle(feats)
    if cycle:
        errors.append("dependency cycle: %s — break the loop (remove one depends_on edge); "
                      "a cycle can never be topologically sorted" % " -> ".join(cycle))
    for w in warnings:
        print(f"  ! {w}")
    if errors:
        print("FEATURES CHECK: FAIL — do not push until this is fixed:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"FEATURES CHECK: OK — {len(feats)} feature(s), entries verified (file + symbol + blob sha), no duplicate id or entry.")
    return 0


def _require_breaking(args):
    """--breaking is mandatory on register and improve. argparse gives
    it as a string 'true'/'false'; returns (bool, error)."""
    if args.breaking is None:
        return None, "--breaking true|false is mandatory"
    low = str(args.breaking).lower()
    if low not in ("true", "false"):
        return None, "--breaking must be true or false"
    return low == "true", None


def cmd_register(args):
    guard = _guard_mutation(args.features_dir, "register a feature")
    if guard:
        print("FEATURES: FAIL — " + guard)
        return 1
    features_dir = args.features_dir
    os.makedirs(features_dir, exist_ok=True)
    team_dir = tc.team_dir_of(features_dir)
    if not tc.valid_id(args.id):
        print(f"FEATURES: FAIL — id {args.id!r} fails the id grammar ^[a-z0-9][a-z0-9-]*$ (max 64 chars, ASCII only)")
        return 1
    breaking, err = _require_breaking(args)
    if err:
        print("FEATURES: FAIL — " + err)
        return 1
    if breaking and not args.breaking_notes:
        print("FEATURES: FAIL — breaking=true needs --breaking-notes explaining what breaks")
        return 1
    if tc.parse_semver(args.version) is None:
        print(f"FEATURES: FAIL — version {args.version!r} is not SemVer MAJOR.MINOR.PATCH (no leading zeros)")
        return 1
    if args.kind not in KINDS:
        print(f"FEATURES: FAIL — --kind must be one of {sorted(KINDS)}")
        return 1
    status = args.status
    if status == "cancelled":
        print("FEATURES: FAIL — registering as cancelled is not allowed — register the feature, "
              "then cancel it with: python3 features.py cancel --id <id> --by <you> --reason ...")
        return 1
    if status == "stable":
        if not args.reviewer:
            print("FEATURES: FAIL — registering as stable needs --reviewer (a different roster member must review it); "
                  "new registrations default to experimental")
            return 1
        if args.reviewer == args.owner:
            print("FEATURES: FAIL — the reviewer must be different from the owner")
            return 1
        if tc.is_prerelease(args.version):
            print(f"FEATURES: FAIL — pre-release version {args.version} can never be stable")
            return 1
    if str(args.version).startswith("0.") and status == "stable":
        print(f"FEATURES: note — version {args.version} is 0.y.z, always experimental; forcing status to experimental")
        status = "experimental"
    keywords = sorted({tc.normalize_keyword(k) for k in args.keywords if tc.normalize_keyword(k)})
    if not keywords:
        print("FEATURES: FAIL — give at least one --keywords entry")
        return 1
    if args.kind == "code" and not args.files:
        print("FEATURES: FAIL — kind=code needs --files (the files implementing the feature)")
        return 1
    deps, derr = parse_depends_on(args.depends_on, features_dir, args.id)
    if derr:
        print("FEATURES: FAIL — " + derr)
        return 1
    # Cycle prevention (v2.5): refuse an edge that would close a
    # loop — the prospective graph (existing features + this one)
    # is checked before anything is written.
    _prospective = dict(load_features(features_dir))
    _prospective[args.id] = {"depends_on": deps}
    _cycle = tc.find_dependency_cycle(_prospective)
    if _cycle:
        print("FEATURES: FAIL — registering %s with these dependencies would create a cycle: %s — "
              "a cycle can never be topologically sorted; drop one depends_on edge"
              % (args.id, " -> ".join(_cycle)))
        return 1
    path = feature_path(features_dir, args.id)
    if os.path.exists(path):
        print(f"FEATURES: FAIL — id {args.id} already exists. Use it instead of registering a duplicate.")
        print("Run: python3 features.py search " + " ".join(args.keywords))
        return 1
    feats = load_features(features_dir)
    for fid, f in feats.items():
        if f.get("entry") == args.entry:
            print(f"FEATURES: FAIL — entry {args.entry} is already registered as {fid}. Reuse that feature.")
            return 1
    err = check_entry(args.entry, args.kind, args.repo_root)
    if err:
        print(f"FEATURES: FAIL — entry invalid: {err}")
        return 1
    blob_sha = ""
    if args.kind == "code":
        fpath = args.entry.rsplit(":", 1)[0]
        blob_sha = tc.git_blob_sha(os.path.join(args.repo_root, fpath)) or ""
    roster_dir = os.path.join(team_dir, "roster")
    roster_ids, rerr = load_roster_active(roster_dir)
    if rerr:
        print(f"FEATURES: warning — {rerr}; owner not verified against the roster.")
    elif args.owner not in roster_ids:
        print(f"FEATURES: FAIL — owner {args.owner} is not an active roster member")
        return 1
    if args.reviewer and roster_ids is not None and args.reviewer not in roster_ids:
        print(f"FEATURES: FAIL — reviewer {args.reviewer} is not an active roster member")
        return 1
    rec = {
        "id": args.id,
        "name": args.name,
        "kind": args.kind,
        "does": args.does,
        "entry": args.entry,
        "entry_blob_sha": blob_sha,
        "files": sorted(set(args.files)),
        "keywords": keywords,
        "owner_id": args.owner,
        "contributors": [args.owner],
        "status": status,
        "version": args.version,
        "breaking": breaking,
        "breaking_notes": args.breaking_notes or "",
        "canonical": bool(args.canonical),
        "contract": bool(args.contract),
        "depends_on": deps,
        "used_by": [],
        "changes": [{"version": args.version, "by": args.owner,
                     "note": "First version", "date": date.today().isoformat()}],
        "last_verified": date.today().isoformat(),
        "status_reason": "registered as %s" % status + ((" by review from " + args.reviewer) if args.reviewer else ""),
        "status_changed_by": args.reviewer or args.owner,
        "status_changed_at": tc.now_iso(),
        "lamport": 1,
    }
    if args.branch:
        rec["branch"] = args.branch
    tc.save_json(path, rec)
    tc.append_event(team_dir, "feature_registered", 1, args.owner,
                    base_commit=tc.git_head_commit(), feature_id=args.id)
    rebuild(team_dir)
    print(f"FEATURES: registered {args.id} [{status}] v{args.version} — entry {args.entry}.")
    print("Remember: commit + push the feature file so the next AI can find it.")
    return 0


def cmd_use(args):
    guard = _guard_mutation(args.features_dir, "use a feature")
    if guard:
        print("FEATURES: FAIL — " + guard)
        return 1
    data, err = tc.load_json_file(feature_path(args.features_dir, args.id))
    if err or not isinstance(data, dict):
        print("FEATURES: FAIL — " + err)
        return 1
    if data.get("status") == "deprecated":
        print(f"FEATURES: WARNING — {args.id} is deprecated. Do not use it for new work.")
        return 1
    if data.get("status") == "cancelled":
        print(f"FEATURES: WARNING — {args.id} is cancelled ({data.get('status_reason')}). "
              "Do not use it, and do not rebuild it.")
        return 1

    def upd(f):
        if args.by not in f.setdefault("used_by", []):
            f["used_by"].append(args.by)
    rec, err = mutate(args.features_dir, args.id, args.by, "feature_used", upd)
    if err:
        print("FEATURES: FAIL — " + err)
        return 1
    print(f"FEATURES: {args.id} is now used by {args.by} (entry: {rec.get('entry')}).")
    return 0


def cmd_improve(args):
    guard = _guard_mutation(args.features_dir, "improve a feature")
    if guard:
        print("FEATURES: FAIL — " + guard)
        return 1
    breaking, err = _require_breaking(args)
    if err:
        print("FEATURES: FAIL — " + err)
        return 1
    if breaking and not args.breaking_notes:
        print("FEATURES: FAIL — breaking=true needs --breaking-notes explaining what breaks")
        return 1
    if not args.note:
        print("FEATURES: FAIL — --note is required (it goes into the changes log)")
        return 1
    data, err = tc.load_json_file(feature_path(args.features_dir, args.id))
    if err or not isinstance(data, dict):
        print(f"FEATURES: FAIL — no feature with id {args.id}. Search first, then register it if it truly does not exist.")
        return 1
    if data.get("status") == "deprecated":
        print(f"FEATURES: FAIL — {args.id} is deprecated. Improve its replacement instead, or ask a human to approve a new feature.")
        return 1
    if data.get("status") == "cancelled":
        print(f"FEATURES: FAIL — {args.id} is cancelled ({data.get('status_reason')}). "
              "It stays as the record of a decision — do not improve it; build a replacement instead.")
        return 1
    old_version = data.get("version", "1.0.0")
    new_version = tc.bump_version(old_version, args.bump)
    if new_version is None:
        print(f"FEATURES: FAIL — current version {old_version} is not SemVer (x.y.z), cannot bump it.")
        return 1
    if tc.parse_semver(new_version) is not None and tc.parse_semver(new_version) <= tc.parse_semver(old_version):
        print(f"FEATURES: FAIL — bump produced {new_version}, not greater than {old_version}")
        return 1
    if args.entry and args.entry != data.get("entry") and args.bump != "major":
        print(f"FEATURES: FAIL — changing entry {data.get('entry')} -> {args.entry} breaks old callers, so --bump must be major, not {args.bump}.")
        return 1
    entry_changed = bool(args.entry and args.entry != data.get("entry"))
    if entry_changed and data.get("contract") is True and not args.contract_change:
        print(f"FEATURES: FAIL — {args.id} is a CONTRACT (shared interface): changing its entry needs "
              "--contract-change \"<what breaks and who must adapt>\" plus --bump major. "
              "An interface change is a deliberate, recorded decision — not a side effect of an improvement.")
        return 1
    if args.entry:
        for fid, other in load_features(args.features_dir).items():
            if fid != args.id and other.get("entry") == args.entry:
                print(f"FEATURES: FAIL — entry {args.entry} is already used by {fid}.")
                return 1
        err = check_entry(args.entry, data.get("kind") or "code", args.repo_root)
        if err:
            print(f"FEATURES: FAIL — new entry invalid: {err}")
            return 1
    keywords = sorted({tc.normalize_keyword(k) for k in (args.keywords or []) if tc.normalize_keyword(k)})
    deps, derr = parse_depends_on(args.depends_on, args.features_dir, args.id)
    if derr:
        print("FEATURES: FAIL — " + derr)
        return 1
    # Cycle prevention (v2.5): the merged graph (existing deps +
    # the new ones) is checked before writing — an edge that would
    # close a loop is refused, with the loop named.
    if deps:
        _prospective = load_features(args.features_dir)
        _merged = merge_depends_on(data.get("depends_on"), deps)
        _prospective[args.id] = dict(data, depends_on=_merged)
        _cycle = tc.find_dependency_cycle(_prospective)
        if _cycle:
            print("FEATURES: FAIL — improving %s with these dependencies would create a cycle: %s — "
                  "a cycle can never be topologically sorted; drop one depends_on edge"
                  % (args.id, " -> ".join(_cycle)))
            return 1
    forced = None
    if str(new_version).startswith("0.") and data.get("status") == "stable":
        forced = "version %s is 0.y.z, always experimental" % new_version
    if tc.is_prerelease(new_version) and data.get("status") == "stable":
        forced = "pre-release version %s can never be stable" % new_version

    def upd(f):
        if args.entry:
            f["entry"] = args.entry
        if args.does:
            f["does"] = args.does
        if args.files:
            f["files"] = sorted(set(f.get("files", [])) | set(args.files))
        if keywords:
            f["keywords"] = sorted(set(f.get("keywords", [])) | set(keywords))
        if deps:
            f["depends_on"] = merge_depends_on(f.get("depends_on"), deps)
        if args.by not in f.setdefault("contributors", [f.get("owner_id")]):
            f["contributors"].append(args.by)
        f["version"] = new_version
        f["breaking"] = breaking
        f["breaking_notes"] = args.breaking_notes or ""
        f["last_verified"] = date.today().isoformat()
        f.setdefault("changes", []).append({
            "version": new_version, "by": args.by,
            "note": args.note + (" (contract change: %s)" % args.contract_change
                                 if args.contract_change else ""),
            "date": date.today().isoformat(),
        })
        if entry_changed:
            fpath = f["entry"].rsplit(":", 1)[0] if (f.get("kind") or "code") == "code" else ""
            f["entry_blob_sha"] = tc.git_blob_sha(os.path.join(args.repo_root, fpath)) or "" if fpath else ""
        if forced:
            f["status"] = "experimental"
            _set_status_fields(f, args.by, forced)
    rec, err = mutate(args.features_dir, args.id, args.by, "feature_improved", upd)
    if err:
        print("FEATURES: FAIL — " + err)
        return 1
    print(f"FEATURES: {args.id} improved {old_version} -> {new_version} by {args.by} (entry: {rec.get('entry')}).")
    if forced:
        print(f"FEATURES: note — status forced to experimental: {forced}")
    print("Remember: commit + push the feature file so the next AI builds on this version, not the old one.")
    return 0


def cmd_deprecate(args):
    guard = _guard_mutation(args.features_dir, "deprecate a feature")
    if guard:
        print("FEATURES: FAIL — " + guard)
        return 1
    data, err = tc.load_json_file(feature_path(args.features_dir, args.id))
    if err or not isinstance(data, dict):
        print(f"FEATURES: FAIL — no feature with id {args.id}")
        return 1
    if data.get("status") == "deprecated":
        print(f"FEATURES: FAIL — {args.id} is already deprecated")
        return 1
    if (data.get("status"), "deprecated") not in ALLOWED_TRANSITIONS:
        print(f"FEATURES: FAIL — cannot move {data.get('status')} -> deprecated")
        return 1
    old_status = data.get("status")

    def upd(f):
        f["status"] = "deprecated"
        if args.replacement:
            f["replacement"] = args.replacement
        _set_status_fields(f, args.by, args.reason)
        f.setdefault("changes", []).append({
            "version": f.get("version"), "by": args.by,
            "note": "Deprecated: %s" % args.reason, "date": date.today().isoformat(),
        })
    rec, err = mutate(args.features_dir, args.id, args.by, "feature_deprecated", upd,
                      extra={"from": old_status})
    if err:
        print("FEATURES: FAIL — " + err)
        return 1
    print(f"FEATURES: {args.id} deprecated (kept, not deleted). Used by: {', '.join(rec.get('used_by', [])) or 'nobody'}." +
          (f" Replacement: {args.replacement}." if args.replacement else ""))
    return 0


def cmd_cancel(args):
    """Cancel a feature mid-flight (v2.2). The feature stays in the
    registry as the record of a decision — it is never deleted — and
    every ACTIVE claim touching its files is released in the same
    push, so the worker stops instead of discovering the
    cancellation at push time. (A worker that already holds a
    stale local claim is also stopped by sync_check, which FAILs
    when the remote cancelled a feature its claim touches.)"""
    guard = _guard_mutation(args.features_dir, "cancel a feature")
    if guard:
        print("FEATURES: FAIL — " + guard)
        return 1
    data, err = tc.load_json_file(feature_path(args.features_dir, args.id))
    if err or not isinstance(data, dict):
        print(f"FEATURES: FAIL — no feature with id {args.id}")
        return 1
    if data.get("status") == "cancelled":
        print(f"FEATURES: FAIL — {args.id} is already cancelled")
        return 1
    if (data.get("status"), "cancelled") not in ALLOWED_TRANSITIONS:
        print(f"FEATURES: FAIL — cannot move {data.get('status')} -> cancelled "
              "(only experimental/stable features can be cancelled; a deprecated feature "
              "stays as history — nobody should be working on it)")
        return 1
    old_status = data.get("status")
    feature_files = data.get("files") or []

    def upd(f):
        f["status"] = "cancelled"
        _set_status_fields(f, args.by, args.reason)
        f.setdefault("changes", []).append({
            "version": f.get("version"), "by": args.by,
            "note": "Cancelled: %s" % args.reason, "date": date.today().isoformat(),
        })
    rec, err = mutate(args.features_dir, args.id, args.by, "feature_cancelled", upd,
                      extra={"from": old_status, "reason": args.reason})
    if err:
        print("FEATURES: FAIL — " + err)
        return 1
    team_dir = tc.team_dir_of(args.features_dir)
    claims_dir = os.path.join(team_dir, "claims")
    released = []
    for stem, (_path, c) in tc.load_records(claims_dir).items():
        if not isinstance(c, dict) or c.get("status") != "active":
            continue
        if not any(tc.paths_overlap(f, g) for f in (c.get("files") or []) for g in feature_files):
            continue
        cpath = os.path.join(claims_dir, stem + ".json")
        cur, _e = tc.load_json_file(cpath)
        if not isinstance(cur, dict) or cur.get("status") != "active":
            continue
        cur["status"] = "released"
        cur["released_by"] = "cancel:" + args.by
        cur["lamport"] = int(cur.get("lamport") or 0) + 1
        tc.save_json(cpath, cur)
        tc.append_event(team_dir, "claim_released", cur["lamport"], args.by,
                        base_commit=tc.git_head_commit(), task_id=stem,
                        extra={"reason": "feature %s cancelled: %s" % (args.id, args.reason)})
        released.append(stem)
    rebuild(team_dir)
    print(f"FEATURES: {args.id} cancelled by {args.by} ({args.reason}). "
          "It stays in the registry — do not rebuild it.")
    if released:
        print(f"FEATURES: released {len(released)} active claim(s) on its files: {', '.join(sorted(released))}. "
              "Commit + push the feature file AND the released claim file(s).")
    else:
        print("FEATURES: no active claim touched its files.")
    return 0


def cmd_reverify(args):
    guard = _guard_mutation(args.features_dir, "reverify a feature")
    if guard:
        print("FEATURES: FAIL — " + guard)
        return 1
    data, err = tc.load_json_file(feature_path(args.features_dir, args.id))
    if err or not isinstance(data, dict):
        print(f"FEATURES: FAIL — no feature with id {args.id}")
        return 1
    kind = data.get("kind") or "code"
    entry = data.get("entry", "")
    err = check_entry(entry, kind, args.repo_root)
    if err:
        print(f"FEATURES: FAIL — entry invalid, cannot re-verify: {err}")
        return 1
    # Cycle gate (v2.5): refreshing dependency versions inside a
    # loop is meaningless — the loop must be broken first.
    _cycle = tc.find_dependency_cycle(load_features(args.features_dir))
    if _cycle and args.id in _cycle:
        print(f"FEATURES: FAIL — {args.id} is part of a dependency cycle: {' -> '.join(_cycle)} — "
              "break the loop (remove one depends_on edge) before re-verifying")
        return 1
    blob_sha = ""
    if kind == "code":
        fpath = entry.rsplit(":", 1)[0]
        blob_sha = tc.git_blob_sha(os.path.join(args.repo_root, fpath))
    # Re-verify what this feature depends on, too: a reverify says
    # "I checked my dependencies at their current versions" — it
    # clears the needs-reverify flag validate.py raises after a
    # dependency bumped MAJOR.
    feats = load_features(args.features_dir)
    refreshed = []
    for dep in data.get("depends_on") or []:
        cur = feats.get(dep.get("id") or "")
        if cur and cur.get("version") and cur["version"] != dep.get("version"):
            refreshed.append((dep["id"], dep.get("version"), cur["version"]))

    def upd(f):
        f["entry_blob_sha"] = blob_sha or ""
        f["last_verified"] = date.today().isoformat()
        for dep in f.get("depends_on") or []:
            cur = feats.get(dep.get("id") or "")
            if cur and cur.get("version"):
                dep["version"] = cur["version"]
    rec, err = mutate(args.features_dir, args.id, args.by, "feature_reverified", upd)
    if err:
        print("FEATURES: FAIL — " + err)
        return 1
    print(f"FEATURES: {args.id} re-verified by {args.by} — entry {entry}, blob {blob_sha or '(n/a)'}")
    for did, old, new in refreshed:
        print(f"FEATURES: dependency refreshed: {did} {old} -> {new}")
    return 0


def cmd_promote(args):
    guard = _guard_mutation(args.features_dir, "promote a feature")
    if guard:
        print("FEATURES: FAIL — " + guard)
        return 1
    data, err = tc.load_json_file(feature_path(args.features_dir, args.id))
    if err or not isinstance(data, dict):
        print(f"FEATURES: FAIL — no feature with id {args.id}")
        return 1
    if data.get("status") == "deprecated":
        print(f"FEATURES: FAIL — {args.id} is deprecated; deprecated -> stable is FORBIDDEN. "
              "It needs a human-approved PR to come back as a new feature — do not flip it back by hand.")
        return 1
    if data.get("status") != "experimental":
        print(f"FEATURES: FAIL — only experimental features can be promoted (got {data.get('status')})")
        return 1
    if args.reviewer == data.get("owner_id"):
        print("FEATURES: FAIL — the reviewer must be different from the owner")
        return 1
    version = data.get("version", "")
    if str(version).startswith("0."):
        print(f"FEATURES: FAIL — version {version} is 0.y.z, always experimental; bump to 1.0.0 first")
        return 1
    if tc.is_prerelease(version):
        print(f"FEATURES: FAIL — pre-release version {version} can never be stable")
        return 1

    def upd(f):
        f["status"] = "stable"
        _set_status_fields(f, args.by, args.reason)
        f["status_changed_by"] = args.reviewer
        f.setdefault("changes", []).append({
            "version": f.get("version"), "by": args.by,
            "note": "Promoted to stable (reviewed by %s): %s" % (args.reviewer, args.reason),
            "date": date.today().isoformat(),
        })
    rec, err = mutate(args.features_dir, args.id, args.by, "feature_promoted", upd,
                      extra={"reviewer": args.reviewer})
    if err:
        print("FEATURES: FAIL — " + err)
        return 1
    print(f"FEATURES: {args.id} promoted experimental -> stable (reviewed by {args.reviewer}).")
    return 0


def main():
    p = argparse.ArgumentParser(description="Search before you build; reuse what exists (team-roster v2).")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("list", "check"):
        sp = sub.add_parser(name)
        sp.add_argument("path", nargs="?", default="team/features")
        if name == "check":
            sp.add_argument("--roster", default="team/roster")
            sp.add_argument("--repo-root", default=".")
    sp = sub.add_parser("search")
    sp.add_argument("query")
    sp.add_argument("path", nargs="?", default="team/features")
    sp = sub.add_parser("register")
    sp.add_argument("--id", required=True)
    sp.add_argument("--name", required=True)
    sp.add_argument("--does", required=True)
    sp.add_argument("--entry", required=True)
    sp.add_argument("--files", nargs="*", default=[])
    sp.add_argument("--owner", required=True)
    sp.add_argument("--keywords", nargs="+", required=True)
    sp.add_argument("--kind", default="code", choices=sorted(KINDS))
    sp.add_argument("--status", default="experimental", choices=sorted(STATUSES))
    sp.add_argument("--version", default="1.0.0")
    sp.add_argument("--breaking", default=None, choices=["true", "false"])
    sp.add_argument("--breaking-notes", default="")
    sp.add_argument("--canonical", action="store_true")
    sp.add_argument("--contract", action="store_true",
                    help="mark this feature a shared interface (contract): entry changes need --contract-change + major bump")
    sp.add_argument("--depends-on", nargs="*", default=[],
                    help="dependencies as id@version, e.g. login-api@1.0.0")
    sp.add_argument("--reviewer", default="")
    sp.add_argument("--branch", default="")
    sp.add_argument("--features-dir", default="team/features")
    sp.add_argument("--repo-root", default=".")
    sp = sub.add_parser("use")
    sp.add_argument("--id", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--features-dir", default="team/features")
    sp = sub.add_parser("improve")
    sp.add_argument("--id", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--bump", default="minor", choices=["patch", "minor", "major"])
    sp.add_argument("--note", default="")
    sp.add_argument("--does", default="")
    sp.add_argument("--entry", default="")
    sp.add_argument("--files", nargs="*", default=[])
    sp.add_argument("--keywords", nargs="*", default=[])
    sp.add_argument("--breaking", default=None, choices=["true", "false"])
    sp.add_argument("--breaking-notes", default="")
    sp.add_argument("--depends-on", nargs="*", default=[],
                    help="add/refresh dependencies as id@version, e.g. login-api@1.0.0")
    sp.add_argument("--contract-change", default="",
                    help="required when changing the entry of a contract feature: what breaks and who must adapt")
    sp.add_argument("--features-dir", default="team/features")
    sp.add_argument("--repo-root", default=".")
    sp = sub.add_parser("deprecate")
    sp.add_argument("--id", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--replacement", default="")
    sp.add_argument("--features-dir", default="team/features")
    sp = sub.add_parser("cancel")
    sp.add_argument("--id", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--reason", required=True,
                    help="why the feature is cancelled — recorded, and shown to anyone who finds it in search")
    sp.add_argument("--features-dir", default="team/features")
    sp = sub.add_parser("reverify")
    sp.add_argument("--id", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--features-dir", default="team/features")
    sp.add_argument("--repo-root", default=".")
    sp = sub.add_parser("promote")
    sp.add_argument("--id", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--reviewer", required=True)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--features-dir", default="team/features")

    args = p.parse_args()
    if args.cmd == "list":
        return cmd_list(args.path)
    if args.cmd == "search":
        return cmd_search(args.path, args.query)
    if args.cmd == "check":
        return cmd_check(args.path, args.roster, args.repo_root)
    if args.cmd == "register":
        return cmd_register(args)
    if args.cmd == "use":
        return cmd_use(args)
    if args.cmd == "improve":
        return cmd_improve(args)
    if args.cmd == "deprecate":
        return cmd_deprecate(args)
    if args.cmd == "cancel":
        return cmd_cancel(args)
    if args.cmd == "reverify":
        return cmd_reverify(args)
    if args.cmd == "promote":
        return cmd_promote(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
