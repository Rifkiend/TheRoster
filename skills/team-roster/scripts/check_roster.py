#!/usr/bin/env python3
"""Validate a team-roster file before push.

Usage:
    python3 check_roster.py [path/to/roster.json]
    python3 check_roster.py team/roster.json

Exit 0 = valid and fresh, safe to push.
Exit 1 = invalid or stale, do NOT push — fix what is listed first.

Rules (see references/roster-schema.md):
- valid JSON, team_name / updated / members present
- every member has all required fields, id unique + lowercase-hyphen
- type in {human, ai-agent, tool}, status in {active, away, retired}
- can_do has 1-5 items, last_verified is a real past YYYY-MM-DD date
- freshness: every ACTIVE member verified within the last 30 days
- contact must not look like a phone number, email, or password
No third-party dependencies — stdlib only.
"""
import json
import re
import sys
from datetime import date, datetime

MAX_AGE_DAYS = 30
ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SENSITIVE_RE = re.compile(r"@|\+?\d[\d\s\-]{7,}\d|password|passwd|token", re.IGNORECASE)
REQUIRED = ["id", "name", "type", "role", "can_do", "contact", "status", "last_verified"]
TYPES = {"human", "ai-agent", "tool"}
STATUSES = {"active", "away", "retired"}


def fail(errors):
    print("ROSTER CHECK: FAIL — do not push until this is fixed:")
    for e in errors:
        print(f"  - {e}")
    return 1


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "team/roster.json"
    errors = []

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return fail([f"roster not found: {path} (copy roster.example.json to team/roster.json first)"])
    except json.JSONDecodeError as e:
        return fail([f"roster is not valid JSON: {e}"])

    if not isinstance(data, dict):
        return fail(["roster must be a JSON object"])
    for field in ("team_name", "updated", "members"):
        if not data.get(field):
            errors.append(f"missing top-level field: {field}")
    members = data.get("members")
    if not isinstance(members, list) or not members:
        errors.append("members must be a non-empty list")
        return fail(errors)

    today = date.today()
    seen_ids = set()
    for i, m in enumerate(members):
        label = m.get("id") or m.get("name") or f"members[{i}]"
        if not isinstance(m, dict):
            errors.append(f"{label}: member must be an object")
            continue
        for field in REQUIRED:
            if field not in m or m[field] in (None, "", []):
                errors.append(f"{label}: missing/empty field: {field}")
        mid = m.get("id", "")
        if mid:
            if not ID_RE.match(str(mid)):
                errors.append(f"{label}: id must be lowercase letters, numbers and hyphens only")
            if mid in seen_ids:
                errors.append(f"{label}: duplicate id")
            seen_ids.add(mid)
        if m.get("type") and m["type"] not in TYPES:
            errors.append(f"{label}: type must be human, ai-agent, or tool (got {m['type']})")
        if m.get("status") and m["status"] not in STATUSES:
            errors.append(f"{label}: status must be active, away, or retired (got {m['status']})")
        can_do = m.get("can_do")
        if isinstance(can_do, list) and not (1 <= len(can_do) <= 5):
            errors.append(f"{label}: can_do must have 1-5 items (got {len(can_do)})")
        elif can_do is not None and not isinstance(can_do, list):
            errors.append(f"{label}: can_do must be a list")
        lv = m.get("last_verified", "")
        if lv:
            if not DATE_RE.match(str(lv)):
                errors.append(f"{label}: last_verified must be YYYY-MM-DD (got {lv})")
            else:
                try:
                    d = datetime.strptime(str(lv), "%Y-%m-%d").date()
                except ValueError:
                    errors.append(f"{label}: last_verified is not a real date: {lv}")
                    d = None
                if d:
                    if d > today:
                        errors.append(f"{label}: last_verified is in the future: {lv}")
                    elif m.get("status") == "active" and (today - d).days > MAX_AGE_DAYS:
                        errors.append(
                            f"{label}: STALE — active member last verified {lv} "
                            f"({(today - d).days} days ago, max {MAX_AGE_DAYS}). "
                            "Re-verify for real, then update the date."
                        )
        if m.get("contact") and SENSITIVE_RE.search(str(m["contact"])):
            errors.append(f"{label}: contact looks like a phone/email/password — use a channel name only (e.g. WhatsApp)")

    if errors:
        return fail(errors)

    active = [m["id"] for m in members if m.get("status") == "active"]
    print(f"ROSTER CHECK: OK — {data.get('team_name')} — {len(members)} members, {len(active)} active, all verified within {MAX_AGE_DAYS} days.")
    for m in members:
        print(f"  - {m['id']} ({m['type']}, {m['status']}): {m['role']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
