# Changelog — TheRoster

All notable changes to this skill, in order. Dates are 2026-11-14 (WITA).
The idea behind every version is the same: make it easier for a team of
humans and disconnected AIs to work together without inventing teammates,
colliding with each other, or rebuilding the same thing twice.

## v1.4.0 — 2026-11-14: remote sync check, close the blind spot

- Rian's question: if AI B improves AI A's feature and pushes, does A
  know? Honest answer: no, not in real time — A only knows when A
  pulls. New script `scripts/sync_check.py` (stdlib only, ~150 lines)
  closes the gap to seconds: it fetches the remote right before the
  push and FAILS if a feature version is newer on the remote, or a
  remote active claim covers your `--files`. No remote / no remote
  branch yet = first sync, passes with a note. Fetch fails (no
  network) = warning only, exit 0 — offline work is not blocked.
- Pre-push hook now runs all four checks (roster, work ledger, feature
  registry, remote sync). `install-hook.sh` updated.
- SKILL.md step 5 is now "Sync before you push": pull, run sync_check,
  re-read the registry; if the remote is newer, STOP, pull, and
  continue on top of it. Simultaneous-push rule: GitHub accepts the
  first push and rejects the second (non-fast-forward) — the rejected
  pusher pulls and reconciles, never `--force push`. One task = one
  branch, merge via PR, never push direct to `main`.
- New section in `references/feature-registry.md`: "Titik buta AI A —
  dan cara menutupnya" (the blind-spot window, how sync_check shrinks
  it, and what still cannot be fixed — true real-time awareness needs
  a server, which this skill deliberately does not have). Cross-
  reference added in `references/conflict-prevention.md`.
- README: badge v1.4.0, new v1.4 blockquote, step 5, blind-spot note
  under How it works, `sync_check.py` in the file tree.

## v1.3.6 — 2026-11-14: lighter flowchart, no more app crash

- Flowchart rebuilt compact and light: no dotted grid, 111 KB
  (was 243 KB), 1,116 elements (was 2,372), 1,246 pt tall (was
  1,764 pt). README now displays it at a fixed 680 px width so the
  GitHub mobile app does not try to render the full-size vector and
  force-close while scrolling past it.

- Redrew `docs/flowchart.svg` in n8n style: flat white cards on a
  dotted canvas, small circle ports, thin curved connections, no
  diamonds or heavy fills, lots of white space. Same workflow, much
  less cramped.

- Clarified `contact`: it is only a channel name. For a code team, `id`
  is the GitHub username and `contact` is just "GitHub". WhatsApp /
  Threads DM are only examples for teams whose people are not on
  GitHub. Updated in SKILL.md, README.md, and roster-schema.md.

- The Mermaid flowchart in the README is replaced by a real image,
  `docs/flowchart.svg` (vector, 70 KB), embedded in the "How it works"
  section, so the workflow is visible at a glance without rendering
  Mermaid. SVG is used instead of PNG because the GitHub connector
  only pushes UTF-8 files.

- New header: centered title, tagline "One team. Every AI. Zero
  guesswork.", badges, and a four-pillar table (know the team / claim the
  work / reuse, don't rebuild / continue in place).

- README now opens with a one-line English description and a Mermaid
  flowchart of the whole workflow (roster → plan → claim → search/reuse/
  continue/build → verify → push), so a reader sees how it works in
  seconds.

- Repo renamed to **TheRoster** (Rian's pick, 2026-11-14) — the skill inside
  stays `team-roster` (Agent Skills names are lowercase), the repo is
  `Rifkiend/TheRoster`, install with `npx skills add Rifkiend/TheRoster`.

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
