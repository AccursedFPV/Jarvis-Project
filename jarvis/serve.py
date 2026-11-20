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

from . import __version__
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
  :root {
    color-scheme: dark;
    --bg: #05070b;
    --surface: #0b1119;
    --surface-2: #0f1a24;
    --line: #17232f;
    --accent: #3b82f6;
    --accent-dim: #13283f;
    --text: #e9eff7;
    --muted: #7b8798;
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font: 14.5px/1.45 -apple-system, system-ui, "Segoe UI", Roboto, sans-serif;
         display: flex; flex-direction: column; height: 100dvh; overflow: hidden; }
  header { display: flex; align-items: center; gap: 8px; padding: 9px 12px 7px;
           border-bottom: 1px solid var(--line); }
  h1 { font-size: 12px; letter-spacing: .3em; margin: 0; font-weight: 600; }
  #status { font-size: 10.5px; color: var(--muted); letter-spacing: .04em; flex: 1; min-width: 0;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  header button { background: transparent; color: var(--muted); border: 1px solid var(--line);
                  border-radius: 999px; padding: 4px 10px; font-size: 11px; letter-spacing: .06em; }
  header button:active { background: var(--surface-2); color: var(--text); }
  #log { flex: 1; overflow-y: auto; padding: 12px 12px 6px; display: flex; flex-direction: column; gap: 8px; }
  .msg { max-width: 90%; padding: 8px 11px; border-radius: 14px; white-space: pre-wrap;
         overflow-wrap: anywhere; font-size: 13.5px; line-height: 1.45; }
  .msg a { color: #8fbcff; text-decoration: underline; text-underline-offset: 2px; }
  .you { align-self: flex-end; background: var(--accent); color: #fff; border-bottom-right-radius: 6px; }
  .jarvis { align-self: flex-start; background: var(--surface); border: 1px solid var(--line);
            border-bottom-left-radius: 6px; }
  .meta { align-self: center; font-size: 10.5px; color: var(--muted); text-align: center; letter-spacing: .02em; }
  .msg img { max-width: 100%; border-radius: 10px; margin-top: 8px; display: block; }
  .attach-name { display: block; margin-top: 4px; font-size: 11px; opacity: .8; }
  .bar { padding: 7px 10px calc(9px + env(safe-area-inset-bottom));
         border-top: 1px solid var(--line); background: var(--bg); }
  #tray { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 8px; }
  #tray:empty { display: none; }
  #tray img { height: 52px; border-radius: 10px; border: 1px solid var(--line); display: block; }
  .thumb { flex: 0 0 auto; font-size: 11px; color: var(--muted); }
  .row { display: flex; gap: 6px; align-items: center; }
  #attach { width: 38px; height: 38px; font-size: 18px; line-height: 1; border-radius: 11px;
            border: 1px solid var(--line); background: var(--surface); color: var(--muted); }
  #file { display: none; }
  #text { flex: 1; min-width: 0; padding: 9px 12px; font-size: 16px; border-radius: 11px;
          border: 1px solid var(--line); background: var(--surface); color: var(--text); }
  #text::placeholder { color: #55616f; }
  #text:focus { outline: none; border-color: #24405f; }
  #send { padding: 0 14px; height: 38px; font-size: 13px; border: 0; border-radius: 11px;
          background: var(--accent); color: #fff; font-weight: 500; min-width: 62px; }
  #send.stop { background: #b3261e; }
  #send:disabled, #attach:disabled, #text:disabled { opacity: .45; }
  #voice { width: 100%; margin: 0 0 12px; padding: 9px 10px; font-size: 12.5px;
           text-align: left; background: var(--surface); color: var(--text);
           border: 1px solid var(--line); border-radius: 10px; }
  #voice b { float: right; font-weight: 500; color: var(--muted); }
  #voice.on b { color: #7fb0ea; }
  #drawer { position: fixed; top: 0; bottom: 0; left: 0; width: 82%; max-width: 340px;
            background: var(--bg); border-right: 1px solid var(--line); transform: translateX(-102%);
            transition: transform .18s ease; overflow-y: auto; padding: 16px 12px; z-index: 5; }
  #drawer.open { transform: none; }
  #drawer h2 { font-size: 10px; letter-spacing: .2em; color: var(--muted); margin: 2px 0 10px;
               font-weight: 600; text-transform: uppercase; }
  .chat { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-radius: 11px;
          margin-bottom: 6px; border: 1px solid transparent; }
  .chat .text { flex: 1; min-width: 0; }
  .chat b { display: block; font-size: 13px; font-weight: 500; overflow: hidden;
            text-overflow: ellipsis; white-space: nowrap; }
  .chat small { color: var(--muted); font-size: 10.5px; }
  .chat.active { background: var(--accent-dim); border-color: #1f3f63; }
  .chat.active b { color: #cfe3ff; }
  .chat.active small::after { content: " - current"; color: #7fb0ea; }
  .del { background: none; border: 0; color: var(--muted); font-size: 15px; padding: 4px 6px; line-height: 1; }
  #shade { position: fixed; inset: 0; background: #000a; display: none; z-index: 4; }
  #shade.open { display: block; }
  .work { align-self: stretch; font-size: 12.5px; color: #a8b7c8; }
  .work-head { display: flex; align-items: center; gap: 7px; padding: 3px 0;
               font-size: 11.5px; color: #7f93a8; user-select: none; -webkit-user-select: none; }
  .work-head .chev, .thought-head .chev, .acts-head .chev {
    font-size: 9px; opacity: .7; transition: transform .15s ease; }
  .work.folded .work-head .chev { transform: rotate(-90deg); }
  .work.folded .work-body { display: none; }
  .work-body { display: flex; flex-direction: column; gap: 7px; margin-top: 5px;
               padding-left: 9px; border-left: 1px solid #17232f; }
  .narr { white-space: pre-wrap; overflow-wrap: anywhere; color: #cfdae6; }
  .spin { width: 9px; height: 9px; border-radius: 50%; flex: 0 0 auto;
          border: 1.5px solid #2b4a6b; border-top-color: var(--accent);
          animation: spin .7s linear infinite; }
  .work.done .spin { display: none; }
  .chev { flex: 0 0 auto; }
  .work-head > span, .thought-head > span, .acts-head > span {
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .thought-head, .acts-head { display: flex; align-items: center; gap: 6px; font-size: 11.5px;
                              color: #8399b1; user-select: none; -webkit-user-select: none; }
  .thought.folded .chev, .acts.folded .chev { transform: rotate(-90deg); }
  .thought.folded .thought-body, .acts.folded .acts-body { display: none; }
  .thought-body { white-space: pre-wrap; overflow-wrap: anywhere; margin: 5px 0 0 15px;
                  font-size: 11.5px; color: #93a8c0; opacity: .92;
                  max-height: 26vh; overflow-y: auto; }
  .acts-body { margin: 4px 0 0 15px; font-size: 11px; color: #7f97b3; }
  .acts-body div { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .acts-body div::before { content: "\\203A  "; color: #4a6b8c; }

</style>
</head>
<body>
<header>
  <button id="chats">Chats</button>
  <h1>JARVIS</h1>
  <span id="status">connecting...</span>
  <button id="new">New</button>
</header>
<main id="log"><div class="meta">Ask me anything.</div></main>
<div class="bar">
  <div id="tray"></div>
  <div class="row">
    <button id="attach" aria-label="Attach a file">+</button>
    <input id="file" type="file" multiple>
    <input id="text" placeholder="Message Jarvis" autocomplete="off" autocapitalize="sentences">
    <button id="send">Send</button>
  </div>
</div>
<div id="shade"></div>
<aside id="drawer"><button id="voice">Spoken replies<b>on</b></button><h2>Previous chats</h2><div id="chatlist"></div></aside>
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
let sending = false;
let stopping = false;
let activeTurn = "";
let controller = null;

let statusBase = "connecting...";
let statusAt = Date.now();
let statusTicker = null;
function setStatus(t) {
  statusBase = t;
  statusAt = Date.now();
  statusEl.textContent = t;
  const working = t === "thinking..." || t === "sending..." ||
    t === "working..." || t === "replying...";
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
  if (message) bubble(message + " (tap Send to retry)", "meta");
}
function escapeHtml(value) {
  return String(value).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
  });
}
function prettyUrl(url) {
  // A phone bubble is not the place for a 120-character Amazon address.
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.replace(/^www\\./, "");
    const path = parsed.pathname === "/" ? "" : parsed.pathname;
    return host + (path.length > 24 ? path.slice(0, 24) + "..." : path);
  } catch (error) { return url; }
}
function linkHtml(url, label) {
  return '<a href="' + String(url).replace(/"/g, "%22") +
    '" target="_blank" rel="noopener noreferrer">' + label + "</a>";
}
function mdToHtml(text) {
  // What the model is allowed to use: [label](link), bare links, bare domains,
  // **bold**. Every one of those becomes something a thumb can tap.
  let out = escapeHtml(text);
  const kept = [];
  function keep(html) { kept.push(html); return "@@keep" + (kept.length - 1) + "@@"; }
  out = out.replace(/\\[([^\\]]{1,160})\\]\\((https?:\\/\\/[^\\s)]+)\\)/g, function (match, label, url) {
    return keep(linkHtml(url, label));
  });
  out = out.replace(/https?:\\/\\/[^\\s<>@]+/g, function (url) {
    const tail = (url.match(/[.,;:!?)]+$/) || [""])[0];
    const clean = tail ? url.slice(0, -tail.length) : url;
    return (clean ? keep(linkHtml(clean, prettyUrl(clean))) : "") + tail;
  });
  // "amazon.com/dp/..." with no scheme is still a link.
  out = out.replace(/(^|[\\s(])((?:www\\.)?(?:[a-z0-9-]+\\.)+(?:co\\.uk|com|net|org|io|ai|dev|app|uk|de|me|tv|gg|so|to|ly|xyz|shop|store|link|news|blog|fm|cc)(?:\\/[^\\s<>@]*)?)/gi,
    function (match, before, url) {
      const tail = (url.match(/[.,;:!?)]+$/) || [""])[0];
      const clean = tail ? url.slice(0, -tail.length) : url;
      return before + (clean ? keep(linkHtml("https://" + clean, clean)) : "") + tail;
    });
  out = out.replace(/\\*\\*([^*]{1,200})\\*\\*/g, "<strong>$1</strong>");
  return out.replace(/@@keep(\\d+)@@/g, function (match, index) {
    return kept[Number(index)] || "";
  });
}
function shortenStep(text) {
  return String(text).replace(/https?:\\/\\/[^\\s)]+/g, function (url) { return prettyUrl(url); });
}

function bubble(content, who) {
  const div = document.createElement("div");
  div.className = "msg " + who;
  if (who === "jarvis") { div.innerHTML = mdToHtml(content); } else { div.textContent = content; }
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
    let chats = data.chats || [];
    if (chatId && !chats.some(function (item) { return item.id === chatId; })) {
      // The open chat must be in its own list even before its first stored turn.
      try {
        const current = await api("/chats/" + encodeURIComponent(chatId));
        chats = [{
          id: current.id,
          title: current.title || "New chat",
          count: (current.messages || []).length,
          when: "now"
        }].concat(chats);
      } catch (error) { /* it was deleted */ }
    }
    chatlist.innerHTML = "";
    if (!chats.length) {
      const empty = document.createElement("div");
      empty.className = "meta";
      empty.textContent = "No chats yet.";
      chatlist.appendChild(empty);
      return;
    }
    chats.forEach(function (item) {
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
    if (!data.messages.length) log.innerHTML = "<div class='meta'>Ask me anything.</div>";
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
      log.innerHTML = "<div class='meta'>Ask me anything.</div>";
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
let voiceOn = localStorage.getItem("jarvis_voice") !== "off";
function voiceLabel() {
  const el = document.getElementById("voice");
  if (!el) return;
  el.classList.toggle("on", voiceOn);
  el.textContent = "Spoken replies";
  const state = document.createElement("b");
  state.textContent = voiceOn ? "on" : "off";
  el.appendChild(state);
}
function cleanForSpeech(text) {
  return String(text)
    .replace(/```[\\s\\S]*?```/g, " ")
    .replace(/\\[([^\\]]{1,120})\\]\\([^)]+\\)/g, "$1")
    .replace(/https?:\\/\\/\\S+/g, " link ")
    .replace(/[*_`#>|]+/g, " ")
    .replace(/\\s+/g, " ")
    .trim();
}
function unlockVoice() {
  // Android only lets a page speak after a tap, so the first Send spends itself
  // on a silent utterance; the real reply then plays with no gesture.
  if (!voiceOn || !("speechSynthesis" in window) || window.jarvisVoiceReady) return;
  try {
    const warm = new SpeechSynthesisUtterance(" ");
    warm.volume = 0;
    speechSynthesis.speak(warm);
    window.jarvisVoiceReady = true;
  } catch (error) { /* no speech engine installed */ }
}
function speak(reply) {
  if (!voiceOn || !("speechSynthesis" in window)) return;
  const text = cleanForSpeech(reply);
  if (!text) return;
  try {
    speechSynthesis.cancel();
    // A sentence at a time: Android Chrome drops very long utterances.
    const sentences = text.match(/[^.!?]{1,240}[.!?]?/g) || [text];
    sentences.slice(0, 60).forEach(function (sentence) {
      const utterance = new SpeechSynthesisUtterance(sentence.trim());
      utterance.rate = 1.02;
      speechSynthesis.speak(utterance);
    });
  } catch (error) { /* speech is a bonus, never the reply */ }
}

function setLocked(locked) {
  ["send", "attach"].forEach(function (id) {
    const el = document.getElementById(id);
    if (el) el.disabled = locked;
  });
  textInput.disabled = locked;
}
const TOOL_VERBS = [
  [/^web_search$/, "searched the web"],
  [/^fetch_url$/, "read a page"],
  [/^run_command$/, "ran commands"],
  [/^(read_file|list_files|search_files)$/, "read files"],
  [/^(write_file|make_dir|move_path|delete_path)$/, "edited files"],
  [/^(remember|forget|learn|recall)$/, "checked memory"],
  [/^(update_plan|complete_step)$/, "planned the work"],
  [/^(open_target|launch_app)$/, "opened things"],
  [/^skill_/, "used a tool"],
];
function verbFor(step) {
  const name = String(step).split("(")[0].trim();
  for (const pair of TOOL_VERBS) { if (pair[0].test(name)) return pair[1]; }
  return "ran " + name.replace(/_/g, " ");
}
function secondsLabel(ms) {
  const total = Math.max(0, Math.round(ms / 1000));
  if (total < 60) return total + "s";
  return Math.floor(total / 60) + "m " + (total % 60) + "s";
}
function workPanel() {
  // One collapsible block per turn: the thought, the tools it ran, what it said
  // on the way - then a single "Worked for 12s" line once the answer lands.
  const panel = document.createElement("div");
  panel.className = "work";
  const head = document.createElement("div");
  head.className = "work-head";
  const spin = document.createElement("span");
  spin.className = "spin";
  const label = document.createElement("span");
  label.textContent = "Working";
  const chev = document.createElement("span");
  chev.className = "chev";
  chev.textContent = "\\u203a";
  head.appendChild(spin);
  head.appendChild(label);
  head.appendChild(chev);
  const body = document.createElement("div");
  body.className = "work-body";
  panel.appendChild(head);
  panel.appendChild(body);
  head.onclick = function () { panel.classList.toggle("folded"); };
  log.appendChild(panel);
  log.scrollTop = log.scrollHeight;
  const started = Date.now();
  const ticker = setInterval(function () {
    label.textContent = "Working " + secondsLabel(Date.now() - started);
  }, 1000);
  let narr = null, group = null, thought = null;
  function scrolled() { log.scrollTop = log.scrollHeight; }
  function chevron() {
    const mark = document.createElement("span");
    mark.className = "chev";
    mark.textContent = "\\u203a";
    return mark;
  }
  function openThought() {
    if (thought) return thought;
    const box = document.createElement("div");
    box.className = "thought folded";
    const top = document.createElement("div");
    top.className = "thought-head";
    top.appendChild(chevron());
    const title = document.createElement("span");
    title.textContent = "Thought";
    top.appendChild(title);
    const text = document.createElement("div");
    text.className = "thought-body";
    box.appendChild(top);
    box.appendChild(text);
    top.onclick = function () { box.classList.toggle("folded"); };
    body.appendChild(box);
    thought = { body: text };
    return thought;
  }
  return {
    panel: panel,
    think: function (text) {
      const view = openThought();
      view.body.textContent += text;
      view.body.scrollTop = view.body.scrollHeight;
      scrolled();
    },
    say: function (text) {
      if (!narr) {
        narr = document.createElement("div");
        narr.className = "narr";
        body.appendChild(narr);
        group = null;
      }
      narr.textContent += text;
      scrolled();
    },
    step: function (text) {
      // Words came after a tool call, so the next text is a new message.
      narr = null;
      if (!group) {
        const box = document.createElement("div");
        box.className = "acts folded";
        const top = document.createElement("div");
        top.className = "acts-head";
        const lines = document.createElement("div");
        lines.className = "acts-body";
        box.appendChild(top);
        box.appendChild(lines);
        top.onclick = function () { box.classList.toggle("folded"); };
        body.appendChild(box);
        group = { head: top, body: lines, verbs: [] };
      }
      const verb = verbFor(text);
      if (group.verbs.indexOf(verb) === -1) group.verbs.push(verb);
      const line = document.createElement("div");
      line.textContent = shortenStep(text);
      line.title = text;
      group.body.appendChild(line);
      const verbs = group.verbs.slice();
      const first = verbs.shift() || "worked";
      group.head.textContent = "";
      group.head.appendChild(chevron());
      const title = document.createElement("span");
      title.textContent = first.charAt(0).toUpperCase() + first.slice(1) +
        (verbs.length ? ", " + verbs.join(", ") : "");
      group.head.appendChild(title);
      scrolled();
    },
    partial: function () { return narr ? narr.textContent : ""; },
    finish: function () {
      clearInterval(ticker);
      panel.classList.add("done", "folded");
      label.textContent = "Worked for " + secondsLabel(Date.now() - started);
      // The last streamed text is moved out as the answer, so drop the copy.
      if (narr) { narr.remove(); narr = null; }
      if (!body.children.length) panel.remove();
      scrolled();
    },
  };
}

function setSendMode(mode) {
  const el = document.getElementById("send");
  if (!el) return;
  el.textContent = mode === "stop" ? "Stop" : "Send";
  el.classList.toggle("stop", mode === "stop");
  // While a turn runs the button has to stay pressable: it is the brake.
  if (mode === "stop") el.disabled = false;
}
async function stopTurn() {
  if (!sending || stopping) return;
  stopping = true;
  setStatus("stopping...");
  document.getElementById("send").disabled = true;
  try {
    await fetch("/cancel", {
      method: "POST",
      headers: { "Authorization": "Bearer " + token, "Content-Type": "application/json" },
      body: JSON.stringify({ turn: activeTurn })
    });
  } catch (error) { /* the abort below still frees the page */ }
  if (controller) controller.abort();
}
async function send(message) {
  if (sending) return;
  sending = true;
  stopping = false;
  unlockVoice();
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  activeTurn = crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2);
  controller = new AbortController();
  setLocked(true);
  setSendMode("stop");
  const files = pending.slice();
  pending = [];
  renderTray();
  const shown = bubble(message || "(" + files.length + " attachment(s))", "you");
  files.forEach(function (item) { showAttachment(shown, item.name, item.kind, item.preview); });
  setStatus("thinking...");

  const view = workPanel();
  function handle(event) {
    if (event.t === "think") {
      view.think(event.d);
    } else if (event.t === "step") {
      view.step(event.d);
      setStatus("working...");
    } else if (event.t === "say") {
      view.say(event.d);
      setStatus("replying...");
    } else if (event.t === "error") {
      throw new Error(event.d);
    } else if (event.t === "done") {
      if (event.chat) {
        chatId = event.chat;
        localStorage.setItem("jarvis_chat", chatId);
      }
      const text = event.reply || "(empty reply)";
      // The work folds away, the answer takes its place, and the phone says it.
      view.finish();
      bubble(text, "jarvis");
      speak(text);
      log.scrollTop = log.scrollHeight;
    }
  }

  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Authorization": "Bearer " + token, "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({ message: message, chat: chatId, stream: true, turn: activeTurn, files: files.map(function (item) { return { name: item.name, kind: item.kind }; }) })
    });
    if (response.status === 401) { localStorage.removeItem("jarvis_token"); throw new Error("Wrong or missing token."); }
    if (!response.ok || !response.body) {
      const data = await response.json().catch(function () { return {}; });
      throw new Error(data.error || ("HTTP " + response.status));
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let waiting = "", failure = null;
    while (!failure) {
      const step = await reader.read();
      if (step.done) break;
      waiting += decoder.decode(step.value, { stream: true });
      const lines = waiting.split("\\n");
      waiting = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        try { handle(JSON.parse(line)); } catch (error) { failure = error; break; }
      }
    }
    if (failure) throw failure;
    setStatus("ready");
  } catch (error) {
    if (stopping || error.name === "AbortError") {
      const partial = view.partial().trim();
      view.finish();
      if (partial) bubble(partial, "jarvis");
      const note = document.createElement("div");
      note.className = "meta";
      note.textContent = "stopped";
      log.appendChild(note);
      log.scrollTop = log.scrollHeight;
      setStatus("ready");
    } else {
      view.finish();
      fail(error.message);
    }
  }
  finally {
    sending = false;
    stopping = false;
    activeTurn = "";
    controller = null;
    setSendMode("send");
    setLocked(false);
  }
}

document.getElementById("send").onclick = function () {
  if (sending) { stopTurn(); return; }
  const value = textInput.value.trim();
  if (!value && !pending.length) return;
  textInput.value = "";
  send(value);
};
textInput.addEventListener("keydown", function (event) {
  if (event.key === "Enter" && !sending) { event.preventDefault(); document.getElementById("send").click(); }
});
document.getElementById("chats").onclick = function () { toggleDrawer(true); };
shade.onclick = function () { toggleDrawer(false); };
document.getElementById("new").onclick = async function () {
  try {
    const data = await api("/chats", { method: "POST" });
    chatId = data.id;
    localStorage.setItem("jarvis_chat", chatId);
    log.innerHTML = "<div class='meta'>Ask me anything.</div>";
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
const voiceButton = document.getElementById("voice");
voiceLabel();
if (voiceButton) voiceButton.onclick = function () {
  voiceOn = !voiceOn;
  localStorage.setItem("jarvis_voice", voiceOn ? "on" : "off");
  voiceLabel();
  if (!voiceOn && "speechSynthesis" in window) speechSynthesis.cancel();
};


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
        # One event per running turn, so /cancel can stop that turn mid-stream.
        self.cancels: Dict[str, threading.Event] = {}
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
        on_reasoning: Any = None,
        on_step: Any = None,
        on_token: Any = None,
        should_stop: Any = None,
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
            reply = self.agent.chat(
                content,
                model=model,
                on_reasoning=on_reasoning,
                on_step=on_step,
                on_token=on_token,
                should_stop=should_stop,
            )
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
    server_version = "Jarvis/0.3"
    protocol_version = "HTTP/1.1"
    _streaming = False

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

    # ------------------------------------------------------------ live answers

    def _start_stream(self) -> None:
        """Open a chunked response so the phone can watch the work happen.

        Length is unknown up front, so the body is sent in chunks: one JSON
        object per line, flushed as each piece arrives.
        """
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self._streaming = True

    def _event(self, payload: Any) -> None:
        if not self._streaming:
            return
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            self.wfile.write(("%x\r\n" % len(data)).encode("ascii") + data + b"\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            # The phone left - screen off, tunnel blip, reload. The turn keeps
            # going and the reply is still saved into the chat.
            self._streaming = False
            self.close_connection = True

    def _end_stream(self) -> None:
        if not self._streaming:
            return
        try:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True
        self._streaming = False

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
                    "version": __version__,
                    "model": self.server.agent.llm.model,
                    "endpoint": self.server.agent.llm.url,
                    "fallbacks": [entry[2] for entry in self.server.agent.llm.fallbacks],
                    "thinking": self.server.agent.llm.thinking or "vendor default",
                    "search": "exa" if os.environ.get("EXA_API_KEY", "").strip() else "scrapers",
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

        if parts.path == "/cancel":
            try:
                payload = json.loads(self._body().decode("utf-8", "replace") or "{}")
            except json.JSONDecodeError:
                payload = {}
            turn = str(payload.get("turn") or "")[:64] if isinstance(payload, dict) else ""
            event = self.server.cancels.get(turn) if turn else None
            if event is not None:
                event.set()
            self._json(200, {"cancelled": event is not None})
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
            turn = str(payload.get("turn") or "")[:64]
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
            if payload.get("stream"):
                # Events, one JSON object per line: think (chain of thought),
                # step (a tool being used), say (answer text), done, error.
                self._start_stream()
                cancel = threading.Event()
                if turn:
                    self.server.cancels[turn] = cancel
                try:
                    reply, chat_id = self.server.chat(
                        message,
                        chat_id,
                        files,
                        images,
                        vision or None,
                        on_reasoning=lambda text: self._event({"t": "think", "d": text}),
                        on_step=lambda text: self._event({"t": "step", "d": text}),
                        on_token=lambda text: self._event({"t": "say", "d": text}),
                        should_stop=cancel.is_set if turn else None,
                    )
                except Exception as exc:
                    self._event({"t": "error", "d": self._failure("chat", exc)})
                    self._end_stream()
                    return
                finally:
                    if turn:
                        self.server.cancels.pop(turn, None)
                self._event({"t": "done", "reply": reply, "chat": chat_id})
                self._end_stream()
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
    "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"
    "X/w5TcIAAAAASUVORK5CYII="
)
ICON_512 = base64.b64decode(
    "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"
    "RU5ErkJggg=="
)
