#!/usr/bin/env bash
# Start Ollama, make sure the configured model is present, then serve Jarvis.
# Runs inside the image built from deploy/Dockerfile.
set -euo pipefail

export OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
export OLLAMA_MODELS="${OLLAMA_MODELS:-/data/ollama}"
export JARVIS_HOME="${JARVIS_HOME:-/data}"
MODEL="${JARVIS_MODEL:-qwen3:14b}"
PORT="${PORT:-8787}"

mkdir -p "$OLLAMA_MODELS" "$JARVIS_HOME"

if ! curl -sf "http://${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
    echo "[jarvis] starting ollama (models in $OLLAMA_MODELS)"
    ollama serve >/tmp/ollama.log 2>&1 &
fi

echo "[jarvis] waiting for ollama..."
for _ in $(seq 1 120); do
    if curl -sf "http://${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
if ! curl -sf "http://${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
    echo "[jarvis] ollama did not start; last log lines:" >&2
    tail -n 20 /tmp/ollama.log >&2 || true
    exit 1
fi

if ! ollama list 2>/dev/null | awk 'NR > 1 { print $1 }' | grep -qx "$MODEL"; then
    echo "[jarvis] pulling $MODEL - first run only, this is the big download"
    ollama pull "$MODEL"
fi

echo "[jarvis] model $MODEL ready; serving on 0.0.0.0:${PORT}"
echo "[jarvis] the phone URL and token are printed by the server below"
exec jarvis --serve --host 0.0.0.0 --port "$PORT"
