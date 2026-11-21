#!/usr/bin/env python3
"""Session-start self-check for team-roster v2 (the `doctor` command).

Run this at the start of every session, before any work:
    python3 doctor.py [--team team] [--skill-dir skills/team-roster]
    python3 doctor.py --ci   # CI mode: the hook check is skipped —
                             # a GitHub Actions checkout never has a
                             # local hook installed, and the 5 checks
                             # (roster, claims, features, validate,
                             # sync) are the CI gate, not the hook.
                             # Everything else is checked identically.

Checks:
1. python3 >= 3.9.
2. Inside a git repo (git rev-parse --show-toplevel).
3. team/ dirs exist: team/roster, team/claims, team/features.
4. team/config.json exists and its skill_version matches the version
   in SKILL.md frontmatter (the repo copy of the skill always wins
   over a global install; a version mismatch is a loud failure).
5. All scripts in <skill-dir>/scripts compile (py_compile).
6. The pre-push hook is installed at <git-common-dir>/hooks/pre-push
   and is executable.
7. The installed hook's bytes match <skill-dir>/hooks/pre-push
   (WARN on mismatch — the installed copy may be intentionally
   customised, but usually it is just stale).
8. team/events.jsonl size (WARN above 1 MB — the append-only log
   grows forever; past months belong in team/events-archive/,
   see scripts/archive_events.py).
9. Worktree mode (v2.3, only when team/config.json has
   worktree_mode="per-claim"): git >= 2.38 so `git merge-tree
   --write-tree` exists for the sync_check merge check (WARN if
   older — the check falls back to a trial merge), and
   scripts/worktree_setup.sh is present (WARN if missing).
10. Signed identity (v2.4, only when team/config.json has
    require_signatures=true): ssh-keygen present (WARN if missing —
    git needs it to verify SSH signatures), and every active roster
    member has a public_key (WARN if not — they cannot push team/
    changes until a key is added via a human-approved roster change).
11. Orca records (v2.6): fan-out groups, decision gates, completion
    receipts, and formal reviews are each checked by their own
    script (fanout.py check / gates.py check / receipts.py check /
    reviews.py check) — a failure in any of them FAILS doctor, the
    same as a claim/feature failure would. A missing script warns
    only (a pre-v2.6 skill copy has no Orca scripts yet).

Exit 0 = healthy. Exit 1 = something is wrong, with install
instructions printed. Warnings do not fail the run.
Stdlib only, Python 3.9+.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import team_common as tc


def main():
    p = argparse.ArgumentParser(description="Session-start self-check for team-roster v2.")
    p.add_argument("--team", default="team")
    p.add_argument("--skill-dir", default="skills/team-roster")
    p.add_argument("--ci", action="store_true",
                   help="CI mode: skip the local pre-push hook check "
                        "(CI has no local hook; the 5 checks are the gate)")
    args = p.parse_args()
    problems, warnings = [], []

    # 1. Python version.
    if sys.version_info < (3, 9):
        problems.append("python3 is %s — team-roster v2 needs Python >= 3.9" % ".".join(map(str, sys.version_info[:3])))
    else:
        print(f"doctor: python3 {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} OK")

    # 2. Git repo.
    root = tc.git_root()
    if not root:
        problems.append("not inside a git repo — run doctor from inside the project repo")
        root = os.getcwd()
    else:
        print(f"doctor: git repo OK ({root})")

    # 3. team/ dirs.
    team_dir = args.team if os.path.isabs(args.team) else os.path.join(root, args.team)
    # Reads come from the main checkout, never from a worktree copy
    # (Dziatkovskii rule).
    team_dir, redirect_note = tc.resolve_team_dir(team_dir)
    if redirect_note:
        print("doctor: note — " + redirect_note, file=sys.stderr)
    for sub in ("roster", "claims", "features"):
        if os.path.isdir(os.path.join(team_dir, sub)):
            print(f"doctor: {os.path.join(args.team, sub)}/ OK")
        else:
            problems.append(f"missing dir {os.path.join(team_dir, sub)} — create it, or run "
                            "scripts/migrate_v1_to_v2.py to convert a v1 tree")

    # 4. config version vs SKILL.md frontmatter version.
    skill_md = os.path.join(args.skill_dir, "SKILL.md")
    skill_version = None
    if os.path.isfile(skill_md):
        with open(skill_md, encoding="utf-8") as f:
            head = f.read(4000)
        m = re.search(r"^\s*version:\s*([0-9]+\.[0-9]+\.[0-9]+)\s*$", head, re.M)
        skill_version = m.group(1) if m else None
        if skill_version is None:
            warnings.append("could not find a version: line in %s frontmatter" % skill_md)
    else:
        warnings.append("SKILL.md not found at %s — cannot verify skill_version" % skill_md)
    cfg_path = os.path.join(team_dir, "config.json")
    cfg, err = tc.load_json_file(cfg_path)
    if err:
        problems.append("team/config.json missing or unreadable (%s) — create it with the v2 defaults "
                        "(see skills/team-roster/scripts/migrate_v1_to_v2.py, or copy from a migrated tree)" % err)
    elif skill_version and cfg.get("skill_version") != skill_version:
        problems.append("skill_version mismatch: team/config.json says %s, SKILL.md says %s — "
                        "the repo copy of the skill wins; update team/config.json or the skill copy"
                        % (cfg.get("skill_version"), skill_version))
    elif skill_version:
        print(f"doctor: skill_version {skill_version} matches SKILL.md OK")

    # 5. Scripts compile (built-in compile(): no .pyc files written).
    scripts_dir = os.path.join(args.skill_dir, "scripts")
    if os.path.isdir(scripts_dir):
        bad = []
        for name in sorted(os.listdir(scripts_dir)):
            if not name.endswith(".py"):
                continue
            try:
                with open(os.path.join(scripts_dir, name), encoding="utf-8") as f:
                    compile(f.read(), name, "exec")
            except (SyntaxError, OSError) as e:
                bad.append("%s: %s" % (name, e))
        if bad:
            problems.append("scripts fail to compile: " + "; ".join(bad))
        else:
            print(f"doctor: all scripts in {scripts_dir} compile OK")
    else:
        problems.append("scripts dir not found: %s" % scripts_dir)

    # 6+7. Hook installed, executable, bytes match the skill copy.
    # Skipped in --ci mode: a CI checkout never has a local hook, and
    # the hook is not the CI gate — the 5 checks are.
    if args.ci:
        print("doctor: CI mode — local pre-push hook check skipped (no local hook in CI)")
    else:
        hook_src = os.path.join(args.skill_dir, "hooks", "pre-push")
        r = tc.run_git(["rev-parse", "--git-common-dir"])
        common = r.stdout.strip() if r.returncode == 0 else ".git"
        if not os.path.isabs(common):
            common = os.path.join(root, common)
        installed = os.path.join(common, "hooks", "pre-push")
        if not os.path.isfile(installed):
            problems.append("pre-push hook NOT installed at %s — run: bash %s/scripts/install-hook.sh"
                            % (installed, args.skill_dir))
        elif not os.access(installed, os.X_OK):
            problems.append("pre-push hook at %s is not executable — run: chmod +x %s" % (installed, installed))
        else:
            print("doctor: pre-push hook installed and executable OK")
            if os.path.isfile(hook_src):
                with open(installed, "rb") as f:
                    have = f.read()
                with open(hook_src, "rb") as f:
                    want = f.read()
                if have != want:
                    warnings.append("installed hook differs from %s — reinstall with install-hook.sh "
                                    "if the difference is not intentional" % hook_src)
                else:
                    print("doctor: installed hook matches the skill copy OK")
            else:
                warnings.append("skill hook source not found at %s — cannot compare bytes" % hook_src)

    # 8. Event log size (warn only): the append-only log grows forever;
    # past months belong in team/events-archive/ (archive_events.py).
    events_path = os.path.join(team_dir, "events.jsonl")
    if os.path.isfile(events_path):
        size = os.path.getsize(events_path)
        if size > 1_000_000:
            warnings.append(
                "team/events.jsonl is %.1f MB — run python3 "
                "scripts/archive_events.py %s to move past months into "
                "team/events-archive/" % (size / 1_000_000, args.team))

    # 9. Worktree mode (v2.3, warn only): only when the team opted in.
    cfg_for_wt = None
    if os.path.isfile(cfg_path):
        cfg_for_wt, _err = tc.load_json_file(cfg_path)
    if isinstance(cfg_for_wt, dict) and cfg_for_wt.get("worktree_mode") == "per-claim":
        print("doctor: worktree_mode=per-claim — checking worktree support")
        r = tc.run_git(["--version"])
        ver = None
        if r.returncode == 0:
            m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", r.stdout)
            if m:
                ver = tuple(int(x) for x in m.groups() if x is not None)
        if ver is None:
            warnings.append("could not determine the git version — worktree merge checks need git >= 2.38")
        elif ver < (2, 38):
            warnings.append("git %s is older than 2.38 — `git merge-tree --write-tree` is unavailable; "
                            "sync_check falls back to a trial merge, which is slower" % ".".join(map(str, ver)))
        else:
            print(f"doctor: git {'.'.join(map(str, ver))} supports merge-tree OK")
        setup = os.path.join(args.skill_dir, "scripts", "worktree_setup.sh")
        if not os.path.isfile(setup):
            warnings.append("worktree_setup.sh not found at %s — create worktrees by hand "
                            "(git worktree add) until it is restored" % setup)
        else:
            print("doctor: worktree_setup.sh present OK")

    # 10. Signed identity (v2.4, warn only): only when the team opted
    # in (require_signatures=true). git shells out to ssh-keygen to
    # verify SSH commit signatures; without it, verification fails.
    # And a member without a public_key cannot push team/ changes
    # while the knob is on — the key enters via a human-approved
    # roster change, so warn early instead of failing at push time.
    cfg_for_sig = None
    if os.path.isfile(cfg_path):
        cfg_for_sig, _err = tc.load_json_file(cfg_path)
    if isinstance(cfg_for_sig, dict) and cfg_for_sig.get("require_signatures"):
        print("doctor: require_signatures=true — checking signature support")
        import shutil
        if shutil.which("ssh-keygen") is None:
            warnings.append("ssh-keygen not found — git needs it to verify SSH commit signatures; "
                            "install OpenSSH client or turn require_signatures off")
        else:
            print("doctor: ssh-keygen present OK")
        roster_dir = os.path.join(team_dir, "roster")
        if os.path.isdir(roster_dir):
            keyless = []
            for _stem, (_p, rec) in tc.load_records(roster_dir).items():
                if isinstance(rec, dict) and rec.get("status") == "active" and not rec.get("public_key"):
                    keyless.append(rec.get("id") or _stem)
            if keyless:
                warnings.append("require_signatures is on but these active members have no public_key "
                                "on the roster: %s — they cannot push team/ changes until a key is added "
                                "(human-approved roster change)" % ", ".join(sorted(keyless)))
            else:
                print("doctor: every active roster member has a public_key OK")

    # 11. Orca records (v2.6): each of the four new record types is
    # checked by its own script. A failure FAILS doctor (a broken
    # gate/fan-out/receipt/review is as blocking as a broken claim);
    # a missing script warns only (pre-v2.6 skill copies have none).
    import subprocess
    _orca = (("fanout.py", "fanout"), ("gates.py", "gates"),
             ("receipts.py", "receipts"), ("reviews.py", "reviews"))
    for _script, _subdir in _orca:
        _spath = os.path.join(scripts_dir, _script)
        if not os.path.isfile(_spath):
            warnings.append(f"{_script} not found in {scripts_dir} — {_subdir} records cannot be "
                            "checked; update the skill copy to v2.7.0")
            continue
        _r = subprocess.run(
            [sys.executable, _spath, "check",
             os.path.join(team_dir, _subdir),
             "--roster", os.path.join(team_dir, "roster")],
            cwd=root, capture_output=True, text=True)
        if _r.stdout.strip():
            print(_r.stdout.rstrip())
        if _r.stderr.strip():
            print(_r.stderr.rstrip(), file=sys.stderr)
        if _r.returncode != 0:
            problems.append(f"{_script} check FAILED on {os.path.join(args.team, _subdir)} — "
                            f"fix the {_subdir} records (see the output above) before starting work")

    for w in warnings:
        print(f"doctor: warning — {w}")
    if problems:
        print("DOCTOR: FAIL — fix these before starting work:")
        for e in problems:
            print(f"  - {e}")
        print("Install steps: bash skills/team-roster/scripts/install-hook.sh")
        print("  (copies hooks/pre-push, verifies it is executable, re-runs doctor)")
        return 1
    print("DOCTOR: OK — healthy. You may start work (load the roster first).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
