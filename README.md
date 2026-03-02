# odoo-review-central

> Self-hosted Odoo code review pipeline for GitHub.  
> Every push. Every branch. Every repo. Zero external APIs.

Inspired by the approach of self-hosted GitLab + LLM code review pipelines — everything stays within your infrastructure, no code ever leaves your network.

---

## What it does

Every time a developer pushes to **any branch** in any enrolled repo:

```
git push  →  GitHub Actions triggers
             ↓
             odoo_code_review.py runs on changed files only
             ↓
             ┌─────────────────────────────────────────┐
             │  ## 🔍 Odoo Code Review                 │
             │  Score   ████████░░  7.8 / 10           │
             │  Grade   🟢 Good                        │
             │  Errors  🔴 0                           │
             │  Warnings 🟡 3                          │
             │  Status  ✅ Passed                      │
             └─────────────────────────────────────────┘
             ↓
             Commit status set  ✅ / 🚫
             PR label updated   review: passed / failed
             Merge blocked      if score ≤ 5.0 / 10
```

**PR labels flow:**
```
(push) → 🟡 review: running → ✅ review: passed
                             → 🔴 review: failed
```

---

## Repository structure

```
odoo-review-central/
├── odoo_code_review.py                          ← review engine (all checks)
├── caller-template.yml                          ← copy this into each repo
├── pre-commit                                   ← local git hook
├── install_hooks.sh                             ← local hook installer
├── .github/
│   └── workflows/
│       └── odoo-review-reusable.yml             ← central workflow logic
└── scripts/
    ├── org_installer.sh                         ← installs to all 20+ repos at once
    └── setup_runner.sh                          ← registers self-hosted runner
```

---

## Setup — 3 steps

### Step 1 — Create the central repo

```bash
# Create a new repo on GitHub: YOUR_ORG/odoo-review-central
# Upload all files from this directory to it
# Set visibility: public  (required for cross-repo reusable workflows)
#   OR private + add repos as collaborators in Settings → Actions → Access
```

### Step 2 — Register your self-hosted runner (once, on your server)

```bash
# Get your runner token from:
# github.com/organizations/YOUR_ORG/settings/actions/runners/new

bash scripts/setup_runner.sh \
  --org   YOUR_ORG \
  --token YOUR_RUNNER_TOKEN
```

This installs the runner as a **systemd service** on your server — it starts automatically on boot and restarts on failure. All repos in the org will use it automatically (falls back to GitHub-hosted if offline).

### Step 3 — Install to all repos at once

```bash
# Requires GitHub CLI: https://cli.github.com
# Install + authenticate: gh auth login

bash scripts/org_installer.sh --org YOUR_ORG
```

This creates a **PR in every repo** adding `.github/workflows/odoo-review.yml`. Review and merge each PR. From that point, every push to that repo triggers the review automatically.

---

## Options

### org_installer.sh flags

```bash
# Preview without making changes
bash scripts/org_installer.sh --org YOUR_ORG --dry-run

# Custom minimum score
bash scripts/org_installer.sh --org YOUR_ORG --min-score 7.0

# Addons in a subdirectory
bash scripts/org_installer.sh --org YOUR_ORG --addons src/addons

# Skip specific repos
bash scripts/org_installer.sh --org YOUR_ORG --skip "repo1,repo2"

# Push directly without creating PRs (not recommended for 20+ repos)
bash scripts/org_installer.sh --org YOUR_ORG --no-pr

# Report only, never block merges
bash scripts/org_installer.sh --org YOUR_ORG --no-block
```

### Per-repo overrides

Each repo's `odoo-review.yml` can override defaults:

```yaml
jobs:
  review:
    uses: YOUR_ORG/odoo-review-central/.github/workflows/odoo-review-reusable.yml@main
    with:
      addons_path:   "src/addons"   # different path for this repo
      min_score:     "7.0"          # higher standard for this repo
      block_on_fail: "false"        # report only, don't block
```

### Adding new repos

For any new repo created in the future, just add the caller workflow:

```bash
# Option A: manually copy caller-template.yml → .github/workflows/odoo-review.yml
# Option B: re-run the installer (skips already-installed repos)
bash scripts/org_installer.sh --org YOUR_ORG
```

---

## Runner strategy

```
Push triggers workflow
        │
        ├─► Try self-hosted runner  (label: self-hosted)
        │         │
        │    ┌────┴────────────────────────────┐
        │    │ Runner online?  →  runs here    │  fast, your hardware
        │    │ Runner offline? →  skips        │
        │    └─────────────────────────────────┘
        │
        └─► GitHub-hosted fallback  (ubuntu-latest)
                  │
             Runs if self-hosted didn't complete
             Slightly slower but always available
```

**No external API calls in either case.** `odoo_code_review.py` is downloaded from this repo and runs entirely locally.

---

## What gets checked

| | Check | Severity |
|---|---|---|
| 🐍 **Python** | Model `_name`/`_description`, snake_case fields, `_id`/`_ids` suffixes | 🔴 Error |
| 🐍 **Python** | camelCase methods/variables, `_compute` patterns, `@api.depends` | 🟡 Warning |
| 🐍 **Python** | SQL injection, `sudo()`, `print()`, mutable defaults, import order | 🟡 Warning |
| 📄 **XML** | Deprecated `attrs`, `<tree>` tag, inline styles, view ID conventions | 🟡 Warning |
| ⚡ **JS/OWL** | OWL 2 patterns, jQuery, `var`, DOM manipulation, `console.log` | 🟡 Warning |
| 🎨 **SCSS** | `!important`, hardcoded colours, ID selectors, px font sizes | 🔵 Info |
| 📋 **CSV** | `ir.model.access.csv` format, permissions, groups | 🟡 Warning |
| 📦 **Manifest** | Required keys, version format, license | 🟡 Warning |

---

## Local pre-commit hook

Optionally enforce the review locally on every `git commit` too:

```bash
bash install_hooks.sh /path/to/your/repo
```

The same score gate applies — blocks commit if score ≤ 5.0/10.  
Emergency bypass: `git commit --no-verify`

---

## Score scale

| Score | Grade | Meaning |
|---|---|---|
| 9–10 | ✅ Excellent | Production ready |
| 7–9  | 🟢 Good | Minor issues only |
| 5–7  | 🟡 Fair | Warnings to address |
| 3–5  | 🟠 Poor | Errors must be fixed |
| 0–3  | 🔴 Critical | Blocked |
