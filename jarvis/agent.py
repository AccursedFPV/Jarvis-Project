"""The assistant loop: talk to the model, run its tools, keep the conversation."""

from __future__ import annotations

import getpass
import json
import os
import platform
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import Config
from .llm import LLM, LLMError
from .memory import Memory
from .tools import Toolbox
from .ui import UI

MAX_HISTORY_MESSAGES = 120
# A turn should not research in circles: this many calls to one tool ends it.
MAX_CALLS_PER_TOOL = 5


def _max_steps() -> int:
    try:
        return max(4, min(120, int(os.environ.get("JARVIS_MAX_STEPS", "16"))))
    except ValueError:
        return 30


SYSTEM_PROMPT = """You are Jarvis, {user}'s personal AI companion, running in a terminal on {os}. \
The current date and time is {today}. The working directory is {cwd}.

You act on this computer through tools: reading, writing and searching files, running shell commands, \
opening files and URLs, launching installed applications, searching the web and reading pages, \
keeping long-term memory, planning multi-step work, and writing new tools for yourself.

How to work:
- Do the task, end to end. Plan briefly, act, check the result, then answer. Never only describe what you would do.
- Announcing is not acting. Never reply with only "I'll check...", "Let me..." or "I will fetch...". If the answer needs data, call the tool in that same turn, with its arguments. The date and time are already in this prompt, so a question about today needs no tool at all.
- Small talk like "how are you" is not a research task: answer it in one line from what you already know. Never search the web for it.
- If a tool fails twice with the same arguments, stop calling it and answer with what you have, saying plainly what did not work.
- Anything with more than two steps: call update_plan with 3-7 concrete steps, then complete_step as each one is finished.
- If you do not know how, find out: web_search, then fetch_url the most promising page and read it before acting.
- If you need a capability you do not have, build it: write_skill writes Python that becomes a real tool (skill_<name>) you can call right away, in this session and every later one. Keep skills small, standard library only, and call a new skill once on real input before trusting it. If it fails, read the error and fix it with write_skill again.
- Finish in a reasonable number of steps. Prefer one good command over five guesses, and stop when the task is actually done rather than polishing.
- Look before you leap: list or read before overwriting, and check that an app or command exists before relying on it.
- Files from the phone arrive as [attached: /path]: read text with read_file, other formats with run_command. A picture is different: it is sent to a vision model and you can see it, so answer from what is in the image. Never poke at an image file with file, identify or a struct parser - that is wasted steps.
- Risky actions are already gated by an approval prompt, so don't ask the user for permission yourself. Say in one short line what you are about to do.
- If a tool fails, read the error, fix the cause or try one sensible alternative, then report the outcome honestly.
- Keep replies short and natural, as if speaking aloud, unless the user asks for detail. Use only light markdown.
- When the user wants things they can open - products to buy, articles to read, a video - give each one as a real link using the exact address from your tool results: [Name](https://...). Never say "here are some links" or "I can provide links" and then list names without addresses: either include the address in that same reply or do not mention links at all.
- If a page will not load, keep the address from the search results, say in one line that it did not open, and answer from the titles and snippets you did see. Separate points with a blank line, and never list the same items twice in one reply.
- Never invent file contents, command output or facts. Never store passwords, API keys or other secrets.
- When you learn something reusable - a command that worked, a fix, a pattern - call learn once with it. Do not record trivia or things already in memory.
- When the user says remember, learn or "from now on", call remember (about them) or learn (about the work) in that same turn, then say in one line what you stored. Never just agree to remember.
- When you need an ability you do not have, offer to build it with write_skill, and build it as soon as they say yes.

Your own code and state:
- You are the Python package at {source}. That is your own source: the tools, this prompt, the phone page, the voice loop. You may read it and change it.
- Your live state is JARVIS_HOME: memory.json, learnings.json, skills/, config.json. Prefer write_skill for new abilities; edit the package only when the behaviour itself has to change.
- Edits to the package take effect only after a restart. Call restart_self last, then say in one line that you are restarting.
- Read a file before you change it, keep the edit small, and after the restart answer one short question to prove you still work.
- If a tool answers "DENIED", the action needed approval and nobody was there to give it. Say exactly that. Never claim you have no files or cannot act - you can.

Long-term memory about the user:
{memory}

Lessons you learned before (may or may not apply):
{lessons}

Current plan:
{plan}
"""


