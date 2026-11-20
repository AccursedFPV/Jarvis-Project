"""A small OpenAI-compatible chat client: streaming, tool calls, no dependencies."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, List, Optional


class LLMError(RuntimeError):
    """The model API is unreachable, misconfigured or returned an error.

    capacity marks a problem on the provider's side - congestion, a rate limit,
    a 5xx - which another model or provider may not share. A rejected key or an
    unknown model name is ours, and trying elsewhere would only hide it.
    """

    capacity = False
    partial = False


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class Reply:
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: str = ""


def _budget() -> float:
    """Total seconds one model reply may take, retries included (JARVIS_LLM_BUDGET)."""
    raw = os.environ.get("JARVIS_LLM_BUDGET", "").strip()
    if not raw:
        return 150.0
    try:
        return max(20.0, min(600.0, float(raw)))
    except ValueError:
        return 150.0


def _thinking() -> Optional[str]:
    """Chain-of-thought setting for each request (JARVIS_THINKING).

    On by default: the reasoning is streamed to the app, so a turn that thinks
    for 30s shows what it is working through instead of looking frozen.
    JARVIS_THINKING=off gives the fastest answers (GLM-4.7 then skips reasoning
    entirely), and =auto leaves the choice to the vendor.
    """
    raw = os.environ.get("JARVIS_THINKING", "").strip().lower()
    if raw in {"off", "disabled", "none", "false", "0", "no", "fast"}:
        return "disabled"
    if raw in {"auto", "default", "vendor"}:
        return None
    return "enabled"


_TOOL_CALL_TAG = re.compile(
    r"</?(tool_call|tool_calls|function_call|tool_code)\b[^>]*>", re.IGNORECASE
)



# Tool markup that some models write into the text instead of calling a tool.
_ARG_MARKUP = re.compile(r"<(?:arg_key|arg_value|parameters|tool_name)\b", re.IGNORECASE)
_ARG_BLOCK = re.compile(
    r"<(arg_key|arg_value|parameters|tool_name)\b[^>]*>.*?"
    r"(?:</(?:arg_key|arg_value|parameters|tool_name)\s*>|$)",
    re.IGNORECASE | re.DOTALL,
)
_LEAK_OPEN = re.compile(
    r"<(arg_key|arg_value|parameters|tool_name|tool_call|tool_calls|function_call|tool_code)\b[^>]*>",
    re.IGNORECASE,
)
_LEAK_CLOSE = re.compile(
    r"</(arg_key|arg_value|parameters|tool_name|tool_call|tool_calls|function_call|tool_code)\s*>",
    re.IGNORECASE,
)


def _clean_reply(text: str) -> str:
    """Remove reasoning that some GLM builds write inside the answer.

    z.ai normally sends the chain of thought as reasoning_content, but the same
    model sometimes emits it as content wrapped in  thinking...</think>, or opens
    the answer with the tail of its thinking and a bare </think>. Either way the
    phone showed a thought, a literal </think>, and then the answer again.
    """
    lowered = text.lower()
    if "<tool_call" in lowered or "<function_call" in lowered or "<tool_code" in lowered:
        # A tool call written out as text is not an answer: the phone showed the
        # literal tags and nothing happened.
        text = re.sub(
            r"<(tool_call|tool_calls|function_call|tool_code)\b[^>]*>.*?</\1\s*>",
            "",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        text = _TOOL_CALL_TAG.sub("", text)
        lowered = text.lower()
    if _ARG_MARKUP.search(lowered):
        # The model wrote a whole tool call as text: the tool name on one line,
        # then <arg_key>steps</arg_key><arg_value>1. ...</arg_value>. The phone
        # showed all of it as if it were the answer.
        text = _ARG_BLOCK.sub("\n", text)
        text = _TOOL_CALL_TAG.sub("", text)
        text = re.sub(r"(?m)^\s*[a-z][a-z0-9_]{2,30}\s*$", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        lowered = text.lower()
    if "<think" not in lowered and "</think" not in lowered:
        return text.strip()
    # Paired blocks first, so an answer that merely mentions one keeps its text.
    text = re.sub(r"<think\b[^>]*>.*?</think\s*>", "", text, flags=re.IGNORECASE | re.DOTALL)
    lowered = text.lower()
    cut = lowered.find("</think")
    if cut != -1:
        # An unpaired closing tag means the answer opened with thinking out loud.
        close = text.find(">", cut)
        if close != -1:
            text = text[close + 1 :]
    text = re.sub(r"</?think\b[^>]*>", "", text, flags=re.IGNORECASE)
    return text.strip()


class _LeakFilter:
    """Holds back tool-call markup so it never reaches the phone.

    Streaming happens before the reply can be cleaned, so the display needs its
    own filter: an opening <arg_key>/<arg_value>/<tool_call> tag starts a block
    that is swallowed whole, and a half-written tag at the end of a chunk waits
    for the rest instead of being shown.
    """

    HOLD = 40

    def __init__(self, emit: Callable[[str], None]) -> None:
        self.emit = emit
        self.buffer = ""
        self.inside = ""

    def feed(self, text: str) -> None:
        if not text:
            return
        self.buffer += str(text)
        while self.buffer:
            if self.inside:
                close = _LEAK_CLOSE.search(self.buffer)
                if close and close.group(1).lower() == self.inside:
                    self.buffer = self.buffer[close.end() :]
                    self.inside = ""
                    continue
                self.buffer = self.buffer[-self.HOLD :]
                return
            opened = _LEAK_OPEN.search(self.buffer)
            if opened:
                before = self.buffer[: opened.start()]
                if before:
                    self.emit(before)
                self.inside = opened.group(1).lower()
                self.buffer = self.buffer[opened.end() :]
                continue
            cut = self.buffer.rfind("<")
            if cut != -1 and ">" not in self.buffer[cut:] and len(self.buffer) - cut <= self.HOLD:
                # Could be the first half of a tag: wait for the next chunk.
                if cut:
                    self.emit(self.buffer[:cut])
                self.buffer = self.buffer[cut:]
                return
            self.emit(self.buffer)
            self.buffer = ""

    def flush(self) -> None:
        if self.buffer and not self.inside:
            self.emit(self.buffer)
        self.buffer = ""


def _log(message: str) -> None:
    """Keep timing and fallback notes in journald, where slowness can be seen."""
    try:
        print("jarvis: llm %s" % message, file=sys.stderr, flush=True)
    except Exception:
        pass


class _ThinkingRejected(Exception):
    """This model refuses to run with thinking disabled."""


class _Retry(Exception):
    def __init__(
        self,
        message: str,
        partial: bool = False,
        wait: Optional[float] = None,
        capacity: bool = True,
    ) -> None:
        super().__init__(message)
        self.partial = partial
        self.wait = wait
        self.capacity = capacity


def _completions_url(base_url: str) -> str:
    url = (base_url or "").rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    return url


class LLM:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 180.0,
        fallbacks: Optional[List[Any]] = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.url = _completions_url(base_url)
        # Reported by /health so a slow answer can be traced to the setting.
        self.thinking = _thinking()
        # (url, api_key, model) tried in order when the one before it is
        # congested. Same provider or a different one; Jarvis does not care.
        self.fallbacks: List[Any] = []
        for item in fallbacks or []:
            try:
                base, key, name = item[0], item[1], item[2]
            except (TypeError, IndexError, KeyError):
                continue
            name = str(name or "").strip()
            if not name:
                continue
            self.fallbacks.append((_completions_url(str(base)), str(key or "") or api_key, name))

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.4,
        on_token: Optional[Callable[[str], None]] = None,
        model: Optional[str] = None,
        on_reasoning: Optional[Callable[[str], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> Reply:
        budget = _budget()
        started = time.monotonic()
        deadline = started + budget
        # Retrying a congested host four times is time the fallback could spend
        # answering, so the primary only retries when there is nothing to fall
        # back to.
        primary_attempts = 2 if self.fallbacks else 4
        targets = [(self.url, self.api_key, model or self.model, primary_attempts)]
        for base, key, name in self.fallbacks:
            targets.append((base, key, name, 2))

        failures: List[str] = []
        for index, (url, key, name, attempts) in enumerate(targets):
            if index and time.monotonic() >= deadline:
                failures.append("%s: no time left" % name)
                break
            try:
                return self._with_retries(
                    url,
                    key,
                    name,
                    messages,
                    tools,
                    temperature,
                    on_token,
                    attempts,
                    deadline,
                    on_reasoning,
                    should_stop,
                )
            except LLMError as exc:
                failures.append("%s: %s" % (name, exc) if len(targets) > 1 else str(exc))
                if index + 1 < len(targets):
                    _log("%s gave nothing usable, trying %s next" % (name, targets[index + 1][2]))
                # Only someone else's congestion is worth another target, and a
                # reply already appearing on the phone cannot be restarted.
                if not exc.capacity or exc.partial:
                    break
        detail = " | ".join(failures) if failures else "The model request failed."
        raise LLMError("%s (gave up after %.0fs)" % (_short(detail, 600), time.monotonic() - started))

    def _with_retries(
        self,
        url: str,
        api_key: str,
        name: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        temperature: float,
        on_token: Optional[Callable[[str], None]],
        attempts: int,
        deadline: float,
        on_reasoning: Optional[Callable[[str], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> Reply:
        started = time.monotonic()
        first_word: List[float] = []

        def relay(text: str) -> None:
            if not first_word:
                first_word.append(time.monotonic())
            if on_token:
                on_token(text)

        last: Optional[_Retry] = None
        for attempt in range(attempts):
            if should_stop and should_stop():
                raise LLMError("Stopped at your request.")
            try:
                reply = self._stream(
                    url, api_key, name, messages, tools, temperature, relay, on_reasoning, should_stop
                )
                now = time.monotonic()
                waited = "%.1fs to first word" % (first_word[0] - started) if first_word else "no words streamed"
                _log(
                    "%s replied in %.1fs (%s, %d chars, %d attempt%s)"
                    % (name, now - started, waited, len(reply.content), attempt + 1, "" if attempt == 0 else "s")
                )
                return reply
            except _Retry as exc:
                last = exc
                if exc.partial or attempt == attempts - 1:
                    break
                # A free tier throttles per minute, so 1.5s is not enough; the
                # vendor's own Retry-After wins when it sends one.
                delay = exc.wait if exc.wait is not None else 2.0 * (3 ** attempt)
                if time.monotonic() + delay > deadline:
                    # Waiting again would leave the phone staring at "thinking"
                    # for minutes, so give up while the reason is still fresh.
                    break
                time.sleep(max(0.0, min(delay, 30.0)))
        error = LLMError(str(last) if last else "The model request failed.")
        if last is not None:
            error.capacity = last.capacity
            error.partial = last.partial
        raise error

    # ------------------------------------------------------------------ stream

    def _stream(
        self,
        url: str,
        api_key: str,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        temperature: float,
        on_token: Optional[Callable[[str], None]],
        on_reasoning: Optional[Callable[[str], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> Reply:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        mode = _thinking()
        if mode:
            payload["thinking"] = {"type": mode}
        try:
            return self._post(url, api_key, payload, on_token, on_reasoning, should_stop)
        except _ThinkingRejected:
            # GLM-5.3 and its flash sibling refuse to run without thinking. An
            # answer with the vendor's default beats an error about a switch.
            payload.pop("thinking", None)
            _log("%s refuses thinking=disabled, continuing with its default" % model)
            return self._post(url, api_key, payload, on_token, on_reasoning, should_stop)

    def _post(
        self,
        url: str,
        api_key: str,
        payload: Dict[str, Any],
        on_token: Optional[Callable[[str], None]],
        on_reasoning: Optional[Callable[[str], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> Reply:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "Authorization": "Bearer " + api_key,
                "User-Agent": "jarvis-cli/0.1",
            },
            method="POST",
        )

        content_parts: List[str] = []
        calls: Dict[int, Dict[str, str]] = {}
        finish = ""
        emitted = False
        # Tool markup is swallowed before it can be drawn on the phone.
        filter_ = _LeakFilter(on_token) if on_token else None
        display = filter_.feed if filter_ is not None else None

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                content_type = (response.headers.get("Content-Type") or "").lower()
                if "application/json" in content_type and "event-stream" not in content_type:
                    # Some gateways ignore stream=true and answer with one JSON body.
                    return _parse_full_response(
                        response.read().decode("utf-8", "replace"), display, on_reasoning
                    )
                for raw_line in response:
                    if should_stop and should_stop():
                        # The phone pressed Stop: keep what already streamed and
                        # close the connection instead of generating the rest.
                        break
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    if chunk.get("error"):
                        raise LLMError(_error_text(chunk["error"]))
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    # The chain of thought arrives separately from the answer; it
                    # is shown in the app but never counted as the reply.
                    thought = delta.get("reasoning_content")
                    if thought and on_reasoning:
                        on_reasoning(str(thought))
                    text = delta.get("content")
                    if text:
                        emitted = True
                        content_parts.append(str(text))
                        if display:
                            display(str(text))
                    for call in delta.get("tool_calls") or []:
                        index = int(call.get("index") or 0)
                        entry = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        if call.get("id"):
                            entry["id"] = str(call["id"])
                        function = call.get("function") or {}
                        if function.get("name"):
                            entry["name"] = str(function["name"])
                        if function.get("arguments"):
                            entry["arguments"] += str(function["arguments"])
                    if choice.get("finish_reason"):
                        finish = str(choice["finish_reason"])
        except urllib.error.HTTPError as exc:
            detail = _read_error_body(exc)
            message = _http_error_message(exc.code, detail)
            if exc.code == 400 and "thinking" in (detail or "").lower():
                raise _ThinkingRejected(message) from exc
            if exc.code == 429 or exc.code >= 500:
                raise _Retry(message, wait=_retry_after(exc.headers), capacity=True) from exc
            raise LLMError(message) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise _Retry(
                "Could not reach the model API at %s: %s" % (url, reason),
                partial=emitted,
                capacity=True,
            ) from exc

        if filter_ is not None:
            filter_.flush()
        return Reply(
            content=_clean_reply("".join(content_parts)),
            tool_calls=[
                ToolCall(
                    id=entry["id"] or "call_%d" % index,
                    name=entry["name"],
                    arguments=entry["arguments"] or "{}",
                )
                for index, entry in sorted(calls.items())
                if entry["name"]
            ],
            finish_reason=finish,
        )


# ---------------------------------------------------------------- body parsing


def _parse_full_response(
    raw: str,
    on_token: Optional[Callable[[str], None]],
    on_reasoning: Optional[Callable[[str], None]] = None,
) -> Reply:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError("The model API returned an unreadable response: %s" % exc) from exc
    if not isinstance(data, dict):
        raise LLMError("The model API returned an unexpected response.")
    if data.get("error"):
        raise LLMError(_error_text(data["error"]))
    choices = data.get("choices") or []
    if not choices:
        return Reply()
    choice = choices[0]
    message = choice.get("message") or {}
    content = _clean_reply(str(message.get("content") or ""))
    thought = message.get("reasoning_content")
    if thought and on_reasoning:
        on_reasoning(str(thought))
    if content and on_token:
        on_token(content)
    calls: List[ToolCall] = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        name = str(function.get("name") or "")
        if not name:
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, dict):
            arguments = json.dumps(arguments)
        calls.append(
            ToolCall(
                id=str(call.get("id") or "call_%d" % len(calls)),
                name=name,
                arguments=str(arguments or "{}"),
            )
        )
    return Reply(content=content, tool_calls=calls, finish_reason=str(choice.get("finish_reason") or ""))


# ---------------------------------------------------------------- error helpers


def _short(value: str, limit: int) -> str:
    value = " ".join(str(value).split())
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _retry_after(headers: Any) -> Optional[float]:
    """Seconds the server asked us to wait, in either form Retry-After allows."""
    try:
        raw = (headers.get("Retry-After") or "").strip() if headers else ""
    except Exception:
        return None
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8", "replace").strip()
    except Exception:
        return ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            return _error_text(error)
        if error:
            return str(error)
        if data.get("message"):
            return str(data["message"])
    return raw


def _error_text(error: Any) -> str:
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or error)
    return str(error)


def _http_error_message(code: int, detail: str) -> str:
    hints = {
        400: "the request was rejected",
        401: "the API key was rejected - check JARVIS_API_KEY / ZAI_API_KEY",
        402: "the account is out of credit or the plan does not cover this endpoint",
        403: "access denied for this key, model or endpoint",
        404: "the endpoint or model was not found - check JARVIS_BASE_URL and JARVIS_MODEL",
        429: "too many requests for this account - wait a few seconds and ask again",
    }
    if code == 429:
        # The same status carries two very different problems: our quota, or
        # their capacity. Telling them apart stops pointless plan upgrades.
        lowered = (detail or "").lower()
        if "overload" in lowered or "1305" in lowered:
            hints[429] = "the provider itself is overloaded (code 1305) - nothing to fix on our side, retry shortly"
        elif "1302" in lowered or "rate limit" in lowered:
            hints[429] = "this account's request rate limit was hit (code 1302) - wait, or use a paid model"
    message = "Model API error %d: %s" % (code, hints.get(code, "request failed"))
    if detail:
        message += " (%s)" % _short(detail, 400)
    return message
