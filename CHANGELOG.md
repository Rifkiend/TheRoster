# Changelog — team-roster

All notable changes to this skill, in order. Dates are 2026-11-14 (WITA).
The idea behind every version is the same: make it easier for a team of
humans and disconnected AIs to work together without inventing teammates,
colliding with each other, or rebuilding the same thing twice.

## v1.3.0 — 2026-11-14: continue in place, don't fork

- Added `features.py improve` — the second AI continues the first AI's
  feature directly, in the same files, under the same `id`.
- Version bumps: `patch` for a bug fix, `minor` for a new compatible
  capability (default), `major` only when the `entry` changes. The script
  refuses an entry change without `--bump major`.
- Owner stays the original owner; the improver is added to `contributors`
  and to the `changes` log (version, who, note, date).
- Rule: same capability = same `id`, higher version. A new `id` means a
  different capability. Build new only if the purpose is genuinely different.
- `features.example.json` now includes `contributors` and `changes`.
- Workflow step 4 is now: search → reuse, or continue in place, or build
  new only if the purpose is genuinely different.

## v1.2.0 — 2026-11-14: feature registry, reuse don't rebuild

- Added the shared feature registry `team/features.json` and
  `scripts/features.py` (`search` / `list` / `check` / `register` / `use` /
  `deprecate`).
- Rule: search with 2–3 keywords before building anything. A stable match
  means reuse its `entry` and record the use; no match means build, then
  register before pushing.
- Duplicate `id` or `entry` registration fails — one capability, one entry.
- Features are never deleted, only deprecated (kept, with a replacement and
  `used_by` protection), so nothing others use disappears.
- Added `references/feature-registry.md`.
- Pre-push hook now runs all three checks (roster, work ledger, features).
- Honest limit: search is keyword matching, not understanding — good
  keywords are essential.

## v1.1.0 — 2026-11-14: anti-collision work ledger

- Roster alone only says WHO the team is, not WHO IS DOING WHAT, so v1.1
  added the shared work ledger `team/work.json` and `scripts/claim.py`
  (`claim` / `release` / `done` / `list` / `check`).
- Rule: pull, check the ledger, claim the task and files, push the claim,
  then work. One file = one owner at a time. Claim lasts 2 hours.
- First push wins; a rejected push means someone claimed it first.
  Never `--force push` a claim.
- Added `references/conflict-prevention.md`.
- Pre-push hook now runs both checks (roster + work ledger).
- Honest limit: not real-time — the other AI only knows after you push the
  claim and they pull — and not a filesystem lock.

## v1.0.0 — 2026-11-14: roster first

- First version. One rule: before any job, load a written team roster
  (`team/roster.json`); if someone is not on the list, they are not your
  teammate — do not invent them.
- `SKILL.md` with a 3-step workflow: load the roster, plan only with people
  on the roster, verify before pushing.
- `scripts/check_roster.py` validates the roster (invalid, or an `active`
  member not verified in the last 30 days, blocks the push).
- Git `pre-push` hook runs the roster check automatically.
- `references/roster-schema.md` and `references/examples.md` — four ready
  rosters: software team, PKL group, Shopee affiliate team, freelance
  client team.
- Built as a minimal, real version of multi-agent team awareness
  (Google A2A Agent Cards, Stanford Generative Agents / Smallville) — no
  server, no protocol, just a file + a hook.