class Agent:
    def __init__(self, llm: LLM, toolbox: Toolbox, memory: Memory, config: Config, ui: UI) -> None:
        self.llm = llm
        self.toolbox = toolbox
        self.memory = memory
        self.config = config
        self.ui = ui
        # A correction appended to the next system prompt, used once.
        self._nudge = ""
        self.history: List[Dict[str, Any]] = []

    # ---------------------------------------------------------------- prompting

    def _last_user_text(self) -> str:
        for message in reversed(self.history):
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, list):
                # A turn carrying a picture is a list of parts; the words are
                # what the lesson lookup can use.
                return " ".join(
                    str(part.get("text") or "") for part in content if isinstance(part, dict)
                ).strip()
            return str(content or "")
        return ""

    def _lessons(self, query: str) -> str:
        learnings = getattr(self.toolbox, "learnings", None)
        if learnings is None:
            return "(nothing learned yet)"
        return learnings.block(query)

    def system_message(self, query: str = "") -> Dict[str, str]:
        try:
            user = getpass.getuser()
        except Exception:
            user = "the user"
        text = SYSTEM_PROMPT.format(
            user=user,
            os="%s %s" % (platform.system(), platform.release()),
            today=datetime.now().strftime("%A, %d %B %Y, %H:%M"),
            cwd=str(Path.cwd()),
            source=str(Path(__file__).resolve().parent),
            memory=self.memory.block(),
            lessons=self._lessons(query),
            plan=self.toolbox.plan_block(),
        )
        return {"role": "system", "content": text}

    def _recent_history(self) -> List[Dict[str, Any]]:
        if len(self.history) <= MAX_HISTORY_MESSAGES:
            return self.history
        cut = len(self.history) - MAX_HISTORY_MESSAGES
        while cut < len(self.history) and self.history[cut].get("role") != "user":
            cut += 1
        return self.history[cut:]

    def request_messages(self) -> List[Dict[str, Any]]:
        system = self.system_message(self._last_user_text())
        if self._nudge:
            # One extra instruction for one call only; kept out of the history.
            system["content"] += "\n\n" + self._nudge
            self._nudge = ""
        return [system] + self._recent_history()

    # -------------------------------------------------------------------- chat

    def chat(
        self,
        user_text: Any,
        on_token: Optional[Callable[[str], None]] = None,
        on_tool: Optional[Callable[[], None]] = None,
        model: Optional[str] = None,
        on_reasoning: Optional[Callable[[str], None]] = None,
        on_step: Optional[Callable[[str], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> str:
        def stopped() -> bool:
            return bool(should_stop and should_stop())

        checkpoint = len(self.history)
        self.history.append({"role": "user", "content": user_text})
        steps = 0
        offer_tools = True
        nudged = False
        empty_nudged = False
        stuck = False
        seen: Dict[str, int] = {}
        per_tool: Dict[str, int] = {}
        try:
            while True:
                if stopped():
                    return _STOPPED
                try:
                    reply = self.llm.chat(
                        self.request_messages(),
                        self.toolbox.schemas() if offer_tools else None,
                        temperature=self.config.temperature,
                        on_token=on_token,
                        model=model,
                        on_reasoning=on_reasoning,
                        should_stop=should_stop,
                    )
                except LLMError:
                    if stopped():
                        return _STOPPED
                    raise
                assistant: Dict[str, Any] = {"role": "assistant", "content": reply.content or ""}
                if reply.tool_calls:
                    assistant["tool_calls"] = [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": call.arguments},
                        }
                        for call in reply.tool_calls
                    ]
                self.history.append(assistant)

                if not reply.tool_calls:
                    text = (reply.content or "").strip()
                    # An empty message after real tool results is a dropout, not
                    # an answer; ask once for the answer in plain text.
                    if stopped():
                        return text or _STOPPED
                    if not text:
                        self.history.pop()
                        if not empty_nudged:
                            empty_nudged = True
                            self._nudge = (
                                "Your last message was empty. The tool results are already above you: "
                                "answer the user now, in plain text, from what they contain - with the "
                                "links if the answer includes any. Do not call another tool."
                            )
                            continue
                        return _EMPTY
                    # Some providers serialise a tool call into the text stream
                    # (<arg_key>...). That is not an answer, and showing it is
                    # worse than useless.
                    if _leaked_call(text):
                        self.history.pop()
                        if not nudged:
                            nudged = True
                            self._nudge = (
                                "Your previous message was a tool call written out as text, which is "
                                "not a tool call and did nothing. Do not write tool syntax or tags. "
                                "Either call the tool properly with its arguments, or answer in plain "
                                "language. Never claim a tool failed when you have not called it."
                            )
                            continue
                        cleaned = _strip_leaked(text)
                        return cleaned if cleaned and not _claims_failure(cleaned) else _GARBLED
                    # Claiming a tool is broken without having tried it is a
                    # wish, so it is handed back once with a real instruction.
                    if steps == 0 and _claims_failure(text) and not nudged:
                        self.history.pop()
                        nudged = True
                        self._nudge = (
                            "You have not called any tool this turn, so you cannot know that one is "
                            "failing: %r. If the request needs the web, call web_search now with real "
                            "search words. If it does not, answer from what you already know." % text[:200]
                        )
                        continue
                    # A promise is not an answer, so a promise on a turn that has
                    # not acted yet is handed back once with the words quoted.
                    if _announces(text):
                        self.history.pop()
                        if not nudged and not steps:
                            nudged = True
                            self._nudge = (
                                "Your previous answer only announced an action: %r. "
                                "If you meant to use a tool, call that tool now and give it its arguments. "
                                "If no tool is needed, answer the question yourself. "
                                "Never reply with only a promise." % text[:200]
                            )
                            continue
                        return _GAVE_UP
                    return text

                if steps + len(reply.tool_calls) > _max_steps():
                    offer_tools = False
                    note = "ERROR: tool step limit reached; no action was taken."
                    for call in reply.tool_calls:
                        self.history.append({"role": "tool", "tool_call_id": call.id, "content": note})
                    continue

                steps += len(reply.tool_calls)
                for call in reply.tool_calls:
                    if stopped():
                        return _STOPPED
                    if on_tool:
                        on_tool()
                    per_tool[call.name] = per_tool.get(call.name, 0) + 1
                    if per_tool[call.name] > MAX_CALLS_PER_TOOL:
                        # Even with new arguments, this many calls means it is
                        # researching in circles. Answer with what it has.
                        stuck = True
                        if on_step:
                            on_step("stopped after %d calls to %s" % (MAX_CALLS_PER_TOOL, call.name))
                        self.history.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.id,
                                "content": "ERROR: you have already used this tool %d times in this turn. "
                                "Stop calling tools and answer from what you already know."
                                % MAX_CALLS_PER_TOOL,
                            }
                        )
                        continue
                    key = "%s|%s" % (call.name, (call.arguments or "").strip()[:400])
                    seen[key] = seen.get(key, 0) + 1
                    if seen[key] > 2:
                        # The same call with the same arguments, a third time. It
                        # is not going to work; stop the turn instead of looping.
                        stuck = True
                        if on_step:
                            on_step("stopped repeating %s" % call.name)
                        self.history.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.id,
                                "content": "ERROR: this exact call was already made twice and did not "
                                "work. Stop calling tools and answer from what you already know.",
                            }
                        )
                        continue
                    args, parse_error = _parse_arguments(call.arguments)
                    if parse_error:
                        result = "ERROR: " + parse_error
                    else:
                        self.ui.tool(call.name, args)
                        if on_step:
                            on_step(_step_label(call.name, args))
                        result = self.toolbox.run(call.name, args)
                    self.history.append({"role": "tool", "tool_call_id": call.id, "content": result})
                if stuck:
                    # No tools from here on: the next reply has to be an answer.
                    offer_tools = False
        except KeyboardInterrupt:
            del self.history[checkpoint:]
            raise


