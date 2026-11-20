"""A small HTTP server so a phone or another computer can use this same Jarvis.

The PC keeps the model, the files, the skills and the memory; the phone is a
thin client. Auth is a bearer token; put both devices on Tailscale (or your LAN)
and the phone can reach the PC from anywhere without exposing anything to the
public internet.
"""

from __future__ import annotations

import base64
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime
from pathlib import Path
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
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/icon-192.png" sizes="192x192">
<link rel="apple-touch-icon" href="/icon-192.png">
<title>Jarvis</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 16px/1.45 -apple-system, system-ui, "Segoe UI", Roboto, sans-serif;
         background: #0d1117; color: #e6edf3; display: flex; flex-direction: column; height: 100dvh; overflow: hidden; }
  header { display: flex; align-items: center; gap: 8px; padding: 12px 14px 8px; }
  h1 { font-size: 15px; letter-spacing: .28em; margin: 0; font-weight: 600; color: #58a6ff; }
  #status { font-size: 12px; color: #8b949e; flex: 1; min-width: 0; }
  header button { background: #161b22; color: #e6edf3; border: 1px solid #30363d; border-radius: 10px;
                  padding: 6px 10px; font-size: 13px; }
  #log { flex: 1; overflow-y: auto; padding: 8px 16px 16px; display: flex; flex-direction: column; gap: 10px; }
  .msg { max-width: 92%; padding: 10px 13px; border-radius: 14px; white-space: pre-wrap; word-wrap: break-word; }
  .you { align-self: flex-end; background: #1f6feb; color: #fff; border-bottom-right-radius: 4px; }
  .jarvis { align-self: flex-start; background: #161b22; border: 1px solid #21262d; border-bottom-left-radius: 4px; }
  .meta { align-self: center; font-size: 12px; color: #8b949e; text-align: center; }
  .msg img { max-width: 100%; border-radius: 10px; margin-top: 6px; display: block; }
  .attach-name { display: block; margin-top: 6px; font-size: 12px; opacity: .85; }
  .bar { padding: 10px 12px calc(12px + env(safe-area-inset-bottom)); border-top: 1px solid #21262d; background: #0d1117; }
  #tray { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 8px; }
  #tray:empty { display: none; }
  #tray img { height: 54px; border-radius: 8px; border: 1px solid #30363d; display: block; }
  .thumb { flex: 0 0 auto; font-size: 11px; color: #8b949e; }
  .row { display: flex; gap: 8px; align-items: center; }
  #attach { width: 46px; height: 46px; font-size: 22px; line-height: 1; border-radius: 12px;
            border: 1px solid #30363d; background: #161b22; color: #e6edf3; }
  #file { display: none; }
  #text { flex: 1; min-width: 0; padding: 12px; font-size: 16px; border-radius: 12px;
          border: 1px solid #30363d; background: #161b22; color: #e6edf3; }
  #send { padding: 0 16px; height: 46px; font-size: 15px; border: 0; border-radius: 12px; background: #1f6feb; color: #fff; }
  #talk { width: 100%; padding: 14px; margin-bottom: 8px; font-size: 16px; font-weight: 600; border: 0;
          border-radius: 14px; background: #238636; color: #fff; touch-action: none;
          user-select: none; -webkit-user-select: none; }
  #talk.on { background: #da3633; }
  #drawer { position: fixed; top: 0; bottom: 0; left: 0; width: 80%; max-width: 330px; background: #0d1117;
            border-right: 1px solid #21262d; transform: translateX(-102%); transition: transform .18s ease;
            overflow-y: auto; padding: 16px; z-index: 5; }
  #drawer.open { transform: none; }
  #drawer h2 { font-size: 12px; letter-spacing: .18em; color: #8b949e; margin: 4px 0 12px; font-weight: 600; }
  .chat { padding: 10px 12px; border-radius: 10px; margin-bottom: 6px; background: #161b22;
          border: 1px solid #21262d; }
  .chat b { display: block; font-size: 14px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .chat small { color: #8b949e; font-size: 11px; }
  .chat.active { border-color: #1f6feb; }
  .chat { display: flex; align-items: center; gap: 8px; }
  .chat .text { flex: 1; min-width: 0; }
  .del { background: none; border: 0; color: #8b949e; font-size: 17px; padding: 2px 6px; line-height: 1; }
  #shade { position: fixed; inset: 0; background: #000a; display: none; z-index: 4; }
  #shade.open { display: block; }
</style>
</head>
<body>
<header>
  <button id="chats">Chats</button>
  <h1>JARVIS</h1>
  <span id="status">connecting...</span>
  <button id="new">New</button>
</header>
<main id="log"><div class="meta">Hold the button and talk, or type.</div></main>
<div class="bar">
  <button id="talk">Hold to talk</button>
  <div id="tray"></div>
  <div class="row">
    <button id="attach" aria-label="Attach a file">+</button>
    <input id="file" type="file" multiple>
    <input id="text" placeholder="Type instead" autocomplete="off" autocapitalize="sentences">
    <button id="send">Send</button>
  </div>
</div>
<div id="shade"></div>
<aside id="drawer"><h2>Previous chats</h2><div id="chatlist"></div></aside>
<script>
let token = localStorage.getItem("jarvis_token") || "";
const params = new URLSearchParams(location.search);
if (params.get("token")) {
  token = params.get("token");
  localStorage.setItem("jarvis_token", token);
  history.replaceState(null, "", "/");
}
const log = document.getElementById("log");
const statusEl = document.getElementById("status");
const textInput = document.getElementById("text");
const tray = document.getElementById("tray");
const fileInput = document.getElementById("file");
const drawer = document.getElementById("drawer");
const shade = document.getElementById("shade");
const chatlist = document.getElementById("chatlist");
let chatId = localStorage.getItem("jarvis_chat") || "";
let pending = [];

let statusBase = "connecting...";
let statusAt = Date.now();
let statusTicker = null;
function setStatus(t) {
  statusBase = t;
  statusAt = Date.now();
  statusEl.textContent = t;
  const working = t === "thinking..." || t === "sending..." || t === "recording...";
  if (working && !statusTicker) {
    statusTicker = setInterval(function () {
      const seconds = Math.round((Date.now() - statusAt) / 1000);
      if (seconds > 2) statusEl.textContent = statusBase + " " + seconds + "s";
    }, 1000);
  }
  if (!working && statusTicker) {
    clearInterval(statusTicker);
    statusTicker = null;
  }
}
function fail(message) {
  setStatus("offline");
  if (message) bubble(message + " (tap hold-to-talk to retry)", "meta");
}
function bubble(content, who) {
  const div = document.createElement("div");
  div.className = "msg " + who;
  div.textContent = content;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}
function fileUrl(name) { return "/file?name=" + encodeURIComponent(name) + "&token=" + encodeURIComponent(token); }
function showAttachment(parent, name, kind, preview) {
  if (kind === "image") {
    const img = document.createElement("img");
    img.src = preview || fileUrl(name);
    img.alt = name;
    parent.appendChild(img);
  } else {
    const span = document.createElement("span");
    span.className = "attach-name";
    span.textContent = "file: " + name;
    parent.appendChild(span);
  }
}
function renderMessage(message) {
  const div = bubble(message.text || "", message.role === "user" ? "you" : "jarvis");
  (message.files || []).forEach(function (item) { showAttachment(div, item.name, item.kind); });
}
async function api(path, options) {
  options = options || {};
  options.headers = Object.assign({ "Authorization": "Bearer " + token }, options.headers || {});
  const response = await fetch(path, options);
  if (response.status === 401) { localStorage.removeItem("jarvis_token"); throw new Error("Wrong or missing token."); }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || ("HTTP " + response.status));
  return data;
}
function toggleDrawer(open) {
  drawer.classList.toggle("open", open);
  shade.classList.toggle("open", open);
  if (open) loadChats();
}
async function loadChats() {
  try {
    const data = await api("/chats");
    chatlist.innerHTML = "";
    if (!data.chats.length) {
      const empty = document.createElement("div");
      empty.className = "meta";
      empty.textContent = "No chats yet.";
      chatlist.appendChild(empty);
      return;
    }
    data.chats.forEach(function (item) {
      const row = document.createElement("div");
      row.className = "chat" + (item.id === chatId ? " active" : "");
      const body = document.createElement("div");
      body.className = "text";
      const title = document.createElement("b");
      title.textContent = item.title;
      const meta = document.createElement("small");
      meta.textContent = item.when + " - " + item.count + " messages";
      body.appendChild(title);
      body.appendChild(meta);
      body.onclick = function () { openChat(item.id); toggleDrawer(false); };
      const remove = document.createElement("button");
      remove.className = "del";
      remove.textContent = "x";
      remove.setAttribute("aria-label", "Delete chat");
      remove.onclick = function (event) {
        event.stopPropagation();
        deleteChat(item.id, item.title);
      };
      row.appendChild(body);
      row.appendChild(remove);
      chatlist.appendChild(row);
    });
  } catch (error) { setStatus("offline"); }
}
async function openChat(id) {
  try {
    const data = await api("/chats/" + encodeURIComponent(id));
    chatId = data.id;
    localStorage.setItem("jarvis_chat", chatId);
    log.innerHTML = "";
    if (!data.messages.length) log.innerHTML = "<div class='meta'>Hold the button and talk, or type.</div>";
    data.messages.forEach(renderMessage);
    log.scrollTop = log.scrollHeight;
    setStatus("ready");
  } catch (error) { fail(error.message); }
}
async function deleteChat(id, title) {
  if (!confirm('Delete "' + title + '"?')) return;
  try {
    await api("/chats/" + encodeURIComponent(id), { method: "DELETE" });
    if (id === chatId) {
      chatId = "";
      localStorage.removeItem("jarvis_chat");
      log.innerHTML = "<div class='meta'>Hold the button and talk, or type.</div>";
      setStatus("ready");
    }
    loadChats();
  } catch (error) { fail(error.message); }
}
function renderTray() {
  tray.innerHTML = "";
  pending.forEach(function (item, index) {
    const box = document.createElement("div");
    box.className = "thumb";
    if (item.kind === "image") {
      const img = document.createElement("img");
      img.src = item.preview;
      img.alt = item.name;
      box.appendChild(img);
    } else {
      box.textContent = item.name;
    }
    box.onclick = function () { pending.splice(index, 1); renderTray(); };
    tray.appendChild(box);
  });
}
function speak(reply) {
  if (!("speechSynthesis" in window)) return;
  const utterance = new SpeechSynthesisUtterance(reply);
  utterance.rate = 1.02;
  speechSynthesis.cancel();
  speechSynthesis.speak(utterance);
}
async function send(message) {
  const files = pending.slice();
  pending = [];
  renderTray();
  const shown = bubble(message || "(" + files.length + " attachment(s))", "you");
  files.forEach(function (item) { showAttachment(shown, item.name, item.kind, item.preview); });
  setStatus("thinking...");
  try {
    const data = await api("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: message, chat: chatId, files: files.map(function (item) { return { name: item.name, kind: item.kind }; }) })
    });
    if (data.chat) {
      chatId = data.chat;
      localStorage.setItem("jarvis_chat", chatId);
    }
    bubble(data.reply || "(empty reply)", "jarvis");
    setStatus("ready");
    speak(data.reply || "");
  } catch (error) { fail(error.message); }
}
document.getElementById("send").onclick = function () {
  const value = textInput.value.trim();
  if (!value && !pending.length) return;
  textInput.value = "";
  send(value);
};
textInput.addEventListener("keydown", function (event) {
  if (event.key === "Enter") { event.preventDefault(); document.getElementById("send").click(); }
});
document.getElementById("chats").onclick = function () { toggleDrawer(true); };
shade.onclick = function () { toggleDrawer(false); };
document.getElementById("new").onclick = async function () {
  try {
    const data = await api("/chats", { method: "POST" });
    chatId = data.id;
    localStorage.setItem("jarvis_chat", chatId);
    log.innerHTML = "<div class='meta'>Hold the button and talk, or type.</div>";
    setStatus("ready");
    toggleDrawer(false);
  } catch (error) { fail(error.message); }
};
document.getElementById("attach").onclick = function () { fileInput.click(); };
fileInput.onchange = async function () {
  const chosen = Array.from(fileInput.files || []);
  for (const file of chosen) {
    try {
      const response = await fetch("/upload?name=" + encodeURIComponent(file.name), {
        method: "POST",
        headers: { "Authorization": "Bearer " + token, "Content-Type": file.type || "application/octet-stream" },
        body: file
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "upload failed");
      pending.push({
        name: data.name,
        kind: data.kind,
        preview: file.type.indexOf("image/") === 0 ? URL.createObjectURL(file) : ""
      });
      renderTray();
    } catch (error) { fail(error.message); }
  }
  fileInput.value = "";
};
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
    node.onaudioprocess = function (event) { chunks.push(new Float32Array(event.inputBuffer.getChannelData(0))); };
    source.connect(node);
    node.connect(mute);
    mute.connect(ctx.destination);
    recording = true;
    const talk = document.getElementById("talk");
    talk.classList.add("on");
    talk.textContent = "Listening - release to send";
    setStatus("recording...");
  } catch (error) { fail("Microphone blocked: " + error.message); }
}
function downsampled(rate) {
  const total = chunks.reduce(function (n, chunk) { return n + chunk.length; }, 0);
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
  const write = function (offset, string) { for (let i = 0; i < string.length; i++) view.setUint8(offset + i, string.charCodeAt(i)); };
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
  const talk = document.getElementById("talk");
  talk.classList.remove("on");
  talk.textContent = "Hold to talk";
  const rate = ctx ? ctx.sampleRate : 48000;
  if (node) node.onaudioprocess = null;
  if (stream) stream.getTracks().forEach(function (track) { track.stop(); });
  if (ctx) ctx.close();
  const blob = wavBlob(downsampled(rate), 16000);
  setStatus("sending...");
  try {
    const data = await api("/voice?chat=" + encodeURIComponent(chatId), { method: "POST", headers: { "Content-Type": "audio/wav" }, body: blob });
    if (data.heard) bubble(data.heard, "you");
    bubble(data.reply || "(empty reply)", "jarvis");
    chatId = data.chat || chatId;
    localStorage.setItem("jarvis_chat", chatId);
    setStatus("ready");
    speak(data.reply || "");
  } catch (error) { fail(error.message); }
}
const talkButton = document.getElementById("talk");
talkButton.addEventListener("pointerdown", function (event) { event.preventDefault(); startRecording(); });
talkButton.addEventListener("pointerup", function (event) { event.preventDefault(); stopRecording(); });
talkButton.addEventListener("pointercancel", stopRecording);
talkButton.addEventListener("pointerleave", function () { if (recording) stopRecording(); });
document.querySelector("form") && document.querySelector("form").addEventListener("submit", function (e) { e.preventDefault(); });

fetch("/health", { headers: { "Authorization": "Bearer " + token } })
  .then(function (response) { return response.ok ? response.json() : Promise.reject(new Error("no auth")); })
  .then(function (data) { setStatus(data.model + " - ready"); })
  .catch(function () { setStatus(token ? "token rejected - reload with ?token=" : "no token - reload with ?token="); });

loadChats();
if (chatId) openChat(chatId);
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


class Chats:
    """Conversations on disk, so an old one can be reopened from the phone."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _file(self, chat_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_-]", "", chat_id or "")[:64]
        return self.root / (safe + ".json")

    def _read(self, chat_id: str) -> Optional[dict]:
        path = self._file(chat_id)
        if not (chat_id and path.is_file()):
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or not data.get("id"):
            return None
        data.setdefault("messages", [])
        return data

    def _write(self, data: dict) -> None:
        path = self._file(str(data["id"]))
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)

    def new(self, title: str = "") -> dict:
        chat_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
        data = {"id": chat_id, "title": title or "New chat", "updated": time.time(), "messages": []}
        self._write(data)
        return data

    def load(self, chat_id: str) -> Optional[dict]:
        return self._read(chat_id)

    def delete(self, chat_id: str) -> bool:
        path = self._file(chat_id)
        if not (chat_id and path.is_file()):
            return False
        try:
            path.unlink()
        except OSError:
            return False
        return True

    def listing(self) -> list:
        items = []
        for path in self.root.glob("*.json"):
            data = self._read(path.stem)
            if not data:
                continue
            updated = float(data.get("updated") or 0)
            items.append(
                {
                    "id": data["id"],
                    "title": data.get("title") or "Chat",
                    "count": len(data.get("messages") or []),
                    "when": time.strftime("%d %b %H:%M", time.localtime(updated)),
                    "_at": updated,
                }
            )
        items.sort(key=lambda item: item["_at"], reverse=True)
        for item in items:
            item.pop("_at", None)
        return items[:60]

    def append(self, data: dict, message: dict) -> dict:
        text = str(message.get("text") or "").strip()
        if not text and not message.get("files"):
            return data
        data.setdefault("messages", []).append(message)
        if len(data["messages"]) == 1 and message.get("role") == "user":
            data["title"] = text[:48] or "Chat"
        data["updated"] = time.time()
        self._write(data)
        return data


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
        self.chats = Chats(state_dir() / "chats")
        self.uploads = state_dir() / "uploads"
        self.uploads.mkdir(parents=True, exist_ok=True)

    def conversation(self, chat_id: str) -> dict:
        data = self.chats.load(chat_id) if chat_id else None
        return data or self.chats.new()

    def chat(
        self,
        message: str,
        chat_id: str = "",
        files: Any = None,
        images: Any = None,
        model: Any = None,
    ) -> Any:
        # One reply at a time: a second message would otherwise queue silently
        # behind a slow one and look like nothing is happening.
        if not self.lock.acquire(timeout=2):
            return "I am still on the previous message - give me a moment.", chat_id
        try:
            data = self.conversation(chat_id)
            # Continue from the stored transcript, not from whatever an earlier
            # phone session left in memory.
            self.agent.history = [
                {"role": item["role"], "content": item.get("text") or ""}
                for item in data.get("messages", [])
                if item.get("text") and item.get("role") in {"user", "assistant"}
            ]
            content: Any = message
            if images:
                # A text-only model rejects image parts, so an image turn goes to
                # the vision model with the words and the picture together.
                content = [{"type": "text", "text": message}]
                content += [{"type": "image_url", "image_url": {"url": url}} for url in images]
            reply = self.agent.chat(content, model=model)
            self.chats.append(data, {"role": "user", "text": message, "files": files or []})
            self.chats.append(data, {"role": "assistant", "text": reply})
            return reply, data["id"]
        finally:
            self.lock.release()

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
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # The phone went away while we were answering (screen off, reload,
            # tunnel blip). Nothing to report and nobody to tell.
            self.close_connection = True

    def _json(self, code: int, payload: Any) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _failure(self, where: str, exc: Exception) -> str:
        """Log a failure to stderr, where journald keeps it, and return the detail.

        The phone only sees a short line; without this the reason lived in an
        HTTP response that a disconnected client then threw away.
        """
        detail = "%s: %s" % (type(exc).__name__, exc)
        try:
            print("jarvis: %s failed - %s" % (where, detail), file=sys.stderr, flush=True)
        except Exception:
            pass
        return detail

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
        if parts.path == "/manifest.webmanifest":
            self._send(200, MANIFEST, "application/manifest+json; charset=utf-8")
            return
        if parts.path == "/icon-192.png":
            self._send(200, ICON_192, "image/png")
            return
        if parts.path == "/icon-512.png":
            self._send(200, ICON_512, "image/png")
            return
        if parts.path == "/chats":
            if not self._authorised(query):
                self._json(401, {"error": "unauthorised"})
                return
            self._json(200, {"chats": self.server.chats.listing()})
            return
        if parts.path.startswith("/chats/"):
            if not self._authorised(query):
                self._json(401, {"error": "unauthorised"})
                return
            data = self.server.chats.load(parts.path.split("/", 2)[2])
            if data is None:
                self._json(404, {"error": "no such chat"})
                return
            self._json(
                200,
                {"id": data["id"], "title": data.get("title"), "messages": data.get("messages") or []},
            )
            return
        if parts.path == "/file":
            if not self._authorised(query):
                self._json(401, {"error": "unauthorised"})
                return
            wanted = (query.get("name") or [""])[0]
            path = self.server.uploads / Path(wanted).name
            if not wanted or not path.is_file():
                self._json(404, {"error": "no such file"})
                return
            self._send(200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            return
        self._json(404, {"error": "not found"})

    # -------------------------------------------------------------------- POST

    def do_POST(self) -> None:
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        if not self._authorised(query):
            self._json(401, {"error": "unauthorised - send Authorization: Bearer <token>"})
            return

        if parts.path == "/upload":
            raw = self._body()
            supplied = Path((query.get("name") or [""])[0] or "upload").name
            name = re.sub(r"[^A-Za-z0-9._ -]", "_", supplied)[:80] or "upload"
            if not raw:
                self._json(400, {"error": "empty upload"})
                return
            stored = "%s-%s" % (datetime.now().strftime("%Y%m%d-%H%M%S"), name)
            try:
                (self.server.uploads / stored).write_bytes(raw)
            except OSError as exc:
                self._json(500, {"error": self._failure("upload", exc)})
                return
            kind = "image" if (mimetypes.guess_type(name)[0] or "").startswith("image/") else "file"
            self._json(200, {"name": stored, "kind": kind, "bytes": len(raw)})
            return

        if parts.path == "/chats":
            data = self.server.chats.new()
            self._json(200, {"id": data["id"], "title": data["title"]})
            return

        if parts.path == "/chat":
            raw = self._body()
            try:
                payload = json.loads(raw.decode("utf-8", "replace") or "{}")
            except json.JSONDecodeError:
                self._json(400, {"error": "body must be JSON"})
                return
            message = str(payload.get("message") or "").strip()[:MAX_MESSAGE]
            chat_id = str(payload.get("chat") or "")[:80]
            files = []
            supplied = payload.get("files")
            if isinstance(supplied, list):
                for item in supplied[:10]:
                    if not isinstance(item, dict):
                        continue
                    name = Path(str(item.get("name") or "")).name
                    if name:
                        files.append({"name": name, "kind": str(item.get("kind") or "file")})
            config = getattr(self.server.agent, "config", None)
            vision = (getattr(config, "vision_model", "") or "").strip()
            images = []
            notes = []
            for item in files:
                path = self.server.uploads / item["name"]
                if item["kind"] != "image":
                    notes.append("\n[attached: %s]" % path)
                    continue
                try:
                    raw = path.read_bytes()
                except OSError:
                    notes.append("\n[attached: %s]" % path)
                    continue
                if not vision:
                    notes.append("\n[attached: %s - pictures cannot be viewed, vision is off]" % path)
                    continue
                if len(raw) > 8 * 1024 * 1024:
                    notes.append("\n[attached: %s - too large to look at]" % path)
                    continue
                kind = mimetypes.guess_type(path.name)[0] or "image/jpeg"
                images.append("data:%s;base64,%s" % (kind, base64.b64encode(raw).decode("ascii")))
            if notes:
                message = (message + "".join(notes)).strip()
            if not message:
                self._json(400, {"error": "message is required"})
                return
            try:
                reply, chat_id = self.server.chat(message, chat_id, files, images, vision or None)
            except Exception as exc:
                self._json(500, {"error": self._failure("chat", exc)})
                return
            self._json(200, {"reply": reply, "chat": chat_id})
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
                self._failure("transcribe", exc)
                self._json(503, {"error": "speech recognition unavailable: %s" % exc})
                return
            heard = " ".join(str(heard or "").split())
            if not heard:
                self._json(200, {"heard": "", "reply": ""})
                return
            try:
                reply, chat_id = self.server.chat(heard, (query.get("chat") or [""])[0])
            except Exception as exc:
                self._json(500, {"error": self._failure("voice chat", exc)})
                return
            self._json(200, {"heard": heard, "reply": reply, "chat": chat_id})
            return

        self._json(404, {"error": "not found"})

    # ------------------------------------------------------------------ DELETE

    def do_DELETE(self) -> None:
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        if not self._authorised(query):
            self._json(401, {"error": "unauthorised - send Authorization: Bearer <token>"})
            return
        if parts.path.startswith("/chats/"):
            if self.server.chats.delete(parts.path.split("/", 2)[2]):
                self._json(200, {"deleted": True})
            else:
                self._json(404, {"error": "no such chat"})
            return
        self._json(404, {"error": "not found"})

    def log_message(self, format: str, *args: Any) -> None:
        return

# ------------------------------------------------------- installable web app

# The manifest and the two icons are what turn Chrome's "Create shortcut" into a
# real install: the app gets its own launcher entry and opens full screen with the
# token already stored in the browser. Chrome asks for a name, a 192px and a 512px
# icon, a start_url and display: standalone - no service worker since Chrome 108.
# The icons are generated PNGs (a ring on the page's own dark background, artwork
# kept inside the maskable safe zone) inlined so deploying a change stays a single
# file.
MANIFEST = json.dumps(
    {
        "id": "/",
        "name": "Jarvis",
        "short_name": "Jarvis",
        "description": "Your own AI companion, running on your server.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#0d1117",
        "theme_color": "#0d1117",
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    },
    indent=2,
).encode("utf-8")

ICON_192 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAMAAAADACAYAAABS3GwHAAAH3ElEQVR42u2dvZXjNhCA0YDfoyOn"
    "KoElsASGF7IElcAOVAIypyyBJbAEZs78GLgAmXoPutPK3pVWws/M4Au+7G53CcwHAjMA6H77/Y8z"
    "QK04GgEQAAABABAAAAEAEAAAAQAQAAABABAAAAEAEAAAAQAQAAABABAAAAEAEAAAAQAQAAABABAA"
    "AAEAEAAAAQAQAAABABAAAAEAEAAAAQAQwBQ//jwfdronOdBmCKA1yPudccfvzDvbzvlFtvAzfPiZ"
    "PXIggKSAb3eOO9Obgf6KGFP43S19gQA5g74PI/KaMeAfsYa/qaePECDVSO8zj/LvvB08bwYEeDfo"
    "mzDFWBUE/VdvhsszNPQpAnxnIXtSMtp/561wYgGNAI8C3xsK+s/wiIAA91Od0diI/8wbYWRqVLkA"
    "ewAMlQX+/4kwIECdWZ254sC/Z641a+QqDP6RgP+UEQFsL3IXgvwhS02LZFdJ8PeVz/VfWRv0CGAj"
    "+E8E9MucEEB3enMiiN9mspwudYaDn/l+3HVBgwB6UpybkHn0HLJOx+thlyf+/uuhmGP4v7Og52kR"
    "gOB/tAtzSJFFCVmsofCuVHMSOII/StB3BZ63KySDKQkcwf9y5XQQ9PxD5sq2GQmckQXvlnEnZSt8"
    "IPAZJWgQoI5sz6SpOhrWCxPZIfsCTBk6uFPcPl2GAWJCAJsV3tFQgmCkYmxIgLC3J+Woby7fHdYH"
    "Kd8GPQLkm99ujGTi3pybxl2kGgVYEnVeNSejEp6EWxBA31zWZIm/YO1kRIB0HZbi3pxqL5AKbZri"
    "3qMWAeJ31kwOW00tZUaA+HPW2CM/t6V9lCD2m2BAAJlbHbYf3JuZY02gYquEq2zhS/DnlWBEAFk5"
    "/4FAzzrdFF8bkC6Ap8ilvljmEeD10b/aAo2xguMBAcqOQsz7y9ZdTghQLvMzEtDFExBiM0JSBTgy"
    "9TE3FToiwPONHqso0xHEUQ7VRCk+IkDeuedEAIs7edciQL7UJ58BkpeR8wjwuLE367nnC3/9/U+z"
    "0wUaBRLEGJg2BMhz1LEVFuyHnWHH76w7509Yw7+5/NuD0alpjwBpR5lZUOBfRvfpi4B/xOX/doL6"
    "Z7b2dnYGsz+DkMCf3wj8e2YJIkTaJ7QiQLpX7CYg+E8RA/+ek5E1WosAaYpfvvCidkkY/FeWkovm"
    "SNPUIwKkyTV3hYK/fbC4jc3ld7WF+qmzVKNxhtKfW8GRP2fw30rQ0FcGBIhUaPGFBFgKBP/P6ZDi"
    "adABAeLm/wdjC16xC+NI2aAeAeJuuz0USHWehdApfGOPCBDvlboVGP1nQQLMCtcBHgHiVRjnikf/"
    "Um+B2ULF3hnJAI2ZBZgECjApm7ZuCPCrMdUUVsLGtrNQDhn77O3CJQLEW1B1GQUYBAswZOy3zkIq"
    "VIIAnTIBvGABPP1WoQCZ5/+rYAFWZVNXBFAogGgQAAFS7/uRLkGDAAhQU/6/WD0AARAAARCAKRBT"
    "IARgEcwiGAFIg5IGRQAKYRTCEICtEGyFYCsEm+HYDMdmOLZDsx2a7dAciOFADAdiOBLJkUiORHIo"
    "nkPxHIrnWhSuReFaFC7G4mIsBOBqRK5GRAAux+VyXATgenSuR+cDGXwggw9k1CpAaFw+kcQnkqoW"
    "gI/k8ZG8qgXgM6l8JrVeASLOMb205/pk0cyHshEgSUOLKbRYIFKhUuTAJFGAWK/aieAVVaMRNzUV"
    "KUDEbJCII3cGgr+L1BerxOeTKsAxUqMvBPHbfbFE6osjAjzf6E2kxbCYbbdKg3+M1AeXvmwQ4HuN"
    "f4rU+CLnngqCv43Y/iepz+kqyDwwFSo79RGdkXPCO8HXMAoZf/uKrslIF+AQcS0gYp+QguAfIrb3"
    "Jr0e4ypaiF07hPXA1/P+mAOO+ASEBgGayJ2CBHmCX2zmR5UACV7L10JbQ+B/GGTWyG2sYrrpFHXS"
    "HLmDFiT4GfxL5LadtTy/U/aKPid4E7SVT3vWBO3aIoD8BXHVa4IEc36VlXensOOWRBIMFQX/kCj4"
    "1RUcNQpwSNR5VRTLIhe5VOX8TQgQ+ejkZ4vj1uiUZ0nYbr3GdnGMZPZ3kSZaO5l4czrlHTsl7thF"
    "86GacJhlSdxGqk/eaRegydDBV9EOytZJU4Z2UV9LcQZe703CRfF/djZKXh+Eeb7P1BabhUKiM7TA"
    "yyXBtSo9CEtrzhmf30ztxBnLcuSU4BoIvsQ6IczvfaFnNpMlcwZTfbkD4l6GIcV6Iczrh0JBb7Zq"
    "bkoAARLcB8scUpDHMGJ3T47sXfg/Y/gZUp7HXH3EnACZs0O1YHbnrEkBbiSYCN4oKWCz28bNCpCx"
    "YmwZ83ujzAtws3doI6C/Nd/va4iNKgS4yaKwLnhuvl/NzdrVCJBxY5hmqrtGsjoBblKlMwH/obJd"
    "5dHQKgXIcDJK01y/6svCqhbgJl06VibCFp65+lsxqhfgbpHsKwh+z+ejEOCRCCdjb4QtPBOBjwDf"
    "mhodE92bk4s1PAO34CFAlIMmm5LR3v/g/lMESFhV9sLeDGv4m3r6CAFyvxmOYcPYlnmUn8LvZqRH"
    "AFEL6D6kGH2EvfzXMwU+/MyehSwCaJajexKCHAEAEAAAAQAQAAABABAAAAEAEAAAAQAQAAABABAA"
    "AAEAEAAAAQAQAAABABAAAAEAEAAAAQABaARAAAAEAEAAAAQAQAAABABAAAAEAEAAAAQAMMO/0Gvj"
    "X/w5TcIAAAAASUVORK5CYII="
)
ICON_512 = base64.b64decode(
    "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"
    "RU5ErkJggg=="
)
