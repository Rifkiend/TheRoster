#!/usr/bin/env python3
"""Typed completion receipts for team-roster v2.6: what happened,
said once, in a record — Orca's worker_done, file-based.

Until v2.6, a claim ended with `claim.py done` — a status flip.
There was no typed answer to "did it succeed or fail, what
changed, what is left?" A dead worker and a finished worker
looked the same until someone read the code. A receipt is that
answer: outcome + summary + files modified, bound to
(task_id, claim_seq) so a stale retry cannot complete the wrong
attempt.

Sharded layout (v2.6): one file per task in
team/receipts/<task-id>.json — the receipt for the claim's latest
attempt. A re-claim of the same task (new claim_seq) overwrites
the receipt; the old attempt's receipt survives in
team/events.jsonl (receipt_filed events are append-only), so
history is not lost, it is just not in the record file.
team/index.json is GENERATED (scripts/rebuild_index.py), never
hand-edited. team/events.jsonl is the append-only audit log.

Receipts are FILED by `claim.py done` (no silent done — see
claim.py), not by this script. This script lists, shows, and
checks them:

Usage:
    python3 receipts.py list   [team/receipts]
    python3 receipts.py check  [team/receipts] [--roster team/roster]
    python3 receipts.py show --task t-002 [--receipts-dir team/receipts]

Rules (enforced by `claim.py done` at write time and by
`receipts.py check` / validate.py at read time):
- The referenced claim must exist; the receipt's claim_seq must
  match the claim's claim_seq — a receipt filed against a stale
  or released claim is rejected (the anti-stale-retry property:
  a retry cannot complete the wrong attempt).
- done_by must be the claim owner, or a human roster member
  filing on the worker's behalf.
- outcome is succeeded|failed; summary is non-empty; done_at is
  a parseable UTC ISO-8601 timestamp ending in Z; lamport >= 1.
- Signing: when require_signatures is on and done_by is a HUMAN
  roster member, the receipt MUST carry a valid `sig` (payload
  `theroster-receipt-v1\\n<done_by>\\n<done_at>\\n<task_id>\\n
  <claim_seq>\\n<outcome>\\n<summary>`, namespace
  `theroster-receipt`, ssh-keygen -Y sign/verify against the
  filer's roster key — team_common.verify_receipt_signature()).
  An ai-agent done_by needs no sig: the receipt file lands in a
  commit, and the commit signature binds it when
  require_signatures is on. A present-but-invalid sig FAILS,
  whoever the filer is.
- A failed outcome still marks the claim done — the attempt is
  over. What it does NOT do is mark the work done: the PRD item
  stays in_progress and the feature stays experimental, because
  the feature's stability gate (validate.py: a done item's
  features must be stable) already refuses a done item built on
  unfinished work.
- What a receipt does NOT do: verify the summary is true. A
  receipt that says "succeeded" for work that does not work is
  caught by the review (a human or a second agent reads the
  diff), not by the receipt — the receipt is the claim of
  completion, the review is the check of it.

Exit 0 = ok. Exit 1 = invalid.
Stdlib only, Python 3.9+.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import team_common as tc

OUTCOMES = {"succeeded", "failed"}


def receipt_path(receipts_dir, task_id):
    return os.path.join(receipts_dir, task_id + ".json")


def _resolve_receipts_dir(receipts_dir):
    """Redirect a receipts dir inside a linked worktree to the main
    checkout's receipts dir (reads only; prints a note on stderr)."""
    team_dir = tc.team_dir_of(receipts_dir)
    team_dir, note = tc.resolve_team_dir(team_dir)
    if note:
        print("RECEIPTS: note — " + note, file=sys.stderr)
        return os.path.join(team_dir, "receipts")
    return receipts_dir


def load_receipts(receipts_dir):
    return tc.load_records(receipts_dir)


