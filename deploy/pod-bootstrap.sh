#!/usr/bin/env bash
# Bootstrap Jarvis on a GPU pod (RunPod / Vast.ai) that has a web terminal.
# Use this when you do not have a machine with Docker to build the image.
#
# In the pod terminal, as root:
#   apt-get update && apt-get install -y git
#   git clone https://github.com/AccursedFPV/Jarvis-Project.git /workspace/Jarvis-Project
#   cd /workspace/Jarvis-Project && bash deploy/pod-bootstrap.sh
#
# It is re-runnable: an existing clone is updated, the venv is reused, the model
# is only pulled once. If Ollama is already serving (the ollama/ollama image
# does this), it reuses it instead of starting a second one.
#
# Run it inside tmux so it survives the browser tab closing:
#   tmux new -s jarvis        # then run the script; detach with Ctrl+B then D
#   tmux attach -t jarvis     # to come back

set -euo pipefail

CODE_DIR="${CODE_DIR:-/workspace/Jarvis-Project}"
STATE_DIR="${JARVIS_HOME:-/workspace/jarvis-data}"
VENV="${VENV_DIR:-/workspace/jarvis-venv}"
MODEL="${JARVIS_MODEL:-qwen3:14b}"
PORT="${PORT:-8787}"
OLLAMA_URL="http://127.0.0.1:11434"
OLLAMA_LOG="${OLLAMA_LOG:-/tmp/ollama.log}"
TOKEN_FILE="$STATE_DIR/server_token"

say() { echo "[jarvis] $*"; }

mkdir -p "$STATE_DIR"

# ---------------------------------------------------------------- prerequisites
missing=()
command -v git >/dev/null 2>&1 || missing+=(git)
command -v curl >/dev/null 2>&1 || missing+=(curl)
command -v python3 >/dev/null 2>&1 || missing+=(python3)
python3 -c "import venv" >/dev/null 2>&1 || missing+=(python3-venv)
if [ "${#missing[@]}" -gt 0 ]; then
    say "installing ${missing[*]}"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing[@]}"
fi

# -------------------------------------------------------------------- the code
if [ -d "$CODE_DIR/.git" ]; then
    say "updating $CODE_DIR"
    git -C "$CODE_DIR" pull --ff-only
else
    say "cloning into $CODE_DIR"
    mkdir -p "$(dirname "$CODE_DIR")"
    git clone https://github.com/AccursedFPV/Jarvis-Project.git "$CODE_DIR"
fi
cd "$CODE_DIR"

# --------------------------------------------------------------------- the venv
if [ ! -x "$VENV/bin/python" ]; then
    say "creating the virtualenv in $VENV"
    python3 -m venv "$VENV"
fi
say "installing Jarvis (plus faster-whisper for phone voice)"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -e . faster-whisper

# -------------------------------------------------------------------- the model
if ! command -v ollama >/dev/null 2>&1; then
    say "installing ollama"
    curl -fsSL https://ollama.com/install.sh | sh
fi
if ! curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
    say "starting ollama (log: $OLLAMA_LOG)"
    nohup ollama serve >"$OLLAMA_LOG" 2>&1 &
fi
say "waiting for ollama to answer"
for _ in $(seq 1 120); do
    curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1 && break
    sleep 1
done
if ! curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
    echo "[jarvis] ollama is not answering; last lines of $OLLAMA_LOG:" >&2
    tail -n 20 "$OLLAMA_LOG" >&2 || true
    exit 1
fi
if ! ollama list 2>/dev/null | awk 'NR > 1 { print $1 }' | grep -qx "$MODEL"; then
    say "pulling $MODEL (first run; several GB)"
    ollama pull "$MODEL"
fi

# ----------------------------------------------------------------- serve Jarvis
export JARVIS_HOME="$STATE_DIR"
export JARVIS_BASE_URL="http://127.0.0.1:11434/v1"
export JARVIS_MODEL="$MODEL"
export JARVIS_LLM_TIMEOUT="${JARVIS_LLM_TIMEOUT:-180}"

say "model $MODEL ready"
say "state (memory, skills, token) is in $STATE_DIR - keep it on the network volume"
if [ -f "$TOKEN_FILE" ]; then
    say "existing token: $(cat "$TOKEN_FILE")"
else
    say "a token will be generated and printed by the server below"
fi
say "starting the server on 0.0.0.0:$PORT"

exec "$VENV/bin/jarvis" --serve --host 0.0.0.0 --port "$PORT"
