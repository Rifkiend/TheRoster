# Contributing to TheRoster

Short version: **load the roster, claim the work, search before you build, PR it — never push straight to `main`.**

## 1. Set up

```bash
git clone https://github.com/Rifkiend/TheRoster.git
cd TheRoster
bash skills/team-roster/scripts/install-hook.sh   # installs the pre-push hook
python3 skills/team-roster/scripts/doctor.py      # session-start check: hook, config, Python, scripts
```

## 2. Load the roster first, check your mailbox

Read `team/roster/` (every member, `active` only for new work) and `team/mailbox/<your-id>.jsonl` before planning anything. Not on the roster, not a teammate — never invent one.

## 3. Fork and branch — never push directly to `main`

```bash
gh repo fork Rifkiend/TheRoster --clone=false   # or fork in the GitHub UI
git checkout -b feat/<short-name>               # one branch per task
```

All changes merge via pull request. Direct pushes to `main` are blocked by branch protection (see README, "Contributing / branch protection").

## 4. Claim first, before touching claimed files

```bash
git fetch origin && git rebase origin/main
python3 skills/team-roster/scripts/claim.py claim --task t-002 --title "Kerjakan checkout" \
  --owner <your-id> --files src/checkout.py
git add team/claims/t-002.json && git commit -m "claim(t-002): checkout" && git push
# push the claim file ALONE, first — then start the work
```

If the claim fails or the push is rejected, someone else is already on it — stop. Heartbeat every 30 minutes on claims longer than 30 minutes; release with `claim.py done` when finished. Max 5 active claims per agent, max 10 files per claim, exact file paths only.

## 5. Search the feature registry before you build

```bash
python3 skills/team-roster/scripts/features.py search login team/features
```

Search reports the top-5, deterministically ranked. If a stable feature does the job, use its `entry` directly (`features.py use --id <id> --by <task>`). If it exists but needs to grow, continue it **in place** — same `id`, same files, bump the version (`features.py improve --id <id> --bump minor|patch|major`). A new `id` means a different capability. New features register as `experimental`; promoting to `stable` needs a reviewer different from the owner.

**Status transitions:** `experimental → stable` (reviewer, not owner) and `stable → deprecated` are the normal moves. `deprecated → stable` is forbidden without a human-approved PR. Every status change records reason + who + when. Features are never deleted, only deprecated.

**SemVer:** `patch` = bug fix, `minor` = new compatible capability, `major` = only when the entry changes.

## 6. Run all local checks before opening the PR

In the same order the hook runs them (the last one fetches the remote — push immediately after, never `git pull` in between):

```bash
python3 skills/team-roster/scripts/doctor.py
python3 skills/team-roster/scripts/check_roster.py team/roster
python3 skills/team-roster/scripts/claim.py check team/claims --roster team/roster --config team/config.json
python3 skills/team-roster/scripts/features.py check team/features --roster team/roster --repo-root .
python3 skills/team-roster/scripts/validate.py team
python3 skills/team-roster/scripts/sync_check.py team/features team/claims --remote origin
```

If any check fails, fix it — do not push with `--no-verify`. If a push is rejected, start the whole sequence again from `git fetch origin`.

## 7. Open a pull request

Push the branch to your fork and open a PR against `main` (the repo ships a PR template — fill in every checkbox). The PR must pass the `team-roster` CI check (the hook's checks, run server-side); a failing check blocks the merge.

Changes to `team/` need a **different reviewer** — never review your own roster, config, or feature changes, and never self-register (a roster entry added in the same push as the work it authorises is rejected). `team/claims/` is the exception: the hook + CI enforce claim ownership instead of a human reviewer.

## 8. No secrets in `team/`

`contact` is a bare channel name (`GitHub`, `WhatsApp`, `Threads DM`) — never a phone number, URL, token, or password. `validate.py` rejects secrets and PII in `team/` files.
