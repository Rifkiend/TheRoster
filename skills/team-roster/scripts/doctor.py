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
