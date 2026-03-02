#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
#  ODOO REVIEW  —  WEBHOOK SERVICE INSTALLER
#
#  Installs the auto-installer webhook service on your server.
#  After this, every new repo created in your org automatically
#  gets the review workflow — no manual steps needed ever again.
#
#  This script:
#    1. Clones odoo-review-central to /opt/odoo-review-central
#       (all 11 files live there — review engine, templates, hooks)
#    2. Installs webhook_service.py as a systemd service
#    3. The service reads caller-template.yml from the cloned repo
#       (same file org_installer.sh uses for existing repos)
#
#  Usage:
#    bash setup_webhook.sh \
#      --org     YOUR_ORG \
#      --token   YOUR_GITHUB_TOKEN \
#      --secret  YOUR_WEBHOOK_SECRET
#
#  After running, register the webhook in GitHub ONCE:
#    github.com/organizations/YOUR_ORG/settings/hooks → Add webhook
#    Payload URL : http://YOUR_SERVER_IP:9001/webhook
#    Content type: application/json
#    Secret      : same as --secret above
#    Events      : ☑ Repositories  (under "Let me select individual events")
# ════════════════════════════════════════════════════════════════════════════

set -e

ORG=""
TOKEN=""
SECRET=""
MIN_SCORE="5.0"
ADDONS_PATH="."
PORT="9001"
CREATE_PR="true"
INSTALL_DIR="/opt/odoo-review-central"
SERVICE_USER="github-runner"    # same user as the Actions runner (setup_runner.sh)

while [[ $# -gt 0 ]]; do
    case "$1" in
        --org)        ORG="$2";         shift 2 ;;
        --token)      TOKEN="$2";       shift 2 ;;
        --secret)     SECRET="$2";      shift 2 ;;
        --min-score)  MIN_SCORE="$2";   shift 2 ;;
        --addons)     ADDONS_PATH="$2"; shift 2 ;;
        --port)       PORT="$2";        shift 2 ;;
        --no-pr)      CREATE_PR="false"; shift  ;;
        --dir)        INSTALL_DIR="$2"; shift 2 ;;
        *) echo "Unknown argument: $1"; exit 1  ;;
    esac
done

if [ -z "$ORG" ] || [ -z "$TOKEN" ] || [ -z "$SECRET" ]; then
    echo ""
    echo "  Usage: bash setup_webhook.sh --org YOUR_ORG --token TOKEN --secret SECRET"
    echo ""
    exit 1
fi

CENTRAL_REPO="${ORG}/odoo-review-central"
CENTRAL_REPO_URL="https://github.com/${CENTRAL_REPO}.git"

echo ""
echo "  ══════════════════════════════════════════════════════════════════"
echo "  🔧  ODOO REVIEW — WEBHOOK SERVICE INSTALLER"
echo "  ══════════════════════════════════════════════════════════════════"
echo "  Org              : $ORG"
echo "  Central repo     : $CENTRAL_REPO"
echo "  Install dir      : $INSTALL_DIR  (all 11 files cloned here)"
echo "  Port             : $PORT"
echo "  Min score        : $MIN_SCORE"
echo "  Create PRs       : $CREATE_PR"
echo ""

# ── Ensure service user exists ────────────────────────────────────────────────
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd -m -s /bin/bash "$SERVICE_USER"
    echo "  ✅  Created user: $SERVICE_USER"
fi

# ── Clone or pull the central repo to INSTALL_DIR ─────────────────────────────
# This brings ALL 11 files to the server:
#   odoo_code_review.py, caller-template.yml, odoo-review-reusable.yml,
#   pre-commit, install_hooks.sh, org_installer.sh, setup_runner.sh,
#   setup_webhook.sh, webhook_service.py, odoo-review-webhook.service, README.md

echo "  📥  Syncing odoo-review-central to $INSTALL_DIR ..."

if [ -d "$INSTALL_DIR/.git" ]; then
    # Already cloned — just pull latest
    GIT_ASKPASS=true git -C "$INSTALL_DIR" pull --quiet \
        "https://${TOKEN}@github.com/${CENTRAL_REPO}.git" main 2>/dev/null \
        || echo "  ⚠️   Pull failed — using existing files"
    echo "  ✅  Updated to latest"
