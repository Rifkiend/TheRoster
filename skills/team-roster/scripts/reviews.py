#!/usr/bin/env python3
"""Formal review record for team-roster v2.6: who reviewed what,
what verdict, which lines, what the worker must fix.

Orca's best UX is Annotate AI Diff — comment on an exact line, the
agent gets focused feedback. TheRoster has no UI and will not
pretend to build one, but the *record* is file-able, and the merge
decision is currently an act with no artifact. This makes it one.

Sharded layout (v2.6): one file per review in team/reviews/<id>.json.
team/index.json is GENERATED (scripts/rebuild_index.py), never
hand-edited. team/events.jsonl is the append-only audit log.

Usage:
    python3 reviews.py list   [team/reviews]
    python3 reviews.py check  [team/reviews] [--roster team/roster]
    python3 reviews.py create --id r-001 --task t-101-a --reviewer teammate-1 \
        --verdict request-changes --note "Timeout path untested" \
        --annotations "src/checkout.py:42:timeout still unhandled here" \
        [--key ~/.ssh/id_ed25519]

Rules:
- Exactly one target: a claim (--task), a PRD item (--prd), or a
  feature (--feature). The target must exist.
- The reviewer must be an active roster member AND must differ
  from the subject's owner (claim owner / PRD owner / feature
  owner — the same rule as stable promotion: nobody reviews their
  own work).
- Verdict is approve or request-changes. Annotations are
  file:line:note triples (valid file path, positive line number,
  non-empty note).
- Delivery: creating a review appends one line to the reviewed
  worker's mailbox file (team/mailbox/<owner>.jsonl) — the
  file-based version of "send the comment back to the agent".
  The line carries ONLY the review id, the verdict word, and a
  pointer to the record file — no reviewer free text — so it can
  never trip the imperative-word scan validate.py runs on the
  mailbox: the verdict word is "accepted" / "changes-requested",
  never the bare word "approve". The worker reads the notes in
  team/reviews/<id>.json. When require_signatures is on and the
  reviewer is human, the delivery line MUST be signed (--key
  required — same scheme as mailbox.py sign); an unsigned human
  delivery line would FAIL validation, so the review is refused
  instead of half-delivered.
- Effect: a latest request-changes review blocks the PRD
  done-gate (prd.py status --to done and validate.py) — on the
  item itself, on any of its features, or on any claim bound to
  the item. A newer approve review on the same subject clears it.
  A request-changes review re-opens work as a follow-up claim:
  claim.py claim --review <review-id> (only a request-changes
  review takes a follow-up) records review_id on the claim.
- Every creation appends a review_created event; a gate
  resolution citing a review appends gate_resolved with the
  review id. No status changes silently.
- Every mutating command bumps lamport (+1), appends one line to
  team/events.jsonl, and rebuilds team/index.json.

Exit 0 = ok. Exit 1 = invalid, duplicate, or forbidden.
Stdlib only, Python 3.9+.
"""
import argparse
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import team_common as tc

VERDICTS = {"approve", "request-changes"}
# The verdict word used in the mailbox delivery line. "approve"
# would trip the imperative-word scan (\bapprove\b) for a human
# reviewer, so the delivery says "accepted" / "changes-requested"
# and the record file carries the real verdict.
DELIVERY_VERDICT = {"approve": "accepted", "request-changes": "changes-requested"}


def review_path(reviews_dir, rid):
    return os.path.join(reviews_dir, rid + ".json")


def _guard_mutation(reviews_dir, action):
    """Error string when reviews_dir is inside a linked worktree, else None.

    team/ state is only written from the main checkout (v2.3,
    Dziatkovskii rule) — a mutation from inside a worktree lands on
    the worktree branch and is invisible to the rest of the team."""
    return tc.guard_team_mutation(tc.team_dir_of(reviews_dir), action)


def _resolve_reviews_dir(reviews_dir):
    """Redirect a reviews dir inside a linked worktree to the main
    checkout's reviews dir (reads only; prints a note on stderr)."""
    team_dir = tc.team_dir_of(reviews_dir)
    team_dir, note = tc.resolve_team_dir(team_dir)
    if note:
        print("REVIEWS: note — " + note, file=sys.stderr)
        return os.path.join(team_dir, "reviews")
    return reviews_dir


