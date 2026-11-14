# Four ready rosters — copy one, replace the names

All four use the same schema (`references/roster-schema.md`). The point: the skill is the same file and the same 3 steps in every job. Only the people change.

## 1. Software team

```json
{
  "team_name": "Tim Backend Checkout",
  "updated": "2026-11-14",
  "members": [
    {"id": "lead", "name": "Lead", "type": "human", "role": "tech lead — reviews, merges", "can_do": ["review PR", "merge", "decide architecture"], "contact": "Slack", "status": "active", "last_verified": "2026-11-14"},
    {"id": "dev-1", "name": "Dev 1", "type": "human", "role": "backend dev — payments", "can_do": ["write API", "fix bugs"], "contact": "Slack", "status": "active", "last_verified": "2026-11-14"},
    {"id": "copilot", "name": "Copilot", "type": "ai-agent", "role": "coding assistant — drafts, tests", "can_do": ["draft code", "write tests"], "contact": "VS Code", "status": "active", "last_verified": "2026-11-14"}
  ]
}
```

## 2. PKL group (Rian's real case)

```json
{
  "team_name": "Teman PKL Kintap",
  "updated": "2026-11-14",
  "members": [
    {"id": "rian", "name": "Rian", "type": "human", "role": "owner — decides antenna/modem, holds the budget", "can_do": ["decide purchase", "test signal"], "contact": "WhatsApp group", "status": "active", "last_verified": "2026-11-14"},
    {"id": "teman-1", "name": "Teman 1", "type": "human", "role": "housemate — shares cost", "can_do": ["share cost", "test signal"], "contact": "WhatsApp group", "status": "active", "last_verified": "2026-11-14"},
    {"id": "teman-2", "name": "Teman 2", "type": "human", "role": "housemate — owns a modem?", "can_do": ["lend modem"], "contact": "WhatsApp group", "status": "away", "last_verified": "2026-11-14"}
  ]
}
```

Note the `away` member: still on the team, just cannot take new work right now. Do not delete them.

## 3. Shopee affiliate team

```json
{
  "team_name": "Affiliate Shopee",
  "updated": "2026-11-14",
  "members": [
    {"id": "rian", "name": "Rian", "type": "human", "role": "owner — approves, runs Shopee app", "can_do": ["approve posts", "pull Komisi Ekstra links"], "contact": "WhatsApp", "status": "active", "last_verified": "2026-11-14"},
    {"id": "rean", "name": "Rean", "type": "ai-agent", "role": "assistant — drafts, monitors, posts on Threads", "can_do": ["draft posts", "monitor prices", "check comments"], "contact": "Muse chat", "status": "active", "last_verified": "2026-11-14"}
  ]
}
```

## 4. Freelance client team

```json
{
  "team_name": "Klien Laporan Otomatis",
  "updated": "2026-11-14",
  "members": [
    {"id": "klien", "name": "Klien", "type": "human", "role": "client — approves the report format, pays", "can_do": ["approve format", "pay invoice"], "contact": "email", "status": "active", "last_verified": "2026-11-14"},
    {"id": "rian", "name": "Rian", "type": "human", "role": "freelancer — builds Excel/Python automation", "can_do": ["build report script", "fix formulas"], "contact": "email", "status": "active", "last_verified": "2026-11-14"}
  ]
}
```

In the freelance case the client is on the roster too — the AI must not invent a second approver, and must not assign client-only work (approve, pay) to the freelancer.
