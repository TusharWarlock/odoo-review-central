#!/usr/bin/env python3
"""
════════════════════════════════════════════════════════════════════════════
 ODOO REVIEW  —  AUTO-INSTALLER WEBHOOK SERVICE

 Listens for GitHub org webhooks and auto-installs the review workflow
 into every new repo the moment it is created.

 It installs caller-template.yml (from this same central repo) into the
 new repo as .github/workflows/odoo-review.yml — the same file that the
 org_installer.sh deploys to existing repos.

 Flow:
   New repo created in org
        │
        └─► GitHub fires webhook → this service receives it
                  │
                  ├─ Verifies HMAC signature
                  ├─ Waits 15s for GitHub to finish initialising the repo
                  ├─ Reads caller-template.yml from central repo on disk
                  ├─ Creates .github/workflows/odoo-review.yml via GitHub API
                  ├─ Opens a PR   (or pushes directly if repo is empty)
                  └─ Creates review: labels on the new repo

 All 11 files live together in odoo-review-central:
   odoo_code_review.py          ← review engine
   caller-template.yml          ← workflow template installed into each repo
   odoo-review-reusable.yml     ← reusable workflow logic
   pre-commit                   ← local git hook
   install_hooks.sh             ← local hook installer
   org_installer.sh             ← bulk installer for existing repos
   setup_runner.sh              ← self-hosted runner setup
   setup_webhook.sh             ← THIS service installer
   webhook_service.py           ← THIS file
   odoo-review-webhook.service  ← systemd unit (reference)
   README.md

 Config (environment variables):
   GITHUB_TOKEN      PAT with repo + workflow scope
   WEBHOOK_SECRET    Secret set in GitHub webhook settings
   CENTRAL_REPO      e.g. your-org/odoo-review-central
   CENTRAL_REPO_DIR  Local path where central repo is cloned (default: /opt/odoo-review-central)
   MIN_SCORE         Minimum score to pass (default: 5.0)
   ADDONS_PATH       Addons path in target repos (default: .)
   PORT              HTTP port (default: 9001)
   CREATE_PR         true/false — open PR or push directly (default: true)
════════════════════════════════════════════════════════════════════════════
"""

import os
import hmac
import hashlib
import json
import time
import logging
import threading
import base64
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
GITHUB_TOKEN     = os.environ.get("GITHUB_TOKEN",     "")
WEBHOOK_SECRET   = os.environ.get("WEBHOOK_SECRET",   "")
CENTRAL_REPO     = os.environ.get("CENTRAL_REPO",     "")
CENTRAL_REPO_DIR = os.environ.get("CENTRAL_REPO_DIR", "/opt/odoo-review-central")
MIN_SCORE        = os.environ.get("MIN_SCORE",         "5.0")
ADDONS_PATH      = os.environ.get("ADDONS_PATH",       ".")
PORT             = int(os.environ.get("PORT",          "9001"))
CREATE_PR        = os.environ.get("CREATE_PR",         "true").lower() == "true"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("odoo-review-webhook")


# ── Load caller-template.yml from disk ────────────────────────────────────────
def load_caller_template():
    """
    Read caller-template.yml from the central repo on disk and substitute
    the org-specific values. This is the exact same file org_installer.sh uses.
    """
    template_path = Path(CENTRAL_REPO_DIR) / "caller-template.yml"

    if template_path.exists():
        content = template_path.read_text()
    else:
        # Fallback: minimal inline template if file not found on disk
        log.warning(f"caller-template.yml not found at {template_path} — using inline fallback")
        content = """\
# Auto-installed by odoo-review-central
name: Odoo Code Review
on:
  push:
    branches:
      - "**"
jobs:
  review:
    uses: CENTRAL_REPO/.github/workflows/odoo-review-reusable.yml@main
    with:
      addons_path:   "ADDONS_PATH"
      min_score:     "MIN_SCORE"
      block_on_fail: "true"
    permissions:
      contents:      read
      pull-requests: write
      statuses:      write
"""

    # Substitute placeholders that org_installer.sh also substitutes
    content = content.replace("YOUR_ORG/odoo-review-central", CENTRAL_REPO)
    content = content.replace("CENTRAL_REPO",  CENTRAL_REPO)
    content = content.replace('"."',           f'"{ADDONS_PATH}"')
    content = content.replace('"5.0"',         f'"{MIN_SCORE}"')
    return content


