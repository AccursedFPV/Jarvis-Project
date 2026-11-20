# Jarvis + Ollama in one container, for a rented GPU (RunPod, Vast.ai) or a
# GPU box at home. The model runs inside this container, so the assistant is
# entirely yours: no model API, no token costs, no third party.
#
#   docker build -t yourname/jarvis:gpu -f deploy/Dockerfile .
#   docker run --gpus all -p 8787:8787 -v jarvis-data:/data yourname/jarvis:gpu
#
# Everything that must survive a restart lives in /data: the Ollama models,
# memory, sessions, skills and the server token.

FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates procps \
    && rm -rf /var/lib/apt/lists/*

# Ollama serves the model on localhost inside this container.
RUN curl -fsSL https://ollama.com/install.sh | sh

WORKDIR /app
COPY pyproject.toml README.md ./
COPY jarvis ./jarvis
# faster-whisper lets POST /voice transcribe on the pod (phone dictation).
RUN python -m pip install --upgrade pip \
    && python -m pip install -e . faster-whisper

COPY deploy/gpu-entrypoint.sh /usr/local/bin/jarvis-gpu
RUN chmod +x /usr/local/bin/jarvis-gpu

ENV JARVIS_HOME=/data \
    JARVIS_BASE_URL=http://127.0.0.1:11434/v1 \
    JARVIS_MODEL=qwen3:14b \
    JARVIS_LLM_TIMEOUT=180 \
    OLLAMA_HOST=127.0.0.1:11434 \
    OLLAMA_MODELS=/data/ollama \
    PORT=8787

EXPOSE 8787
VOLUME ["/data"]

CMD ["/usr/local/bin/jarvis-gpu"]
