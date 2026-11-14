## What this PR does

<!-- One or two sentences. Link the claim task id (e.g. t-002) and, if it touches a feature, its registry id. -->

## Checklist

- [ ] I loaded the roster (`team/roster/`) and checked my mailbox (`team/mailbox/<my-id>.jsonl`) before planning this work.
- [ ] I claimed the work first — the claim file (`team/claims/<task-id>.json`) was committed and pushed **alone, before** any work on the claimed files.
- [ ] I searched the feature registry (`features.py search`) and considered the top-5 results: I reused a stable feature, continued an existing one in place (same `id`, version bumped), or — only if none fit — registered a new one as `experimental`.
- [ ] All local checks pass, in hook order:
  - [ ] `doctor.py`
  - [ ] `check_roster.py team/roster`
  - [ ] `claim.py check team/claims --roster team/roster --config team/config.json`
  - [ ] `features.py check team/features --roster team/roster --repo-root .`
  - [ ] `validate.py team`
  - [ ] `sync_check.py team/features team/claims --remote origin` (pushed immediately after, no `git pull` in between)
- [ ] No direct push to `main` — this PR targets `main` from a feature branch on my fork.
- [ ] `team/` changes (if any) have a different reviewer — I did not review my own roster, config, or feature changes. (Claim files are the exception: hook + CI enforce claim ownership.)
- [ ] No secrets in `team/` files — no tokens, passwords, phone numbers, or URLs in `contact` (bare channel name only).

<!-- On repos using the skill, the `team-roster` CI check must pass before this PR can merge. If it fails, fix it here — never merge around it. (On TheRoster itself there is no `team/` to check, so PRs here are reviewed by the human owner.) -->
