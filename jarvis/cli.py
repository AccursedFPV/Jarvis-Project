"""Command-line interface for Jarvis."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from . import __version__, voice
from .agent import Agent, load_history, save_history
from .config import (
    Config,
    api_key,
    api_key_source,
    apply_env_overrides,
    auto_approve_env,
    config_file,
    is_local_url,
    llm_fallbacks,
    llm_timeout,
    load_config,
    memory_file,
    resolve_base_url,
    resolve_model,
    session_file,
    state_dir,
)
from .learning import Learnings
from .llm import LLM, LLMError
from .memory import Memory
from .skills import Skills
from .tools import TOOLS, Toolbox
from .ui import UI

LOCAL_SETUP_HELP = """
Jarvis runs its model on this computer, so it needs a local model server.

  1. Install Ollama (free): https://ollama.com/download
  2. In any terminal, pull the default model once:

       ollama pull qwen3:8b

  3. Run `jarvis` again. Ollama starts itself when needed.

qwen3:8b wants roughly 6 GB of free RAM or video memory. On a smaller
machine use a lighter model:

       ollama pull qwen3:4b
       jarvis --model qwen3:4b

LM Studio, llama.cpp and vLLM work too - point JARVIS_BASE_URL at them.
Already have a cloud key? Add JARVIS_API_KEY and JARVIS_BASE_URL to use
that instead.

Config file: {config_path}
""".format(config_path=config_file())

CLOUD_SETUP_HELP = """
Jarvis is pointed at a cloud endpoint but no API key is set.

  1. Get a free key at https://z.ai (glm-4.7-flash costs $0 per token).
  2. Set it in your environment:

       macOS / Linux:   export JARVIS_API_KEY="your-key"
       PowerShell:      $env:JARVIS_API_KEY = "your-key"
       cmd.exe:         set JARVIS_API_KEY=your-key

     ZAI_API_KEY and OPENAI_API_KEY are also accepted.
  3. Run `jarvis` again.

To keep everything on this computer instead - no key, no account:

       unset JARVIS_BASE_URL
       ollama pull qwen3:8b

Config file: {config_path}
""".format(config_path=config_file())


def _model_installed(model: str, installed: list) -> bool:
    wanted = model.strip().lower()
    names = {name.strip().lower() for name in installed}
    if wanted in names:
        return True
    # Ollama treats "qwen3:8b" and "qwen3:8b:latest" as the same model.
    return wanted + ":latest" in names or wanted.replace(":latest", "") in {
        name.replace(":latest", "") for name in names
    }


def _local_preflight(ui: UI, base_url: str, model: str) -> bool:
    """Check the local model server before the first request, with plain fixes."""
    url = base_url.rstrip("/") + "/models"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read(2_000_000).decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError:
        return True  # something is listening; let the model call explain itself
    except Exception:
        ui.error("No model server is answering at %s." % base_url)
        print(LOCAL_SETUP_HELP)
        return False

    entries = payload.get("data") if isinstance(payload, dict) else None
    installed = [str(item.get("id")) for item in entries or [] if isinstance(item, dict) and item.get("id")]
    if installed and not _model_installed(model, installed):
        ui.error("The model %r is not installed on this computer." % model)
        print("Models you have: %s" % ", ".join(installed))
        print("\nGet it with:      ollama pull %s" % model)
        print("Or use one above:  jarvis --model %s" % installed[0])
        return False
    return True

COMMAND_HELP = """
Commands:
  /help              show this help
  /new               forget the conversation and start fresh
  /memory            list what Jarvis remembers about you
  /remember <fact>   add a long-term memory
  /forget <fact>     remove a long-term memory
  /tools             list the actions Jarvis can take
  /skills            list the tools Jarvis wrote for itself
  /plan              show the current task plan
  /learn <lesson>    add a lesson to its long-term lessons
  /lessons           list learned lessons
  /model [name]      show or change the model for this session
  /config            show model, endpoint and approval settings
  /exit              quit (also /quit or Ctrl+D)
