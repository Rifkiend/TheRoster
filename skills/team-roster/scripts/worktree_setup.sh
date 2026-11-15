#!/usr/bin/env bash
# worktree_setup.sh — create a git worktree for a TheRoster worktree claim
# (v2.3) and apply the shared-paths convention from team/config.json.
#
# Usage (run from the MAIN checkout — never from inside a worktree):
#   bash scripts/worktree_setup.sh <worktree-path> <branch> [base-commit] [--team team]
#
#   <worktree-path>  where the worktree lives — MUST be outside the repo
#   <branch>         fresh branch for this claim (default in claim.py: wt/<task-id>)
#   [base-commit]    commit to branch from (default: the claim's base_commit,
#                    i.e. main's HEAD at claim time; pass it explicitly)
#
# What it does:
#   1. Refuses when run from inside a linked worktree (team/ state is
#      only ever written from the main checkout — Dziatkovskii rule).
#   2. git worktree add <path> -b <branch> <base>.
#   3. Shared dirs (worktree_shared_dirs in team/config.json, default
#      node_modules + .cache): symlinked from the main checkout, but
#      ONLY when the dir exists there AND is gitignored — a tracked dir
#      is never symlinked.
#   4. Copy files (worktree_copy_files, default .env + .env.local):
#      COPIED, never symlinked — a symlink would make the worktree's
#      env edits land on the main checkout's secrets file.
#
# After the work: push the branch, open a PR, merge, then remove the
# worktree (git worktree remove <path>) — never leave a merged worktree
# behind. Before pushing the branch, run:
#   python3 scripts/sync_check.py team/features team/claims --claim <task-id>
# which fails on a stale base or a merge conflict with origin/main.
set -euo pipefail

usage() {
    echo "usage: bash worktree_setup.sh <worktree-path> <branch> [base-commit] [--team team]" >&2
    echo "  run from the MAIN checkout, not from inside a worktree." >&2
    exit 2
}

TEAM="team"
WT_PATH=""
BRANCH=""
BASE="HEAD"
while [ $# -gt 0 ]; do
    case "$1" in
        --team) TEAM="${2:-}"; shift 2 ;;
        --team=*) TEAM="${1#--team=}"; shift ;;
        -h|--help) usage ;;
        *)
            if [ -z "$WT_PATH" ]; then WT_PATH="$1";
            elif [ -z "$BRANCH" ]; then BRANCH="$1";
            elif [ "$BASE" = "HEAD" ]; then BASE="$1";
            else usage; fi
            shift ;;
    esac
done
[ -n "$WT_PATH" ] && [ -n "$BRANCH" ] || usage

# --- 1. must run from the main checkout -----------------------------------
MAIN="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$MAIN" ] || { echo "worktree_setup: FAIL — not inside a git repo" >&2; exit 1; }
FIRST="$(git worktree list --porcelain | awk '/^worktree /{print $2; exit}')"
if [ "$MAIN" != "$FIRST" ]; then
    echo "worktree_setup: FAIL — refusing to create a worktree from inside a linked worktree ($MAIN)." >&2
    echo "  team/ state is only written from the main checkout ($FIRST) — cd there first." >&2
    exit 1
fi

# --- resolve the worktree path (relative to the main checkout) ------------
WT_ABS="$(python3 -c "import os,sys; print(os.path.normpath(os.path.join(sys.argv[1], sys.argv[2])))" "$MAIN" "$WT_PATH")"
case "$WT_ABS" in
    "$MAIN"|"$MAIN"/*)
        echo "worktree_setup: FAIL — worktree path $WT_PATH is inside the repo ($MAIN)." >&2
        echo "  a worktree must live OUTSIDE the main checkout." >&2
        exit 1 ;;
esac

# --- config: shared dirs + copy files --------------------------------------
read_list() { # $1 = key, $2 = default (space-separated)
    python3 - "$TEAM/config.json" "$1" "$2" <<'PY'
import json, sys
path, key, default = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    vals = cfg.get(key)
    if isinstance(vals, list) and vals:
        print("\n".join(str(v) for v in vals))
        sys.exit(0)
except (OSError, ValueError):
    pass
print("\n".join(default.split()))
PY
}
SHARED_DIRS="$(read_list worktree_shared_dirs "node_modules .cache")"
COPY_FILES="$(read_list worktree_copy_files ".env .env.local")"

# --- 2. create the worktree -------------------------------------------------
git worktree add "$WT_ABS" -b "$BRANCH" "$BASE"

# --- 3. shared dirs: symlink only if present AND gitignored ----------------
while IFS= read -r dir; do
    [ -n "$dir" ] || continue
    if [ -d "$MAIN/$dir" ]; then
        if git -C "$MAIN" check-ignore -q "$dir" 2>/dev/null; then
            ln -s "$MAIN/$dir" "$WT_ABS/$dir"
            echo "worktree_setup: symlinked $dir -> $MAIN/$dir"
        else
            echo "worktree_setup: WARNING — $dir exists in the main checkout but is NOT gitignored; not symlinked (never symlink tracked dirs)" >&2
        fi
    fi
done <<< "$SHARED_DIRS"

# --- 4. copy files: copy, never symlink --------------------------------------
while IFS= read -r file; do
    [ -n "$file" ] || continue
    if [ -f "$MAIN/$file" ]; then
        cp "$MAIN/$file" "$WT_ABS/$file"
        echo "worktree_setup: copied $file (not symlinked)"
    fi
done <<< "$COPY_FILES"

echo "worktree_setup: OK — worktree $WT_ABS on branch $BRANCH (base $BASE)"
echo "  Remember: the claim itself must be pushed to main ALONE first, before any work commits."
echo "  Before pushing this branch: python3 scripts/sync_check.py team/features team/claims --claim <task-id>"
echo "  After the merge: git worktree remove $WT_ABS"
