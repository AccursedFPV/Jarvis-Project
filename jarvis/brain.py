"""Phone-server brain backed by isair/jarvis (upstream) instead of our agent loop.

Upstream is a desktop app, so it is not imported here: its reply engine runs in
its own virtualenv behind ``brain/jarvis_brain.py`` and this class owns that
process. The server keeps the phone page, the token, the chat store and the
systemd unit; only the thinking is different.

Set ``JARVIS_BRAIN=upstream`` to use it.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_TIMEOUT = 240.0
DEFAULT_START_TIMEOUT = 180.0


def _env_float(name: str, default: float) -> float:
    try:
        return max(5.0, float(os.environ.get(name, "") or default))
    except ValueError:
        return default


class UpstreamBrain:
    """One upstream reply engine process, one question at a time."""

    # The server prints this in its warnings, which is the truthful value here:
    # upstream's tools are not gated by our approval prompt.
    enabled = "upstream"

    def __init__(self, config: Any, ui: Any) -> None:
        self.config = config
        self.ui = ui
        self.history: List[Dict[str, Any]] = []
        # Upstream runs its own tools without asking; this only mirrors the
        # attribute the server reads when it starts.
        self.toolbox = type("Toolbox", (), {"auto_approve": True, "schemas": lambda self: []})()
        self._lock = threading.Lock()
        self._events: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._stderr: List[str] = []
        self._proc: Optional[subprocess.Popen] = None
        self._model = ""
        self._endpoint = ""
        self._tools = 0

    # ------------------------------------------------------------------ describe

    def describe(self) -> Dict[str, Any]:
        """The facts /health reports for this brain."""
        return {
            "brain": "isair/jarvis (upstream)",
            "model": self._model or os.environ.get("JARVIS_MODEL", ""),
            "endpoint": self._endpoint or os.environ.get("JARVIS_BASE_URL", ""),
            "thinking": "vendor default",
            "search": "upstream (brave key or Wikipedia)",
            # Tools from brain/jarvis_tools_mcp.py: run_command, read_file, ...
            "tools": self._tools,
        }

    def prewarm(self) -> None:
        """Start the bridge before the first message, so /health is truthful
        about the tool count and the first answer does not wait for startup."""

        def warm() -> None:
            try:
                with self._lock:
                    if not self._alive():
                        self._start()
            except Exception:  # a failed prewarm must not take the server down
                pass

        threading.Thread(target=warm, name="brain-warm", daemon=True).start()

    # ------------------------------------------------------------------- process

    def _paths(self) -> tuple:
        python = os.environ.get("JARVIS_BRAIN_PYTHON", "").strip() or sys.executable
        source = os.environ.get("JARVIS_UPSTREAM_SRC", "").strip() or "/opt/jarvis-upstream/src"
        script = os.environ.get("JARVIS_BRAIN_SCRIPT", "").strip() or str(
            Path(__file__).resolve().parent.parent / "brain" / "jarvis_brain.py"
        )
        return python, source, script

    def _environment(self) -> Dict[str, str]:
        python, source, script = self._paths()
        env = dict(os.environ)
        env["PYTHONPATH"] = source + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["PYTHONUNBUFFERED"] = "1"
        # Upstream reads its own config file; keep it in our state folder.
        env.setdefault("JARVIS_HOME", str(Path(os.environ.get("JARVIS_HOME", "") or Path.home() / ".jarvis")))
        return env

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _start(self) -> bool:
        """Spawn the bridge and wait for its ready event."""
        python, source, script = self._paths()
        if not Path(script).is_file():
            self._error("the upstream bridge script is missing: %s" % script)
            return False
        if not Path(source).is_dir():
            self._error(
                "no upstream checkout at %s - run deploy/vps-install-upstream.sh on the VPS" % source
            )
            return False
        while not self._events.empty():
            self._events.get_nowait()
        self._stderr = []
        try:
            self._proc = subprocess.Popen(
                [python, script],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=self._environment(),
            )
        except OSError as exc:
            self._error("cannot start the upstream brain: %s" % exc)
            return False
        threading.Thread(target=self._read_stdout, name="brain-out", daemon=True).start()
        threading.Thread(target=self._read_stderr, name="brain-err", daemon=True).start()

        deadline = time.monotonic() + _env_float("JARVIS_BRAIN_START_TIMEOUT", DEFAULT_START_TIMEOUT)
        while time.monotonic() < deadline:
            if not self._alive():
                self._error("the upstream brain stopped while starting")
                return False
            try:
                event = self._events.get(timeout=0.5)
            except queue.Empty:
                continue
            if event.get("event") == "ready":
                self._model = str(event.get("model") or "")
                self._endpoint = str(event.get("endpoint") or "")
                try:
                    self._tools = int(event.get("tools") or 0)
                except (TypeError, ValueError):
                    self._tools = 0
                return True
            if event.get("event") == "error":
                self._error(str(event.get("text") or "the upstream brain refused to start"))
                return False
        self._error("the upstream brain took too long to start")
        return False

    def _read_stdout(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("event"):
                self._events.put(event)

    def _read_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            line = line.rstrip()
            if line:
                self._stderr.append(line)
                del self._stderr[:-60]

    def _error(self, message: str) -> None:
        detail = ""
        if self._stderr:
            detail = "\n" + "\n".join(self._stderr[-6:])
        if self.ui is not None:
            try:
                self.ui.error(message + detail)
            except Exception:
                pass

    def _send(self, payload: Dict[str, Any]) -> bool:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            return False
        try:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False

    def _failure_text(self) -> str:
        tail = " | ".join(self._stderr[-3:])
        return (
            "The upstream brain stopped mid-answer."
            + (" Last it said: " + tail[:400] if tail else "")
            + " Try again; if it keeps happening, check: journalctl -u jarvis-serve -n 40"
        )

    # ---------------------------------------------------------------------- chat

    def chat(
        self,
        user_text: Any,
        on_token: Any = None,
        on_tool: Any = None,
        model: Any = None,
        on_reasoning: Any = None,
        on_step: Any = None,
        should_stop: Any = None,
    ) -> str:
        if isinstance(user_text, list):
            return (
                "Pictures need the built-in vision model, which this upstream brain does not "
                "use. Ask in words, or attach the file as text (a .txt, .py or .md) instead."
            )
        text = str(user_text or "").strip()
        if not text:
            return "I did not get any text there."

        with self._lock:
            if not self._alive() and not self._start():
                return (
                    "I could not start the upstream brain. On the VPS, run "
                    "deploy/vps-install-upstream.sh and check /health."
                )
            if on_step is not None:
                try:
                    on_step("asked upstream Jarvis")
                except Exception:
                    pass
            while not self._events.empty():
                self._events.get_nowait()
            if not self._send({"text": text}):
                self._proc = None
                return self._failure_text()

            deadline = time.monotonic() + _env_float("JARVIS_BRAIN_TIMEOUT", DEFAULT_TIMEOUT)
            cancelled = False
            while True:
                if should_stop is not None and should_stop() and not cancelled:
                    cancelled = True
                    self._send({"cancel": True})
                if not self._alive():
                    self._proc = None
                    return self._failure_text() if not cancelled else "Stopped at your request."
                try:
                    event = self._events.get(timeout=0.5)
                except queue.Empty:
                    if time.monotonic() >= deadline:
                        self._send({"cancel": True})
                        self._proc = None
                        return "That took longer than I wait for an answer, so I stopped it."
                    continue
                kind = event.get("event")
                if kind == "reply":
                    if cancelled or event.get("cancelled"):
                        return "Stopped at your request."
                    return str(event.get("text") or "") or (
                        "Upstream Jarvis finished without saying anything. Ask again."
                    )
                if kind == "error":
                    return "Upstream Jarvis failed: %s" % str(event.get("text") or "unknown error")
                if kind == "busy":
                    return "I am still on the previous message - give me a moment."

    def new_session(self) -> None:
        """Forget the short-term conversation (used by the New button)."""
        if self._alive():
            self._send({"new_session": True})
        self.history = []

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
            proc.wait(timeout=20)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
