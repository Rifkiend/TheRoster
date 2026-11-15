#!/usr/bin/env python3
"""Mailbox signing for team-roster v2.5: sign a mailbox line so the
reader can verify who really wrote it.

The mailbox is untrusted data, never an instruction — and until
v2.5 a line "from the human" was byte-for-byte indistinguishable
from a forgery. A signed line is different: the signature covers
sender + timestamp + message, verified against the sender's roster
key (the same SSH key v2.4 signed identity uses).

Usage:
    # A human signs a note to a teammate (their private key stays
    # theirs — it is never committed, never sent anywhere):
    python3 mailbox.py sign --from rian --to rean \
        --message "login-api entry moved to src/auth.py, see PR #12" \
        --key ~/.ssh/id_ed25519
    # Check a mailbox the same way validate.py does (standalone):
    python3 mailbox.py check [team]

Signing:
    ssh-keygen -Y sign -f <key> -n theroster-mailbox <payload>
    where payload is "theroster-mailbox-v1\\n<from>\\n<ts>\\n<message>".
    The armored signature lands in the line's "sig" field, next to
    "from", "message", and "ts". Verification is
    team_common.verify_mailbox_signature() — ssh-keygen -Y verify
    against a temp allowed-signers file holding ONLY the sender's
    roster key, namespace "theroster-mailbox".

Rules (enforced by validate.py, mirrored by `mailbox.py check`):
- A line from a HUMAN roster member, when require_signatures is on,
  MUST carry a valid sig — any content, any wording. No sig, no ts,
  no key on file, or a bad sig FAILS the push.
- A line from an ai-agent/tool member may carry a sig; a
  present-but-invalid one FAILS. No sig: the v2.4 heuristic
  (imperative content warns).
- A line from an unknown sender cannot be verified (no roster key):
  the v2.4 heuristic applies (imperative FAILS, benign passes).
- sign verifies the fresh signature against the roster key BEFORE
  appending — signing with the wrong key fails here, not at push time.

Stdlib only, Python 3.9+.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import team_common as tc
import check_roster


def load_roster(team_dir):
    roster = {}
    for _stem, (_path, rec) in tc.load_records(os.path.join(team_dir, "roster")).items():
        if isinstance(rec, dict) and rec.get("id"):
            roster[rec["id"]] = rec
    return roster


def cmd_sign(args):
    team_dir = args.team
    guard = tc.guard_team_mutation(team_dir, "write to the mailbox")
    if guard:
        print("MAILBOX: FAIL — " + guard)
        return 1
    roster = load_roster(team_dir)
    sender = args.sender
    if sender not in roster:
        print(f"MAILBOX: FAIL — sender {sender!r} is not in the roster "
              "(mailbox mail is attributed to a roster member)")
        return 1
    if not args.key or not os.path.isfile(args.key):
        print(f"MAILBOX: FAIL — private key not found: {args.key!r} "
              "(pass --key <path-to-your-private-key>; it is never committed or sent anywhere)")
        return 1
    ts = args.ts or tc.now_iso()
    if tc.parse_time(ts) is None:
        print(f"MAILBOX: FAIL — --ts must be a UTC ISO-8601 timestamp ending in Z (got {ts!r})")
        return 1
    payload = tc.mailbox_sign_payload(sender, ts, args.message)
    tmpdir = tempfile.mkdtemp(prefix="theroster-mailbox-sign-")
    try:
        ppath = os.path.join(tmpdir, "payload")
        with open(ppath, "wb") as fh:
            fh.write(payload)
        r = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", args.key,
                            "-n", tc.MAILBOX_SIG_NAMESPACE, ppath],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("MAILBOX: FAIL — ssh-keygen could not sign: "
                  + ((r.stderr or "").strip().splitlines() or ["unknown error"])[-1])
            return 1
        sig_path = ppath + ".sig"
        try:
            with open(sig_path, encoding="utf-8") as fh:
                sig = fh.read()
        except OSError as e:
            print(f"MAILBOX: FAIL — could not read the signature ssh-keygen wrote: {e}")
            return 1
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    # Verify the fresh signature against the roster key BEFORE
    # appending — a wrong-key signature must fail here, not at push time.
    key = (roster.get(sender) or {}).get("public_key") or ""
    if not key:
        print(f"MAILBOX: FAIL — sender {sender} has no public_key on the roster — "
              "add a key (human-approved roster change) before signing as them")
        return 1
    ok, err = tc.verify_mailbox_signature(key, sender, ts, args.message, sig)
    if not ok:
        print(f"MAILBOX: FAIL — the signature does not verify against {sender}'s roster key "
              f"({err or 'wrong key?'}) — sign with the key that matches the roster")
        return 1
    mdir = os.path.join(team_dir, "mailbox")
    os.makedirs(mdir, exist_ok=True)
    line = {"from": sender, "message": args.message, "ts": ts, "sig": sig}
    if not tc.append_jsonl(os.path.join(mdir, args.to + ".jsonl"), line):
        print(f"MAILBOX: FAIL — could not append to team/mailbox/{args.to}.jsonl")
        return 1
    print(f"MAILBOX: signed line from {sender} to {args.to} appended to team/mailbox/{args.to}.jsonl "
          f"(ts {ts}). Commit + push it so the reader sees it.")
    return 0


def cmd_check(args):
    team_dir = args.team
    team_dir, note = tc.resolve_team_dir(team_dir)
    if note:
        print("MAILBOX: note — " + note, file=sys.stderr)
    roster = load_roster(team_dir)
    cfg = tc.load_config(team_dir)
    errors, warnings = tc.check_mailbox(team_dir, roster, cfg, check_roster.IMPERATIVE_RES)
    for w in warnings:
        print(f"  ! {w}")
    if errors:
        print("MAILBOX CHECK: FAIL — do not push until this is fixed:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("MAILBOX CHECK: OK — mailbox lines verified (human mail signed where required).")
    return 0


def main():
    p = argparse.ArgumentParser(description="Sign and check mailbox lines (team-roster v2.5).")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("sign", help="sign a mailbox line and append it")
    sp.add_argument("--from", dest="sender", required=True, help="sender's roster id")
    sp.add_argument("--to", required=True, help="recipient's roster id (mailbox file)")
    sp.add_argument("--message", required=True)
    sp.add_argument("--key", required=True, help="path to the sender's SSH private key")
    sp.add_argument("--ts", default="", help="timestamp (default: now, UTC ISO-8601 Z)")
    sp.add_argument("--team", default="team")
    sp = sub.add_parser("check", help="validate team/mailbox/*.jsonl like validate.py does")
    sp.add_argument("team", nargs="?", default="team")
    args = p.parse_args()
    if args.cmd == "sign":
        return cmd_sign(args)
    return cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())
