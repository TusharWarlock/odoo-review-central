#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
#  ODOO REVIEW  —  SELF-HOSTED RUNNER SETUP
#
#  Registers your server as a GitHub Actions self-hosted runner for your
#  entire org. All repos in the org will use this runner automatically.
#
#  Run this ONCE on your server.
#
#  Requirements:
#    - Ubuntu 20.04+ / Debian
#    - Python 3.10+
#    - Git
#    - GitHub CLI authenticated (gh auth login)
#    - Org admin permissions
#
#  Usage:
#    bash setup_runner.sh --org YOUR_ORG --token RUNNER_TOKEN
#
#  Get your token:
#    GitHub → Settings → Actions → Runners → New self-hosted runner
# ════════════════════════════════════════════════════════════════════════════

set -e

# ── Must run as root ──────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo ""
    echo "  ❌  This script must be run as root."
    echo ""
    echo "  Run:  sudo bash setup_runner.sh --org YOUR_ORG --token YOUR_TOKEN"
    echo ""
    exit 1
fi

ORG=""
TOKEN=""
RUNNER_NAME="${HOSTNAME}-odoo-review"
RUNNER_DIR="/opt/github-runner-odoo"
RUNNER_USER="github-runner"
ACCOUNT_TYPE=""    # auto-detected: "org" or "user"

# ── Auto-detect latest runner version ────────────────────────────────────────
echo "  🔍  Detecting latest GitHub Actions runner version..."
RUNNER_VERSION=$(curl -sSf "https://api.github.com/repos/actions/runner/releases/latest"     | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'].lstrip('v'))" 2>/dev/null     || echo "2.321.0")
echo "  ✅  Runner version: $RUNNER_VERSION"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --org)    ORG="$2";          shift 2 ;;
        --token)  TOKEN="$2";        shift 2 ;;
        --name)   RUNNER_NAME="$2";  shift 2 ;;
        --dir)    RUNNER_DIR="$2";   shift 2 ;;
        --type)   ACCOUNT_TYPE="$2"; shift 2 ;;   # "org" or "user"
        *) echo "Unknown: $1"; exit 1 ;;
    esac
done

if [ -z "$ORG" ] || [ -z "$TOKEN" ]; then
    echo ""
    echo "  Usage: bash setup_runner.sh --org YOUR_ORG --token RUNNER_TOKEN"
    echo ""
    echo "  Get your token from:"
    echo "  Personal account : https://github.com/settings/actions/runners/new"
    echo "  Organisation     : https://github.com/organizations/YOUR_ORG/settings/actions/runners/new"
    echo ""
    exit 1
fi

echo ""
# ── Auto-detect account type (org vs personal) ───────────────────────────────
if [ -z "$ACCOUNT_TYPE" ]; then
    echo "  🔍  Detecting account type for: $ORG ..."
    GH_TYPE=$(curl -sf "https://api.github.com/users/${ORG}"         | python3 -c "import sys,json; print(json.load(sys.stdin).get('type','User'))" 2>/dev/null         || echo "User")
    if [ "$GH_TYPE" = "Organization" ]; then
        ACCOUNT_TYPE="org"
    else
        ACCOUNT_TYPE="user"
    fi
    echo "  ✅  Account type: $ACCOUNT_TYPE"
fi

# Runner URL differs between org and personal accounts
if [ "$ACCOUNT_TYPE" = "org" ]; then
    RUNNER_URL="https://github.com/${ORG}"
else
    RUNNER_URL="https://github.com/${ORG}"   # same URL, but token source differs
fi

echo "  ══════════════════════════════════════════════════════════════════"
echo "  🖥️   SELF-HOSTED RUNNER SETUP"
echo "  ══════════════════════════════════════════════════════════════════"
echo "  Account      : $ORG  ($ACCOUNT_TYPE)"
echo "  Runner URL   : $RUNNER_URL"
echo "  Runner name  : $RUNNER_NAME"
echo "  Install dir  : $RUNNER_DIR"
echo ""

# ── 1. Install system dependencies ───────────────────────────────────────────
echo "  📦  Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip git curl jq libicu-dev

# ── 2. Create runner user ─────────────────────────────────────────────────────
if ! id "$RUNNER_USER" &>/dev/null; then
    useradd -m -s /bin/bash "$RUNNER_USER"
    echo "  ✅  Created user: $RUNNER_USER"
fi

# ── 3. Download runner ────────────────────────────────────────────────────────
echo "  📥  Downloading GitHub Actions runner v${RUNNER_VERSION}..."
mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"

ARCH=$(uname -m)
case "$ARCH" in
    x86_64)  RUNNER_ARCH="x64"   ;;
    aarch64) RUNNER_ARCH="arm64" ;;
    *)       RUNNER_ARCH="x64"   ;;
esac

curl -sSfL \
  "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz" \
  -o runner.tar.gz
tar xzf runner.tar.gz
rm runner.tar.gz
chown -R "$RUNNER_USER":"$RUNNER_USER" "$RUNNER_DIR"

