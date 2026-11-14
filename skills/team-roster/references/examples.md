# Example teams — sharded layout (v2)

All teams below use the same schema (`references/roster-schema.md`, `references/conflict-prevention.md`, `references/feature-registry.md`). The point: the skill is the same file and the same 5 steps in every job. Only the people change.

**The core case:** an AI about to build something — a website first of all — loads the roster *before* it writes a line. The roster tells it who sets the direction, who owns each part, and who reviews — so the AI knows where it is going before it starts going there.

**Layout note (v2):** every team below is stored sharded, not as three
monolithic files. Copy the starter repo instead of the old single files:

```bash
cp -r skills/team-roster/examples/starter-repo/* .
# copies team/ + src/ + scripts/ — the entry files are included
# deliberately, so features.py check passes on the copy out of the box
# edit team/roster/*.json, team/config.json — replace every example person
# with your real team. Do NOT hand-edit team/index.json (generated).
```

The old single-file examples (`roster.example.json`, `work.example.json`,
`features.example.json`) were removed in v2 — do not copy them for new
teams. To migrate a v1 tree, run `scripts/migrate_v1_to_v2.py`, which
also accepts the `*.example.json` filenames.

## 1. Website-build team

An AI (or several AIs) building a website for a client. Before any code, the roster answers the questions that decide the direction: who sets it, who owns the look, who owns which code, who says it is ready to launch.

- `client.json` — human, client / owner, `can_do: ["decide direction", "approve design", "approve launch"]`, contact `email`, active
- `designer.json` — human, designer, `can_do: ["design pages", "approve look"]`, contact `Figma`, active
- `frontend.json` — ai-agent, frontend dev, `can_do: ["build pages", "build components"]`, contact `GitHub`, active
- `backend.json` — ai-agent, backend dev, `can_do: ["build API", "own database"]`, contact `GitHub`, active
- `reviewer.json` — human, QA / reviewer, `can_do: ["review", "approve launch"]`, contact `GitHub`, active

Example claim: `frontend` claims `files: ["src/pages/pricing.tsx", "src/components/PricingCard.tsx"]` for task `t-003` "Build pricing page". Example feature (`team/features/landing-hero.json`): `landing-hero`, `kind: code`, `entry: src/components/Hero.tsx:Hero`, stable 1.0.0 — the next AI reuses it instead of rebuilding a hero section from scratch.

## 2. Software product team

Roster (one file per member, `team/roster/<id>.json`):

- `lead.json` — human, tech lead, `can_do: ["review PR", "merge", "decide architecture"]`, contact `GitHub`, active
- `backend-dev.json` — human, backend dev (payments), `can_do: ["write API", "fix bugs"]`, contact `GitHub`, active
- `coder.json` — ai-agent, coding assistant, `can_do: ["draft code", "write tests"]`, contact `VS Code`, active

Each member file carries `added_by`, `added_in_pr`, `verification: "human-approved"`, and `lamport: 1` — see `examples/starter-repo/team/roster/owner.json` for the full shape.

## 3. Open-source project

- `maintainer.json` — human, maintainer, `can_do: ["review PR", "merge", "cut release"]`, contact `GitHub`, active
- `contributor.json` — human, contributor, `can_do: ["fix bugs", "update docs"]`, contact `GitHub`, active
- `docs-writer.json` — ai-agent, docs writer, `can_do: ["write docs", "update changelog"]`, contact `GitHub`, active
- `triage-bot.json` — ai-agent, triage bot, `can_do: ["label issues", "close duplicates"]`, contact `GitHub`, active

Note what `can_do` does here: the triage bot can label issues, but it cannot merge — the roster is the boundary, and the hook enforces it.

## 4. Content & creator team

- `editor.json` — human, editor, `can_do: ["approve script", "approve publish"]`, contact `email`, active
- `writer.json` — ai-agent, script writer, `can_do: ["draft script", "research"]`, contact `chat`, active
- `video-editor.json` — human, video editor, `can_do: ["edit video", "add captions"]`, contact `chat`, active
- `thumb-designer.json` — human, thumbnail designer, `can_do: ["design thumbnail"]`, contact `chat`, active

Example claim: `writer` claims `scope: {"type": "area", "name": "slot: script minggu ini"}` with `work_type: writing`.

## 5. Small online-store team

- `owner.json` — human, store owner, `can_do: ["approve posts", "approve discounts"]`, contact `WhatsApp`, active
- `assistant.json` — ai-agent, assistant, `can_do: ["draft posts", "monitor prices", "check comments"]`, contact `chat`, active

Example claim: `assistant` claims `scope: {"type": "area", "name": "slot: morning post"}` with `work_type: writing`. Example feature (`team/features/price-monitor.json` in the example tree): `price-monitor`, `kind: code`, `entry: scripts/monitor_harga.py:cek_harga`, stable 1.0.0.

## 6. Campus group project

- `member-1.json` — human, student, `can_do: ["share cost", "test"]`, contact `WhatsApp group`, active
- `member-2.json` — human, student, `can_do: ["share cost", "lend gear"]`, contact `WhatsApp group`, active
- `member-3.json` — human, student, `can_do: ["take notes"]`, contact `WhatsApp group`, `away`

Note the `away` member: still on the team, just cannot take new work right now. Do not delete them.

Example claim for this team (`team/claims/t-002.json` shape): `member-1` claims
`scope: {"type": "artifact", "name": "alat: modem"}` — one tool, one
borrower. See `examples/starter-repo/team/claims/t-001.json` for the full claim shape.

## 7. Freelance client team

- `client.json` — human, client, `can_do: ["approve format", "pay invoice"]`, contact `email`, active
- `freelancer.json` — human, freelancer, `can_do: ["build report script", "fix formulas"]`, contact `email`, active

In the freelance case the client is on the roster too — the AI must not invent a second approver, and must not assign client-only work (approve, pay) to the freelancer. Example feature: `sales-report-auto`, `kind: code`, `entry: build_report.py`, or `kind: doc` if the deliverable is the report document itself.

## The example tree

`skills/team-roster/examples/starter-repo/` is a complete, copyable
starter repo — `team/` plus the entry files the example features point
at, so all five hook checks pass on the copy out of the box. The names
in it are placeholders — replace every example person with your real team.

```
examples/starter-repo/
├── team/
│   ├── config.json            # canonical_remote origin, branch main, TTLs, quotas
│   ├── roster/
│   │   ├── owner.json         # human owner
│   │   ├── assistant.json     # ai-agent assistant
│   │   └── teammate-1.json    # human teammate
│   ├── claims/
│   │   └── t-001.json         # owner, login-api claim, feat/login-api
│   ├── features/
│   │   ├── login-api.json     # stable 1.0.0, canonical, entry src/auth/login.py:login
│   │   └── price-monitor.json # stable 1.0.0, canonical, entry scripts/monitor_harga.py:cek_harga
│   ├── events.jsonl           # claim_created + 2× feature_registered
│   ├── mailbox/
│   │   └── assistant.jsonl    # one example message from owner
│   ├── .gitattributes         # events.jsonl merge=union
│   └── .gitignore             # index.json is generated, never committed
├── src/auth/login.py          # the login-api entry file
├── scripts/monitor_harga.py  # the price-monitor entry file
└── tests/test_login.py        # smoke test for the example entry point
```

`team/index.json` is intentionally NOT in the example tree — it is
generated by the scripts from the record files, never hand-edited.
`team/handoffs/` starts empty: a handoff record is created only when a claim
actually transfers.