# ------------------------------------------------------------------- sessions


_GAVE_UP = (
    "I stopped myself there: I kept repeating the same step without getting anywhere. "
    "Ask me again, or tell me exactly what you want me to do."
)

_STOPPED = "Stopped at your request."

_EMPTY = (
    "I did the steps but ended up with nothing to say, which is on me. "
    "Ask me again, or give me one link to start from."
)

_GARBLED = (
    "That came out garbled on my side - I wrote a tool call as text instead of running it. "
    "Ask me again and I will try once more."
)

# Tool syntax some models put in the text instead of in tool_calls.
LEAKED_CALL = re.compile(
    r"</?(arg_key|arg_value|tool_call|tool_calls|function_call|tool_name|parameters)\b[^>]*>",
    re.IGNORECASE,
)

FAILURE_CLAIM = re.compile(
    r"\b(tool|search|search engine|web)\b[^.!?]{0,60}"
    r"\b(fail|fails|failing|failed|broken|not working|unavailable|error|errors)\b",
    re.IGNORECASE,
)


def _leaked_call(text: str) -> bool:
    """True when a reply contains tool-call markup instead of a real call."""
    return bool(LEAKED_CALL.search(text or ""))


def _claims_failure(text: str) -> bool:
    """True when a reply says a tool is broken without any tool having run."""
    return bool(FAILURE_CLAIM.search(" ".join((text or "").split())))