def load_reviews(reviews_dir):
    return tc.load_records(reviews_dir)


def load_roster(team_dir):
    """(active_ids, human_ids, by_id, error)."""
    roster_dir = os.path.join(team_dir, "roster")
    active, humans, by_id = set(), set(), {}
    if not os.path.isdir(roster_dir):
        return active, humans, by_id, "roster dir not found: %s" % roster_dir
    for _stem, (_path, rec) in tc.load_records(roster_dir).items():
        if not isinstance(rec, dict) or not rec.get("id"):
            continue
        by_id[rec["id"]] = rec
        if rec.get("status") == "active":
            active.add(rec["id"])
        if rec.get("type") == "human":
            humans.add(rec["id"])
    return active, humans, by_id, None


def target_exists(team_dir, kind, sid):
    subdir = {"claim": "claims", "prd": "prd", "feature": "features"}.get(kind)
    if not subdir:
        return False
    data, _err = tc.load_json_file(os.path.join(team_dir, subdir, sid + ".json"))
    return isinstance(data, dict)


def parse_annotation(raw):
    """Parse "file:line:note". Returns (dict, error)."""
    parts = str(raw).split(":", 2)
    if len(parts) != 3:
        return None, "annotation %r must be file:line:note (e.g. src/x.py:42:timeout unhandled)" % raw
    fpath, line_s, note = parts
    err = tc.path_syntax_error(fpath)
    if err:
        return None, "annotation %r: %s" % (raw, err)
    try:
        line_no = int(line_s)
    except ValueError:
        return None, "annotation %r: line %r is not a number" % (raw, line_s)
    if line_no < 1:
        return None, "annotation %r: line must be a positive number" % raw
    if not note.strip():
        return None, "annotation %r: note must not be empty" % raw
    return {"file": fpath, "line": line_no, "note": note.strip()}, None


def rebuild(team_dir):
    try:
        import rebuild_index
        rebuild_index.rebuild(team_dir)
    except Exception as e:  # index rebuild must never fail a mutation
        print(f"REVIEWS: warning — could not rebuild team/index.json: {e}", file=sys.stderr)


