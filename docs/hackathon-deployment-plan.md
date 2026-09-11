# Hackathon deployment plan — models on GB10, agentic loop on OpenShift

**Current state (active): `compose.luna.yml` is the only active build.** The
Luna-only translator + PostgreSQL + LiteLLM stack is the operator path. The
Qwen/P21/vLLM/audio sections below are historical design and are **deferred**;
do not follow their commands or start those services now. Sibling-repository
agent-loop and tool execution remain deferred.

Legacy combined-stack status: design agreed, not built. Spike (`chat.ts`) proven live against the
ChatGPT subscription (200s pre-quota); declarative aliasing, lsp trial, and
quota reset (~18:15 UTC) tracked separately. §10–§14 expand the GB10 side to
a containerized LiteLLM front door (Qwen-ASR + subscription LLMs behind one
OpenAI-compatible surface), Cloudflare/domain-ready but LAN-first until cutover.
§15 locks the voice pipeline: `qwen-asr` is the audio entry point (audio + text
in), producing intent + instructions that hand down to the GPT model for toolcalls.
Split of responsibilities: the **tool loop lives on OpenShift** (Bun session,
all tools, WS to phones); **GB10 serves models only** and never executes tools.

## 1. Topology

```
phones (venue Wi-Fi / internet)
  │  WSS to OpenShift Route (team keys, WS frames — unchanged protocol)
  ▼
OpenShift — agent server pod(s) (Bun + Hono, SQLite, ALL tools)
  │  owns: WS sessions, barge-in, device suspend/resume, server-tool
  │  execution (LTA/OneMap/weather/events/places), stage composition,
  │  compaction, transcripts. Holds ONE secret: the LiteLLM virtual key
  │  (+ CF Access client creds in edge phase).
  │  egress only: https://gb10.hoshinoht.dev:443 + DNS (NetworkPolicy)
  ▼  Cloudflare edge (gb10.hoshinoht.dev) ── tunnel (outbound-only) ──▶ GB10
GB10 box (docker compose, one `gb10` net) ──┬── litellm:4000 (front door, ONLY published port)
                                            ├── translator:8787 (holds subscription refresh token)
                                            ├── vllm-asr:8000 (Qwen-ASR, compose-net only, no published ports)
                                            └── cloudflared (profile `edge`, disabled until domain cutover)
```

- **OpenShift runs the loop; GB10 runs the models.** No agent code, no tools,
  no SQLite, no WS state on the GB10. No model weights, no subscription
  refresh token, no vLLM key on OpenShift.
- The **GB10 holds the OAuth service** (evolved `chat.ts` → `translator`
  container): subscription credential exposed as OpenAI-compatible
  `/v1/chat/completions` + `/v1/responses` (§12). Never directly reachable;
  only LiteLLM talks to it over the compose network.
- **LiteLLM is the only listener** clients ever see: one OpenAI base URL for
  both model families (`qwen-asr*` → vLLM, `gpt-*`/`codex-*` → translator).
  Team keys become LiteLLM virtual keys; per-team budgets live here instead
  of hand-rolled buckets.
- The **OpenShift namespace only holds model-access secrets** (LiteLLM virtual
  key, plus CF Access client secret in edge phase, plus JWT-signing secret if
  teams still authenticate at the loop):
  cluster compromise ≠ subscription compromise. Phones never talk to GB10;
  GB10 never calls back into the cluster.
- The loop calls **only** LiteLLM (`:4000` LAN or `gb10.hoshinoht.dev` edge);
  LiteLLM calls translator/vLLM over the compose network. No vLLM key in
  OpenShift, no participant path to either backend directly.
- GB10 reaches the internet via Cloudflare Tunnel (`cloudflared`, outbound-only):
  no inbound firewall holes, no event-NAT port forwarding, domain-fronted at
  `gb10.hoshinoht.dev`. Tunnel is **defined now, started later** (§14):
  until cutover everything works over LAN/Tailscale against
  `http://<gb10>:4000/v1` with the same keys.

## 2. Request path (verified live)

- LAN/dev (now): `POST http://<gb10>:4000/v1/<route>` with
  `Authorization: Bearer <litellm-key>` ← teams/loop.
- Edge (later, no client change but base URL): `POST
  https://gb10.hoshinoht.dev/v1/<route>` ← teams/loop (TLS, edge auth below).
  Cutover is `docker compose --profile edge up -d cloudflared` + DNS; the app
  only swaps `MODEL_BASE_URL`.
