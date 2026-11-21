#!/usr/bin/env bash
# Give the phone server the brain from https://github.com/isair/jarvis.
#
# Run as root on the VPS, after the phone server itself is installed:
#
#   cd ~/Jarvis-Project && git pull
#   sudo cp -r jarvis/*.py /opt/jarvis/jarvis/ && sudo cp -r brain /opt/jarvis/
#   sudo bash deploy/vps-install-upstream.sh
#
# Upstream is a desktop app, but its answering path is a plain Python package.
# This installs that package in its own virtualenv at /opt/jarvis-upstream and
# switches /etc/jarvis.env to JARVIS_BRAIN=upstream. The phone page, token,
# chat history, systemd unit, Tailscale address and port do not change.
#
# Deliberately not installed: PyQt6, faster-whisper (torch), sounddevice,
# piper/chatterbox, playwright, faiss. The typed-question path does not import
# them, and they would not fit a small VPS.
#
# To go back to the built-in agent:
#   sudo sed -i 's/^JARVIS_BRAIN=.*/JARVIS_BRAIN=builtin/' /etc/jarvis.env
#   sudo systemctl restart jarvis-serve

set -euo pipefail

APP_DIR="/opt/jarvis"
UPSTREAM_DIR="/opt/jarvis-upstream"
UPSTREAM_REPO="https://github.com/isair/jarvis.git"
ENV_FILE="/etc/jarvis.env"
SERVICE_USER="jarvis"

fail() { echo "error: $*" >&2; exit 1; }

[ "$(id -u)" = "0" ] || fail "run this as root (sudo bash deploy/vps-install-upstream.sh)"
[ -f "$APP_DIR/jarvis/serve.py" ] || fail "no phone server at $APP_DIR - run deploy/vps-install.sh first"
[ -f "$APP_DIR/brain/jarvis_brain.py" ] || fail "brain/jarvis_brain.py is missing - copy the repo to $APP_DIR first"
[ -f "$APP_DIR/brain/requirements.txt" ] || fail "brain/requirements.txt is missing - copy the repo to $APP_DIR first"
[ -f "$ENV_FILE" ] || fail "$ENV_FILE is missing - run deploy/vps-install.sh first"

# Upstream needs 3.11 or later.
PY_OK="$(python3 -c 'import sys; print(int(sys.version_info >= (3, 11)))' 2>/dev/null || echo 0)"
[ "$PY_OK" = "1" ] || fail "upstream needs Python 3.11+; this box has $(python3 -V 2>&1)"

echo "==> installing git and python3-venv if they are missing"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3-venv python3-pip >/dev/null

echo "==> fetching upstream isair/jarvis"
if [ -d "$UPSTREAM_DIR/.git" ]; then
    git -C "$UPSTREAM_DIR" fetch --depth 1 origin main
    git -C "$UPSTREAM_DIR" reset --hard origin/main
else
    git clone --depth 1 "$UPSTREAM_REPO" "$UPSTREAM_DIR"
fi

echo "==> creating the text-only virtualenv"
python3 -m venv "$UPSTREAM_DIR/.venv"
"$UPSTREAM_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$UPSTREAM_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/brain/requirements.txt"

echo "==> checking the upstream core imports"
if ! PYTHONPATH="$UPSTREAM_DIR/src" "$UPSTREAM_DIR/.venv/bin/python" -c \
    "from jarvis.reply.engine import run_reply_engine" ; then
    fail "the upstream core did not import - check the pip output above"
fi

echo "==> pointing $ENV_FILE at the upstream brain"
set_env() {
    local key="$1" value="$2"
    if grep -q "^${key}=" "$ENV_FILE"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
        printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    fi
}
set_env JARVIS_BRAIN upstream
set_env JARVIS_BRAIN_PYTHON "$UPSTREAM_DIR/.venv/bin/python"
set_env JARVIS_UPSTREAM_SRC "$UPSTREAM_DIR/src"
chmod 600 "$ENV_FILE"

# The bridge writes its own config and SQLite memory under JARVIS_HOME.
chown -R "$SERVICE_USER":"$SERVICE_USER" "$UPSTREAM_DIR" "$APP_DIR/brain" 2>/dev/null || true

echo "==> restarting the service"
systemctl restart jarvis-serve
sleep 2
if ! systemctl is-active --quiet jarvis-serve; then
    echo "error: the service did not come back - check: journalctl -u jarvis-serve -n 50" >&2
    exit 1
fi

TOKEN="$(grep '^JARVIS_SERVER_TOKEN=' "$ENV_FILE" | cut -d= -f2)"
echo
echo "Upstream brain installed. It reports itself in /health:"
curl -s -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8787/health" || true
echo
echo
echo "The first message takes longer than usual: it starts the brain process."
echo "  logs   : journalctl -u jarvis-serve -n 40"
echo "  revert : sudo sed -i 's/^JARVIS_BRAIN=.*/JARVIS_BRAIN=builtin/' $ENV_FILE && sudo systemctl restart jarvis-serve"