def _strip_leaked(text: str) -> str:
    """Drop the lines that carry leaked tool markup, keep the prose."""
    lines = [line for line in (text or "").splitlines() if not LEAKED_CALL.search(line)]
    return "\n".join(lines).strip()

PROMISE = re.compile(
    r"^\W*(sure[,!]?\s*)?(i'?ll|i will|i am going to|i'm going to|i shall|let me|allow me)\b",
    re.IGNORECASE,
)


def _announces(text: str) -> bool:
    """True for a short reply that only says what it is about to do.

    GLM-4.7 does this: "I'll check the current date and time" and stops, with no
    tool call, so nothing happens and the user is left asking what it means.
    """
    body = " ".join((text or "").split())
    if not body or len(body) > 320 or body.endswith("?"):
        return False
    return bool(PROMISE.match(body))


def _step_label(name: str, args: Dict[str, Any]) -> str:
    """One readable line for the app: what Jarvis is doing, and to what."""
    parts = []
    for key, value in list(args.items())[:3]:
        if isinstance(value, str):
            text = " ".join(value.split())
            if len(text) > 60:
                text = text[:57] + "..."
        else:
            text = json.dumps(value, ensure_ascii=False)
            if len(text) > 60:
                text = text[:57] + "..."
        parts.append("%s=%s" % (key, text))
    return "%s(%s)" % (name, ", ".join(parts))


def _parse_arguments(raw: str) -> Tuple[Dict[str, Any], Optional[str]]:
    text = (raw or "").strip()
    if not text:
        return {}, None
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, "invalid JSON arguments: %s" % exc
    if not isinstance(value, dict):
        return {}, "tool arguments must be a JSON object"
    return value, None


def load_history(path: Path) -> List[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    messages = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(messages, list):
        return []
    return [
        message
        for message in messages
        if isinstance(message, dict) and message.get("role") in {"user", "assistant", "tool"}
    ]


def save_history(path: Path, history: List[Dict[str, Any]]) -> None:
    trimmed = [dict(message) for message in history[-MAX_HISTORY_MESSAGES:]]
    while trimmed and trimmed[0].get("role") != "user":
        trimmed.pop(0)
    if not trimmed:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"messages": trimmed}, indent=2), encoding="utf-8")
    temp.replace(path)
