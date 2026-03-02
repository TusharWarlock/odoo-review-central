#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║          ODOO CODE REVIEW — HOOK INSTALLER                              ║
# ║                                                                          ║
# ║  Installs the pre-commit hook into the current git repository.          ║
# ║  Run this once from inside your git repo.                               ║
# ║                                                                          ║
# ║  Usage:                                                                  ║
# ║    bash install_hooks.sh                      # install in current repo  ║
# ║    bash install_hooks.sh /path/to/repo        # install in given repo    ║
# ║    bash install_hooks.sh --uninstall          # remove the hook          ║
# ╚══════════════════════════════════════════════════════════════════════════╝

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REVIEW_SCRIPT="$SCRIPT_DIR/odoo_code_review.py"
HOOK_SOURCE="$SCRIPT_DIR/pre-commit"

# ── Parse args ────────────────────────────────────────────────────────────────
UNINSTALL=false
TARGET_REPO=""

for arg in "$@"; do
    case "$arg" in
        --uninstall) UNINSTALL=true ;;
        --help|-h)
            echo "Usage: bash install_hooks.sh [repo_path] [--uninstall]"
            exit 0 ;;
        *) TARGET_REPO="$arg" ;;
    esac
done

# ── Resolve target repo ───────────────────────────────────────────────────────
if [ -n "$TARGET_REPO" ]; then
    cd "$TARGET_REPO"
fi

if ! git rev-parse --git-dir &>/dev/null 2>&1; then
    echo ""
    echo "  ❌  Not a git repository: $(pwd)"
    echo "  Run this script from inside a git repo, or pass the repo path as argument."
    echo ""
    exit 1
fi

GIT_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_DIR="$GIT_ROOT/.git/hooks"
HOOK_DEST="$HOOKS_DIR/pre-commit"

echo ""
echo "  ═══════════════════════════════════════════════════════════════════"
echo "  🔧  ODOO CODE REVIEW — HOOK INSTALLER"
echo "  ═══════════════════════════════════════════════════════════════════"
echo "  Git repo     : $GIT_ROOT"
echo "  Hooks dir    : $HOOKS_DIR"
echo "  Review script: $REVIEW_SCRIPT"
echo ""

# ── Uninstall ─────────────────────────────────────────────────────────────────
if [ "$UNINSTALL" = true ]; then
    if [ -f "$HOOK_DEST" ]; then
        rm "$HOOK_DEST"
        echo "  ✅  Pre-commit hook removed from $HOOKS_DIR"
    else
        echo "  ℹ️   No pre-commit hook found at $HOOKS_DIR"
    fi
    echo ""
    exit 0
fi

# ── Validate review script exists ────────────────────────────────────────────
if [ ! -f "$REVIEW_SCRIPT" ]; then
    echo "  ❌  odoo_code_review.py not found at: $REVIEW_SCRIPT"
    echo "  Make sure install_hooks.sh is in the same directory as odoo_code_review.py"
    echo ""
    exit 1
fi

if [ ! -f "$HOOK_SOURCE" ]; then
    echo "  ❌  pre-commit hook script not found at: $HOOK_SOURCE"
    echo "  Make sure pre-commit is in the same directory as install_hooks.sh"
    echo ""
    exit 1
fi

# ── Backup existing hook if present ──────────────────────────────────────────
if [ -f "$HOOK_DEST" ]; then
    BACKUP="$HOOK_DEST.backup.$(date +%Y%m%d_%H%M%S)"
    mv "$HOOK_DEST" "$BACKUP"
    echo "  📦  Existing hook backed up to: $BACKUP"
fi

# ── Install ───────────────────────────────────────────────────────────────────
cp "$HOOK_SOURCE" "$HOOK_DEST"
chmod +x "$HOOK_DEST"

# Embed the review script path so hook always finds it
sed -i "s|REVIEW_SCRIPT=.*|REVIEW_SCRIPT=\"\${ODOO_REVIEW_SCRIPT:-$REVIEW_SCRIPT}\"|" "$HOOK_DEST"

echo "  ✅  Pre-commit hook installed successfully!"
echo ""
echo "  ═══════════════════════════════════════════════════════════════════"
echo "  HOW IT WORKS"
echo "  ═══════════════════════════════════════════════════════════════════"
echo "  • Runs automatically on every  git commit"
echo "  • Checks only files changed since last commit (git-diff mode)"
echo "  • Blocks commit if score is ≤ 5.0 / 10"
echo "  • To skip in an emergency:     git commit --no-verify"
echo ""
echo "  CONFIGURATION  (set as environment variables)"
echo "  ───────────────────────────────────────────────"
echo "  ODOO_MIN_SCORE      Minimum score to allow commit  (default: 5.0)"
echo "  ODOO_REVIEW_SCRIPT  Path to odoo_code_review.py"
echo "  ODOO_PYTHON         Python executable              (default: python3)"
echo ""
echo "  Example — lower the threshold:"
echo "    export ODOO_MIN_SCORE=7.0"
echo ""
echo "  Example — uninstall:"
echo "    bash install_hooks.sh --uninstall"
echo "  ═══════════════════════════════════════════════════════════════════"
echo ""