else
    # Fresh clone
    rm -rf "$INSTALL_DIR"
    GIT_ASKPASS=true git clone --quiet \
        "https://${TOKEN}@github.com/${CENTRAL_REPO}.git" \
        "$INSTALL_DIR"
    echo "  ✅  Cloned to $INSTALL_DIR"
fi

chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"
chmod +x "$INSTALL_DIR"/*.sh 2>/dev/null || true

# ── Verify caller-template.yml exists ────────────────────────────────────────
if [ ! -f "$INSTALL_DIR/caller-template.yml" ]; then
    echo "  ❌  caller-template.yml not found in $INSTALL_DIR"
    echo "  Make sure it exists in your odoo-review-central repo on GitHub."
    exit 1
fi
echo "  ✅  caller-template.yml found — webhook will use this for new repos"

# ── Write systemd service ─────────────────────────────────────────────────────
cat > /etc/systemd/system/odoo-review-webhook.service << SERVICE
[Unit]
Description=Odoo Review Auto-Installer Webhook Service
Documentation=https://github.com/${CENTRAL_REPO}
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}

# Required
Environment="GITHUB_TOKEN=${TOKEN}"
Environment="WEBHOOK_SECRET=${SECRET}"
Environment="CENTRAL_REPO=${CENTRAL_REPO}"

# Points to the cloned repo — webhook_service.py reads caller-template.yml from here
Environment="CENTRAL_REPO_DIR=${INSTALL_DIR}"

# Optional
Environment="MIN_SCORE=${MIN_SCORE}"
Environment="ADDONS_PATH=${ADDONS_PATH}"
Environment="CREATE_PR=${CREATE_PR}"
Environment="PORT=${PORT}"

ExecStart=/usr/bin/python3 ${INSTALL_DIR}/webhook_service.py

Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=odoo-review-webhook

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable  odoo-review-webhook
systemctl restart odoo-review-webhook

sleep 3
STATUS=$(systemctl is-active odoo-review-webhook)

# ── Verify health endpoint ────────────────────────────────────────────────────
sleep 2
HEALTH=$(curl -sf "http://localhost:${PORT}/health" 2>/dev/null || echo "{}")
TEMPLATE_OK=$(echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('template_exists','?'))" 2>/dev/null || echo "?")

echo ""
echo "  ══════════════════════════════════════════════════════════════════"
if [ "$STATUS" = "active" ]; then
    echo "  ✅  Webhook service is RUNNING"
    echo "  ✅  caller-template.yml loaded: $TEMPLATE_OK"
else
    echo "  ⚠️   Service status: $STATUS"
    echo "  Run: journalctl -u odoo-review-webhook -f"
fi
echo ""
echo "  Files on server ($INSTALL_DIR):"
ls "$INSTALL_DIR"/*.py "$INSTALL_DIR"/*.yml "$INSTALL_DIR"/*.sh 2>/dev/null \
    | while read f; do echo "    ✔  $(basename $f)"; done

echo ""
echo "  ══════════════════════════════════════════════════════════════════"
echo "  NEXT STEP — Register webhook in GitHub (ONE TIME ONLY)"
echo "  ══════════════════════════════════════════════════════════════════"

PUBLIC_IP=$(curl -sSf https://api.ipify.org 2>/dev/null || echo "YOUR_SERVER_IP")

echo ""
echo "  Go to: https://github.com/organizations/${ORG}/settings/hooks"
echo "  Click: Add webhook"
echo ""
echo "    Payload URL  :  http://${PUBLIC_IP}:${PORT}/webhook"
echo "    Content type :  application/json"
echo "    Secret       :  (the --secret value you provided)"
echo "    Which events :  ☑  Repositories"
echo ""
echo "  Test: create a new repo in your org — within 20 seconds it will"
echo "  automatically get a PR with .github/workflows/odoo-review.yml"
echo ""
echo "  Useful commands:"
echo "    systemctl status  odoo-review-webhook"
echo "    journalctl -u odoo-review-webhook -f"
echo "    curl http://localhost:${PORT}/health"
echo ""
echo "  To update when odoo-review-central changes:"
echo "    cd $INSTALL_DIR && git pull"
echo "    systemctl restart odoo-review-webhook"
echo "  ══════════════════════════════════════════════════════════════════"
echo ""
