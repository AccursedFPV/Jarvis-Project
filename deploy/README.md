# Running Jarvis on a VPS

Use this while your own PC is unavailable. The VPS runs **Jarvis** (the agent,
tools, memory, skills, the phone API). It does **not** run the model: a
$7–10/month VPS has no GPU, and an 8B model on 2 vCPU generates a couple of
tokens per second — unusable for a tool-calling assistant. Point Jarvis at a
free-tier cloud model until your PC is back, then switch the endpoint to the PC.

## What to rent

| Provider | Plan | Specs | Price | Notes |
| --- | --- | --- | --- | --- |
| Hetzner | CX23 | 2 vCPU / 4 GB / 40 GB | ≈ €5.99 with IPv4 (~$7/mo) | Best value; hourly billing, cancel any time; EU locations |
| OVHcloud | VPS-1 | 2 vCore / 4 GB / 40 GB | from ~$4.54/mo | Cheapest 4 GB plan; US and EU regions |
| Vultr | Cloud Compute | 1 vCPU / 2 GB | $10/mo | US regions, hourly billing |
| Oracle Cloud | Ampere A1 (Always Free) | 2 OCPU / 12 GB | $0 | Free, but signup friction and idle instances can be reclaimed |

2 GB is the practical minimum if you want server-side speech-to-text
(`POST /voice`); 1 GB works for text only. Hourly billing matters here: if you
only need this until January, you can delete the server and pay for weeks, not
months — about $10–15 total.

Avoid 24-month prepay deals for a stopgap: the renewal price is usually well
above $10.

## Install

```bash
# on your computer, from the project folder
scp -r . root@<vps-ip>:/root/Jarvis
ssh root@<vps-ip>
cd /root/Jarvis && bash deploy/vps-install.sh
```

The installer creates a `jarvis` service user, installs into `/opt/jarvis` with
its own virtualenv (plus the `[stt]` extra, so `/voice` can transcribe there), generates
`/etc/jarvis.env` with a random token, installs
`contrib/jarvis-serve.service`, starts it, and configures `ufw` so port 8787 is
only reachable over `tailscale0`.

## Add a model

The VPS has no GPU, so use a free-tier OpenAI-compatible API while your PC is
away. Z.ai's `glm-4.7-flash` is priced at $0 per token:

```bash
nano /etc/jarvis.env
#   JARVIS_API_KEY=your-free-key
#   JARVIS_BASE_URL=https://api.z.ai/api/paas/v4
#   JARVIS_MODEL=glm-4.7-flash
systemctl restart jarvis-serve
journalctl -u jarvis-serve -n 20
```

Any OpenAI-compatible endpoint works (Gemini's OpenAI layer, Groq, OpenRouter
free models). Free tiers have rate limits — typically one request at a time —
so expect the occasional short wait when you fire off several tasks quickly.

## Reach it from the phone

1. On the VPS: `curl -fsSL https://tailscale.com/install.sh | sh && tailscale up`
2. Re-apply the firewall rule now that the interface exists:
   `ufw allow in on tailscale0 to any port 8787 proto tcp`
3. On the Pixel: install Tailscale, sign into the same account.
4. Find the VPS's Tailscale name: `tailscale status` (or the machines list).
5. On the phone open `http://<vps-name>:8787/?token=<token>` — the token is in
   `/etc/jarvis.env`. Add it to the home screen.

Tailscale keeps the server off the public internet entirely, and encrypts the
trip from your phone. Without it, 8787 is plain HTTP with a token — do not
expose that to the internet.

## When your PC arrives (January)

Two options:

- **Move it back home.** `scp` the state folder from the VPS
  (`/var/lib/jarvis`) to `~/.jarvis` on the PC, then run `jarvis --listen`
  locally. Delete the VPS.
- **Keep the VPS as the always-on brain.** Leave the server running, install
  Tailscale on the PC, and point the VPS at the PC's GPU model:

  ```bash
  # /etc/jarvis.env on the VPS
  JARVIS_BASE_URL=http://<pc-tailscale-name>:11434/v1
  JARVIS_MODEL=qwen3:14b
  JARVIS_API_KEY=local      # Ollama ignores it; Jarvis just needs a value here
  systemctl restart jarvis-serve
  ```

  Now the phone talks to the VPS, the VPS talks to your 5070 Ti at home, and
  you cancel the cloud model bill. When the PC is asleep you get a clear
  connection error instead of an answer — keep the cloud credentials around if
  you want to switch back for those times.

## Operations

```bash
systemctl status jarvis-serve     # is it up
journalctl -u jarvis-serve -f     # live logs (tool calls, approvals)
systemctl restart jarvis-serve    # after editing /etc/jarvis.env
```

Risky actions (shell commands, writes, deletes) ask for approval in the server's
terminal, which on a VPS is the journal — nobody is there to answer, so the
action is denied. For an autonomous server, set `auto_approve` in
`/var/lib/jarvis/config.json` or add `-y` to `ExecStart`. Do that only after you
are comfortable with what it can do.

## Other deploy paths

- **Rented GPU (RunPod / Vast.ai), no Docker needed:** `deploy/pod-bootstrap.sh`, documented in [gpu-cloud.md](gpu-cloud.md).
- **One container with Ollama + Jarvis:** `deploy/Dockerfile` and `deploy/docker-compose.yml`.
