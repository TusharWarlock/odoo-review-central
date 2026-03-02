#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
#  ODOO REVIEW  —  ORG-WIDE INSTALLER
#
#  Adds the Odoo code review workflow to ALL repos in your GitHub org/account
#  in one command. Skips repos that already have it. Creates a PR in each
#  repo so you can review and merge it.
#
#  Requirements:
#    - GitHub CLI installed:  https://cli.github.com
#    - Authenticated:         gh auth login
#
#  Usage:
#    bash org_installer.sh --org YOUR_ORG
#    bash org_installer.sh --org YOUR_ORG --dry-run        # preview only
#    bash org_installer.sh --org YOUR_ORG --min-score 7.0  # custom threshold
#    bash org_installer.sh --org YOUR_ORG --addons src     # custom path
#    bash org_installer.sh --org YOUR_ORG --no-pr          # direct push (skip PR)
# ════════════════════════════════════════════════════════════════════════════

set -e

# ── Defaults ──────────────────────────────────────────────────────────────────
ORG=""
DRY_RUN=false
CREATE_PR=true
MIN_SCORE="5.0"
ADDONS_PATH="."
BLOCK_ON_FAIL="true"
SKIP_REPOS=""                    # comma-separated list of repos to skip
CENTRAL_REPO=""                  # auto-set from ORG below
BRANCH_NAME="chore/add-odoo-code-review"
COMMIT_MSG="ci: add Odoo code review workflow"
PR_TITLE="🔍 Add Odoo code review"
PR_BODY="Adds the automated Odoo code review workflow from \`odoo-review-central\`.

**What this does:**
- Runs on every push to any branch
- Checks Odoo coding standards (Python, XML, JS, SCSS)
- Posts a report as a PR comment with score + details
- Blocks merge if score ≤ ${MIN_SCORE}/10
- Uses self-hosted runner first, falls back to GitHub-hosted

