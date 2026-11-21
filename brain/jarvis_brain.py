#!/usr/bin/env python3
"""Headless brain for the phone server, backed by isair/jarvis (upstream).

Upstream ships as a desktop app, but its answering path is a plain Python
package: config, a SQLite memory and a tool loop with no Qt, no microphone and
no packaged app in it. This script is that path with none of the desktop shell,
talking one JSON object per line:

    in   {"text": "..."}        ask a question
         {"cancel": true}       drop the reply in flight
         {"new_session": true}  forget the short-term conversation
    out  {"event": "ready", ...}
         {"event": "reply", "text": "..."}
         {"event": "error", "text": "..."}
         {"event": "busy"}
         {"event": "session", "text": "new"}

It must run inside upstream's virtualenv with its ``src`` on PYTHONPATH:

    PYTHONPATH=/opt/jarvis-upstream/src /opt/jarvis-upstream/.venv/bin/python \
        /opt/jarvis/brain/jarvis_brain.py

The model is whatever JARVIS_BASE_URL / JARVIS_API_KEY / JARVIS_MODEL say, so it
runs against the same OpenAI-compatible endpoint the phone server already uses.
Nothing is downloaded: no Whisper, no TTS, no face. This is the text brain only.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

# The protocol owns stdout. Upstream prints progress to stdout, so keep a handle
# to the real stream and send everything else to stderr.
PROTOCOL = sys.stdout


def emit(event: str, **fields: Any) -> None:
    """Write one protocol event line, never raising into the caller."""
    payload = {"event": event}
    payload.update(fields)
    try:
        PROTOCOL.write(json.dumps(payload) + "\n")
        PROTOCOL.flush()
    except (BrokenPipeError, ValueError):
        pass


def _setting(*names: str, default: str = "") -> str:
    """First non-empty environment value among ``names``."""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return default


def _home() -> Path:
    return Path(_setting("JARVIS_HOME", default=str(Path.home() / ".jarvis"))).expanduser()


def _config_path() -> Path:
    explicit = _setting("JARVIS_BRAIN_CONFIG")
    return Path(explicit) if explicit else _home() / "upstream" / "config.json"


def _seed_config(path: Path, base_url: str, api_key: str, model: str) -> None:
    """Keep upstream's config in step with the service environment.

    Only the keys this bridge owns are written, so anything tuned by hand
    (prompts, timeouts, MCP servers) survives a restart.
    """
    existing: Dict[str, Any] = {}
    try:
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
    except (OSError, ValueError):
        existing = {}
    existing.update(
        {
            "llm_provider": "openai_compatible",
            "llm_base_url": base_url,
            "llm_api_key": api_key,
            "llm_chat_model": model,
            "fast_model": model,
            "db_path": str(path.parent / "jarvis.db"),
            "tts_enabled": False,
            "dictation_enabled": False,
            "location_enabled": False,
            "background_tasks_enabled": False,
            "document_search_enabled": False,
            # A cheap endpoint will not answer /embeddings; fail that fast
            # instead of holding a turn open for a timeout.
            "llm_embedding_timeout_sec": 3.0,
            "dialogue_memory_timeout": 900.0,
        }
    )
    brave = _setting("BRAVE_SEARCH_API_KEY", "JARVIS_BRAVE_SEARCH_API_KEY")
    if brave:
        existing["brave_search_api_key"] = brave
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)


class Brain:
    """One question at a time through upstream's reply engine."""

    def __init__(self) -> None:
        self.cfg: Optional[Any] = None
        self.db: Optional[Any] = None
        self.memory: Optional[Any] = None
        self.engine: Optional[Any] = None
        self.diary_update: Optional[Any] = None
        self.busy = threading.Lock()
        self.cancel: Optional[threading.Event] = None

    def start(self) -> bool:
        model = _setting("JARVIS_BRAIN_MODEL", "JARVIS_MODEL")
        base_url = _setting("JARVIS_BRAIN_BASE_URL", "JARVIS_BASE_URL")
        api_key = _setting("JARVIS_BRAIN_API_KEY", "JARVIS_API_KEY")
        if not model or not base_url:
            emit(
                "error",
                text="the upstream brain needs JARVIS_MODEL and JARVIS_BASE_URL in its environment",
            )
            return False

        path = _config_path()
        _seed_config(path, base_url=base_url, api_key=api_key or "local", model=model)
        os.environ["JARVIS_CONFIG_PATH"] = str(path)

        from jarvis.config import load_settings
        from jarvis.memory.conversation import DialogueMemory, update_diary_from_dialogue_memory
        from jarvis.memory.db import Database
        from jarvis.reply.engine import run_reply_engine

        self.cfg = load_settings()
        self.db = Database(self.cfg.db_path, self.cfg.sqlite_vss_path)
        self.memory = DialogueMemory(
            inactivity_timeout=self.cfg.dialogue_memory_timeout, max_interactions=20
        )
        self.engine = run_reply_engine
        self.diary_update = update_diary_from_dialogue_memory
        emit(
            "ready",
            model=self.cfg.llm_chat_model,
            endpoint=self.cfg.llm_base_url,
            home=str(path.parent),
        )
        return True

    def ask(self, text: str) -> None:
        """Answer one question on a worker thread so cancel can arrive."""
        if not self.busy.acquire(blocking=False):
            emit("busy")
            return
        cancel = threading.Event()
        self.cancel = cancel
        threading.Thread(target=self._answer, args=(text, cancel), name="brain-query", daemon=True).start()

    def _answer(self, text: str, cancel: threading.Event) -> None:
        try:
            assert self.engine is not None and self.cfg is not None
            assert self.db is not None and self.memory is not None
            with contextlib.redirect_stdout(sys.stderr):
                reply = self.engine(
                    db=self.db,
                    cfg=self.cfg,
                    tts=None,
                    text=text,
                    dialogue_memory=self.memory,
                    language=None,
                    quiet=True,
                    cancel_event=cancel,
                )
            if cancel.is_set():
                emit("reply", text="", cancelled=True)
                return
            emit("reply", text=str(reply or ""))
            self._maybe_write_diary()
        except Exception as exc:  # a broken turn must not kill the bridge
            traceback.print_exc(file=sys.stderr)
            emit("error", text="%s: %s" % (type(exc).__name__, exc))
        finally:
            if self.cancel is cancel:
                self.cancel = None
            self.busy.release()

    def _maybe_write_diary(self) -> None:
        """Save what was said to long-term memory when upstream thinks it is due."""
        try:
            assert self.memory is not None and self.db is not None and self.cfg is not None
            if not self.memory.should_update_diary():
                return
            self.write_diary(force=False)
        except Exception:
            traceback.print_exc(file=sys.stderr)

    def write_diary(self, force: bool) -> None:
        try:
            assert self.diary_update is not None and self.db is not None
            assert self.cfg is not None and self.memory is not None
            with contextlib.redirect_stdout(sys.stderr):
                self.diary_update(
                    db=self.db,
                    dialogue_memory=self.memory,
                    cfg=self.cfg,
                    source_app="stdin",
                    voice_debug=False,
                    timeout_sec=min(float(self.cfg.llm_chat_timeout_sec), 45.0),
                    force=force,
                    on_token=None,
                    thinking=bool(getattr(self.cfg, "llm_thinking_enabled", False)),
                    graph_picker_model=self.cfg.fast_model,
                )
        except Exception:
            traceback.print_exc(file=sys.stderr)

    def new_session(self) -> None:
        from jarvis.memory.conversation import DialogueMemory

        assert self.cfg is not None
        self.memory = DialogueMemory(
            inactivity_timeout=self.cfg.dialogue_memory_timeout, max_interactions=20
        )
        emit("session", text="new")

    def close(self) -> None:
        if self.cancel is not None:
            self.cancel.set()
        self.write_diary(force=True)
        try:
            if self.db is not None:
                self.db.close()
        except Exception:
            traceback.print_exc(file=sys.stderr)


def main() -> int:
    brain = Brain()
    if not brain.start():
        return 1
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                emit("error", text="bad request line")
                continue
            if not isinstance(payload, dict):
                emit("error", text="bad request line")
                continue
            if payload.get("cancel"):
                if brain.cancel is not None:
                    brain.cancel.set()
                continue
            if payload.get("new_session"):
                brain.new_session()
                continue
            text = payload.get("text")
            if isinstance(text, str) and text.strip():
                brain.ask(text)
                continue
            emit("error", text="no text in request")
    except KeyboardInterrupt:
        pass
    finally:
        brain.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
