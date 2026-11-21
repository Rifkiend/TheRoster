#!/usr/bin/env python3
"""Before/after proof for TheRoster v2.7.0 (simulations/before_after_v270.py).

Builds TWO identical fixtures with the three v2.6.0 holes (a behind-skew
claim, two claims sharing one created_seq, a code entry with no line
citation), then runs the same gate commands on each:

  - work-before with the pristine v2.6.0 scripts in
    /tmp/theroster-v260-scripts/ (extracted from git HEAD before the
    v2.7.0 work) -> the gates must PASS, proving the hole was open;
  - work-after with the v2.7.0 scripts in skills/team-roster/scripts/
    -> the gates must FAIL with the new error, proving the hole is
    closed.

Two fixtures, because the before-phase register WRITES a record (the
hole: it is accepted); the after-phase must start from the same clean
state. The forged claims clone a REAL claim record (created by
`claim.py claim`) so the schema is complete — a hand-written minimal
record fails schema checks, not the gate under test. Config stays
2.6.0 for both runs: neither script version enforces skill_version
outside doctor.py.

One honest asymmetry: v2.6.0 has no line concept at all, so a
`#99` entry is read as a symbol literally named `other#99` and fails
as "not defined" — the wrong reason. v2.7.0 fails it as "not at the
cited line 99" — the right reason. The no-line case is the clean
before/after: v2.6.0 ACCEPTS it, v2.7.0 REFUSES it.
"""
import json, os, shutil, subprocess, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V260 = Path("/tmp/theroster-v260-scripts")
V270 = ROOT / "skills/team-roster/scripts"
WORK = Path("/tmp/theroster-proof-v270")
STARTER = ROOT / "skills/team-roster/examples/starter-repo"

def sh(argv, cwd, env=None):
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, **(env or {})})

