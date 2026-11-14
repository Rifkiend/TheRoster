#!/bin/bash
# Install the team-roster pre-push hook into this git repo.
# Usage: bash scripts/install-hook.sh
# Run from the repo root. Requires: git, python3.
set -e
if [ ! -d .git ]; then
  echo "Not a git repo — run this from the repo root."
  exit 1
fi
# Find the skill folder (this script lives in skills/team-roster/scripts/)
SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cp "$SKILL_DIR/hooks/pre-push" .git/hooks/pre-push
chmod +x .git/hooks/pre-push
echo "Installed pre-push hook. It runs all four checks on every git push:"
echo "  roster -> work ledger -> feature registry -> remote sync check."
echo "If team/roster.json does not exist yet, copy the example first:"
echo "  mkdir -p team && cp $SKILL_DIR/roster.example.json team/roster.json"
