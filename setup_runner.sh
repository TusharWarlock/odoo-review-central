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

ORG=""
TOKEN=""
RUNNER_NAME="${HOSTNAME}-odoo-review"
RUNNER_DIR="/opt/github-runner-odoo"
RUNNER_USER="github-runner"
RUNNER_VERSION="2.317.0"   # update as needed: https://github.com/actions/runner/releases

while [[ $# -gt 0 ]]; do
    case "$1" in
        --org)    ORG="$2";   shift 2 ;;
        --token)  TOKEN="$2"; shift 2 ;;
        --name)   RUNNER_NAME="$2"; shift 2 ;;
        --dir)    RUNNER_DIR="$2";  shift 2 ;;
        *) echo "Unknown: $1"; exit 1 ;;
    esac
done

if [ -z "$ORG" ] || [ -z "$TOKEN" ]; then
    echo ""
    echo "  Usage: bash setup_runner.sh --org YOUR_ORG --token RUNNER_TOKEN"
    echo ""
    echo "  Get your token at:"
    echo "  https://github.com/organizations/YOUR_ORG/settings/actions/runners/new"
    echo ""
    exit 1
fi

echo ""
echo "  ══════════════════════════════════════════════════════════════════"
echo "  🖥️   SELF-HOSTED RUNNER SETUP"
echo "  ══════════════════════════════════════════════════════════════════"
echo "  Org          : $ORG"
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

# ── 4. Configure runner ───────────────────────────────────────────────────────
echo "  ⚙️   Configuring runner for org: $ORG ..."
sudo -u "$RUNNER_USER" "$RUNNER_DIR/config.sh" \
    --url "https://github.com/${ORG}" \
    --token "$TOKEN" \
    --name "$RUNNER_NAME" \
    --labels "self-hosted,odoo-review,linux" \
    --runnergroup "Default" \
    --work "$RUNNER_DIR/_work" \
    --unattended \
    --replace

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
echo "  Org           : https://github.com/organizations/$ORG/settings/actions/runners"
echo ""
echo "  Useful commands:"
echo "    systemctl status  github-runner-odoo"
echo "    systemctl restart github-runner-odoo"
echo "    journalctl -u github-runner-odoo -f"
echo "  ══════════════════════════════════════════════════════════════════"
echo ""
