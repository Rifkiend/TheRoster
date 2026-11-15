# Feedback channels — evidence (2026-11-15, no version bump)

Change: GitHub Discussions enabled on the repo (browser, owner login) +
`.github/ISSUE_TEMPLATE/` (bug report, feature request, `config.yml`) +
README `## Feedback` section + one CHANGELOG bullet under v2.6.0.
Skill version stays 2.6.0; no scripts touched.

## Validation (run locally 2026-11-15 ~21:30 WITA)

24 checks, 23 PASS + 1 script artifact (see below):

- Both templates: YAML frontmatter parses (`name`, `title` with `[bug]` /
  `[feature]` prefix, `labels` bug / enhancement), body has `##` sections,
  no TODO placeholders.
- `config.yml`: parses; `blank_issues_enabled: true`; exactly 1 contact link,
  pointing at `https://github.com/Rifkiend/TheRoster/discussions`.
- README: `## Feedback` section present, placed before `## Known limits`;
  all three links present (Discussions, `issues/new?template=bug_report.md`,
  `issues/new?template=feature_request.md`); both template filenames exist.

The single FAIL line was a bug in the check script itself: the loop also ran
the "template file exists" check on the Discussions URL, which carries no
`template=` parameter. Not a repo defect; every real check passed.

## Live verification

- Discussions page: enabled via browser (owner login); the browser task
  confirmed the page loads with its categories.
- Templates + README: merged to main via PR, verified live via raw fetch.

## Honest limits

- Issue-template rendering on github.com was not eyeballed (form preview
  only renders after merge, on the New Issue page).
- Whether anyone actually uses the channels is unknowable until the first
  real discussion/issue arrives.
