---
name: team-roster
description: Know your team before you start any job, and don't collide with teammates who are already working. Load the team roster first — who is on the team, what each person or agent can do, how to reach them, and when the list was last verified — then check the work ledger, claim the task and files before touching them, and release the claim when done. Use when starting any group task, assigning work, mentioning a teammate, editing a shared file, or before pushing/committing work that involves other people. Never invent a teammate who is not in the roster, and never edit a file someone else has claimed.
license: MIT
compatibility: Requires Python 3.9+ for scripts/check_roster.py and scripts/claim.py; git for the pre-push hook and for syncing the work ledger. Works in Claude Code, GitHub Copilot, Cursor, Codex, Gemini CLI, and any agent that reads the Agent Skills open standard (agentskills.io).
metadata:
  author: Rian (@rikan_7843)
  version: 1.1.0
  tags: team, roster, collaboration, multi-agent, pre-push, conflict-prevention
---

# team-roster

## The idea in one sentence

Before you do any job, read the team list first. If someone is not on the list, they are not your teammate — do not invent them, do not assign work to them, do not claim you contacted them.

This works for any job, not just code: a coding team, a PKL group, a Shopee affiliate team, a freelance client team, a content team. People, AI agents, and tools can all be on the roster — what matters is that the list is written down, verified, and loaded before work starts.

## Why this exists

AI models are stateless. Without a written list, an AI does not know who your teammates are, so it guesses — and a guessed teammate is a hallucination. A roster fixes that with one rule: **the roster is the single source of truth about the team.**

The roster alone does NOT stop collisions. It tells you WHO the team is, not WHO IS DOING WHAT right now. Two disconnected AIs cannot magically see each other — the only bridge is a shared file synced through git. That is the work ledger, `team/work.json`: one file = one owner at a time, claimed before work starts, released when done. Full rules: `references/conflict-prevention.md`.

## Workflow — always do these 4 steps

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

### 3. Claim the work before you touch it — no claim, no edit

The roster tells you who CAN do the work. The work ledger (`team/work.json`) tells you who IS doing it right now. Never skip this step, even for a "small" edit.

```bash
git pull
python3 scripts/claim.py list team/work.json
python3 scripts/claim.py claim --task t-002 --title "Kerjakan checkout" --owner <your-roster-id> --files src/checkout.py tests/test_checkout.py --branch feat/checkout
git add team/work.json && git commit -m "claim: t-002 kerjakan checkout" && git push
```

- If `claim` says FAIL (someone else already claimed one of those files), STOP. Pick other work or wait. Do not edit the file anyway.
- If your `git push` of the claim is rejected, someone claimed it first — `git pull`, re-read `work.json`, and stop if it conflicts. Never `--force push` a claim.
- Claim lasts 2 hours. If the work takes longer, claim again with the same `--task` to extend it. Do not keep working after expiry without re-claiming.
- Work on your own branch (one task = one branch). Only touch files in your claim. If you need another file, claim it first.
- When done, release it right away: `python3 scripts/claim.py done --task t-002`, then commit + push. Merge through a pull request, never push straight to `main`.

For non-code jobs, "files" can be the thing being claimed instead of a path: `produk: isku-kunci-sok`, `slot: threads 2026-11-15 pagi`, `alat: modem orbit`, `laporan: klien X — november`. Same rule: one owner at a time.

### 4. Verify before you push

Before any `git push`, commit that involves teammates, or handoff to the team, run both checks:

```bash
python3 scripts/check_roster.py team/roster.json
python3 scripts/claim.py check team/work.json
```

(The script paths are relative to this skill folder when testing the skill itself; in a real project, copy the scripts or point them at the project's roster/work files.)

- Exit 0 on both = roster is valid and fresh, and no file is claimed twice. Proceed.
- Exit 1 = roster is invalid/stale, or the work ledger has a double claim / an owner who is not on the roster. Fix it, do not push. See the script output for exactly what is wrong.
- A roster is stale when any `active` member has `last_verified` more than 30 days ago. Update `last_verified` only after actually checking that the person is still on the team in that role — never just change the date.

The `hooks/pre-push` file in this skill is a ready git hook: install it with `scripts/install-hook.sh` and git will run both checks automatically on every push.

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
- Do not edit a file someone else has claimed, even for a "tiny fix".
- Do not `--force push` a claim or a roster change.
- Do not claim a whole folder or repo — a claim that broad blocks everyone.

## References

- `references/roster-schema.md` — full JSON schema and validation rules.
- `references/conflict-prevention.md` — how the work ledger stops collisions, and what it does NOT guarantee (not real-time, not a filesystem lock).
- `references/examples.md` — four ready rosters: software team, PKL group, Shopee affiliate team, freelance client team.
- `roster.example.json` — copy-paste starter roster.
- `work.example.json` — copy-paste starter work ledger (copy to `team/work.json`).
- `scripts/check_roster.py` — roster validator. Run it before push.
- `scripts/claim.py` — claim / release / list / check work. Run it before touching shared work.
- `scripts/install-hook.sh` — installs `hooks/pre-push` into `.git/hooks/`.
