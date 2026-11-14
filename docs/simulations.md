# Simulations — which scenarios were tried

Six scenarios, run against the **real skill scripts** (not mocks), each on a
fresh copy of the starter repo. Every step records the exact command, its
exit code, and its real output. 45 steps total; all 45 gates behaved as
expected (9 of them are deliberate rejections — the gate working, not failing).

Re-run everything: `python3 simulations/run_simulations.py`
(it writes `simulations/transcripts.json` — the full transcript this page
summarises).

## The six scenarios

| # | Scenario | What it proves | Without TheRoster |
|---|----------|----------------|-------------------|
| s1 | Two AIs, one file | A second claim on the same file is refused; releasing the claim hands the file over cleanly | Both edit the same file; whoever pushes last silently overwrites the other's work |
| s2 | Search before you build | The registry answers `login-api [stable]` — reuse, don't rebuild; a duplicate id is refused; a claim can reserve a feature instead of rebuilding it | A second login gets built from scratch and two versions live side by side |
| s3 | A PRD split across AIs + the done-gate | `planned → done` is refused (must pass through `in_progress`); `status --to done` refuses an unregistered feature; `validate.py` refuses a done item whose feature is still experimental or whose claim is still attached | The PRD is marked "done" in a doc while the feature doesn't exist yet |
| s4 | A feature cancelled mid-work | Cancelling releases the overlapping active claim in the same push; `sync_check` in a second clone that hasn't fetched yet refuses the push with the CANCELLED message | The owner cancels in chat; the AI never reads it, keeps working, and pushes a week's work into the bin |
| s5 | A contract changed carelessly | Changing a contract entry without `--contract-change` + `--bump major` is refused; a major bump requires `--breaking-notes` and `--note`; `validate.py` warns that dependents need reverify; `reverify` clears it | The login entry changes silently and every caller breaks at runtime |
| s6 | A stranger claims to be a teammate | A claim by someone not on the roster is refused; a self-asserted roster entry is refused; the same person, once human-approved, can claim work | An unknown AI joins the work with no trace of who it is or who is responsible |

## Three things the simulations clarified

These were wrong assumptions in the first draft of the scenarios, corrected
by what the scripts actually do — the scripts were not changed:

1. The done-gate is split across two layers. `prd.py status --to done`
   only checks that the referenced features are **registered**. Whether they
   are **stable** is `validate.py`'s job — it fails a done item whose
   features are still `experimental`, and fails a claim still attached to
   a done item.
2. `register` / `improve` require `--breaking true|false`. A breaking
   change also requires `--breaking-notes`, and any `--bump` requires
   `--note` (it goes into the changes log).
3. A PRD item cannot jump `planned → done`. The only paths are
   `planned → in_progress → done` (or `→ cancelled`); done and cancelled
   are terminal — more work means a new item.

## Try it, break it, tell us

Install: `npx skills add Rifkiend/TheRoster` (then pick your agents).
Found a scenario the skill handles badly, or an idea to make it better?
Open an issue on GitHub — that is where this repo is improved.
