# team-roster — an Agent Skill

**One sentence:** before you start any job, read the team list first. If someone is not on the list, they are not your teammate.

AI models are stateless — without a written list, an AI does not know who your teammates are, so it guesses. A guessed teammate is a hallucination. This skill fixes that with one rule: **the roster is the single source of truth about the team.**

It works for any job, not just code: a software team, a PKL group, a Shopee affiliate team, a freelance client team, a content team. People, AI agents, and tools can all be on the roster.

> **v1.1 — anti-collision:** the roster alone tells you WHO the team is, not WHO IS DOING WHAT. Two disconnected AIs can't magically see each other, so v1.1 adds a shared work ledger (`team/work.json`) synced through git: pull, claim the task + files, push the claim, then work. One file = one owner at a time. First push wins; a rejected push means someone claimed it first. This makes collisions rare, not zero — it is not real-time, and it is not a filesystem lock. See `skills/team-roster/references/conflict-prevention.md`.

## The 4 steps (the whole skill)

1. **Load the roster first** — `team/roster.json`. If there is no roster, stop and make one. Do not guess the team.
2. **Plan only with people on the roster** — assign work only to `active` members whose `can_do` matches. If nobody can do it, say so. Do not invent a teammate.
3. **Claim the work before you touch it** — `git pull`, then `python3 scripts/claim.py claim --task t-002 --title "Kerjakan checkout" --owner <your-id> --files src/checkout.py`, then commit + push the claim *before* you start working. If the claim fails or the push is rejected, someone else is already on it — stop. Release with `claim.py done` when finished.
4. **Verify before you push** — run `python3 scripts/check_roster.py team/roster.json` and `python3 scripts/claim.py check team/work.json`. A roster is stale when an `active` member was last verified more than 30 days ago. Stale or double-claimed = push blocked.

## Install

This repo follows the [Agent Skills open standard](https://agentskills.io) (Anthropic, Dec 2025). One skill = one folder with a `SKILL.md`. It works in Claude Code, GitHub Copilot, Cursor, Codex, Gemini CLI, and 70+ tools.

**Option A — install with the skills CLI (recommended):**

```bash
npx skills add <github-user>/team-roster-skill
```

**Option B — copy it into your project:**

```bash
# Claude Code
mkdir -p .claude/skills && cp -r skills/team-roster .claude/skills/

# Codex / most other agents
mkdir -p .agents/skills && cp -r skills/team-roster .agents/skills/

# GitHub Copilot
mkdir -p .github/skills && cp -r skills/team-roster .github/skills/

# Cursor
mkdir -p .cursor/skills && cp -r skills/team-roster .cursor/skills/
```

**Option C — personal, for every project:**

```bash
mkdir -p ~/.claude/skills && cp -r skills/team-roster ~/.claude/skills/
```

Then start your roster:

```bash
mkdir -p team && cp skills/team-roster/roster.example.json team/roster.json
# edit team/roster.json — replace every example person with your real team
```

Install the pre-push hook so git checks the roster automatically:

```bash
bash skills/team-roster/scripts/install-hook.sh
```

Validate the skill itself (needs the `skills-ref` validator):

```bash
uvx --from "git+https://github.com/agentskills/agentskills#subdirectory=skills-ref" skills-ref validate skills/team-roster
```

## What's in here

```
skills/team-roster/
├── SKILL.md                  # the skill — name, description, 4-step workflow
├── roster.example.json       # starter roster — copy to team/roster.json
├── work.example.json         # starter work ledger — copy to team/work.json
├── scripts/
│   ├── check_roster.py       # roster validator: invalid or stale (30+ days) = exit 1, push blocked
│   ├── claim.py              # claim / release / done / list / check work — no claim, no edit
│   └── install-hook.sh       # installs hooks/pre-push into .git/hooks/
├── hooks/
│   └── pre-push              # git hook — runs both checks on every push
└── references/
    ├── roster-schema.md      # full JSON schema + validation rules
    ├── conflict-prevention.md # how the work ledger stops collisions, and its limits
    └── examples.md           # 4 ready rosters: software, PKL, affiliate, freelance
```

Start the work ledger the same way as the roster:

```bash
cp skills/team-roster/work.example.json team/work.json
python3 skills/team-roster/scripts/claim.py list team/work.json
```

## Examples

See `skills/team-roster/references/examples.md`: a software team, a PKL group, a Shopee affiliate team, and a freelance client team — same skill, same file, only the people change.

## Rules the skill enforces

- Only people in the roster exist. No invented teammates.
- `active` = can be assigned work. `away` = still on the team, no new work. `retired` = keep for history, never assign.
- `contact` is a channel name (`WhatsApp`, `Threads DM`), never a phone number or password.
- `last_verified` is only updated after actually checking — never just re-date it.

## Origin

Idea: Rian — an AI that works in a group should know its teammates *before* it meets them, the way a skill tracks your own friends before a push. Built as a minimal, real version of multi-agent team awareness (Google A2A Agent Cards, Stanford Generative Agents / Smallville) — no server, no protocol, just a file + a hook.

## License

MIT — see `LICENSE`.