- LiteLLM routing (config.yaml, §11):
  - `qwen-asr*` → `http://vllm:8000/v1` via `openai/` provider prefix
    (standard OpenAI-compatible passthrough — the documented vLLM route).
  - `gpt-*` / `codex-*` → `http://translator:8787/v1` via `openai/` provider
    prefix (translator looks like any other OpenAI-compatible upstream).
- Translator → `POST https://chatgpt.com/backend-api/codex/responses`
  - Headers: `Authorization: Bearer <access>`, `ChatGPT-Account-ID: <accountId>`
  - Body: `{model: <base-id>, input: [{type:message…}], store:false, stream:true}`
  - Backend-validated constraints: input must be a list, `store:false` mandatory,
    SSE streaming mandatory. Model IDs alias to base upstream IDs (`-1m`/`-fast`
    suffixes stripped — backend only knows base IDs).
- Translator/vLLM path stays inside the compose network; vLLM publishes no
  host ports in prod (`expose:` only).
- Qwen-ASR route shape (resolved from vLLM + Qwen docs, confirm live on our
  checkpoint per §7): vLLM serves Qwen3-ASR-1.7B behind **both**
  `POST /v1/audio/transcriptions` (multipart `file`+`model`, OpenAI SDK
  `client.audio.transcriptions.create`) **and** `POST /v1/chat/completions`
  with `audio_url` content parts. Default the loop to `/v1/audio/transcriptions`;
  keep `audio_url` chat as fallback for contextual/prompted ASR. Requires the
  `vllm[audio]` image variant. Sources: vLLM OpenAI-compatible server docs
  (Transcriptions API), vLLM Qwen3-ASR recipe (`vllm serve Qwen/Qwen3-ASR-1.7B`),
  QwenLM/Qwen3-ASR README (`qwen-asr-serve` wrapper).
- Voice pipeline (full contract in §15): audio (+ optional caption text) enters
  at `qwen-asr` for transcript → intent + instructions; the dell-innofest
  session then runs its EXISTING tool loop against `gpt-*` with that text.
  Text-only input skips ASR. Orchestration stays in the session (not the
  translator) because device tools (`get_current_location`,
  `confirm_understanding`) suspend on the phone's WebSocket — a server-side
  tool loop could never resolve them. The translator's `/v1/audio/*` helper
  covers ASR→intent only, never tool execution.

## 3. Authentication design

| Layer | Mechanism |
|---|---|
| Edge (Cloudflare) | Access service tokens (`CF-Access-Client-Id/Secret`); unauthenticated traffic dies before the tunnel |
| Loop → gateway | Short-lived HS256 JWTs (5-min TTL, per-team claims), minted by the loop which already authenticates teams; static service key as fallback only. Post-LiteLLM (§10): teams present LiteLLM virtual keys to `:4000`; loop-JWTminting is subsumed by LiteLLM budgets unless OpenShift still needs its own team boundary |
| Teams → loop | Random team keys, stored hashed (argon2/bcrypt), auto-expire at event end, one-command revocation (or LiteLLM virtual keys directly, if the loop is bypassed) |
| GB10 → backends | Subscription Bearer (in-memory only, translator only) + vLLM `--api-key` over compose network (no host ports) |

Deliberately **not** doing a custom AES key-exchange handshake: TLS already
provides wire confidentiality/integrity, and a handshake just relocates key
distribution. App-layer AES only matters for hiding plaintext from Cloudflare
itself (tunnel edge terminates TLS) — out of scope unless the threat model
grows; say so explicitly if it does.

## 4. Secrets management (strict)

- Subscription **refresh token**: enters GB10 via sealed/encrypted channel only
  (never git, images, logs, or chat). At rest: encrypted volume, `0600`, owned
  by the unprivileged gateway user. Never leaves the box; access tokens minted
  in memory.
- vLLM key: 256-bit random, in the gateway's local config (`0600`), localhost
  scope only.
- Service/JWT secrets: OpenShift `Secret` (etcd-encrypted), mounted to loop pods
  only. Tunnel token JSON: `0600` on GB10 alongside the subscription token.
- Logging: redacted structured logs on both sides (mirror Codex's redaction
  list: tokens, codes, verifiers, states). No credential ever in error bodies.
- Post-event order: **revoke OAuth grant** (`auth.openai.com/oauth/revoke`) →
  rotate service + Access tokens → wipe token store → delete secrets.