**No changes to your code.** Only adds \`.github/workflows/odoo-review.yml\`.

_Installed by [odoo-review-central](https://github.com/${ORG}/odoo-review-central)_"

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --org)          ORG="$2";           shift 2 ;;
        --min-score)    MIN_SCORE="$2";     shift 2 ;;
        --addons)       ADDONS_PATH="$2";   shift 2 ;;
        --skip)         SKIP_REPOS="$2";    shift 2 ;;
        --dry-run)      DRY_RUN=true;       shift   ;;
        --no-pr)        CREATE_PR=false;    shift   ;;
        --no-block)     BLOCK_ON_FAIL=false; shift  ;;
        --help|-h)
            sed -n '/^#/p' "$0" | sed 's/^# \{0,2\}//'
            exit 0 ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

if [ -z "$ORG" ]; then
    echo ""
    echo "  ❌  --org is required.  Usage: bash org_installer.sh --org YOUR_ORG"
    echo ""
    exit 1
fi

CENTRAL_REPO="${ORG}/odoo-review-central"

# ── Check prerequisites ───────────────────────────────────────────────────────
echo ""
echo "  ══════════════════════════════════════════════════════════════════"
echo "  🔧  ODOO REVIEW ORG-WIDE INSTALLER"
echo "  ══════════════════════════════════════════════════════════════════"
echo "  Org / User     : $ORG"
echo "  Central repo   : $CENTRAL_REPO"
echo "  Min score      : $MIN_SCORE / 10"
echo "  Block on fail  : $BLOCK_ON_FAIL"
echo "  Addons path    : $ADDONS_PATH"
echo "  Dry run        : $DRY_RUN"
echo "  Create PRs     : $CREATE_PR"
[ -n "$SKIP_REPOS" ] && echo "  Skipping       : $SKIP_REPOS"
echo ""

if ! command -v gh &>/dev/null; then
    echo "  ❌  GitHub CLI (gh) not found."
    echo "  Install: https://cli.github.com"
    exit 1
fi

if ! gh auth status &>/dev/null; then
    echo "  ❌  Not authenticated. Run: gh auth login"
    exit 1
fi

echo "  ✅  GitHub CLI authenticated as: $(gh api user --jq .login)"
echo ""

# ── Generate the workflow file content ───────────────────────────────────────
generate_workflow() {
    cat << WORKFLOW
# Auto-installed by odoo-review-central
# https://github.com/${CENTRAL_REPO}
name: Odoo Code Review

on:
  push:
    branches:
      - "**"

jobs:
  review:
    uses: ${CENTRAL_REPO}/.github/workflows/odoo-review-reusable.yml@main
    with:
      addons_path:   "${ADDONS_PATH}"
      min_score:     "${MIN_SCORE}"
      block_on_fail: "${BLOCK_ON_FAIL}"
    permissions:
      contents:      read
      pull-requests: write
      statuses:      write
WORKFLOW
}

# ── Fetch all repos ───────────────────────────────────────────────────────────
echo "  📋  Fetching repos for: $ORG ..."
echo ""

# Try as org first, fallback to user
REPOS=$(gh repo list "$ORG" --limit 200 --json name,isArchived,isFork \
        --jq '.[] | select(.isArchived == false) | .name' 2>/dev/null || \
        gh repo list --limit 200 --json name,isArchived \
        --jq '.[] | select(.isArchived == false) | .name')

if [ -z "$REPOS" ]; then
    echo "  ❌  No repos found for: $ORG"
    exit 1
fi

TOTAL=$(echo "$REPOS" | wc -l | tr -d ' ')
echo "  Found $TOTAL active repos"
echo ""

# ── Process each repo ─────────────────────────────────────────────────────────
INSTALLED=0
SKIPPED=0
ALREADY=0
FAILED=0

while IFS= read -r REPO; do
    FULL_REPO="${ORG}/${REPO}"

    # Skip the central repo itself
    [ "$REPO" = "odoo-review-central" ] && { echo "  ⏭️  $REPO (central repo — skip)"; ((SKIPPED++)); continue; }

    # Skip user-specified repos
    if [[ ",$SKIP_REPOS," == *",$REPO,"* ]]; then
        echo "  ⏭️  $REPO (in skip list)"
        ((SKIPPED++))
        continue
    fi

    # Check if workflow already exists
    if gh api "repos/${FULL_REPO}/contents/.github/workflows/odoo-review.yml" &>/dev/null 2>&1; then
        echo "  ✅  $REPO (already installed)"
        ((ALREADY++))
        continue
    fi

    echo "  📦  $REPO — installing..."

    if [ "$DRY_RUN" = true ]; then
        echo "      [dry-run] would create .github/workflows/odoo-review.yml"
        ((INSTALLED++))
        continue
    fi

    # Get default branch
    DEFAULT_BRANCH=$(gh api "repos/${FULL_REPO}" --jq .default_branch 2>/dev/null || echo "main")

    WORKFLOW_CONTENT=$(generate_workflow)

    if [ "$CREATE_PR" = true ]; then
        # Clone, branch, add file, push, open PR
        TMPDIR_REPO=$(mktemp -d)
        (
            cd "$TMPDIR_REPO"
            gh repo clone "$FULL_REPO" . -- --depth=1 --quiet
            git checkout -b "$BRANCH_NAME" 2>/dev/null || git checkout "$BRANCH_NAME"
            mkdir -p .github/workflows
            echo "$WORKFLOW_CONTENT" > .github/workflows/odoo-review.yml
            git add .github/workflows/odoo-review.yml
            git config user.name  "odoo-review-bot"
            git config user.email "odoo-review-bot@noreply"
            git commit -m "$COMMIT_MSG"
            git push origin "$BRANCH_NAME" --force --quiet
            gh pr create \
                --repo "$FULL_REPO" \
                --title "$PR_TITLE" \
                --body  "$PR_BODY" \
                --base  "$DEFAULT_BRANCH" \
                --head  "$BRANCH_NAME" \
                2>/dev/null || echo "      PR already exists"
        ) && {
            echo "      ✅ PR created in $REPO"
            ((INSTALLED++))
        } || {
            echo "      ❌ Failed — $REPO"
            ((FAILED++))
        }
        rm -rf "$TMPDIR_REPO"

    else
        # Direct push — add file via API without PR
        ENCODED=$(echo "$WORKFLOW_CONTENT" | base64 | tr -d '\n')
        gh api "repos/${FULL_REPO}/contents/.github/workflows/odoo-review.yml" \
            --method PUT \
            --field message="$COMMIT_MSG" \
            --field content="$ENCODED" \
            --field branch="$DEFAULT_BRANCH" \
            --silent && {
            echo "      ✅ Pushed directly to $REPO/$DEFAULT_BRANCH"
            ((INSTALLED++))
        } || {
            echo "      ❌ Failed — $REPO"
            ((FAILED++))
        }
    fi

done <<< "$REPOS"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "  ══════════════════════════════════════════════════════════════════"
echo "  INSTALLATION SUMMARY"
echo "  ══════════════════════════════════════════════════════════════════"
[ "$DRY_RUN" = true ] && echo "  ⚠️   DRY RUN — no changes were made"
echo "  ✅  Newly installed   : $INSTALLED"
echo "  ✔️   Already had it    : $ALREADY"
echo "  ⏭️   Skipped           : $SKIPPED"
[ $FAILED -gt 0 ] && echo "  ❌  Failed            : $FAILED"
echo ""

if [ "$CREATE_PR" = true ] && [ $INSTALLED -gt 0 ] && [ "$DRY_RUN" = false ]; then
    echo "  📬  PRs created in $INSTALLED repo(s)."
    echo "  Review and merge them at:"
    echo "  https://github.com/pulls?q=is:pr+is:open+head:${BRANCH_NAME}"
fi
echo ""
