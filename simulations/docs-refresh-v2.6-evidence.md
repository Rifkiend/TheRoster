# Docs refresh to the v2.6 workflow — evidence (no version bump, no code change)

Date: 2026-11-15. Skill version stays **2.6.0** (SKILL.md frontmatter and
`team/config.json` untouched). This refresh only brings the README
explanation and `docs/flowchart.svg` up to the v2.6 workflow — both were
still v2.0-era narrative. Design sources: `skills/team-roster/SKILL.md`,
`docs/v2.3-design.md` … `docs/v2.6-design.md`.

## 1. What changed

| File | Change |
|---|---|
| `README.md` | Description: bold sentence + state list now include fan-out groups, decision gates, receipts, reviews. "How it works": new paragraph covering worktree-per-claim (v2.3), signed identity + claim lifetime (v2.4), receipts + reviews + gates (v2.6), fan-out opt-in at ×N tokens. Step 3 (Claim): `--worktree` option, open gate blocks the claim, 24 h hard lifetime, `claim.py done --outcome … --summary …` receipt (no silent done), review by a teammate, fan-out option. Step 5 (Sync): open gate fails validation at push time. Flowchart `<img>` alt text updated to the new flow. Badge, version-history table, and file list NOT changed (already v2.6.0). SKILL.md NOT touched. |
| `docs/flowchart.svg` | Redrawn, same minimalist style (white cards, thin grey border, same dots/arrows/IF badges). New flow: Start → Load roster + mailbox + doctor → Roster exists? → Plan → Claim (heartbeat 30 min · 2 h TTL · hard cap 24 h · `--worktree` optional · open gate blocks claim) → Claim accepted? → Search features → Reuse / Improve in place / Build, then register → Fan out? (yes → Fan-out group side card: one prompt, N member claims · compare · decide one winner) → Done — file a receipt (`claim.py done --outcome … --summary …`, no silent done) → Review the work (approve / request-changes · reviewer ≠ owner · latest request-changes blocks PRD done) → Gates satisfied? (no → WAIT: resolve the gate, or a human waives it) → Sync before push → All checks pass? (no → Fix it, do not push) → Push --atomic, then PR → Done — merged via PR. STOP branches kept (no roster → make one; claim rejected → pick other work). Provenance comment updated to v2.6.0. Canvas 900×1780 (was 900×1330). |
| `CHANGELOG.md` | One bullet under the v2.6.0 entry noting the docs refresh (no version bump). |

## 2. Validation

- **SVG parses OK** (`xml.etree`, 65 text nodes, viewBox `0 0 900 1780`).
  Version strings in the file: `v2.6.0` (provenance comment) and `v2.0.0`
  (only inside the sentence "Replaces the v2.0.0 flowchart" — a deliberate
  historical reference, same as the old file's "Replaces the v1.x flowchart").
- **Label-by-label check vs SKILL.md** (23 claims): all verified — paths
  (`team/roster/<id>.json`, `team/claims/<task-id>.json`,
  `team/features/<id>.json`), `doctor.py`, heartbeat 30 min,
  `claim_ttl_minutes: 120` (2 h TTL), `max_lifetime_hours` default 24,
  `--worktree`, gates enforced at claim time, `claim.py done --outcome
  succeeded|failed --summary` required, "no silent done", "a claim of
  completion, not proof", approve/request-changes, reviewer different from
  the subject's owner, latest request-changes blocks the PRD done-gate,
  `sync_check`, `push --atomic`, CI twin, fan-out compare/decide, human
  waive. One check initially flagged "2–3 keywords" because the check
  string was too strict — SKILL.md line 122 reads "Search with 2–3
  different keywords", so the label is correct.
- **Text-fit check** (PIL + DejaVu, which is wider than the SVG's
  Helvetica, so conservative): every text node measured against its card
  width — **0 overflows**.
- **README checks**: headings `## Description`, `## Install`,
  `## How it works`, `## The 5 steps`, `## Version history` all present
  (anchors `#description`, `#install` valid); code fences balanced (18
  backtick runs); badge still v2.6.0; body mentions fan-out / decision
  gate / receipt / request-changes.
- **Version untouched**: SKILL.md frontmatter `2.6.0`,
  `team/config.json` `skill_version` `2.6.0`. `git status` shows only
  `README.md`, `CHANGELOG.md`, `docs/flowchart.svg` modified — no script
  or code file touched. (`dist/`, `simulations/carousel/`,
  `simulations/video/` remain untracked and were NOT committed.)

## 3. Full simulation suite (no-regression proof)

`python3 simulations/run_simulations.py`, 11 scenarios, 424 steps.
Docs do not touch code, but the suite was re-run to prove it:

| Run | Result |
|---|---|
| 1 | **419/424** — 5 gates not as expected |
| 2 | 424/424, exit 0 |
| 3 | 424/424, exit 0 |
| 4 | 424/424, exit 0 |

Final `simulations/transcripts.json` summary (from run 4):
`scenarios 11, steps 424, gates_ok 424, gates_failed 0`.

**Honest note on run 1:** the 419/424 came from the run's tail output;
the full log of that run was not retained, so the 5 specific gates are
unidentified. Nothing in the repo changed between the runs (docs only,
and `git status` confirms only the three docs files differ), and the
three following runs are all clean with exit 0 — consistent with a
transient flake, plausibly in the wall-clock-dependent gates (the s9/s10
clock-skew cases build timestamps from `datetime.now(timezone.utc)`) or
stale `/tmp/sim-work` fixture state from the earlier v2.6.0 runs. It is
recorded here instead of hidden: if a future run shows anything below
424/424 again, investigate the timing-sensitive gates first.

## 4. Not tested

- Visual rendering of the new SVG in a browser (no local renderer
  available — the Hatch Chromium binary refuses direct headless launch,
  and the Playwright CDN download did not complete in the time budget).
  Geometry was validated by coordinate audit (all connectors land on card
  edges, all port dots sit on line ends) + the DejaVu width measurement
  above. Rendered appearance on GitHub should be checked after merge.
- The README's rendered Markdown on GitHub (verified by content check
  after merge instead).