INSTALL_PR_BODY = f"""\
## 🔍 Odoo Code Review — Auto-installed

This PR was opened automatically when this repository was created.

**What this adds:**
- Runs `odoo_code_review.py` on every push to every branch
- Checks Odoo coding standards (Python, XML, JS, SCSS, CSV, manifest)
- Posts a full report as a PR comment with score and per-file details
- Sets commit status ✅ / 🚫 on every push
- Blocks merge if score ≤ {MIN_SCORE} / 10
- Uses self-hosted runner first, falls back to GitHub-hosted automatically

**No changes to your code.** Only adds `.github/workflows/odoo-review.yml`.

_Auto-installed by [odoo-review-central](https://github.com/{CENTRAL_REPO})_
"""


# ── GitHub API ────────────────────────────────────────────────────────────────
def gh_api(method, path, data=None):
    url  = f"https://api.github.com{path}"
    body = json.dumps(data).encode() if data else None
    req  = urllib.request.Request(
        url, data=body, method=method,
        headers={
            "Authorization":        f"Bearer {GITHUB_TOKEN}",
            "Accept":               "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type":         "application/json",
            "User-Agent":           "odoo-review-webhook/1.0",
        }
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


# ── Core installer ────────────────────────────────────────────────────────────
def install_review_workflow(full_repo, repo_name, default_branch):
    log.info(f"[{repo_name}] Installing odoo-review workflow...")

    # Already installed?
    status, _ = gh_api("GET", f"/repos/{full_repo}/contents/.github/workflows/odoo-review.yml")
    if status == 200:
        log.info(f"[{repo_name}] Already installed — skipping")
        return

    # Get latest repo state
    status, repo_info = gh_api("GET", f"/repos/{full_repo}")
    if status != 200:
        log.error(f"[{repo_name}] Could not fetch repo info")
        return

    is_empty       = repo_info.get("size", 0) == 0
    default_branch = repo_info.get("default_branch", default_branch or "main")

    # Load caller-template.yml from disk (same file org_installer.sh uses)
    workflow_content  = load_caller_template()
    encoded_content   = base64.b64encode(workflow_content.encode()).decode()

    _ensure_labels(full_repo, repo_name)

    if is_empty or not CREATE_PR:
        _push_directly(full_repo, repo_name, encoded_content, default_branch)
    else:
        _create_pr(full_repo, repo_name, encoded_content, default_branch)


def _push_directly(full_repo, repo_name, encoded_content, branch):
    status, result = gh_api("PUT", f"/repos/{full_repo}/contents/.github/workflows/odoo-review.yml", {
        "message": "ci: add Odoo code review workflow",
        "content": encoded_content,
        "branch":  branch,
    })
    if status in (200, 201):
        log.info(f"[{repo_name}] ✅ Pushed directly to {branch}")
    else:
        log.error(f"[{repo_name}] ❌ Direct push failed ({status}): {result}")


def _create_pr(full_repo, repo_name, encoded_content, default_branch):
    branch_name = "chore/add-odoo-code-review"

    # Get SHA of default branch tip
    status, ref = gh_api("GET", f"/repos/{full_repo}/git/ref/heads/{default_branch}")
    if status != 200:
        log.warning(f"[{repo_name}] Cannot get branch SHA — pushing directly")
        _push_directly(full_repo, repo_name, encoded_content, default_branch)
        return

    base_sha = ref["object"]["sha"]

    # Create branch
    gh_api("POST", f"/repos/{full_repo}/git/refs", {
        "ref": f"refs/heads/{branch_name}",
        "sha": base_sha,
    })

    # Add file on that branch
    status, _ = gh_api("PUT", f"/repos/{full_repo}/contents/.github/workflows/odoo-review.yml", {
        "message": "ci: add Odoo code review workflow",
        "content": encoded_content,
        "branch":  branch_name,
    })
    if status not in (200, 201):
        log.error(f"[{repo_name}] ❌ Could not create file on branch")
        return

    # Open PR
    status, pr = gh_api("POST", f"/repos/{full_repo}/pulls", {
        "title": "🔍 Add Odoo code review",
        "body":  INSTALL_PR_BODY,
        "head":  branch_name,
        "base":  default_branch,
    })
    if status in (200, 201):
        log.info(f"[{repo_name}] ✅ PR created → {pr.get('html_url')}")
    else:
        log.error(f"[{repo_name}] ❌ PR failed ({status})")


def _ensure_labels(full_repo, repo_name):
    for label in [
        {"name": "review: pending",  "color": "cccccc", "description": "Odoo review queued"},
        {"name": "review: running",  "color": "e4e669", "description": "Odoo review in progress"},
        {"name": "review: passed",   "color": "0e8a16", "description": "Odoo review passed ✅"},
        {"name": "review: failed",   "color": "d73a49", "description": "Odoo review failed 🚫"},
    ]:
        gh_api("POST", f"/repos/{full_repo}/labels", label)


# ── HTTP handler ──────────────────────────────────────────────────────────────
class WebhookHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        log.debug(f"HTTP {fmt % args}")

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {
                "status":       "ok",
                "central_repo": CENTRAL_REPO,
                "template":     str(Path(CENTRAL_REPO_DIR) / "caller-template.yml"),
                "template_exists": (Path(CENTRAL_REPO_DIR) / "caller-template.yml").exists(),
            })
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/webhook":
            self._respond(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)

        # Verify HMAC signature
        if WEBHOOK_SECRET:
            sig      = self.headers.get("X-Hub-Signature-256", "")
            expected = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, expected):
                log.warning("Invalid webhook signature")
                self._respond(401, {"error": "invalid signature"})
                return

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._respond(400, {"error": "invalid JSON"})
            return

        self._respond(200, {"status": "accepted"})

        event = self.headers.get("X-GitHub-Event", "")
        threading.Thread(
            target=self._handle, args=(event, payload), daemon=True
        ).start()

    def _handle(self, event, payload):
        action    = payload.get("action", "")
        repo      = payload.get("repository", {})
        full_name = repo.get("full_name", "")
        repo_name = repo.get("name", "")
        default_b = repo.get("default_branch", "main")

        if event == "repository" and action == "created":
            if repo_name in ("odoo-review-central",):
                log.info(f"[{repo_name}] Central repo — skipping")
                return
            log.info(f"New repo: {full_name} — waiting 15s to initialise...")
            time.sleep(15)
            install_review_workflow(full_name, repo_name, default_b)
        else:
            log.debug(f"Ignored: {event}/{action}")

    def _respond(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    errors = []
    if not GITHUB_TOKEN:   errors.append("GITHUB_TOKEN not set")
    if not CENTRAL_REPO:   errors.append("CENTRAL_REPO not set (e.g. your-org/odoo-review-central)")
    if not WEBHOOK_SECRET: log.warning("WEBHOOK_SECRET not set — requests will not be verified")
    if errors:
        for e in errors: log.error(f"Config: {e}")
        raise SystemExit(1)

    template_path = Path(CENTRAL_REPO_DIR) / "caller-template.yml"
    if not template_path.exists():
        log.warning(f"caller-template.yml not found at {template_path}")
        log.warning("Make sure CENTRAL_REPO_DIR points to the cloned odoo-review-central repo")

    log.info("══════════════════════════════════════════════════════")
    log.info("  Odoo Review — Auto-Installer Webhook Service")
    log.info("══════════════════════════════════════════════════════")
    log.info(f"  Central repo     : {CENTRAL_REPO}")
    log.info(f"  Central repo dir : {CENTRAL_REPO_DIR}")
    log.info(f"  caller-template  : {template_path} ({'✅' if template_path.exists() else '⚠️  missing'})")
    log.info(f"  Min score        : {MIN_SCORE}")
    log.info(f"  Create PRs       : {CREATE_PR}")
    log.info(f"  Listening on     : http://0.0.0.0:{PORT}/webhook")
    log.info(f"  Health check     : http://0.0.0.0:{PORT}/health")
    log.info("══════════════════════════════════════════════════════")

    server = HTTPServer(("0.0.0.0", PORT), WebhookHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
