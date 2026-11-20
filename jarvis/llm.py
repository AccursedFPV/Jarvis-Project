"""A small OpenAI-compatible chat client: streaming, tool calls, no dependencies."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, List, Optional


class LLMError(RuntimeError):
    """The model API is unreachable, misconfigured or returned an error."""


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


class _Retry(Exception):
    def __init__(self, message: str, partial: bool = False, wait: Optional[float] = None) -> None:
        super().__init__(message)
        self.partial = partial
        self.wait = wait


class LLM:
    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 180.0) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        url = base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        self.url = url

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.4,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> Reply:
        attempts = 4
        last: Optional[_Retry] = None
        for attempt in range(attempts):
            try:
                return self._stream(messages, tools, temperature, on_token)
            except _Retry as exc:
                last = exc
                if exc.partial or attempt == attempts - 1:
                    break
                # A free tier throttles per minute, so 1.5s is not enough; the
                # vendor's own Retry-After wins when it sends one.
                delay = exc.wait if exc.wait is not None else 2.0 * (3 ** attempt)
                time.sleep(max(0.0, min(delay, 30.0)))
        raise LLMError(str(last) if last else "The model request failed.")

    # ------------------------------------------------------------------ stream

    def _stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        temperature: float,
        on_token: Optional[Callable[[str], None]],
    ) -> Reply:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "Authorization": "Bearer " + self.api_key,
                "User-Agent": "jarvis-cli/0.1",
            },
            method="POST",
        )

        content_parts: List[str] = []
        calls: Dict[int, Dict[str, str]] = {}
        finish = ""
        emitted = False

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                content_type = (response.headers.get("Content-Type") or "").lower()
                if "application/json" in content_type and "event-stream" not in content_type:
                    # Some gateways ignore stream=true and answer with one JSON body.
                    return _parse_full_response(response.read().decode("utf-8", "replace"), on_token)
                for raw_line in response:
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
                    text = delta.get("content")
                    if text:
                        emitted = True
                        content_parts.append(str(text))
                        if on_token:
                            on_token(str(text))
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
            if exc.code == 429 or exc.code >= 500:
                raise _Retry(message, wait=_retry_after(exc.headers)) from exc
            raise LLMError(message) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise _Retry("Could not reach the model API at %s: %s" % (self.url, reason), partial=emitted) from exc

        return Reply(
            content="".join(content_parts),
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


def _parse_full_response(raw: str, on_token: Optional[Callable[[str], None]]) -> Reply:
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
    content = str(message.get("content") or "")
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
        429: "the free tier allows only a few model calls per minute - wait a few seconds and ask again",
    }
    message = "Model API error %d: %s" % (code, hints.get(code, "request failed"))
    if detail:
        message += " (%s)" % _short(detail, 400)
    return message
