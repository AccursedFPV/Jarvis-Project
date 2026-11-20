# Jarvis

A personal AI companion for your terminal — and it runs on your own computer. The model, your conversation, and everything Jarvis does with your files and apps stay on your machine. No account, no API key, no subscription, no telemetry.

Talk to it in plain language and it can look through your files, run commands, open apps and websites, and remember things about you — asking for approval before anything risky.

It is a single Python package with **no required dependencies** (voice input is an optional extra), so there is nothing to compile and no dependency tree to install.

## What it can do

- Chat with streaming replies from a model running locally
- **Files** — list, read, search by name or content, write, create folders, move, delete
- **Shell** — run commands from any folder and read their output
- **Apps and links** — open URLs, files and folders, launch installed applications
- **Web** — search the web and read pages when it needs to find something out
- **Plans** — break a multi-step task down, track the steps and finish them in order
- **Teaches itself** — writes new Python tools for itself and keeps what it learned
- **Memory** — store durable facts and preferences that are injected into every conversation
- **Sessions** — resume the previous conversation
- **Voice** — optional microphone input, or a full always-listening mode that wakes on "Hey Jarvis" and answers out loud

## Requirements

- Python 3.9 or newer
- [Ollama](https://ollama.com/download) (free) or another local OpenAI-compatible server
- About 6 GB of free RAM for the default 8B model; less for the smaller one. A GPU helps a lot but is not required.

## Install

```bash
git clone https://github.com/AccursedFPV/Jarvis-Project.git
cd Jarvis-Project
python -m pip install -e .
```

That gives you the `jarvis` command. You can also run it without installing:

```bash
python -m jarvis
```

## Run it on your own machine

Pull the model once (this is the only download):

```bash
ollama pull qwen3:8b
```

Then just start talking:

```bash
jarvis
```

Jarvis talks to Ollama at `http://127.0.0.1:11434/v1`, which is the default. No key, no sign-in. From then on everything works offline — the model file lives on your disk and inference happens on your hardware.

If nothing is listening yet, Jarvis tells you exactly what to run instead of failing with a network error, and it checks that the model you asked for is actually installed.

### Pick a model that fits your machine

| Model | Download | Good for |
| --- | --- | --- |
| `qwen3:8b` (default) | ~5 GB | Best balance of tool use and speed on a normal desktop |
| `qwen3:4b` | ~2.5 GB | Small laptops, older machines, CPU-only |
| `qwen3:14b` | ~9 GB | 12 GB+ of video memory and harder multi-step work |
| `qwen2.5-coder:7b` | ~4.5 GB | Reading and writing code |

Any Ollama model that supports **tool calling** works, which includes the Qwen 2.5/3, Llama 3.1+ and Mistral families. Choose one with:

```bash
jarvis --model qwen3:4b
```

or make it permanent in `~/.jarvis/config.json`:

```json
{ "model": "qwen3:4b" }
```

Using LM Studio, llama.cpp or vLLM instead? Point Jarvis at its OpenAI-compatible port:

```bash
export JARVIS_BASE_URL="http://127.0.0.1:1234/v1"
export JARVIS_MODEL="qwen3-8b"
```

## Use it

```bash
jarvis
```

```text
  J A R V I S  ·  personal AI companion
  qwen3:8b  ·  /home/you

you> what is eating space in my downloads folder?
  [list_files] /home/you/Downloads
jarvis> Your three biggest items are ...
```

Ask one question and exit:

```bash
jarvis "rename every screenshot in ~/Desktop to start with the date"
```

Resume where you left off:

```bash
jarvis --continue
```

Talk to it (needs the voice extra, see below):

```bash
jarvis --voice
```

Auto-approve risky actions for a single run:

```bash
jarvis -y "clean the package caches"
```

## It extends itself

Three things make it more than a chat window with a toolbelt:

- **It plans.** For anything longer than a couple of steps it writes a short plan (`update_plan`), ticks steps off (`complete_step`), and sees that plan in every turn until the task is done.
- **It researches.** `web_search` finds pages, `fetch_url` reads them, and it acts on what it read instead of guessing.
- **It builds what it is missing.** `write_skill` writes a Python tool into `~/.jarvis/skills/`. The tool appears as `skill_<name>` immediately — in that session and in every later one — with its own arguments. If the code is broken it gets the error back and fixes it, which is the self-programming loop.

It also remembers what worked. `learn` stores a short lesson, and lessons matching what you are asking for are put back into its prompt next time, so a repeated job should get faster.

```text
you> how much free space is left and what is using the most of it?

  [update_plan] 3 step(s)
  [write_skill] disk_usage
  [skill_disk_usage] disk_usage
  [complete_step] step 3
jarvis> 214 GB free of 1 TB. The biggest folder is ...
```

### What "learning" does and does not mean

It keeps **procedural memory** — lessons and tools, on your disk in `~/.jarvis` — and it can extend its own capabilities without you editing code. It does **not** retrain the model's weights. An 8B model on your PC does not get smarter in the neural-network sense from chatting, and no terminal program can do that in a session; what changes is what it knows how to do: new tools, remembered commands, and preferences it stored.

A skill is ordinary Python run with your permissions, and writing one always asks for approval first (`-y` skips that). Read them in `~/.jarvis/skills/` before trusting them; delete a bad one with `delete_skill` or by removing the file.

## In-conversation commands

| Command | Effect |
| --- | --- |
| `/help` | show command help |
| `/new` | forget the conversation and start fresh |
| `/memory` | list long-term memory |
| `/remember <fact>` | add a long-term memory |
| `/forget <fact>` | remove a long-term memory |
| `/tools` | list every action Jarvis can take, including skills |
| `/skills` | list the tools Jarvis wrote for itself |
| `/plan` | show the current task plan |
| `/learn <lesson>` | add a lesson by hand |
| `/lessons` | list learned lessons |
| `/model [name]` | show or change the model for this session |
| `/config` | show endpoint, model and approval mode |
| `/exit` | quit (also `/quit`, Ctrl+D) |

## What stays on your computer

Everything, with three deliberate exceptions you control:

- **`web_search` / `fetch_url`** — when it needs to find something out, it sends the search words to DuckDuckGo and the URL to the site it reads. Nothing else uses the network.
- **Skills it writes** — plain Python files in `~/.jarvis/skills` that run locally, like any script you approve.
- **`--voice`** — the bundled speech recognizer (`SpeechRecognition`) uses Google's online recognizer, so audio from your microphone goes to Google. `--listen` (below) is the fully local alternative: wake word, transcription and speech all run on your machine, and no audio is uploaded.
- **`open_target`** — opening a URL hands it to your browser, which then does whatever it normally does.

No accounts, no telemetry, no logging service, no usage reporting. Your conversation history and long-term memory are plain JSON files in `~/.jarvis` on your own disk. Delete that folder and Jarvis forgets everything.

## What it costs

Your electricity. The model and the program are free and open, and there is no server to pay for.

If you ever want a stronger model than your hardware can run, you can point Jarvis at a cloud endpoint instead (see below) — but that is optional and off by default.

## Safety

Writing, moving and deleting files, running shell commands, opening things and launching apps all trigger an approval prompt that shows the exact action:

```text
! Approval needed: delete /home/you/tmp/build and everything inside it
  allow this? [y]es / [N]o / [a]lways for this tool:
```

- `y` allows this once, `N` or Enter denies it, `a` allows that tool for the rest of the session.
- `-y` / `--yes` (or `"auto_approve": true` in the config) skips prompts for a fully hands-off run.
- Jarvis runs with your user account's permissions. It cannot do anything you could not do yourself, but review shell commands and deletions before approving them.
- The system prompt tells Jarvis never to store passwords or API keys in memory, but memory is a plain JSON file on disk — keep that in mind.

## Configuration

Jarvis reads `~/.jarvis/config.json` if it exists; environment variables win over the file.

```json
{
  "model": "qwen3:8b",
  "base_url": "http://127.0.0.1:11434/v1",
  "temperature": 0.4,
  "auto_approve": false,
  "speak": false
}
```

State is kept in `~/.jarvis`:

- `memory.json` — long-term facts
- `session.json` — last conversation for `--continue`
- `config.json` — optional settings

Set `JARVIS_HOME` to use a different folder. `jarvis --config` shows exactly what was resolved, including where the model is running.

## Voice

Install the optional speech-recognition dependency:

```bash
python -m pip install -e ".[voice]"
```

PyAudio is needed for microphone access:

- **Windows / macOS:** `python -m pip install pyaudio`
- **Debian/Ubuntu:** `sudo apt install portaudio19-dev && python -m pip install pyaudio`

Then run `jarvis --voice` to speak your messages and hear replies, or `jarvis --speak` to only hear replies. Text-to-speech uses `say` on macOS, SAPI on Windows, or `spd-say`/`espeak-ng` on Linux — always local. Speech-to-text is the one part that is not local: the recognizer sends audio to Google. Use `--listen` instead if that matters to you.

## Always listening: "Hey Jarvis"

This is the voice-assistant mode: it sits in the background, waits for the wake word, transcribes what you said on your own machine, runs the normal assistant loop (plans, tools, skills, memory) and answers out loud.

```bash
python -m pip install -e ".[listen]"
python -m pip install piper-tts      # optional: much nicer voice

# optional but recommended: a Piper voice (skip to use the OS voice)
mkdir -p ~/.jarvis/voices
# download en_US-lessac-medium.onnx (+ .json) from the Piper voices releases
# into ~/.jarvis/voices - Jarvis picks up the first .onnx it finds

jarvis --listen
```

First run downloads the wake-word model (~400 KB, `hey_jarvis`) and the speech model (`base.en`, ~75 MB); after that nothing else is downloaded and no audio leaves the machine.

Say **"Hey Jarvis"**, wait for *"Yes?"*, then talk. It stops recording when you pause, thinks, answers aloud, and goes back to waiting. Say "stop" or "goodbye" to make it quit listening. `Ctrl+C` also stops it.

Tuning in `~/.jarvis/config.json`:

```json
{
  "wake_word": "hey_jarvis",
  "wake_threshold": 0.5,
  "stt_model": "base.en",
  "piper_voice": "~/.jarvis/voices/en_US-lessac-medium.onnx"
}
```

- `wake_threshold` lower (0.35) hears you more easily but wakes on the TV more; higher (0.65) is stricter.
- Other wake words shipped with openWakeWord: `hey_mycroft`, `alexa`. `hey_jarvis` is the most reliable for this name.
- `stt_model`: `base.en` is fast on CPU; `small.en` is more accurate and slower; `tiny.en` is fastest.
- Overrides: `JARVIS_WAKE_WORD`, `JARVIS_WAKE_THRESHOLD`, `JARVIS_STT_MODEL`, `JARVIS_PIPER_VOICE`.

Risky actions still ask for approval. In `--listen` mode that prompt appears in the terminal the service was started from, so for a hands-free setup run with `-y` or set `"auto_approve": true` — and expect Jarvis to act immediately.

### Start it at login

Ready-made files are in `contrib/`:

- **Linux** — `contrib/jarvis-listen.service` (systemd user unit): copy to `~/.config/systemd/user/`, then `systemctl --user enable --now jarvis-listen`. Logs with `journalctl --user -u jarvis-listen -f`.
- **macOS** — `contrib/com.jarvis.listen.plist` (launchd agent): edit your username, copy to `~/Library/LaunchAgents/`, then `launchctl load` it. Approve the microphone prompt once.
- **Windows** — `contrib/jarvis-listen.vbs`: drop it in `shell:startup` to run hidden at login.

Expect roughly a second for the wake word and transcription, plus however long your model takes to think. Speech is synthesised after the reply is finished, so long answers start speaking a moment later.

## From your phone, anywhere

This is the setup where the PC is the brain and the phone is a remote control: same model, same files, same memory, same skills, one copy of everything on your PC.

**On the PC:**

```bash
jarvis --serve
```

It prints something like:

```text
Jarvis server listening on http://<this-pc>:8787
Phone: http://<this-pc>:8787/?token=3f9c...
```

**Reach it from outside your house** — use [Tailscale](https://tailscale.com) (free personal tier, encrypted, no port forwarding):

1. Install Tailscale on the PC and on the Pixel, signed into the same account.
2. Find the PC's Tailscale name (for example `desktop-abc123`) or its `100.x.y.z` address.
3. On the Pixel, open `http://desktop-abc123:8787/?token=<the token from the PC>`.
4. Menu → *Add to Home screen* so it opens like an app.

The token is stored in the phone's local storage after the first load, so the URL works normally afterwards. It lives in `~/.jarvis/server_token`; delete that file (or set `JARVIS_SERVER_TOKEN`) to change it. **Never port-forward 8787 to the internet** — Tailscale is the secure path, the token is the second lock.

**Talking to it from the phone:** type or attach a photo, and the answer comes back in the chat. When a turn finishes the phone reads it aloud with its own voice engine; the *Spoken replies* switch at the top of the Chats drawer turns that off. While Jarvis works, the chat shows a folding activity block — a *Thought* section, what it said on the way, and the searches or commands it ran, grouped like "Searched the web, read pages" — which closes to a single *Worked for 12s* line when the answer arrives. Links in a reply are tappable, whether or not they start with `https://`.

**What the phone can actually do:**

- Ask questions and get answers (the PC's model does the thinking).
- Ask for PC work — "find the biggest files in my downloads", "rename these photos", "start my dev server" — and the PC's tools run it. Risky actions still ask for approval **in the terminal on the PC**; run `jarvis --serve -y` (or set `"auto_approve": true`) for hands-free.
- Everything shares one conversation, one memory and one skills folder, so "who am I" or "what did we decide yesterday" works from either device.

**Limits worth knowing:**

- **The PC must be awake.** If it sleeps, the phone has nothing to talk to. Set Windows to stay awake, or enable Wake-on-LAN and wake it first. A ~$4–6/month VPS could hold the server for when the PC is off, but the 8B+ model still needs the PC's GPU — that's the real reason to keep the PC on.
- **Real "Hey Jarvis" on the phone needs an Android app.** A browser tab cannot listen in the background (and Android suspends the mic when the screen locks), so the web client is typed input with spoken replies, and the hold-to-talk microphone is parked for now. A small Android app with a foreground service could run the same `hey_jarvis` model on the phone and call this server — that is the next piece, and the API is already there for it (`POST /chat`, `POST /voice`, `GET /health`).
- Your conversation and memory now travel over your private network to your own PC. Still yours, not a third party's.

### Search that works from a server

Search engines treat a datacenter address as a bot. From a Hetzner box DuckDuckGo answers with an "anomaly" page and Bing answers with *Best Buy* and a dictionary entry, so the only engine left was Wikipedia — which happily "answered" *best cheap gaming mouse on Amazon* with articles about Amazon Luna and a video game. That fallthrough is gone: a shopping query never reaches Wikipedia now, and an engine that fails is rested for ten minutes instead of costing fifteen seconds on every retry. An honest *search is down* is better than five junk searches.

For search that actually works from the VPS, give it a key. Exa's free tier is about 2,800 searches, then $10 of credit a month, no card: <https://exa.ai>.

1. Create the key, then put it in the service (never in a file in this repo):

   ```bash
   sudo mkdir -p /etc/systemd/system/jarvis-serve.service.d
   sudo tee /etc/systemd/system/jarvis-serve.service.d/search.conf >/dev/null <<'EOF'
   [Service]
   Environment="EXA_API_KEY=paste-your-key-here"
   EOF
   sudo systemctl daemon-reload && sudo systemctl restart jarvis-serve
   ```

2. Prove it from the box:

   ```bash
   jarvis search "best earbuds"
   ```

   The `engine` line must say `exa`, and `curl /health` now reports `"search": "exa"` instead of `"scrapers"`.

### Run it on a VPS instead

While your own PC is unavailable, a small VPS can run Jarvis itself — the agent, tools, memory, skills and the phone API. It cannot run the model: a $7–10/month server has no GPU. Use a free-tier API for the thinking until your PC is back, then point `JARVIS_BASE_URL` at the PC's Ollama over Tailscale and cancel that bill.

Want the model itself hosted instead of a free API? A rented GPU (RunPod ~$0.34/hr for a 4090, Vast.ai marketplace, or a GPU VM) can run Ollama and Jarvis in one container — see **[deploy/gpu-cloud.md](deploy/gpu-cloud.md)** for the costs, which only make sense on demand rather than 24/7.

Full instructions, current prices and the January migration steps are in **[deploy/README.md](deploy/README.md)**. The short version:

```bash
scp -r . root@<vps-ip>:/root/Jarvis
ssh root@<vps-ip> && cd Jarvis && bash deploy/vps-install.sh
# then put a free API key in /etc/jarvis.env and: systemctl restart jarvis-serve
```

That gives you a systemd service, a generated token, `ufw` limited to Tailscale, and server-side Whisper for phone voice. Hetzner CX23 (2 vCPU / 4 GB, ~$7/mo, hourly billing) is the best value; OVH VPS-1 (~$4.54) is the cheapest 4 GB plan; Oracle's Always Free ARM tier is $0 if you can get through signup.

### A model for that GPU

An RTX 5070 Ti with 16 GB runs a much better model than the default. Pull a 14B and point Jarvis at it:

```bash
ollama pull qwen3:14b
export JARVIS_MODEL="qwen3:14b"
```

That model is noticeably better at multi-step tool use and coding than `qwen3:8b`, at roughly half the speed. Keep `qwen3:8b` on the phone path if replies feel slow.

## Optional: use a cloud model

Only if your machine cannot run a model you are happy with. Any OpenAI-compatible endpoint works; Z.ai's `glm-4.7-flash` is free per token.

```bash
export JARVIS_API_KEY="your-free-key"
export JARVIS_BASE_URL="https://api.z.ai/api/paas/v4"
export JARVIS_MODEL="glm-4.7-flash"
```

`ZAI_API_KEY` and `OPENAI_API_KEY` are accepted too. Remove `JARVIS_BASE_URL` to go back to the local model.

## How it works

Jarvis is a small agent loop:

1. Your message and a system prompt (including long-term memory) go to the model along with a JSON schema for every tool.
2. The model streams its reply. If it asks for tools, Jarvis approves and runs them, feeds the results back, and repeats.
3. The final text is streamed to your terminal, saved to the session file, and optionally spoken.

The model client, tool runner and UI are all in `jarvis/` and use only the Python standard library — the only network call is to the model endpoint you configured, which by default is your own machine.

## Troubleshooting

- **`No model server is answering at http://127.0.0.1:11434/v1`** — install Ollama and pull a model: `ollama pull qwen3:8b`. On Linux, `ollama serve` may need to be running.
- **`The model 'qwen3:8b' is not installed`** — run `ollama pull qwen3:8b`, or start Jarvis with `--model` naming a model you already have (`ollama list`).
- **Replies are very slow** — the model is bigger than your hardware likes. Try `qwen3:4b`, and close other heavy apps.
- **`Model API error 404`** — the base URL or model name is wrong. Defaults are `http://127.0.0.1:11434/v1` and `qwen3:8b`.
- **The model answers but never uses tools** — it does not support tool calling. Use a Qwen, Llama 3.1+ or Mistral model.
- **A skill does not load** — `/skills` marks it `BROKEN` with the reason. Ask Jarvis to fix it, or delete the file in `~/.jarvis/skills/`.
- **`web_search` returns nothing** — the search engine can rate-limit automated queries. Ask it to `fetch_url` a site directly, or rephrase.
- **No microphone** — install PyAudio for `--voice`, or `libportaudio2` plus the `listen` extra for `--listen`; or just type your messages.
- **`--listen` says a package is unavailable** — `python -m pip install -e ".[listen]"`. On Linux also `sudo apt install libportaudio2`.
- **It wakes itself up** — the microphone is muted while it thinks and speaks, but speakers near the mic can still leak. Use headphones or raise `wake_threshold`.
- **No sound from Piper** — check the `.onnx` is in `~/.jarvis/voices` or set `piper_voice`; without it Jarvis falls back to the OS voice.
- **No speech output** — install `espeak-ng` or `spd-say` on Linux; macOS and Windows have built-in voices.