## 5. OpenShift side

- `Secret`: gateway service key + JWT-signing secret + (optional) CF Access
  client secret if the loop must present it (prefer edge-enforced instead).
- `NetworkPolicy`: default-deny; ingress to loop pods only; egress only
  `gb10.hoshinoht.dev` via Cloudflare IP ranges on 443 + DNS. (L3/L4 can't pin
  the hostname — TLS cert/SNI checks + service tokens do the real auth.)
- Deployment: resource limits (noisy-neighbor protection), non-root,
  read-only FS, pinned image digests.
- Quotas: per-team token buckets at the loop (Plus quota dies fast — one
  runaway loop ruins the event); loud 429-to-organizer behavior, with team
  identity + correlation IDs in audit logs.

## 6. GB10 side

- Docker Compose is the deployment unit (reuses the deleted Nemotron recipe's
  proven flags: `vllm/vllm-openai:<ver>-aarch64-cu130-ubuntu2404` image,
  `hf-cache` named volume, `ipc: host`, NVIDIA `driver: nvidia` reservation,
  `/v1/models` healthcheck with long `start_period`). Full service layout in §11.
- `cloudflared` is a compose service under `profiles: ["edge"]` (not started
  until domain cutover); tunnel ingress:
  `gb10.hoshinoht.dev → http://litellm:4000`, catch-all deny. See §14.
- Translator as unprivileged container (Bun, zero-dependency shape kept);
  vLLM with `--api-key` (compose-net scope only — loop never reaches it directly).
- Host firewall: allow Cloudflare-bound egress + key-only admin SSH; nothing
  inbound (tunnel needs none). LAN phase: allow event-NAT/Tailscale → `:4000` only.
- Prereqs: `hoshinoht.dev` zone on Cloudflare (edge phase); `HF_TOKEN` for the
  gated Qwen checkpoint; Docker + NVIDIA container toolkit on the Spark.

## 7. Verify before build day

1. Qwen-ASR live confirm on our checkpoint: `POST /v1/audio/transcriptions`
   via OpenAI SDK against `vllm serve <our-qwen>`; fall back to
   `/v1/chat/completions`+`audio_url` for prompted ASR. One long transcription
   end-to-end (shake out mid-stream reconnects, not bandwidth).
2. LiteLLM → both backends: `qwen-asr` transcription + `gpt-*` chat through
   `:4000` with a virtual key, streaming SSE intact.
3. Zone on Cloudflare + tunnel DNS working from event network (edge profile
   only; LAN path must already pass 1–2 before this).
