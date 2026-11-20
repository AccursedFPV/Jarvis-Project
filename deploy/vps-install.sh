#!/usr/bin/env bash
# Install the Jarvis server on a fresh Ubuntu 22.04/24.04 VPS.
#
# Run as root, from the project folder that you copied to the VPS:
#
#   scp -r Jarvis-Project root@<vps-ip>:/root/
#   ssh root@<vps-ip>
#   cd /root/Jarvis-Project && bash deploy/vps-install.sh
#
# What it does: creates a service user, installs the project into /opt/jarvis
# with its own virtualenv, writes /etc/jarvis.env (with a random token) the
# first time, sets up the systemd service, and starts it.
#
# The model does NOT run here - a cheap VPS has no GPU. Point JARVIS_BASE_URL
# at a free-tier cloud model until your own PC is back online, then switch it
# to the PC's Ollama address over Tailscale.

set -euo pipefail

APP_DIR="/opt/jarvis"
STATE_DIR="/var/lib/jarvis"
SERVICE_USER="jarvis"
ENV_FILE="/etc/jarvis.env"
PORT="${JARVIS_PORT:-8787}"

fail() { echo "error: $*" >&2; exit 1; }

[ "$(id -u)" = "0" ] || fail "run this as root (sudo -i, or prefix with sudo)"
[ -f "pyproject.toml" ] || fail "run this from the project folder (pyproject.toml not found)"

echo "==> installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip ufw ca-certificates >/dev/null

if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    echo "==> creating the $SERVICE_USER user"
    useradd --system --create-home --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

echo "==> copying the project to $APP_DIR"
mkdir -p "$APP_DIR"
if [ "$(readlink -f .)" != "$(readlink -f "$APP_DIR")" ]; then
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --exclude '.git' --exclude '.venv' --exclude '__pycache__' ./ "$APP_DIR"/
    else
        cp -r ./jarvis ./pyproject.toml ./README.md ./contrib ./deploy "$APP_DIR"/
    fi
fi

echo "==> creating the virtualenv and installing Jarvis"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
# [stt] adds numpy + faster-whisper so POST /voice can transcribe here. It is not
# [listen]: that also pulls openWakeWord, whose tflite-runtime has no 3.12 wheel,
# and a headless server has no wake word to listen for anyway.
"$APP_DIR/.venv/bin/pip" install --quiet -e "$APP_DIR[stt]"

echo "==> writing $ENV_FILE"
if [ ! -f "$ENV_FILE" ]; then
    TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
    cat > "$ENV_FILE" <<EOF
# Jarvis server environment. Edit the API key line, then:
#   systemctl restart jarvis-serve
JARVIS_HOME=$STATE_DIR
JARVIS_SERVER_TOKEN=$TOKEN
JARVIS_API_KEY=
JARVIS_BASE_URL=https://api.z.ai/api/paas/v4
JARVIS_MODEL=glm-4.7-flash
# give up on a slow/unreachable model quickly so the phone is not left waiting
JARVIS_LLM_TIMEOUT=90
# Set to 1 so the server approves risky actions itself (writing files, running
# commands) - needed before it can edit its own code from the phone. Leave it at
# 0 and those actions are refused, because nobody is at the terminal to approve.
JARVIS_AUTO_APPROVE=0
PYTHONUNBUFFERED=1
EOF
    chmod 600 "$ENV_FILE"
    echo "    a new token was generated - see: grep TOKEN $ENV_FILE"
else
    echo "    keeping the existing $ENV_FILE"
fi

mkdir -p "$STATE_DIR"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$STATE_DIR" "$APP_DIR"
chmod 700 "$STATE_DIR"

echo "==> installing the systemd service"
install -m 644 "$APP_DIR/contrib/jarvis-serve.service" /etc/systemd/system/jarvis-serve.service
systemctl daemon-reload
systemctl enable --now jarvis-serve >/dev/null

# restart_self needs one narrow root permission, and only for this unit.
echo "==> letting Jarvis restart itself after it edits its own code"
SYSTEMCTL="$(command -v systemctl || echo /usr/bin/systemctl)"
SUDOERS="/etc/sudoers.d/jarvis-restart"
printf '%s ALL=(root) NOPASSWD: %s restart jarvis-serve, %s status jarvis-serve\n' \\
    "$SERVICE_USER" "$SYSTEMCTL" "$SYSTEMCTL" > "$SUDOERS"
chmod 440 "$SUDOERS"
if ! visudo -cf "$SUDOERS" >/dev/null 2>&1; then
    rm -f "$SUDOERS"
    echo "    (the sudoers rule did not validate - skipped; restart_self will be unavailable)"
fi

echo "==> firewall: SSH open, $PORT only over Tailscale"
ufw allow OpenSSH >/dev/null 2>&1 || true
ufw allow in on tailscale0 to any port "$PORT" proto tcp >/dev/null 2>&1 || true
yes | ufw enable >/dev/null 2>&1 || true

sleep 1
if systemctl is-active --quiet jarvis-serve; then
    echo
    echo "Jarvis is running."
    echo "  status : systemctl status jarvis-serve"
    echo "  logs   : journalctl -u jarvis-serve -f"
    echo "  token  : grep JARVIS_SERVER_TOKEN $ENV_FILE"
    echo
    echo "Next:"
    echo "  1. put a free API key in $ENV_FILE (JARVIS_API_KEY=...), then"
    echo "     systemctl restart jarvis-serve"
    echo "  2. install Tailscale on this VPS and on your phone:"
    echo "     curl -fsSL https://tailscale.com/install.sh | sh && tailscale up"
    echo "  3. on the phone open http://<this-vps-tailscale-name>:$PORT/?token=<token>"
else
    echo "error: the service did not stay up - check: journalctl -u jarvis-serve -n 50" >&2
    exit 1
fi
