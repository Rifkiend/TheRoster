---
name: team-roster
description: Know your team before you start any job. Load the team roster first — who is on the team, what each person or agent can do, how to reach them, and when the list was last verified — then plan and split work only among people on that list. Use when starting any group task, assigning work, mentioning a teammate, or before pushing/committing work that involves other people. Never invent a teammate who is not in the roster.
license: MIT
compatibility: Requires Python 3.9+ for scripts/check_roster.py; git for the pre-push hook. Works in Claude Code, GitHub Copilot, Cursor, Codex, Gemini CLI, and any agent that reads the Agent Skills open standard (agentskills.io).
metadata:
  author: Rian (@rikan_7843)
  version: 1.0.0
  tags: team, roster, collaboration, multi-agent, pre-push
---

# team-roster

## The idea in one sentence

Before you do any job, read the team list first. If someone is not on the list, they are not your teammate — do not invent them, do not assign work to them, do not claim you contacted them.

This works for any job, not just code: a coding team, a PKL group, a Shopee affiliate team, a freelance client team, a content team. People, AI agents, and tools can all be on the roster — what matters is that the list is written down, verified, and loaded before work starts.

## Why this exists

AI models are stateless. Without a written list, an AI does not know who your teammates are, so it guesses — and a guessed teammate is a hallucination. A roster fixes that with one rule: **the roster is the single source of truth about the team.**

## Workflow — always do these 3 steps

### 1. Load the roster first

Find the roster before doing anything else. Look in this order:

1. `team/roster.json` in the current project
2. `friends/roster.json`
3. `roster.json` in the project root
4. The example in this skill: `roster.example.json` (copy it to start a new roster, do not use its example people as real teammates)

Read the whole roster into context. Note for each entry: `id`, `name`, `role`, `can_do`, `contact`, `status`, `last_verified`.

If no roster exists, STOP and say so. Offer to create one from `roster.example.json`. Do not proceed by guessing the team.

### 2. Plan only with people on the roster

- Assign a task only to someone whose `can_do` matches the task and whose `status` is `active`.
- If the task needs a skill nobody on the roster has, say that plainly: "nobody on the team can do X" — do not invent a new teammate to fill the gap.
- When you mention a teammate in a message, draft, or report, use only their roster `name` and `role`. Never add a teammate from memory, from another project, or from a guess.
- `status: away` means: can be mentioned, cannot be assigned new work right now.
- `status: retired` means: keep on the list for history, never assign work. Do not delete retired people — history prevents re-inventing them later.

Types of teammates (`type` field): `human`, `ai-agent`, or `tool`. A tool is not a person — never assign human judgement work to a tool entry, and never message a tool as if it were a person.

### 3. Verify before you push

Before any `git push`, commit that involves teammates, or handoff to the team, run the check:

```bash
python3 scripts/check_roster.py team/roster.json
```

(The script path is relative to this skill folder when testing the skill itself; in a real project, copy the script or point it at the project's roster.)

- Exit 0 = roster is valid and fresh. Proceed.
- Exit 1 = roster is invalid or stale. Fix it, do not push. See the script output for exactly what is wrong.
- A roster is stale when any `active` member has `last_verified` more than 30 days ago. Update `last_verified` only after actually checking that the person is still on the team in that role — never just change the date.

The `hooks/pre-push` file in this skill is a ready git hook: install it with `scripts/install-hook.sh` and git will run the check automatically on every push.

## Roster format

See `references/roster-schema.md` for the full schema. Minimal entry:

```json
{
  "id": "rian",
  "name": "Rian",
  "type": "human",
  "role": "owner — decides, approves, runs the Shopee app",
  "can_do": ["approve posts", "pull Komisi Ekstra links", "record voice"],
  "contact": "WhatsApp",
  "status": "active",
  "last_verified": "2026-11-14"
}
```

Rules for the file itself:
- `id` is lowercase, hyphens only, unique. One person = one id, even if they have two roles.
- `can_do` is a short list of verbs, not a biography. If it is longer than 5 items, the role is too vague — split it.
- No passwords, no phone numbers, no private emails in the roster. `contact` is a channel name ("WhatsApp", "Threads DM", "email"), not the address itself.

## What NOT to do

- Do not invent a teammate to make a plan look complete.
- Do not copy a teammate from another project into this one without verifying them here.
- Do not treat an example roster as a real team.
- Do not silently drop a teammate from the roster when they go quiet — set `status: away` first, `retired` only when confirmed.
- Do not update `last_verified` without checking.

## References

- `references/roster-schema.md` — full JSON schema and validation rules.
- `references/examples.md` — four ready rosters: software team, PKL group, Shopee affiliate team, freelance client team.
- `roster.example.json` — copy-paste starter roster.
- `scripts/check_roster.py` — validator. Run it before push.
- `scripts/install-hook.sh` — installs `hooks/pre-push` into `.git/hooks/`.
