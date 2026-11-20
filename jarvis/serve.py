"""A small HTTP server so a phone or another computer can use this same Jarvis.

The PC keeps the model, the files, the skills and the memory; the phone is a
thin client. Auth is a bearer token; put both devices on Tailscale (or your LAN)
and the phone can reach the PC from anywhere without exposing anything to the
public internet.
"""

from __future__ import annotations

import hmac
import io
import json
import os
import secrets
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit

from .config import state_dir
from .ui import UI

DEFAULT_PORT = 8787
TOKEN_FILE = "server_token"
MAX_BODY = 24 * 1024 * 1024
MAX_MESSAGE = 8000

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0d1117">
<title>Jarvis</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 16px/1.45 -apple-system, system-ui, "Segoe UI", Roboto, sans-serif;
         background: #0d1117; color: #e6edf3; display: flex; flex-direction: column; height: 100dvh; }
  header { display: flex; align-items: baseline; gap: 10px; padding: 14px 16px 8px; }
  h1 { font-size: 15px; letter-spacing: .28em; margin: 0; font-weight: 600; color: #58a6ff; }
  #status { font-size: 12px; color: #8b949e; }
  #log { flex: 1; overflow-y: auto; padding: 8px 16px 16px; display: flex; flex-direction: column; gap: 10px; }
  .msg { max-width: 92%; padding: 10px 13px; border-radius: 14px; white-space: pre-wrap; word-wrap: break-word; }
  .you { align-self: flex-end; background: #1f6feb; color: #fff; border-bottom-right-radius: 4px; }
  .jarvis { align-self: flex-start; background: #161b22; border: 1px solid #21262d; border-bottom-left-radius: 4px; }
  .meta { align-self: center; font-size: 12px; color: #8b949e; }
  .bar { padding: 10px 12px calc(12px + env(safe-area-inset-bottom)); border-top: 1px solid #21262d; background: #0d1117; }
  #talk { width: 100%; padding: 16px; font-size: 17px; font-weight: 600; border: 0; border-radius: 14px;
          background: #238636; color: #fff; touch-action: none; user-select: none; -webkit-user-select: none; }
  #talk.on { background: #da3633; }
  form { display: flex; gap: 8px; margin-top: 8px; }
  input { flex: 1; padding: 12px; font-size: 16px; border-radius: 12px; border: 1px solid #30363d;
          background: #161b22; color: #e6edf3; }
  form button { padding: 0 16px; font-size: 15px; border: 0; border-radius: 12px; background: #1f6feb; color: #fff; }
</style>
</head>
<body>
<header><h1>JARVIS</h1><span id="status">connecting...</span></header>
<main id="log"><div class="meta">Hold the button and talk, or type.</div></main>
<div class="bar">
  <button id="talk">Hold to talk</button>
  <form id="form"><input id="text" placeholder="Type instead" autocomplete="off" autocapitalize="sentences"><button>Send</button></form>
</div>
<script>
let token = localStorage.getItem("jarvis_token") || "";
const params = new URLSearchParams(location.search);
if (params.get("token")) {
  token = params.get("token");
  localStorage.setItem("jarvis_token", token);
  history.replaceState(null, "", "/");
}
const log = document.getElementById("log");
const status = document.getElementById("status");
const talk = document.getElementById("talk");
const form = document.getElementById("form");
const text = document.getElementById("text");

function setStatus(t) { status.textContent = t; }
function bubble(text, who) {
  const div = document.createElement("div");
  div.className = "msg " + who;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}
function fail(message) {
  setStatus("offline");
  if (message) bubble(message + " (tap hold-to-talk to retry)", "meta");
}

async function send(path, body, type) {
  if (!token) { token = prompt("Jarvis token (shown on the PC when the server starts):") || ""; localStorage.setItem("jarvis_token", token); }
  const response = await fetch(path, { method: "POST", headers: { "Authorization": "Bearer " + token, "Content-Type": type }, body: body });
  if (response.status === 401) { localStorage.removeItem("jarvis_token"); throw new Error("Wrong or missing token."); }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || ("HTTP " + response.status));
  return data;
}

function speak(reply) {
  if (!("speechSynthesis" in window)) return;
  const utterance = new SpeechSynthesisUtterance(reply);
  utterance.rate = 1.02;
  speechSynthesis.cancel();
  speechSynthesis.speak(utterance);
}

async function ask(message) {
  bubble(message, "you");
  setStatus("thinking...");
  try {
    const data = await send("/chat", JSON.stringify({ message }), "application/json");
    bubble(data.reply || "(empty reply)", "jarvis");
    setStatus("ready");
    speak(data.reply || "");
  } catch (error) { fail(error.message); }
}

form.addEventListener("submit", event => {
  event.preventDefault();
  const value = text.value.trim();
  if (!value) return;
  text.value = "";
  ask(value);
});

let recording = false, chunks = [], ctx = null, stream = null, node = null;

async function startRecording() {
  if (recording) return;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    const source = ctx.createMediaStreamSource(stream);
    node = ctx.createScriptProcessor(2048, 1, 1);
    const mute = ctx.createGain();
    mute.gain.value = 0;
    chunks = [];
    node.onaudioprocess = event => { chunks.push(new Float32Array(event.inputBuffer.getChannelData(0))); };
    source.connect(node);
    node.connect(mute);
    mute.connect(ctx.destination);
    recording = true;
    talk.classList.add("on");
    talk.textContent = "Listening - release to send";
    setStatus("recording...");
  } catch (error) { fail("Microphone blocked: " + error.message); }
}

function downsampled(rate) {
  const total = chunks.reduce((n, chunk) => n + chunk.length, 0);
  const joined = new Float32Array(total);
  let offset = 0;
  for (const chunk of chunks) { joined.set(chunk, offset); offset += chunk.length; }
  const ratio = rate / 16000;
  if (ratio <= 1) return joined;
  const length = Math.floor(joined.length / ratio);
  const out = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    const start = Math.floor(i * ratio), end = Math.min(Math.floor((i + 1) * ratio), joined.length);
    let sum = 0;
    for (let j = start; j < end; j++) sum += joined[j];
    out[i] = sum / Math.max(1, end - start);
  }
  return out;
}

function wavBlob(samples, rate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const write = (offset, string) => { for (let i = 0; i < string.length; i++) view.setUint8(offset + i, string.charCodeAt(i)); };
  write(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true); write(8, "WAVE"); write(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  write(36, "data"); view.setUint32(40, samples.length * 2, true);
  let offset = 44;
  for (const sample of samples) {
    const value = Math.max(-1, Math.min(1, sample));
    view.setInt16(offset, value < 0 ? value * 0x8000 : value * 0x7fff, true);
    offset += 2;
  }
  return new Blob([view], { type: "audio/wav" });
}

async function stopRecording() {
  if (!recording) return;
  recording = false;
  talk.classList.remove("on");
  talk.textContent = "Hold to talk";
  const rate = ctx ? ctx.sampleRate : 48000;
  if (node) node.onaudioprocess = null;
  if (stream) stream.getTracks().forEach(track => track.stop());
  if (ctx) ctx.close();
  const blob = wavBlob(downsampled(rate), 16000);
  setStatus("sending...");
  try {
    const data = await send("/voice", blob, "audio/wav");
    if (data.heard) bubble(data.heard, "you");
    bubble(data.reply || "(empty reply)", "jarvis");
    setStatus("ready");
    speak(data.reply || "");
  } catch (error) { fail(error.message); }
}

talk.addEventListener("pointerdown", event => { event.preventDefault(); startRecording(); });
talk.addEventListener("pointerup", event => { event.preventDefault(); stopRecording(); });
talk.addEventListener("pointercancel", stopRecording);
talk.addEventListener("pointerleave", () => { if (recording) stopRecording(); });

fetch("/health", { headers: { "Authorization": "Bearer " + token } })
  .then(response => response.ok ? response.json() : Promise.reject(new Error("no auth")))
  .then(data => setStatus(data.model + " - ready"))
  .catch(() => setStatus(token ? "token rejected - reload with ?token=" : "no token - reload with ?token="));
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------- token


def load_or_create_token() -> str:
    """JARVIS_SERVER_TOKEN, else ~/.jarvis/server_token (created on first run)."""
    configured = os.environ.get("JARVIS_SERVER_TOKEN", "").strip()
    if configured:
        return configured
    path = state_dir() / TOKEN_FILE
    if path.is_file():
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
        if value:
            return value
    value = secrets.token_urlsafe(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return value


def _decode_wav(raw: bytes) -> Any:
    import numpy as np

    with wave.open(io.BytesIO(raw), "rb") as handle:
        if handle.getsampwidth() != 2:
            raise ValueError("expected 16-bit PCM audio")
        rate = handle.getframerate()
        channels = handle.getnchannels()
        data = np.frombuffer(handle.readframes(handle.getnframes()), dtype="int16")
    audio = data.astype("float32") / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != 16000 and len(audio) > 1:
        target = max(1, int(round(len(audio) * 16000.0 / rate)))
        audio = np.interp(
            np.linspace(0, len(audio) - 1, target),
            np.arange(len(audio)),
            audio,
        ).astype("float32")
    return audio


# --------------------------------------------------------------------- server


class JarvisServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: Any,
        agent: Any,
        ui: UI,
        token: str,
        transcriber: Optional[Any] = None,
    ) -> None:
        super().__init__(address, Handler)
        self.agent = agent
        self.ui = ui
        self.token = token
        self.lock = threading.Lock()
        self._transcriber = transcriber

    def chat(self, message: str) -> str:
        with self.lock:
            return self.agent.chat(message)

    def transcribe(self, audio: Any) -> str:
        if self._transcriber is None:
            from .listen import WhisperTranscriber

            self._transcriber = WhisperTranscriber(self.agent.config.stt_model)
        return self._transcriber.transcribe(audio)


class Handler(BaseHTTPRequestHandler):
    server_version = "Jarvis/0.2"
    protocol_version = "HTTP/1.1"

    # ----------------------------------------------------------------- answers

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: Any) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _authorised(self, query: Any) -> bool:
        header = (self.headers.get("Authorization") or "").strip()
        if header.lower().startswith("bearer "):
            header = header[7:].strip()
        supplied = header or (self.headers.get("X-Jarvis-Token") or "").strip()
        if not supplied:
            supplied = (query.get("token") or [""])[0]
        expected = getattr(self.server, "token", "")
        return bool(supplied) and bool(expected) and hmac.compare_digest(supplied, expected)

    def _body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return b""
        if length <= 0:
            return b""
        if length > MAX_BODY:
            return b""
        return self.rfile.read(length)

    # --------------------------------------------------------------------- GET

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        if parts.path in {"/", "/index.html"}:
            self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parts.path == "/health":
            if not self._authorised(query):
                self._json(401, {"error": "unauthorised"})
                return
            self._json(
                200,
                {
                    "ok": True,
                    "model": self.server.agent.llm.model,
                    "endpoint": self.server.agent.llm.url,
                    "skills": len(self.server.agent.toolbox.schemas()),
                },
            )
            return
        self._json(404, {"error": "not found"})

    # -------------------------------------------------------------------- POST

    def do_POST(self) -> None:
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        if not self._authorised(query):
            self._json(401, {"error": "unauthorised - send Authorization: Bearer <token>"})
            return

        if parts.path == "/chat":
            raw = self._body()
            try:
                payload = json.loads(raw.decode("utf-8", "replace") or "{}")
            except json.JSONDecodeError:
                self._json(400, {"error": "body must be JSON"})
                return
            message = str(payload.get("message") or "").strip()[:MAX_MESSAGE]
            if not message:
                self._json(400, {"error": "message is required"})
                return
            try:
                reply = self.server.chat(message)
            except Exception as exc:
                self._json(500, {"error": "%s: %s" % (type(exc).__name__, exc)})
                return
            self._json(200, {"reply": reply})
            return

        if parts.path == "/voice":
            raw = self._body()
            if not raw:
                self._json(400, {"error": "send 16-bit PCM WAV audio"})
                return
            try:
                audio = _decode_wav(raw)
            except Exception as exc:
                self._json(400, {"error": "could not read the audio: %s" % exc})
                return
            try:
                heard = self.server.transcribe(audio)
            except Exception as exc:
                self._json(503, {"error": "speech recognition unavailable: %s" % exc})
                return
            heard = " ".join(str(heard or "").split())
            if not heard:
                self._json(200, {"heard": "", "reply": ""})
                return
            try:
                reply = self.server.chat(heard)
            except Exception as exc:
                self._json(500, {"error": "%s: %s" % (type(exc).__name__, exc)})
                return
            self._json(200, {"heard": heard, "reply": reply})
            return

        self._json(404, {"error": "not found"})

    def log_message(self, format: str, *args: Any) -> None:
        return