def check_receipts(receipts_dir, roster_path=None, claims=None, cfg=None):
    """Validate every receipt record. Returns (errors, warnings).

    Shared by `receipts.py check` and validate.py — the per-script
    check is the single source of the rules. `claims` (id -> claim
    record) may be passed in by validate.py so the caller does not
    load the claims twice; otherwise it is loaded from team/claims.
    `cfg` is the loaded team config (require_signatures decides
    whether a human filer's sig is mandatory)."""
    receipts_dir = _resolve_receipts_dir(receipts_dir)
    team_dir = tc.team_dir_of(receipts_dir)
    if claims is None:
        claims = {}
        for _stem, (_path, rec) in tc.load_records(os.path.join(team_dir, "claims")).items():
            if isinstance(rec, dict):
                claims[_stem] = rec
    if cfg is None:
        cfg = tc.load_config(team_dir)
    errors, warnings = [], []
    receipts = load_receipts(receipts_dir)
    by_id = {}
    humans = set()
    if roster_path and os.path.isdir(roster_path):
        for _stem, (_path, rec) in tc.load_records(roster_path).items():
            if isinstance(rec, dict) and rec.get("id"):
                by_id[rec["id"]] = rec
                if rec.get("type") == "human":
                    humans.add(rec["id"])
    require_sigs = bool(cfg.get("require_signatures"))
    for tid, (_path, rec) in sorted(receipts.items()):
        label = tid
        if not tc.valid_id(tid):
            errors.append(f"{label}: task_id fails the id grammar ^[a-z0-9][a-z0-9-]*$ (max 64 chars, ASCII only)")
            continue
        if not isinstance(rec, dict):
            errors.append(f"{label}: not a JSON object")
            continue
        if rec.get("task_id") != tid:
            errors.append(f"{label}: task_id inside the file ({rec.get('task_id')!r}) does not match the filename — one receipt per task, named <task-id>.json")
        for field in ("task_id", "claim_seq", "outcome", "summary",
                      "done_by", "done_at", "lamport"):
            if field not in rec or rec[field] in (None, ""):
                if field in ("claim_seq", "lamport") and rec.get(field) == 0:
                    continue
                errors.append(f"{label}: missing/empty field: {field}")
        if rec.get("outcome") not in OUTCOMES:
            errors.append(f"{label}: outcome must be succeeded or failed (got {rec.get('outcome')!r})")
        if not isinstance(rec.get("summary"), str) or not rec.get("summary", "").strip():
            errors.append(f"{label}: summary must be a non-empty string")
        if rec.get("done_at") and tc.parse_time(rec["done_at"]) is None:
            errors.append(f"{label}: done_at must be a UTC ISO-8601 timestamp ending in Z (got {rec['done_at']!r})")
        if not isinstance(rec.get("lamport"), int) or rec.get("lamport", 0) < 1:
            errors.append(f"{label}: lamport must be an int >= 1")
        for f in rec.get("files_modified") or []:
            err = tc.path_syntax_error(f)
            if err:
                errors.append(f"{label}: files_modified: {err}")
        for f in rec.get("follow_ups") or []:
            if not isinstance(f, str) or not f.strip():
                errors.append(f"{label}: follow_ups entries must be non-empty strings")
        claim = claims.get(tid)
        if claim is None:
            errors.append(f"{label}: references claim {tid}, which does not exist — "
                          "a receipt without a claim is a forgery")
            continue
        try:
            receipt_seq = int(rec.get("claim_seq") or 0)
        except (TypeError, ValueError):
            receipt_seq = 0
        try:
            claim_seq = int(claim.get("claim_seq") or 0)
        except (TypeError, ValueError):
            claim_seq = 0
        if receipt_seq != claim_seq:
            errors.append(f"{label}: claim_seq {receipt_seq} does not match claim {tid}'s claim_seq {claim_seq} — "
                          "a stale retry cannot complete the wrong attempt")
        done_by = rec.get("done_by") or ""
        owner = claim.get("owner_id") or ""
        if done_by != owner and done_by not in humans:
            errors.append(f"{label}: done_by {done_by} is neither the claim owner ({owner}) nor a human roster "
                          "member — only the worker (or a human filing for them) files the receipt")
        # Signing: a human filer, with require_signatures on, MUST
        # carry a valid sig. A present-but-invalid sig FAILS for
        # any filer. An agent filer with the knob on needs no sig —
        # the commit signature binds the receipt file.
        sig = rec.get("sig") or ""
        if done_by in humans and require_sigs:
            if not sig:
                errors.append(f"{label}: done_by {done_by} is human and require_signatures is on — "
                              "the receipt must carry a valid sig (file it with claim.py done --key ...)")
            else:
                key = (by_id.get(done_by) or {}).get("public_key") or ""
                if not key:
                    errors.append(f"{label}: done_by {done_by} is signed, but has no public_key on the roster")
                else:
                    ok, err = tc.verify_receipt_signature(
                        key, done_by, rec.get("done_at") or "", tid,
                        receipt_seq, rec.get("outcome") or "",
                        rec.get("summary") or "", sig)
                    if not ok:
                        errors.append(f"{label}: invalid sig — {err or 'not signed by %s' % done_by}")
        elif sig:
            key = (by_id.get(done_by) or {}).get("public_key") or ""
            if not key:
                warnings.append(f"{label}: carries a sig, but {done_by} has no public_key on the roster — "
                                "the sig cannot be verified")
            else:
                ok, err = tc.verify_receipt_signature(
                    key, done_by, rec.get("done_at") or "", tid,
                    receipt_seq, rec.get("outcome") or "",
                    rec.get("summary") or "", sig)
                if not ok:
                    errors.append(f"{label}: invalid sig — {err or 'not signed by %s' % done_by}")
    return errors, warnings