"""


# --------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="Jarvis - a personal AI companion in your terminal that can use your files, apps and shell.",
        epilog="Examples:\n"
        '  jarvis                                  start a conversation\n'
        '  jarvis "find my biggest downloads"      ask one question and exit\n'
        '  jarvis --continue                        pick up the last conversation\n'
        '  jarvis --voice                           talk to Jarvis and hear replies\n'
        '  jarvis --listen                          wake on "Hey Jarvis" and answer out loud\n'
        '  jarvis --serve                           let your phone use this PC over Tailscale/LAN\n'
        '  jarvis -y "clean up the temp folder"     auto-approve risky actions\n'
        '  jarvis --config                          show resolved settings\n',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("prompt", nargs="*", help="ask a single question and exit")
    parser.add_argument("-c", "--continue", dest="resume", action="store_true", help="resume the previous conversation")
    parser.add_argument("-n", "--new", action="store_true", help="start a fresh conversation")
    parser.add_argument("--model", help="model name for this run")
    parser.add_argument("--base-url", help="OpenAI-compatible API base URL for this run")
    parser.add_argument("-y", "--yes", action="store_true", help="auto-approve risky actions for this run")
    parser.add_argument("--voice", action="store_true", help="listen through the microphone and speak replies")
    parser.add_argument(
        "--listen",
        action="store_true",
        help="always listening: wake on the wake word, transcribe and answer out loud (local models)",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="run the phone/other-device server (token auth) and keep running",
    )
    parser.add_argument("--host", help="address for --serve (default 0.0.0.0)")
    parser.add_argument("--port", type=int, help="port for --serve (default 8787)")
    parser.add_argument("--speak", action="store_true", help="speak replies out loud")
    parser.add_argument("--no-color", action="store_true", help="disable colored output")
    parser.add_argument("--config", action="store_true", help="show configuration and API key status, then exit")
    parser.add_argument("--version", action="version", version="jarvis " + __version__)
    return parser


# ---------------------------------------------------------------------- setup


def print_config(config: Config) -> None:
    source = api_key_source()
    base_url = resolve_base_url(config)
    if source:
        key_status = "set via " + source
    elif is_local_url(base_url):
        key_status = "not needed (local endpoint)"
    else:
        key_status = "missing"
    exists = "exists" if config_file().is_file() else "not created yet"
    print("model        : %s" % resolve_model(config))
    print("base URL     : %s" % base_url)
    print("runs on      : %s" % ("this computer (local server)" if is_local_url(base_url) else "a cloud endpoint"))
    print("API key      : %s" % key_status)
    print("temperature  : %s" % config.temperature)
    print("auto-approve : %s" % ("yes" if config.auto_approve else "no"))
    print("config file  : %s (%s)" % (config_file(), exists))
    print("state folder : %s" % state_dir())
    print("session file : %s" % session_file())
    print("memory file  : %s" % memory_file())


def _prompt_for_approval(ui: UI):
    def approve(name: str, args: Dict[str, Any]) -> str:
        ui.warn("Approval needed: " + _describe_action(name, args))
        try:
            answer = input("  allow this? [y]es / [N]o / [a]lways for this tool: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ui.newline()
            return "no"
        if answer in {"y", "yes"}:
            return "yes"
        if answer in {"a", "always"}:
            return "always"
        return "no"

    return approve


def _describe_action(name: str, args: Dict[str, Any]) -> str:
    if name == "run_command":
        where = " in %s" % args["cwd"] if args.get("cwd") else ""
        return "run shell command %r%s" % (args.get("command", ""), where)
    if name == "write_file":
        verb = "append to" if args.get("append") else "write"
        return "%s %s (%d chars)" % (verb, args.get("path", "?"), len(str(args.get("content", ""))))
    if name == "delete_path":
        extra = " and everything inside it" if args.get("recursive") else ""
        return "delete %s%s" % (args.get("path", "?"), extra)
    if name == "move_path":
        return "move %s -> %s" % (args.get("source", "?"), args.get("destination", "?"))
    if name == "write_skill":
        return "write a new tool skill_%s (%d lines of Python)" % (
            args.get("name", "?"),
            str(args.get("code", "")).count("\n") + 1,
        )
    if name == "delete_skill":
        return "delete the tool skill_%s" % args.get("name", "?")
    if name == "open_target":
        return "open %s" % args.get("target", "?")
    if name == "launch_app":
        return "launch app %s" % args.get("name", "?")
    return "run tool %s" % name


# ----------------------------------------------------------------- rendering


class StreamPrinter:
    """Print the assistant label lazily, then stream tokens after it."""

    def __init__(self, ui: UI) -> None:
        self.ui = ui
        self.started = False

    def __call__(self, text: str) -> None:
        if not self.started:
            self.ui.assistant_prefix()
            self.started = True
        self.ui.token(text)

    def finish(self) -> None:
        if self.started:
            self.ui.newline()
            self.started = False


# ----------------------------------------------------------------------- REPL


def _read_input(ui: UI, voice_enabled: bool) -> Optional[str]:
    if voice_enabled:
        ui.info("Listening... (speak now, or wait to type instead)")
        try:
            heard = voice.listen()
        except Exception as exc:
            ui.warn("Microphone problem: %s" % exc)
            heard = None
        if heard:
            ui.user_said(heard)
            return heard
        ui.warn("I didn't catch that - type your message instead.")
    try:
        return input(ui.prompt())
    except EOFError:
        return None
    except KeyboardInterrupt:
        ui.newline()
        return None


def _handle_command(text: str, agent: Agent, ui: UI) -> Optional[str]:
    parts = text.split(maxsplit=1)
    command = parts[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""

    if command in {"/exit", "/quit", "/q"}:
        return "exit"
    if command == "/help":
        print(COMMAND_HELP)
    elif command == "/new":
        agent.history.clear()
        try:
            session_file().unlink()
        except OSError:
            pass
        ui.info("Started a new conversation.")
    elif command == "/memory":
        facts = agent.memory.facts()
        if not facts:
            ui.info("Nothing in long-term memory yet.")
        else:
            ui.info("Long-term memory (%d):" % len(facts))
            for fact in facts:
                print("  - " + fact)
    elif command == "/remember":
        if not argument:
            ui.warn("Usage: /remember <fact>")
        else:
            print(agent.toolbox.remember(argument))
    elif command == "/forget":
        if not argument:
            ui.warn("Usage: /forget <fact>")
        else:
            print(agent.toolbox.forget(argument))
    elif command == "/tools":
        for spec in agent.toolbox.schemas():
            function = spec["function"]
            print("  %-18s %s" % (function["name"], function["description"]))
    elif command == "/skills":
        skills = agent.toolbox.skills
        rows = skills.describe() if skills else []
        if not rows:
            ui.info("No self-written skills yet. Ask Jarvis to build one for a task.")
        else:
            for row in rows:
                print("  " + row)
    elif command == "/plan":
        print(agent.toolbox.plan_block())
    elif command == "/learn":
        if not argument:
            ui.warn("Usage: /learn <lesson>")
        else:
            print(agent.toolbox.learn(argument))
    elif command == "/lessons":
        learnings = agent.toolbox.learnings
        items = learnings.texts() if learnings else []
        if not items:
            ui.info("No lessons learned yet.")
        else:
            ui.info("Lessons (%d):" % len(items))
            for item in items:
                print("  - " + item)
    elif command == "/model":
        if argument:
            agent.llm.model = argument
            ui.info("Model set to %s for this session." % argument)
        else:
            ui.info("Model: %s" % agent.llm.model)
    elif command == "/config":
        ui.info("Model: %s" % agent.llm.model)
        ui.info("Endpoint: %s" % agent.llm.url)
        ui.info("Approvals: %s" % ("auto-approve" if agent.toolbox.auto_approve else "ask before risky actions"))
        ui.info("Session: %s" % session_file())
    else:
        ui.warn("Unknown command %r. Type /help." % command)
    return None


def repl(agent: Agent, ui: UI, speak_replies: bool, voice_enabled: bool) -> int:
    ui.banner(agent.llm.model, str(Path.cwd()))
    while True:
        text = _read_input(ui, voice_enabled)
        if text is None:
            print()
            ui.info("Goodbye.")
            return 0
        text = text.strip()
        if not text:
            continue
        if text.startswith("/"):
            if _handle_command(text, agent, ui) == "exit":
                print()
                ui.info("Goodbye.")
                return 0
            continue

        printer = StreamPrinter(ui)
        try:
            reply = agent.chat(text, on_token=printer, on_tool=printer.finish)
        except KeyboardInterrupt:
            printer.finish()
            ui.warn("Interrupted.")
            continue
        except LLMError as exc:
            printer.finish()
            ui.error(str(exc))
            continue
        printer.finish()

        if not reply:
            ui.warn("The model returned an empty reply.")
        save_history(session_file(), agent.history)
        if speak_replies and reply:
            if not voice.speak(reply):
                ui.warn("No text-to-speech engine found; install espeak-ng or spd-say (Linux), or use macOS/Windows.")


def run_once(agent: Agent, ui: UI, prompt: str, speak_reply: bool) -> int:
    printer = StreamPrinter(ui)
    try:
        reply = agent.chat(prompt, on_token=printer, on_tool=printer.finish)
    except KeyboardInterrupt:
        printer.finish()
        ui.warn("Interrupted.")
        return 130
    except LLMError as exc:
        printer.finish()
        ui.error(str(exc))
        return 1
    printer.finish()
    save_history(session_file(), agent.history)
    if speak_reply and reply:
        voice.speak(reply)
    return 0


# ----------------------------------------------------------------------- main


def _run_listen(agent: Agent, ui: UI, config: Config) -> int:
    """Always-listening wake-word mode; heavy parts are imported lazily."""
    from .listen import VoiceLoop
    from .voice import VoiceError

    try:
        loop = VoiceLoop(
            agent,
            ui,
            config,
            printer_factory=lambda: StreamPrinter(ui),
            on_command=lambda text: _handle_command(text, agent, ui),
        )
    except VoiceError as exc:
        ui.error(str(exc))
        return 1

    ui.info(
        "Risky actions still need approval - use -y or \"auto_approve\": true for hands-free."
    )
    try:
        loop.run()
    except VoiceError as exc:
        ui.error(str(exc))
        return 1
    except KeyboardInterrupt:
        print()
    finally:
        loop.close()
    ui.info("Goodbye.")
    return 0


def _run_server(agent: Agent, ui: UI, config: Config, host: Optional[str], port: Optional[int]) -> int:
    """Serve the same assistant to the phone; the PC does all the work."""
    from .serve import DEFAULT_PORT, JarvisServer, load_or_create_token

    token = load_or_create_token()
    bind = (host or "0.0.0.0").strip()
    number = int(port or DEFAULT_PORT)
    try:
        server = JarvisServer((bind, number), agent, ui, token)
    except OSError as exc:
        ui.error("cannot listen on %s:%d - %s" % (bind, number, exc))
        return 1

    shown = "<this-pc>" if bind in {"0.0.0.0", "::"} else bind
    ui.info("Jarvis server listening on http://%s:%d" % (shown, number))
    ui.info("Phone: http://%s:%d/?token=%s" % (shown, number, token))
    ui.info("Token is also saved in %s" % (state_dir() / "server_token"))
    ui.info("From anywhere: run Tailscale on this PC and the phone, then use the PC's Tailscale name.")
    if agent.toolbox.auto_approve:
        ui.warn("Risky actions run without asking: anyone with the token can use this shell.")
    else:
        ui.info("Risky actions are refused here - no one is at a terminal to approve them.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
        ui.info("Server stopped.")
    finally:
        server.server_close()
    return 0


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    ui = UI(color=not args.no_color)

    config = apply_env_overrides(load_config())
    if args.model:
        config.model = args.model
    if args.base_url:
        config.base_url = args.base_url
    if args.speak or args.voice:
        config.speak = True

    if args.config:
        print_config(config)
        return 0

    base_url = resolve_base_url(config)
    key = api_key()
    if not key:
        if is_local_url(base_url):
            key = "local"  # local servers ignore the key but want a header
        else:
            ui.error("No API key configured.")
            print(CLOUD_SETUP_HELP)
            return 1

    model = resolve_model(config)
    if is_local_url(base_url) and not _local_preflight(ui, base_url, model):
        return 1

    llm = LLM(
        api_key=key,
        base_url=base_url,
        model=model,
        timeout=llm_timeout(),
        fallbacks=llm_fallbacks(base_url, key),
    )
    memory = Memory(memory_file())
    toolbox = Toolbox(
        memory=memory,
        # A server has no terminal to answer the prompt, so in that mode the
        # question is either auto-approved (JARVIS_AUTO_APPROVE=1) or refused.
        approve=None if args.serve else _prompt_for_approval(ui),
        auto_approve=config.auto_approve or args.yes or auto_approve_env(),
        skills=Skills(state_dir() / "skills"),
        learnings=Learnings(state_dir() / "learnings.json"),
    )
    agent = Agent(llm=llm, toolbox=toolbox, memory=memory, config=config, ui=ui)

    if args.resume and not args.new:
        history = load_history(session_file())
        if history:
            agent.history = history
            ui.info("Resumed previous conversation (%d messages)." % len(history))
        else:
            ui.info("No previous conversation to resume.")

    voice_enabled = False
    if args.voice:
        available, reason = voice.mic_available()
        if available:
            voice_enabled = True
        else:
            ui.warn(reason)
            ui.info("Continuing with typed input.")

    if args.listen:
        return _run_listen(agent, ui, config)

    if args.serve:
        return _run_server(agent, ui, config, args.host, args.port)

    prompt = " ".join(args.prompt).strip()
    if prompt:
        return run_once(agent, ui, prompt, config.speak)

    try:
        return repl(agent, ui, config.speak, voice_enabled)
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    sys.exit(main())