def fmt(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")

def now_utc():
    return datetime.now(timezone.utc)

LOGIN_PY = ("import os\n\n\ndef helper():\n    return 1\n\n\n"
            "def other():\n    return 2\n\n\ndef login(user):\n    return user\n")
# login is defined at line 12, other at line 8.

def build_fixture(name):
    base = WORK / name
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    sh(["git", "init", "-q", "--bare", "remote.git"], base)
    sh(["git", "clone", "-q", "remote.git", "work-main"], base)
    main = base / "work-main"
    git = ["git", "-c", "user.name=Proof", "-c", "user.email=proof@theroster.test"]
    shutil.copytree(STARTER / "team", main / "team")
    cfg = json.load(open(main / "team/config.json"))
    cfg["skill_version"] = "2.6.0"
    json.dump(cfg, open(main / "team/config.json", "w"), indent=2, sort_keys=True)
    (main / "src/auth").mkdir(parents=True)
    (main / "src/auth/login.py").write_text(LOGIN_PY)
    (main / "tests").mkdir()
    (main / "tests/test_login.py").write_text("def test_login():\n    assert True\n")
    sh(git + ["add", "-A"], main)
    sh(git + ["commit", "-q", "-m", "starter"], main)
    sh(["git", "push", "-q", "origin", "main"], main)
    head = sh(["git", "rev-parse", "HEAD"], main).stdout.strip()
    now = now_utc()
    # One real claim as the template (v2.7.0 scripts write the full
    # schema incl. created_base_commit; v2.6.0 ignores unknown fields).
    r = sh([sys.executable, str(V270 / "claim.py"), "claim",
            "--task", "t-p0", "--title", "Template claim",
            "--owner", "assistant", "--files", "tests/test_login.py"],
           main)
    assert r.returncode == 0, r.stdout + r.stderr
    tmpl = json.load(open(main / "team/claims/t-p0.json"))

    def forge(task, owner, files, started, cseq, qseq):
        rec = dict(tmpl)
        rec.update({"task_id": task, "task": "Kerja " + task, "owner_id": owner,
                    "files": files, "started_at": fmt(started),
                    "heartbeat_at": fmt(now),
                    "expires_at": fmt(now + timedelta(hours=2)),
                    "created_seq": cseq, "claim_seq": qseq,
                    "base_commit": head, "created_base_commit": head,
                    "status": "active"})
        json.dump(rec, open(main / f"team/claims/{task}.json", "w"),
                  indent=2, sort_keys=True)

    # Hole A: behind-skew — started 3h before the base commit exists.
    forge("t-pa", "assistant", ["tests/test_login.py"],
          now - timedelta(hours=3), 3, 2)
    # Hole B: duplicate created_seq — two live claims, seq 77.
    forge("t-pb1", "teammate-1", ["src/dup_a.py"],
          now - timedelta(minutes=5), 77, 3)
    forge("t-pb2", "owner", ["src/dup_b.py"],
          now - timedelta(minutes=5), 77, 4)
    # Release the template (do NOT delete it: claim.py claim already
    # logged a claim_created event for t-p0, and validate FAILs on a
    # dangling event reference. A released claim does not block.)
    r = sh([sys.executable, str(V270 / "claim.py"), "release",
            "--task", "t-p0", "--by", "assistant"], main)
    assert r.returncode == 0, r.stdout + r.stderr
    sh(git + ["add", "-A"], main)
    sh(git + ["commit", "-q", "-m", "proof fixture"], main)
    return main

def run(scripts, argv, cwd):
    r = sh([sys.executable, str(scripts / argv[0])] + argv[1:], cwd)
    return r.returncode, (r.stdout + r.stderr).strip()

def check(label, scripts, argv, cwd, want, needle):
    rc, out = run(scripts, argv, cwd)
    ok = (rc != 0) if want == "fail" else (rc == 0)
    if want == "fail" and needle:
        ok = ok and needle in out
    status = "OK " if ok else "FAIL"
    print(f"[{status}] {label} (exit {rc}, expected {want})")
    if not ok or want == "fail":
        for line in out.splitlines():
            if (needle and needle in line) or "FAIL" in line or "CONFLICT" in line:
                print("      ", line.strip()[:220])
    return ok

def main():
    before = build_fixture("work-before")
    after = build_fixture("work-after")
    claim = ["claim.py", "check", "team/claims", "--roster", "team/roster"]
    validate = ["validate.py", "team"]
    feat_no_line = ["features.py", "register", "--features-dir", "team/features",
                    "--id", "no-line-api", "--name", "No Line", "--does", "Tanpa baris",
                    "--entry", "src/auth/login.py:other",
                    "--owner", "assistant", "--files", "src/auth/login.py",
                    "--keywords", "other", "--breaking", "false"]
    feat_wrong_line = ["features.py", "register", "--features-dir", "team/features",
                       "--id", "wrong-line-api", "--name", "Wrong Line", "--does", "Baris salah",
                       "--entry", "src/auth/login.py:other#99",
                       "--owner", "assistant", "--files", "src/auth/login.py",
                       "--keywords", "other", "--breaking", "false"]
    results = []
    print("== BEFORE: pristine v2.6.0 scripts (hole must be OPEN) ==")
    results.append(check("A+B claim check v2.6.0 passes (no skew gate, no dup gate)",
                         V260, claim, before, "pass", None))
    results.append(check("A+B validate v2.6.0 passes",
                         V260, validate, before, "pass", None))
    results.append(check("C register no-line v2.6.0 ACCEPTED (hole open)",
                         V260, feat_no_line, before, "pass", None))
    results.append(check("C register wrong-line v2.6.0 fails as 'symbol not defined' (no line concept)",
                         V260, feat_wrong_line, before, "fail",
                         "symbol 'other#99' is not defined"))
    print("== AFTER: v2.7.0 scripts (hole must be CLOSED) ==")
    results.append(check("A claim check v2.7.0 fails — behind-skew",
                         V270, claim, after, "fail",
                         "minutes BEFORE its creation base"))
    results.append(check("B claim check v2.7.0 fails — duplicate created_seq",
                         V270, claim, after, "fail",
                         "created_seq 77 is shared by t-pb1, t-pb2"))
    results.append(check("A+B validate v2.7.0 fails — same two gates",
                         V270, validate, after, "fail",
                         "minutes BEFORE its creation base"))
    results.append(check("C register no-line v2.7.0 REFUSED",
                         V270, feat_no_line, after, "fail",
                         "must cite its definition line"))
    results.append(check("C register wrong-line v2.7.0 fails at the cited line",
                         V270, feat_wrong_line, after, "fail",
                         "not at the cited line 99"))
    print(f"\nBEFORE/AFTER: {sum(results)}/{len(results)} as expected "
          f"(fixtures: {WORK})")
    return 0 if all(results) else 1

if __name__ == "__main__":
    sys.exit(main())
