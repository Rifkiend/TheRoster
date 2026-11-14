---
name: team-roster
description: Know your team before you start any job, don't collide with teammates who are already working, don't rebuild a feature that already exists — continue and improve it in place instead. Load the team roster first — who is on the team, what each person or agent can do, how to reach them, and when the list was last verified — then check the work ledger, claim the task and files before touching them, search the feature registry before building anything, reuse a stable feature instead of rebuilding it, improve it in place with a version bump when it needs to grow, and register anything genuinely new you build. Use when starting any group task, assigning work, mentioning a teammate, editing a shared file, building or improving a feature, or before pushing/committing work that involves other people. Never invent a teammate who is not in the roster, never edit a file someone else has claimed, never rebuild a feature that is already in the registry, and never fork a feature to improve it — improve the original.
license: MIT
compatibility: Requires Python 3.9+ for scripts/check_roster.py, scripts/claim.py, and scripts/features.py; git for the pre-push hook and for syncing the roster, work ledger, and feature registry. Works in Claude Code, GitHub Copilot, Cursor, Codex, Gemini CLI, and any agent that reads the Agent Skills open standard (agentskills.io).
metadata:
  author: Rian (@rikan_7843)
  version: 1.3.0
  tags: team, roster, collaboration, multi-agent, pre-push, conflict-prevention, feature-registry, reuse, continue-in-place
---

# team-roster

## The idea in one sentence

Before you do any job, read the team list first. If someone is not on the list, they are not your teammate — do not invent them, do not assign work to them, do not claim you contacted them.

This works for any job, not just code: a coding team, a PKL group, a Shopee affiliate team, a freelance client team, a content team. People, AI agents, and tools can all be on the roster — what matters is that the list is written down, verified, and loaded before work starts.

## Why this exists

AI models are stateless. Without a written list, an AI does not know who your teammates are, so it guesses — and a guessed teammate is a hallucination. A roster fixes that with one rule: **the roster is the single source of truth about the team.**

The roster alone does NOT stop collisions. It tells you WHO the team is, not WHO IS DOING WHAT right now. Two disconnected AIs cannot magically see each other — the only bridge is a shared file synced through git. That is the work ledger, `team/work.json`: one file = one owner at a time, claimed before work starts, released when done. Full rules: `references/conflict-prevention.md`.

The roster + work ledger also do NOT stop duplicate features. They stop two people editing the same file at the same time, but they do not stop two people building the same feature one after another. That is the feature registry, `team/features.json`: before you build anything, search it. If a stable feature already does what you need, use it — do not rebuild it. If you build something new, register it there, so the next AI finds it instead of rebuilding it again. A feature that is not registered does not exist for the next AI. Full rules: `references/feature-registry.md`.

## Workflow — always do these 5 steps

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

### 4. Search before you build — reuse, don't rebuild

Before building any feature, search the feature registry (`team/features.json`). Search with 2–3 different keywords — one empty search does not prove the feature does not exist.

```bash
git pull
python3 scripts/features.py search login team/features.json
python3 scripts/features.py search auth team/features.json
```

- If a `stable` match exists: USE IT. Call its `entry` (`file:function`). Do not rebuild it. Then record that you use it: `python3 scripts/features.py use --id login-api --by <your-task-id>`.
- If it exists but needs to grow: **CONTINUE IT IN PLACE — do not fork it.** This is the normal case, not the exception. The first AI builds a good feature; the second AI continues it directly, so the work really builds up instead of doubling. Steps:
  1. Claim its files first (step 3) — `claim.py claim` on the feature's `files`.
  2. Improve the same files, keep the same `id` and the same `entry` if old callers must keep working.
  3. Bump the version with `features.py improve` — `patch` for a bug fix, `minor` for a new compatible capability (default), `major` only if the `entry` changes or old callers break. The script refuses an entry change without `--bump major`.
  ```bash
  python3 scripts/features.py improve --id login-api --by <your-roster-id> --bump minor --note "Tambah login Google" --does "Login email/password atau Google, mengembalikan token sesi" --files src/auth/google.py
  git add team/features.json && git commit -m "improve: login-api 1.0.0 -> 1.1.0 (login Google)" && git push
  ```
  Owner stays the original owner; you are added to `contributors` and to the `changes` log, so the next AI can see who built on what. If the improvement is a genuinely different purpose (not the same capability grown bigger), only then build a new feature — and say in the new entry why the old one could not be continued.