4. Plus-quota headroom at event start (we 429'd ourselves in testing — check reset).
5. `docker compose config` renders with dummy secrets; `up vllm litellm
   translator` reaches `healthy` without the `edge` profile.

## 8. File inventory

- `chat.ts` — proven single-shot spike (prompt in, text out). Grows into the
  `translator` container (§12): + OpenAI-compatible `/v1/*` surface, + team-key
  auth (delegated to LiteLLM — translator trusts compose-net + service key),
  + quotas/audit (delegated to LiteLLM budgets/logs), + tool loop
  (`function_call` → dispatch → `function_call_output`), + OAuth refresh in memory.
- `docker-compose.yml` (to write) — §11 service layout; `cloudflared` gated
  behind `profiles: ["edge"]`.
- `litellm-config.yaml` (to write) — §11 routing + virtual keys + budgets.
- `translator/` (to write) — `Dockerfile` (bun, non-root) + evolved `chat.ts`.
- This plan — the thing you're reading.

## 9. Explicit non-goals

- No public-internet hardening (no enterprise IdP, no WAF tuning beyond edge defaults).
- No multi-tenancy beyond team keys; no billing/metering (LiteLLM budgets are
  quota guardrails, not invoicing).
- No HA for the GB10 (single box; bring a spare power cable and printed firewall rules).

## 10. Why LiteLLM front door + translator sidecar (not LiteLLM-alone, not mini-alone)

| Option | How it handles the two backends | Verdict |
|---|---|---|
| LiteLLM alone | Qwen-ASR works natively (`model: openai/<name>` + `api_base: http://vllm:8000/v1` — the documented OpenAI-compatible route). Subscription LLMs do **not**: LiteLLM has no provider for the Codex OAuth backend (`chatgpt.com/backend-api/codex/responses` + `Bearer` + `ChatGPT-Account-ID` + `store:false` + list-input + SSE). No config-only path. | Rejected alone |
| Mini server alone (evolved `chat.ts` as the only listener) | Works — full control over Codex quirks — but reimplements virtual keys, per-team budgets, spend/audit logs, retries/fallbacks by hand. | Viable fallback if LiteLLM fights us on event day |
| **Recommended: LiteLLM + translator sidecar** | Translator presents the subscription as a boring OpenAI-compatible upstream (`http://translator:8787/v1`); LiteLLM routes `qwen-asr*` → vLLM and `gpt-*` → translator with the same `openai/` provider shape. LiteLLM owns keys/budgets/logs; translator owns Codex protocol + OAuth refresh. | **Build this** |

Cutover risk is contained: if LiteLLM misbehaves, point the loop at
`http://translator:8787/v1` directly (same OpenAI shape, service key instead
of virtual key) and keep running mini-only.

## 11. Container layout (DGX Spark, LAN-first)

One compose project, one `gb10` bridge network. Only LiteLLM publishes a host
port. Compose sketch (exact pins at build time):

```yaml
services:
  vllm: # Custom Qwen ASR — no host ports in prod
    image: vllm/vllm-openai:v0.20.0-aarch64-cu130-ubuntu2404 # reuse Nemotron recipe pin
    command: ["<QWEN_ASR_HF_ID>", "--served-model-name", "qwen-asr",
      "--host", "0.0.0.0", "--port", "8000",
      "--gpu-memory-utilization", "0.5", # ASR-only box; LLM is remote — lower than Nemotron's 0.7
      "--trust-remote-code", "--api-key", "${VLLM_API_KEY}"]
    environment: { HF_TOKEN: ${HF_TOKEN:-} }
    expose: ["8000"]
    volumes: [hf-cache:/root/.cache/huggingface]
    ipc: host
    deploy: { resources: { reservations: { devices: [{ driver: nvidia, count: all, capabilities: [gpu] }] } } }
    healthcheck: { test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/v1/models')"], interval: 30s, timeout: 5s, retries: 10, start_period: 20m }
    restart: unless-stopped

  translator: # openai-auth Codex backend → OpenAI-compatible /v1/*
    build: ./translator # bun, non-root, evolved chat.ts
    command: ["bun", "gateway.ts"]
    environment:
      OPENAI_REFRESH_TOKEN_FILE: /run/secrets/refresh_token
      OPENAI_ACCOUNT_ID: ${OPENAI_ACCOUNT_ID:-}
      VLLM_API_KEY: ${VLLM_API_KEY:-} # only if translator ever proxies ASR directly (debug path)
      SERVICE_KEY: ${SERVICE_KEY:-} # compose-net trust between litellm → translator
    expose: ["8787"]
    secrets: [refresh_token]
    healthcheck: { test: ["CMD", "bun", "-e", "fetch('http://127.0.0.1:8787/healthz').then(r=>{if(!r.ok)process.exit(1)})"], interval: 15s, timeout: 5s, retries: 5 }
    restart: unless-stopped

  litellm: # ONLY published port; single OpenAI base URL for the loop
    image: ghcr.io/berriai/litellm:main-stable
    command: ["--config", "/app/config.yaml", "--port", "4000"]
    volumes: ["./litellm-config.yaml:/app/config.yaml:ro"]
    environment:
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY:-}
      TRANSLATOR_API_KEY: ${SERVICE_KEY:-} # forwarded as Bearer to translator
      VLLM_API_KEY: ${VLLM_API_KEY:-}
    ports: ["4000:4000"] # LAN phase: event-NAT/Tailscale → :4000. Edge phase: ONLY cloudflared dials this.
    depends_on: { translator: { condition: service_healthy }, vllm: { condition: service_healthy } }
    healthcheck: { test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4000/health')"], interval: 30s, timeout: 5s, retries: 5 }
    restart: unless-stopped

  cloudflared: # defined now, started later — do NOT bring up until domain cutover
    image: cloudflare/cloudflared:latest
    profiles: ["edge"]
    command: ["tunnel", "--no-autoupdate", "run", "--token", "${CLOUDFLARE_TUNNEL_TOKEN}"]
    depends_on: [litellm]
    restart: unless-stopped

volumes: { hf-cache: ~ }
secrets: { refresh_token: { file: ./secrets/refresh_token } }
```

LiteLLM config sketch (`litellm-config.yaml`):

```yaml
general_settings: { master_key: os.environ/LITELLM_MASTER_KEY }
model_list:
  - model_name: qwen-asr # loop sends model="qwen-asr"
    litellm_params: { model: openai/qwen-asr, api_base: http://vllm:8000/v1, api_key: os.environ/VLLM_API_KEY }
  - model_name: gpt-5.6-sol # loop's existing alias; translator strips to base ID upstream
    litellm_params: { model: openai/gpt-5.6-sol, api_base: http://translator:8787/v1, api_key: os.environ/TRANSLATOR_API_KEY }
  # add further gpt-*/codex-* aliases the same way — all point at the translator.
```

Notes:
- `QWEN_ASR_HF_ID` defaults to `Qwen/Qwen3-ASR-1.7B`; override for the custom
  checkpoint (HF ID or mounted local path). Custom fine-tunes of Qwen-Audio
  keep the same two vLLM routes, but re-run §7.1 against the exact weights.
- `vllm[audio]` is required for transcription input preprocessing — the base
  `vllm-openai` aarch64 image already ships audio extras; the pip fallback is
  `uv pip install "vllm[audio]==<pin>"` per the Nemotron script.
- Transcription timeouts: LiteLLM `general_settings.pass_through_request_timeout`
  / per-model `timeout` should be ≥600s for long audio; vLLM streams the result.
- GPU sizing: 1.7B ASR at `gpu-memory-utilization 0.5` leaves headroom for the
  OS + translator + litellm on the 128 GB unified pool; raise only if long-audio
  KV pressure shows up.

## 12. Translator contract (the evolved `chat.ts`)

Inbound (OpenAI-compatible, compose-net only + `SERVICE_KEY` Bearer):
- `GET /healthz` → `200 ok` (no auth; used by compose healthcheck).
- `POST /v1/chat/completions` (OpenAI chat shape) and `POST /v1/responses`
  (Responses shape) → translate to Codex `POST
  https://chatgpt.com/backend-api/codex/responses`, stream SSE back verbatim
  (LiteLLM passes streams through untouched).
- `POST /v1/audio/transcriptions` passthrough stays the primary ASR path
  (session calls it directly through LiteLLM for debug/transcript-alone).
  `POST /v1/audio/intent` helper (voice entry point, §15 — new): multipart
  `file` (wav/mp3/m4a/ogg/webm) + optional `context` text (the WS caption) +
  `model=qwen-asr` passthrough fields (`language`, `prompt`). Translator calls
  `http://vllm:8000/v1/audio/transcriptions` (fallback: chat `audio_url` for
  prompted ASR) and returns `{transcript, intent, instructions}` — then STOPS.
  Tool execution stays in the dell-innofest session (see §15 why): the helper
  never takes `tools` and never runs a `function_call` loop.
- Translation rules (all backend-validated in the spike): coerce `input` to a
  list, force `store:false`, force `stream:true`, strip `-1m`/`-fast` alias
  suffixes to base upstream IDs, attach `Authorization: Bearer <access>` +
  `ChatGPT-Account-ID`, refresh OAuth in memory on 401-once-then-retry.
- Translation rules (all backend-validated in the spike): coerce `input` to a
  list, force `store:false`, force `stream:true`, strip `-1m`/`-fast` alias
  suffixes to base upstream IDs, attach `Authorization: Bearer <access>` +
  `ChatGPT-Account-ID`, refresh OAuth in memory on 401-once-then-retry.
- Errors: never leak tokens/codes/verifiers (mirror Codex redaction list);
  map upstream 429 to `429` + `Retry-After` so LiteLLM budgets + loop backoff engage.
  ASR failure short-circuits before any GPT spend (return `502 asr_failed` with
  `transcript` empty + `error.code`); GPT failure returns transcript + intent
  alongside the error so the caller can retry text-only.

Secrets (unchanged strictness from §4): refresh token enters via
`secrets/refresh_token` (`0600`, never git/images/logs); access tokens only in
memory; `SERVICE_KEY`/`VLLM_API_KEY`/`LITELLM_MASTER_KEY` are 256-bit randoms in
`.env` (`0600`).

## 13. Secrets & env (compose)

`.env` (all `0600`, never committed; see `.env.example` at build time):

```
HF_TOKEN=                 # gated Qwen checkpoint read
VLLM_API_KEY=<256-bit>    # compose-net only
SERVICE_KEY=<256-bit>     # litellm → translator Bearer
LITELLM_MASTER_KEY=sk-..  # operator only; teams get virtual keys
OPENAI_ACCOUNT_ID=<id>    # from stored auth.json at setup
CLOUDFLARE_TUNNEL_TOKEN=  # empty until edge cutover; cloudflared stays down without it
```

`secrets/refresh_token` — the subscription refresh token, one line, no newline
issues. LiteLLM virtual keys per team are minted via the LiteLLM API/UI
(`master_key` required), budgeted (TPM/RPM + max spend), auto-expire at event end.

## 14. Cloudflare / domain readiness (build now, switch on later)

Readiness without activation:
- App code is base-URL agnostic: one `MODEL_BASE_URL` env (`http://<gb10>:4000/v1`
  now → `https://gb10.hoshinoht.dev/v1` later). Auth is always
  `Authorization: Bearer <litellm-virtual-key>`; the translator's `SERVICE_KEY`
  never leaves the box.
- LiteLLM binds `:4000` for both phases. Cloudflare ingress (when enabled):
  `gb10.hoshinoht.dev → http://litellm:4000`, catch-all deny. Tunnel is
  outbound-only (`cloudflared tunnel run --token`), so the host firewall stays
  closed inbound in both phases.
- Edge auth layers on top, doesn't replace: Cloudflare Access service tokens
  (`CF-Access-Client-Id/Secret`) kill unauthenticated traffic before the tunnel;
  LiteLLM virtual keys still enforce per-team identity/budgets behind it. The
  loop presents Access tokens only in the edge phase (prefer edge-enforced;
  fall back to loop-presented per §5).
- LAN bypass is intentional until cutover: requests without `CF-Access-*`
  headers are accepted on `:4000` (event-NAT/Tailscale scope). After cutover,
  optionally set LiteLLM `general_settings.allowed_ips` / firewall `:4000` to
  tunnel-only — one-line change, documented at build time, not now.
- Health separation: tunnel health hits `/health` on LiteLLM; compose health
  uses per-service `/healthz`/`/v1/models` internally. No tunnel dependency for
  LAN operation — `docker compose up vllm translator litellm` never starts
  `cloudflared` (missing `edge` profile + empty token = stays down).

Cutover checklist (later): zone `hoshinoht.dev` on Cloudflare → tunnel token
issued → `.env` filled → `docker compose --profile edge up -d cloudflared` →
  DNS `gb10.hoshinoht.dev` → tunnel → end-to-end `curl` from event network →
  swap `MODEL_BASE_URL` → lock down `:4000` ingress.

## 15. Voice pipeline — audio (+ text) → intent → GPT toolcalls

`qwen-asr` is the entry point. The box supports two input modalities; only audio
touches ASR. Read against `dell-innofest` as built: the Flutter client sends
`{t:'user_audio', data:<base64 16kHz mono WAV>}` (+ optional caption text) over
the existing WebSocket; the Bun session (`ws/session.ts`) owns the turn,
barge-in, device suspend/resume, compaction and persistence. This pipeline keeps
all of that intact and swaps ONLY what the model sees.

```
Flutter user_audio (wav b64 + caption?) ── WS, unchanged
  │  session.onUserMessage (audio branch)
  ▼
Stage 1 — session → POST :4000/v1/audio/intent (file=wav, context=caption)
  │  litellm → translator → vllm qwen-asr → {transcript, intent, instructions}
  ▼
Stage 2 — session runs its EXISTING streamText loop with model=gpt-*
  │  messages=[...(history), user(transcript + intent + instructions + caption)]
  │  tools=allTools (server + device + stage), stopWhen stepCountIs(10)
  ▼
WS frames to phone, unchanged: turn_start → text_delta/tool_call/tool_output → turn_end
text-only user_msg ──► skips Stage 1, straight to Stage 2 (unchanged code path)
```

Why orchestration stays in the session. Device tools (`get_current_location`,
`confirm_understanding`) suspend mid-turn on `session.callDevice()` and resolve
only when the phone replies over the SAME socket. A translator-side tool loop
has no socket to the phone, so it could serve server tools but would hang or
wrongly fail every device call. Hence: translator does ASR→intent (pure,
no tools); the session does intent→toolcalls (stateful, WS-bound). The extra
tunnel round trip (ASR, then streaming chat) is the cost of keeping barge-in,
`confirm_understanding` echo-back, viewport-aware `set_stage`/`say`, and the
30s device timeout working with zero Flutter changes.

Stage 1 — ASR + intent (`qwen-asr`, via translator helper):
- Transport: `POST /v1/audio/intent`, multipart `file` + optional `context`
  (caption) + `model=qwen-asr` + `language`/`prompt` passthrough. Exposed through
  LiteLLM as `pass_through_endpoints: path /v1/audio/intent →
  target http://translator:8787/v1/audio/intent` so teams keep one base URL.
  Raw `POST /v1/audio/transcriptions` on `qwen-asr` stays for debug.
- ASR backends (both vLLM-native for Qwen3-ASR): default
  `/v1/audio/transcriptions` (`file`+`model`); prompted fallback
  `/v1/chat/completions` with `audio_url` + system prompt
  "Transcribe, then emit JSON `{transcript, intent, instructions}`" for
  custom vocabulary / contextual ASR — this is where the custom checkpoint
  earns its keep over stock Whisper.
- Output schema (translator normalizes both routes):
  `{"transcript": str, "intent": str, "instructions": str, "language": str,
  "confidence": float|null}`. `intent` is a short verb (`check_bus`,
  `plan_route`, …); `instructions` is the actionable restatement GPT executes.
  Session appends `user: [transcript + intent/instructions + caption]` to history;
  the raw WAV is never added to GPT context — `replaceAudioParts()` placeholder
  (`[voice message, ~Ns]`) now covers the pre-Stage-1 message only.
- Limits: cap ~25 MB / ~10 min per call; longer audio chunks client-side.
  `422 empty_audio` (<0.5s) before GPU; ASR fail → `502 asr_failed`, no GPT
  spend; low-confidence/empty → `intent:"clarify"` so the loop asks back via
  `say` instead of firing tools.

Stage 2 — handoff to GPT toolcalls (unchanged session machinery):
- `model.ts` becomes dual-model on ONE base URL: `asrClient` (transcription
  helper, `model="qwen-asr"`) + `model` (tool loop, `model="gpt-5.6-sol"` or
  alias). Both point at `MODEL_BASE_URL=http://<gb10>:4000/v1`; only the model
  IDs differ. `provider.chat()` stays (Responses API is still unsupported).
- What simplifies: GPT now sees text only, so `AUDIO_INLINE_SYSTEM` (Voxtral
  system+audio rejection) and `AUDIO_FORCE_FIRST_TOOL` (spoken-turn tool refusal)
  become dead flags — leave defaulted ON for the native-audio fallback, force 0
  in pipeline mode. `prepareStep` audio-stripping after step 0 goes away; token
  estimates drop the 25 tok/s audio price on the GPT side (ASR latency replaces it).
- What stays: `set_stage`/`say` composition + pixel budget, device suspend/resume
  + 30s timeout + barge-in abort/steer, compaction/budget (now text-only, so
  windows go further), SQLite transcript + `[voice message]` placeholders,
  `confirm_understanding` gating (now confirms the transcript, which is more
  reliable than confirming heard audio).
- GPT 429 → bubble `429` + `Retry-After` with transcript+intent attached so the
  retry is text-only (no second ASR charge). Timeouts: ASR ≤120s, GPT ≤300s.

Agentic-side changes (dell-innofest, NOT this repo — listed so the gateway
contract doesn't drift):
1. `env.ts`: add `ASR_MODEL_NAME=qwen-asr` (+ optional `ASR_BASE_URL` defaulting
   to `MODEL_BASE_URL`); pipeline flag to switch audio branch.
2. `ws/session.ts` audio branch: call `/v1/audio/intent` first, then feed the
   returned text into the existing `streamOnce()` path. No frame changes.
3. `scripts/audio-smoke.ts`: new ladder gates the PIPELINE, not native audio —
   transcription reachable → intent JSON valid → streaming `gpt-*` tool deltas
   from transcribed text → full WS voice turn. Old native-audio ladder stays as
   the `--native` fallback while Voxtral/Nemotron paths exist.
4. `audio.ts` marker-WAV trick still works for offline tests (mock "hears" the
   marker at Stage 1, returns canned intent).

Verify additions (§7): pipeline turn end-to-end over LAN (`user_audio` →
intent JSON → `get_server_time` tool → `say`), then same over edge; barge-in
mid-Stage-2 still steers; device-timeout path still terminates the turn.