# ── 4. Get registration token + configure runner ──────────────────────────────
#
#  GitHub runner scopes:
#    Personal account  → repo-level only  (no account-wide runners)
#    Organisation      → org-level        (shared across all repos)
#
#  For personal accounts we register against a specific repo (odoo-review-central).
#  The reusable workflow still triggers on all other repos via workflow_call —
#  the runner just needs to be registered somewhere under your account.
# ─────────────────────────────────────────────────────────────────────────────

if [ "$ACCOUNT_TYPE" = "org" ]; then
    # ── Org: use provided token directly (from org runner settings page) ──────
    echo "  ⚙️   Configuring org-level runner for: $ORG ..."
    RUNNER_URL="https://github.com/${ORG}"
    REGISTRATION_TOKEN="$TOKEN"
    CONFIG_ARGS=(
        --url "$RUNNER_URL"
        --token "$REGISTRATION_TOKEN"
        --name "$RUNNER_NAME"
        --labels "self-hosted,odoo-review,linux"
        --runnergroup "Default"
        --work "$RUNNER_DIR/_work"
        --unattended
        --replace
    )
else
    # ── Personal account: register runner under odoo-review-central repo ──────
    #
    #  TOKEN here must be a Classic PAT with scope: repo
    #  Generate at: https://github.com/settings/tokens → Generate new token (classic)
    #  Scopes needed: ☑ repo  (full control of private repositories)
    #
    #  We use this PAT to call the API and get a short-lived registration token.
    # ─────────────────────────────────────────────────────────────────────────

    CENTRAL_REPO_NAME="odoo-review-central"
    echo "  🔑  Generating registration token via API for: $ORG/$CENTRAL_REPO_NAME ..."
    echo "  (TOKEN must be a Classic PAT with 'repo' scope)"

    API_RESPONSE=$(curl -sf         -X POST         -H "Accept: application/vnd.github+json"         -H "Authorization: Bearer ${TOKEN}"         -H "X-GitHub-Api-Version: 2022-11-28"         "https://api.github.com/repos/${ORG}/${CENTRAL_REPO_NAME}/actions/runners/registration-token"         2>&1)

    REGISTRATION_TOKEN=$(echo "$API_RESPONSE"         | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('token',''))" 2>/dev/null || echo "")

    if [ -z "$REGISTRATION_TOKEN" ]; then
        echo ""
        echo "  ❌  Could not get registration token. API response:"
        echo "  $API_RESPONSE"
        echo ""
        echo "  Make sure your TOKEN is a Classic PAT with 'repo' scope:"
        echo "  https://github.com/settings/tokens → Generate new token (classic) → ☑ repo"
        echo ""
        exit 1
    fi
    echo "  ✅  Registration token obtained (valid for 1 hour)"

    RUNNER_URL="https://github.com/${ORG}/${CENTRAL_REPO_NAME}"
    echo "  ⚙️   Configuring repo-level runner at: $RUNNER_URL ..."
    CONFIG_ARGS=(
        --url "$RUNNER_URL"
        --token "$REGISTRATION_TOKEN"
        --name "$RUNNER_NAME"
        --labels "self-hosted,odoo-review,linux"
        --work "$RUNNER_DIR/_work"
        --unattended
        --replace
    )
fi

sudo -u "$RUNNER_USER" "$RUNNER_DIR/config.sh" "${CONFIG_ARGS[@]}"

# ── 5. Install as systemd service ─────────────────────────────────────────────
echo "  🔧  Installing systemd service..."

cat > /etc/systemd/system/github-runner-odoo.service << SERVICE
[Unit]
Description=GitHub Actions Self-Hosted Runner (Odoo Review)
After=network.target

[Service]
ExecStart=${RUNNER_DIR}/run.sh
User=${RUNNER_USER}
WorkingDirectory=${RUNNER_DIR}
KillMode=process
KillSignal=SIGTERM
TimeoutStopSec=5min
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable  github-runner-odoo
systemctl restart github-runner-odoo

# ── 6. Verify ─────────────────────────────────────────────────────────────────
sleep 3
STATUS=$(systemctl is-active github-runner-odoo)

echo ""
echo "  ══════════════════════════════════════════════════════════════════"
if [ "$STATUS" = "active" ]; then
    echo "  ✅  Runner is ONLINE and running as systemd service"
else
    echo "  ⚠️   Runner status: $STATUS — check: journalctl -u github-runner-odoo"
fi
echo ""
echo "  Runner name   : $RUNNER_NAME"
echo "  Labels        : self-hosted, odoo-review, linux"
if [ "$ACCOUNT_TYPE" = "org" ]; then
    echo "  View runners  : https://github.com/organizations/$ORG/settings/actions/runners"
else
    echo "  View runners  : https://github.com/$ORG/odoo-review-central/settings/actions/runners"
fi
echo ""
echo "  Useful commands:"
echo "    systemctl status  github-runner-odoo"
echo "    systemctl restart github-runner-odoo"
echo "    journalctl -u github-runner-odoo -f"
echo "  ══════════════════════════════════════════════════════════════════"
echo ""