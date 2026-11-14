#!/bin/bash
# Install the team-roster pre-push hook into this git repo.
# Usage: bash scripts/install-hook.sh [--skill-dir skills/team-roster]
# Run from the repo root, or anywhere inside the repo. Requires: git, python3.
set -e
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo .)"
cd "$ROOT"
# Find the skill folder (this script lives in skills/team-roster/scripts/).
SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
COMMON_DIR="$(git rev-parse --git-common-dir)"
case "$COMMON_DIR" in
  /*) HOOK_DIR="$COMMON_DIR" ;;
  *)  HOOK_DIR="$ROOT/$COMMON_DIR" ;;
esac
mkdir -p "$HOOK_DIR/hooks"
cp "$SKILL_DIR/hooks/pre-push" "$HOOK_DIR/hooks/pre-push"
chmod +x "$HOOK_DIR/hooks/pre-push"
if [ ! -x "$HOOK_DIR/hooks/pre-push" ]; then
  echo "install-hook: FAILED — hook was copied but is not executable: $HOOK_DIR/hooks/pre-push"
  exit 1
fi
echo "Installed pre-push hook at $HOOK_DIR/hooks/pre-push (executable: yes)."
echo "It runs these five checks on every git push, in order:"
echo "  1. roster    — check_roster.py team/roster"
echo "  2. claims    — claim.py check team/claims"
echo "  3. features  — features.py check team/features (entry + blob sha)"
echo "  4. validate  — validate.py team (cross-file: ownership, quotas, renames, secrets/PII)"
echo "  5. sync      — sync_check.py team/features team/claims (fetches the remote first)"
echo "Running the session-start self-check (doctor.py) ..."
python3 "$SKILL_DIR/scripts/doctor.py" --team team --skill-dir "$SKILL_DIR"
echo "If team/roster does not exist yet, create it first:"
echo "  mkdir -p team/roster team/claims team/features"
echo "  (or run: python3 $SKILL_DIR/scripts/migrate_v1_to_v2.py team — to convert a v1 tree)"