# ---------------------------------------------------------------- commands

def cmd_list(receipts_dir):
    receipts_dir = _resolve_receipts_dir(receipts_dir)
    receipts = load_receipts(receipts_dir)
    if not receipts:
        print("RECEIPTS: no receipts yet — file one with: python3 claim.py done --task t-001 --by <you> --outcome succeeded --summary ...")
        return 0
    print(f"RECEIPTS: {len(receipts)} receipt(s):")
    for tid in sorted(receipts):
        rec = receipts[tid][1]
        if not isinstance(rec, dict):
            print(f"  - {tid}: UNREADABLE (not a JSON object)")
            continue
        print(f"  - {tid} [claim #{rec.get('claim_seq')}] {rec.get('outcome')} — {rec.get('done_by')} "
              f"at {rec.get('done_at')} — {rec.get('summary')}")
    return 0


def cmd_check(receipts_dir, roster_path):
    team_dir = tc.team_dir_of(_resolve_receipts_dir(receipts_dir))
    cfg = tc.load_config(team_dir)
    errors, warnings = check_receipts(receipts_dir, roster_path, cfg=cfg)
    for w in warnings:
        print(f"  ! {w}")
    if errors:
        print("RECEIPTS CHECK: FAIL — do not push until this is fixed:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("RECEIPTS CHECK: OK — receipt records valid.")
    return 0


def cmd_show(args):
    receipts_dir = _resolve_receipts_dir(args.receipts_dir)
    data, err = tc.load_json_file(receipt_path(receipts_dir, args.task))
    if err or not isinstance(data, dict):
        print(f"RECEIPTS: FAIL — no receipt for task {args.task} ({err or 'not a JSON object'})")
        return 1
    import json
    print(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


def main():
    p = argparse.ArgumentParser(description="Typed completion receipts for team-roster v2.6 (filed by claim.py done).")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("list", "check"):
        sp = sub.add_parser(name)
        sp.add_argument("path", nargs="?", default="team/receipts")
        if name == "check":
            sp.add_argument("--roster", default="team/roster")
    sp = sub.add_parser("show")
    sp.add_argument("--task", required=True)
    sp.add_argument("--receipts-dir", default="team/receipts")

    args = p.parse_args()
    if args.cmd == "list":
        return cmd_list(args.path)
    if args.cmd == "check":
        return cmd_check(args.path, args.roster)
    if args.cmd == "show":
        return cmd_show(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
