"""Configuration, paths and API settings for Jarvis."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_MODEL = "qwen3:8b"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")

API_KEY_ENV_VARS = ("JARVIS_API_KEY", "ZAI_API_KEY", "OPENAI_API_KEY")
BASE_URL_ENV_VARS = ("JARVIS_BASE_URL", "ZAI_API_BASE", "OPENAI_BASE_URL")


def state_dir() -> Path:
    """Where Jarvis keeps its memory, session and config."""
    override = os.environ.get("JARVIS_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".jarvis"


def config_file() -> Path:
    return state_dir() / "config.json"


def session_file() -> Path:
    return state_dir() / "session.json"


def memory_file() -> Path:
    return state_dir() / "memory.json"


@dataclass
class Config:
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    temperature: float = 0.4
    auto_approve: bool = False
    speak: bool = False
    # Always-listening voice mode
    wake_word: str = "hey_jarvis"
    wake_threshold: float = 0.5
    stt_model: str = "base.en"
    piper_voice: str = ""


def load_config() -> Config:
    """Load ~/.jarvis/config.json over the defaults; ignore broken values."""
    config = Config()
    path = config_file()
    if not path.is_file():
        return config
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return config
    if not isinstance(data, dict):
        return config

    if isinstance(data.get("model"), str) and data["model"].strip():
        config.model = data["model"].strip()
    if isinstance(data.get("base_url"), str) and data["base_url"].strip():
        config.base_url = data["base_url"].strip()
    if isinstance(data.get("temperature"), (int, float)):
        config.temperature = float(data["temperature"])
    if isinstance(data.get("auto_approve"), bool):
        config.auto_approve = data["auto_approve"]
    if isinstance(data.get("speak"), bool):
        config.speak = data["speak"]
    if isinstance(data.get("wake_word"), str) and data["wake_word"].strip():
        config.wake_word = data["wake_word"].strip()
    if isinstance(data.get("wake_threshold"), (int, float)):
        config.wake_threshold = max(0.05, min(0.95, float(data["wake_threshold"])))
    if isinstance(data.get("stt_model"), str) and data["stt_model"].strip():
        config.stt_model = data["stt_model"].strip()
    if isinstance(data.get("piper_voice"), str):
        config.piper_voice = data["piper_voice"].strip()
    return config


def apply_env_overrides(config: Config) -> Config:
    """Environment wins over config.json, as everywhere else."""
    for env_name, attribute in (
        ("JARVIS_WAKE_WORD", "wake_word"),
        ("JARVIS_STT_MODEL", "stt_model"),
        ("JARVIS_PIPER_VOICE", "piper_voice"),
    ):
        value = os.environ.get(env_name, "").strip()
        if value:
            setattr(config, attribute, value)
    threshold = os.environ.get("JARVIS_WAKE_THRESHOLD", "").strip()
    if threshold:
        try:
            config.wake_threshold = max(0.05, min(0.95, float(threshold)))
        except ValueError:
            pass
    return config


def auto_approve_env() -> bool:
    """JARVIS_AUTO_APPROVE=1 lets a headless server approve risky actions itself.

    Without it, a server has nobody at a terminal to answer the approval prompt,
    so write_file and run_command are refused and Jarvis cannot change its own
    code. Off by default: with it on, anyone holding the server token can use
    this shell.
    """
    raw = os.environ.get("JARVIS_AUTO_APPROVE", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def api_key() -> Optional[str]:
    for name in API_KEY_ENV_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def api_key_source() -> Optional[str]:
    for name in API_KEY_ENV_VARS:
        if os.environ.get(name, "").strip():
            return name
    return None


def resolve_base_url(config: Config) -> str:
    for name in BASE_URL_ENV_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return config.base_url


def resolve_model(config: Config) -> str:
    value = os.environ.get("JARVIS_MODEL", "").strip()
    return value or config.model


def llm_timeout() -> float:
    """Seconds to wait for the model before retrying (JARVIS_LLM_TIMEOUT)."""
    raw = os.environ.get("JARVIS_LLM_TIMEOUT", "").strip()
    if not raw:
        return 180.0
    try:
        return max(5.0, min(600.0, float(raw)))
    except ValueError:
        return 180.0


def is_local_url(base_url: str) -> bool:
    """True for a model server on this machine (Ollama, LM Studio, llama.cpp)."""
    try:
        host = (urlparse(base_url).hostname or "").lower()
    except ValueError:
        return False
    return host in LOCAL_HOSTS
