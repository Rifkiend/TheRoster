# Roster schema — team-roster v1

File location in a real project: `team/roster.json` (preferred) or `friends/roster.json` or `roster.json` in the project root.

## Top-level fields

| Field | Required | Meaning |
|---|---|---|
| `team_name` | yes | Human-readable team name. |
| `updated` | yes | `YYYY-MM-DD` — last time anyone edited this file. |
| `members` | yes | Array, at least 1 entry. |

## Member fields

| Field | Required | Rules |
|---|---|---|
| `id` | yes | Lowercase, numbers and hyphens only, e.g. `rian`, `teman-pkl-1`. Must be unique in the file. |
| `name` | yes | Display name as the team actually calls them. |
| `type` | yes | `human`, `ai-agent`, or `tool`. |
| `role` | yes | One short line: what this person is responsible for. |
| `can_do` | yes | Array of 1–5 short verb phrases, e.g. `["approve posts", "record voice"]`. |
| `contact` | yes | Channel name only, e.g. `WhatsApp`, `Threads DM`, `email`, `Muse chat`. Never a phone number, address, or password. |
| `status` | yes | `active`, `away`, or `retired`. |
| `last_verified` | yes | `YYYY-MM-DD` — last date someone actually confirmed this person is still on the team in this role. |

## Status meanings

- `active` — can be assigned work.
- `away` — still on the team, cannot take new work right now. Can be mentioned.
- `retired` — no longer on the team. Keep the entry for history, never assign work.

## Validation rules (enforced by `scripts/check_roster.py`)

1. File must be valid JSON with `team_name`, `updated`, `members`.
2. Every member has all required fields, no empty strings.
3. `id` unique, lowercase-hyphen format.
4. `type` is one of `human`, `ai-agent`, `tool`.
5. `status` is one of `active`, `away`, `retired`.
6. `can_do` has 1–5 items.
7. `last_verified` is a real `YYYY-MM-DD` date, not in the future.
8. Freshness: every `active` member must have `last_verified` within the last 30 days. Stale = fail, push is blocked until it is re-verified (actually checked, not just re-dated).
9. No member may have `contact` that looks like a phone number, email address, or password (basic pattern check — the real rule is human judgement, the script only catches the obvious cases).

## Weekly ritual (2 minutes)

Once a week, before the first push of the week:
1. Run `python3 scripts/check_roster.py team/roster.json`.
2. For anyone who left or changed role: update their entry, set `retired` if they left, never delete.
3. For anyone still active: confirm and update `last_verified` to today.