def check_reviews(reviews_dir, roster_path=None):
    """Validate every review record. Returns (errors, warnings).

    Shared by `reviews.py check` and validate.py — the per-script
    check is the single source of the rules."""
    reviews_dir = _resolve_reviews_dir(reviews_dir)
    team_dir = tc.team_dir_of(reviews_dir)
    errors, warnings = [], []
    reviews = load_reviews(reviews_dir)
    active = None
    by_id = {}
    if roster_path and os.path.isdir(roster_path):
        for _stem, (_path, rec) in tc.load_records(roster_path).items():
            if isinstance(rec, dict) and rec.get("id"):
                by_id[rec["id"]] = rec
        active = {rid for rid, m in by_id.items() if m.get("status") == "active"}
    for rid, (_path, rec) in sorted(reviews.items()):
        label = rid
        if not tc.valid_id(rid):
            errors.append(f"{label}: id fails the id grammar ^[a-z0-9][a-z0-9-]*$ (max 64 chars, ASCII only)")
            continue
        if not isinstance(rec, dict):
            errors.append(f"{label}: not a JSON object")
            continue
        if rec.get("id") != rid:
            errors.append(f"{label}: id inside the file ({rec.get('id')!r}) does not match the filename — one review per file, named <id>.json")
        for field in ("id", "reviewer", "verdict", "note", "created_at", "lamport"):
            if field not in rec or rec[field] in (None, ""):
                if field == "lamport" and rec.get(field) == 0:
                    continue
                errors.append(f"{label}: missing/empty field: {field}")
        targets = [k for k in ("task_id", "prd_item", "feature_id") if rec.get(k)]
        if len(targets) != 1:
            errors.append(f"{label}: exactly one target required (task_id / prd_item / feature_id), got {len(targets)}")
        else:
            kind = {"task_id": "claim", "prd_item": "prd", "feature_id": "feature"}[targets[0]]
            sid = rec[targets[0]]
            if not tc.valid_id(sid):
                errors.append(f"{label}: target id {sid!r} fails the id grammar")
            elif not target_exists(team_dir, kind, sid):
                errors.append(f"{label}: target {kind} {sid} does not exist")
            else:
                owner = tc.gated_subject_owner(team_dir, kind, sid)
                if owner and rec.get("reviewer") == owner:
                    errors.append(f"{label}: reviewer {rec['reviewer']} owns the reviewed {kind} {sid} — "
                                  "nobody reviews their own work")
        if rec.get("verdict") not in VERDICTS:
            errors.append(f"{label}: verdict must be approve or request-changes (got {rec.get('verdict')!r})")
        if not isinstance(rec.get("note"), str) or not rec.get("note", "").strip():
            errors.append(f"{label}: note must be a non-empty string")
        annotations = rec.get("annotations")
        if annotations is None:
            errors.append(f"{label}: missing annotations (use [] for none)")
        elif not isinstance(annotations, list):
            errors.append(f"{label}: annotations must be a list")
        else:
            for ann in annotations:
                if not isinstance(ann, dict):
                    errors.append(f"{label}: annotation {ann!r} is not an object")
                    continue
                err = tc.path_syntax_error(ann.get("file") or "")
                if err:
                    errors.append(f"{label}: annotation: {err}")
                if not isinstance(ann.get("line"), int) or ann.get("line", 0) < 1:
                    errors.append(f"{label}: annotation line must be a positive number (got {ann.get('line')!r})")
                if not isinstance(ann.get("note"), str) or not ann.get("note", "").strip():
                    errors.append(f"{label}: annotation note must not be empty")
        if active is not None and rec.get("reviewer") not in active:
            errors.append(f"{label}: reviewer {rec.get('reviewer')} is not an active roster member")
        if rec.get("created_at") and tc.parse_time(rec["created_at"]) is None:
            errors.append(f"{label}: created_at must be a UTC ISO-8601 timestamp ending in Z (got {rec['created_at']!r})")
        if not isinstance(rec.get("lamport"), int) or rec.get("lamport", 0) < 1:
            errors.append(f"{label}: lamport must be an int >= 1")
    return errors, warnings


def delivery_message(review_id, kind, subject_id, verdict, n_annotations):
    """The mailbox line for a review: id + verdict word + pointer.

    No reviewer free text — the note and the annotation notes live
    in team/reviews/<id>.json, so the delivery can never trip the
    imperative-word scan (no URLs, no 'approve', no orders)."""
    return ("Review %s on %s %s — verdict: %s. %d annotation(s). "
            "See team/reviews/%s.json."
            % (review_id, kind, subject_id, DELIVERY_VERDICT[verdict],
               n_annotations, review_id))


def deliver_mailbox(team_dir, reviewer, owner_id, message, key_path, require_sigs):
    """Append the delivery line to the worker's mailbox file.

    When require_signatures is on and the reviewer is human, the
    line is signed via mailbox.py (same scheme as mailbox.py sign);
    otherwise it is appended unsigned. Returns (ok, error). The
    caller validates the key requirement before writing the review
    record, so a signing failure here means nothing was recorded
    yet — the caller must not proceed to write the record."""
    roster_dir = os.path.join(team_dir, "roster")
    by_id = {}
    for _stem, (_path, rec) in tc.load_records(roster_dir).items():
        if isinstance(rec, dict) and rec.get("id"):
            by_id[rec["id"]] = rec
    reviewer_rec = by_id.get(reviewer) or {}
    is_human = reviewer_rec.get("type") == "human"
    if require_sigs and is_human:
        import mailbox
        args = types.SimpleNamespace(
            sender=reviewer, to=owner_id, message=message,
            key=key_path or "", ts="", team=team_dir)
        rc = mailbox.cmd_sign(args)
        if rc != 0:
            return (False, "could not sign the mailbox delivery line (see above)")
        return (True, None)
    mdir = os.path.join(team_dir, "mailbox")
    os.makedirs(mdir, exist_ok=True)
    line = {"from": reviewer, "message": message, "ts": tc.now_iso()}
    if not tc.append_jsonl(os.path.join(mdir, owner_id + ".jsonl"), line):
        return (False, "could not append to team/mailbox/%s.jsonl" % owner_id)
    return (True, None)