- If an `experimental` match exists: use it with care, or improve it in place the same way (claim its files first, step 3). Do not build a copy next to it with a similar name.
- If a `deprecated` match exists: do not use it for new work, and do not improve it — improve its replacement if one is listed.
- If there is no match: build it (after claiming, step 3), test it, then **register it before you push**:
  ```bash
  python3 scripts/features.py register --id checkout-api --name "Checkout API" --does "Create a checkout order and return its payment link" --entry src/checkout.py:create_order --files src/checkout.py tests/test_checkout.py --owner <your-roster-id> --keywords checkout order payment
  git add team/features.json && git commit -m "register: checkout-api" && git push
  ```
  A feature that is not registered will be rebuilt by the next AI — and that rebuild will not be their fault, because they could not find yours.

Never delete a feature. If it is no longer wanted, deprecate it (`features.py deprecate --id old-login --replacement login-api`) and keep the entry — someone's old work may still use it (check `used_by`). Deletion is a human decision by the owner, only after `used_by` is empty and 30 days have passed since deprecation.

### 5. Verify before you push

Before any `git push`, commit that involves teammates, or handoff to the team, run all three checks:

```bash
python3 scripts/check_roster.py team/roster.json
python3 scripts/claim.py check team/work.json
python3 scripts/features.py check team/features.json
```

(The script paths are relative to this skill folder when testing the skill itself; in a real project, copy the scripts or point them at the project's roster/work/feature files.)

- Exit 0 on all three = roster is valid and fresh, no file is claimed twice, and no feature id or entry is registered twice. Proceed.
- Exit 1 = roster is invalid/stale, the work ledger has a double claim / an owner who is not on the roster, or the feature registry has a duplicate id/entry / an owner who is not on the roster. Fix it, do not push. See the script output for exactly what is wrong.
- A roster is stale when any `active` member has `last_verified` more than 30 days ago. Update `last_verified` only after actually checking that the person is still on the team in that role — never just change the date.

The `hooks/pre-push` file in this skill is a ready git hook: install it with `scripts/install-hook.sh` and git will run all three checks automatically on every push.

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
- Do not rebuild a feature that is already in the registry, even if you think you can build it better — improve the existing one in place, or say why it cannot be reused.
- Do not fork a feature to improve it. Same capability = same `id`, same `entry`, higher version. A new `id` means a different capability.
- Do not delete a feature, a teammate, or a claim to "clean up". Deprecate / release / retire them instead, so history stays visible.

## References

- `references/roster-schema.md` — full JSON schema and validation rules.
- `references/conflict-prevention.md` — how the work ledger stops collisions, and what it does NOT guarantee (not real-time, not a filesystem lock).
- `references/feature-registry.md` — how the feature registry stops duplicate builds, and why features are deprecated, never deleted.
- `references/examples.md` — four ready rosters: software team, PKL group, Shopee affiliate team, freelance client team.
- `roster.example.json` — copy-paste starter roster.
- `work.example.json` — copy-paste starter work ledger (copy to `team/work.json`).
- `features.example.json` — copy-paste starter feature registry (copy to `team/features.json`).
- `scripts/check_roster.py` — roster validator. Run it before push.
- `scripts/claim.py` — claim / release / list / check work. Run it before touching shared work.
- `scripts/features.py` — search / register / use / improve / deprecate / check features. Run it before building anything, and `improve` it when continuing someone else's feature.
- `scripts/install-hook.sh` — installs `hooks/pre-push` into `.git/hooks/`.
