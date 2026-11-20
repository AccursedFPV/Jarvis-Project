# Running Jarvis on a rented GPU (RunPod / Vast.ai)

Yes — and this is the only cheap-ish way to have *your own model* while your PC
is unavailable. The catch is arithmetic: GPU rent is per hour, so "always on"
is expensive and "on when you use it" is affordable.

## The numbers (checked November 2026)

| GPU | RunPod Community | 24/7 for a month | 2 h a day |
| --- | --- | --- | --- |
| RTX A5000 24 GB | ~$0.16/hr | ~$117 | ~$10 |
| RTX 3090 24 GB | ~$0.22/hr | ~$161 | ~$13 |
| **RTX 4090 24 GB** | **~$0.34/hr** | **~$248** | **~$20** |
| RTX 5090 32 GB | ~$0.69/hr | ~$504 | ~$41 |

Add **$0.07/GB/month** for a network volume that keeps the model and your
memory between sessions: 40 GB ≈ $2.80/month. Billing is per second, so a
20-minute test costs about 11 cents.

Vast.ai is a marketplace: listings are usually cheaper than RunPod Community,
but price, reliability and disk vary by host, and interruptible instances can be
taken back mid-session. Pick an **on-demand** offer, not interruptible, if you
want it to stay up while you talk to it.

**Verdict for a ≤$10/month budget:** RunPod/Vast cannot be an always-on
assistant at that price. Two workable shapes:

- **Always-on, cheap:** small VPS ($5–7/mo, see `README.md`) + a free-tier model
  API. Phone access any time; the model is not yours.
- **GPU on demand:** RunPod pod at ~$0.34/hr while you use it. Model is fully
  yours, no per-token cost, nothing external.

You can run both and switch `JARVIS_BASE_URL` between them.

## Build the image

One container runs Ollama and Jarvis together (see `deploy/Dockerfile`), so a
pod is the whole system:

```bash
docker build -t yourname/jarvis:gpu -f deploy/Dockerfile .
docker push yourname/jarvis:gpu
```

Skip the push if you are running it locally or on a GPU VM with the source.

## RunPod

1. **Create a network volume** (Storage → New Network Volume), 40 GB, in the
   same region as the GPU you will rent. Standard tier is $0.07/GB/month.
2. **Deploy a Pod** → *Custom Image* → `yourname/jarvis:gpu` → GPU: RTX 4090
   (Community is fine and half the price) → attach the network volume, mount
   path `/data`.
3. In *Expose HTTP Ports* put `8787`.
4. Deploy. First boot pulls the model (~9 GB for `qwen3:14b`) into `/data`;
   watch it in the pod logs. Later starts reuse it.
5. Your URL is `https://<pod-id>-8787.proxy.runpod.net/?token=<token>` — the
   token is in the pod logs on first start and stored at
   `/data/server_token`. Open it on the Pixel and add it to the home screen.
6. **Stop the pod when you are done.** Storage keeps billing (~$2.80/month for
   40 GB), the GPU does not. Restart it and everything is where you left it.

Picking a model: 24 GB of VRAM comfortably runs `qwen3:14b`; `qwen3:8b` is
faster, `qwen3:32b` does not fit. Set `JARVIS_MODEL` in the pod environment to
change it.

## Vast.ai

1. Filter offers by GPU (RTX 4090), **on-demand**, disk ≥ 60 GB.
2. Image: `yourname/jarvis:gpu` (or `ollama/ollama` if you prefer to install
   Jarvis yourself in the instance via SSH).
3. On-start command / ports: map container port `8787` to a host port, e.g.
   `-p 8787:8787`; Vast gives you `<host-ip>:<port>`.
4. First boot pulls the model — disk must have room for ~10 GB.
5. Open `http://<host-ip>:<port>/?token=<token>` on the phone.

Two Vast-specific cautions: the direct IP:port is **plain HTTP**, so the token
travels unencrypted — put Tailscale on the instance and use its address
instead, or keep the sessions short and low-stakes. And instance disk is not
the same as a persistent volume; check the offer if you want memory to survive
a destroy.

## Costs you can actually keep

- Test with `qwen3:8b` (5 GB, faster pull) before committing to 14B.
- Stop the pod the moment you are done; use the console's stop button, not
  terminate, so the volume survives.
- Do not rent 24/7 for a personal assistant unless you are happy with
  $117–250/month. The VPS + free API path exists for exactly this reason.

## Security

The pod URL from RunPod is public HTTPS with token auth. Anyone with the token
can run commands on that pod — and that pod has your network volume. Treat the
token like a password, do not share the URL, and remember that a GPU pod has no
files from your real PC: the tools operate on the pod, not your home machine.