# ---------------------------------------------------------------- commands

def cmd_list(reviews_dir):
    reviews_dir = _resolve_reviews_dir(reviews_dir)
    reviews = load_reviews(reviews_dir)
    if not reviews:
        print("REVIEWS: no reviews yet — create one with: python3 reviews.py create --id r-001 --task t-001 --reviewer <you> --verdict approve --note ...")
        return 0
    print(f"REVIEWS: {len(reviews)} review(s):")
    for rid in sorted(reviews):
        rec = reviews[rid][1]
        if not isinstance(rec, dict):
            print(f"  - {rid}: UNREADABLE (not a JSON object)")
            continue
        target = rec.get("task_id") or rec.get("prd_item") or rec.get("feature_id") or "?"
        tkind = ("claim" if rec.get("task_id") else
                 "prd" if rec.get("prd_item") else "feature")
        print(f"  - {rid} [{rec.get('verdict')}] — {tkind} {target} "
              f"(reviewer: {rec.get('reviewer')}, {len(rec.get('annotations') or [])} annotation(s)) — {rec.get('note')}")
    return 0


def cmd_check(reviews_dir, roster_path):
    errors, warnings = check_reviews(reviews_dir, roster_path)
    for w in warnings:
        print(f"  ! {w}")
    if errors:
        print("REVIEWS CHECK: FAIL — do not push until this is fixed:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("REVIEWS CHECK: OK — review records valid.")
    return 0


def cmd_create(args):
    guard = _guard_mutation(args.reviews_dir, "create a review")
    if guard:
        print("REVIEWS: FAIL — " + guard)
        return 1
    reviews_dir = args.reviews_dir
    os.makedirs(reviews_dir, exist_ok=True)
    team_dir = tc.team_dir_of(reviews_dir)
    cfg = tc.load_config(team_dir)
    if not tc.valid_id(args.id):
        print(f"REVIEWS: FAIL — id {args.id!r} fails the id grammar ^[a-z0-9][a-z0-9-]*$ (max 64 chars, ASCII only)")
        return 1
    targets = [(k, v) for k, v in (("claim", args.task), ("prd", args.prd),
                                   ("feature", args.feature)) if v]
    if len(targets) != 1:
        print("REVIEWS: FAIL — give exactly one target: --task, --prd, or --feature")
        return 1
    kind, sid = targets[0]
    if not tc.valid_id(sid):
        print(f"REVIEWS: FAIL — target id {sid!r} fails the id grammar")
        return 1
    if not target_exists(team_dir, kind, sid):
        print(f"REVIEWS: FAIL — target {kind} {sid} does not exist")
        return 1
    if args.verdict not in VERDICTS:
        print(f"REVIEWS: FAIL — --verdict must be approve or request-changes (got {args.verdict!r})")
        return 1
    if not args.note.strip():
        print("REVIEWS: FAIL — --note must not be empty (say what the verdict rests on)")
        return 1
    annotations = []
    for raw in (args.annotations or []):
        ann, err = parse_annotation(raw)
        if err:
            print("REVIEWS: FAIL — " + err)
            return 1
        annotations.append(ann)
    active, humans, by_id, rerr = load_roster(team_dir)
    if rerr:
        print(f"REVIEWS: warning — {rerr}; reviewer not verified against the roster.")
    elif args.reviewer not in active:
        print(f"REVIEWS: FAIL — reviewer {args.reviewer} is not an active roster member")
        return 1
    owner = tc.gated_subject_owner(team_dir, kind, sid)
    if owner and args.reviewer == owner:
        print(f"REVIEWS: FAIL — {args.reviewer} owns the reviewed {kind} {sid} — nobody reviews their own work")
        return 1
    if not owner:
        print(f"REVIEWS: FAIL — target {kind} {sid} has no owner_id — cannot deliver the review to anyone")
        return 1
    path = review_path(reviews_dir, args.id)
    if os.path.exists(path):
        print(f"REVIEWS: FAIL — id {args.id} already exists. Use it, or pick a new id.")
        return 1
    # Signing requirement, checked BEFORE anything is written: when
    # require_signatures is on and the reviewer is human, the
    # mailbox delivery line must be signed — an unsigned human
    # delivery would FAIL validation, so refuse instead of
    # half-delivering the review.
    require_sigs = bool(cfg.get("require_signatures"))
    is_human = (by_id.get(args.reviewer) or {}).get("type") == "human"
    if require_sigs and is_human:
        if not args.key or not os.path.isfile(args.key):
            print("REVIEWS: FAIL — require_signatures is on and the reviewer is human: "
                  "the mailbox delivery line must be signed — pass --key <your-private-key> "
                  "(it never leaves your machine)")
            return 1
        if not (by_id.get(args.reviewer) or {}).get("public_key"):
            print(f"REVIEWS: FAIL — reviewer {args.reviewer} has no public_key on the roster — "
                  "add a key (human-approved roster change) before signing as them")
            return 1
    now = tc.now_iso()
    field = {"claim": "task_id", "prd": "prd_item", "feature": "feature_id"}[kind]
    rec = {
        "id": args.id,
        "task_id": sid if kind == "claim" else "",
        "prd_item": sid if kind == "prd" else "",
        "feature_id": sid if kind == "feature" else "",
        "reviewer": args.reviewer,
        "verdict": args.verdict,
        "note": args.note,
        "annotations": annotations,
        "created_at": now,
        "lamport": 1,
    }
    # Delivery first: the mailbox line points at the review id, and
    # a signing failure must leave NOTHING recorded — the review
    # record is written only after the delivery succeeds.
    msg = delivery_message(args.id, kind, sid, args.verdict, len(annotations))
    ok, err = deliver_mailbox(team_dir, args.reviewer, owner,
                              msg, args.key, require_sigs)
    if not ok:
        print("REVIEWS: FAIL — " + (err or "mailbox delivery failed"))
        return 1
    tc.save_json(path, rec)
    tc.append_event(team_dir, "review_created", 1, args.reviewer,
                    base_commit=tc.git_head_commit(),
                    extra={"review_id": args.id, "verdict": args.verdict,
                           field: sid})
    rebuild(team_dir)
    print(f"REVIEWS: created {args.id} [{args.verdict}] on {kind} {sid} by {args.reviewer}.")
    print(f"Delivered to {owner}'s mailbox (team/mailbox/{owner}.jsonl). "
          "Commit + push the review file AND the mailbox file.")
    return 0


def main():
    p = argparse.ArgumentParser(description="Formal review records for team-roster v2.6: who reviewed what, what verdict.")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("list", "check"):
        sp = sub.add_parser(name)
        sp.add_argument("path", nargs="?", default="team/reviews")
        if name == "check":
            sp.add_argument("--roster", default="team/roster")
    sp = sub.add_parser("create")
    sp.add_argument("--id", required=True)
    sp.add_argument("--task", default="")
    sp.add_argument("--prd", default="")
    sp.add_argument("--feature", default="")
    sp.add_argument("--reviewer", required=True)
    sp.add_argument("--verdict", required=True, choices=sorted(VERDICTS))
    sp.add_argument("--note", required=True)
    sp.add_argument("--annotations", nargs="*", default=[],
                    help="file:line:note, repeatable, e.g. src/x.py:42:timeout unhandled")
    sp.add_argument("--key", default="",
                    help="reviewer's SSH private key — required when require_signatures is on "
                         "and the reviewer is human (signs the mailbox delivery line)")
    sp.add_argument("--reviews-dir", default="team/reviews")

    args = p.parse_args()
    if args.cmd == "list":
        return cmd_list(args.path)
    if args.cmd == "check":
        return cmd_check(args.path, args.roster)
    if args.cmd == "create":
        return cmd_create(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
