"""Terminal output helpers."""

from __future__ import annotations

import os
import sys
from typing import Any, Dict

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"


def _color_supported() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def _short(value: str, limit: int = 140) -> str:
    value = " ".join(str(value).split())
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


class UI:
    def __init__(self, color: bool = True) -> None:
        self.color = color and _color_supported()
        if os.name == "nt":
            # Enable ANSI escape handling in Windows consoles that need it.
            os.system("")

    def paint(self, code: str, text: str) -> str:
        if not self.color:
            return text
        return code + text + RESET

    def banner(self, model: str, cwd: str) -> None:
        print()
        print(self.paint(BOLD, "  J A R V I S") + self.paint(DIM, "  -  personal AI companion"))
        print(self.paint(DIM, "  %s  |  %s" % (model, cwd)))
        print(self.paint(DIM, "  /help for commands  |  /exit to quit  |  Ctrl+C to cancel a reply"))
        print()

    def prompt(self) -> str:
        return self.paint(GREEN, "you> ")

    def assistant_prefix(self) -> None:
        print(self.paint(CYAN, "jarvis> "), end="", flush=True)

    def token(self, text: str) -> None:
        print(text, end="", flush=True)

    def newline(self) -> None:
        print()

    def info(self, message: str) -> None:
        print(self.paint(DIM, message))

    def warn(self, message: str) -> None:
        print(self.paint(YELLOW, "! " + message))

    def error(self, message: str) -> None:
        print(self.paint(RED, "error: " + message))

    def tool(self, name: str, args: Dict[str, Any]) -> None:
        detail = _tool_detail(name, args)
        print(self.paint(DIM, "  [%s] %s" % (name, _short(detail))))

    def user_said(self, text: str) -> None:
        print(self.paint(GREEN, "you> ") + text)


def _tool_detail(name: str, args: Dict[str, Any]) -> str:
    if name == "run_command":
        return str(args.get("command", ""))
    if name == "write_file":
        return "%s (%d chars)" % (args.get("path", "?"), len(str(args.get("content", ""))))
    if name in {"read_file", "list_files", "make_dir", "delete_path", "search_files"}:
        return str(args.get("path", ".") or ".") + (" (" + str(args.get("query")) + ")" if args.get("query") else "")
    if name == "move_path":
        return "%s -> %s" % (args.get("source", "?"), args.get("destination", "?"))
    if name == "fetch_url":
        return str(args.get("url", "?"))
    if name == "open_target":
        return str(args.get("target", "?"))
    if name == "launch_app":
        return str(args.get("name", "?"))
    if name == "remember":
        return str(args.get("fact", "?"))
    if name == "forget":
        return str(args.get("fact", "?"))
    if name == "web_search":
        return str(args.get("query", "?"))
    if name == "write_skill":
        return str(args.get("name", "?"))
    if name == "delete_skill":
        return str(args.get("name", "?"))
    if name == "learn":
        return str(args.get("text", "?"))
    if name == "recall":
        return str(args.get("query", "?"))
    if name == "update_plan":
        return "%d step(s)" % len(args.get("steps") or [])
    if name == "complete_step":
        return "step %s" % args.get("step", "?")
    if name.startswith("skill_"):
        return name[len("skill_"):]
    return ""
