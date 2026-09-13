# Dell InnovateFest 2026 Serving

One OpenAI-compatible LiteLLM gateway for the Luna text model and the local
OmniLion omnimodal model on NVIDIA DGX Spark.

## Public API

The production gateway is:

```text
https://gb10.hoshinoht.dev
```

It exposes two deployment-key-scoped aliases:

| Alias | Endpoint | Backend |
| --- | --- | --- |
| `gpt-5.6-luna` | `POST /v1/chat/completions` | Luna translator to Codex Responses |
| `omnilion` | `POST /v1/chat/completions`, `POST /v1/audio/transcriptions` | Local OmniLion NVFP4 inference |

See [`docs/api.md`](docs/api.md) for client examples,
[`docs/luna-operations.md`](docs/luna-operations.md) for operator procedures,
and [`docs/omnilion-container.md`](docs/omnilion-container.md) for the dormant
container profile.

## Architecture

LiteLLM is the only public model gateway. PostgreSQL, the Luna translator, the
OmniLion adapter, and native vLLM remain private. The optional
Cloudflare Tunnel sends traffic to `http://litellm:4000`.

OmniLion is pinned to the public W4A16 NVFP4 release:

```text
repository: LLJYY/OmniLion-NVFP4-W4A16
revision: cc2628352cf7eb76c93b992c4f3079f4b3bc9498
release-manifest SHA-256: b69cec09f3e6dfbf713485eb637c2909ed43704791f4ecb208b0b69bb0e7d8e0
```

The runtime downloads that revision anonymously, verifies the manifest,
installs its bundled vLLM plugin, and binds native vLLM only to Docker's bridge
gateway. A private adapter proxies omnimodal Chat Completions and converts the
standard transcription request into the model's audio Chat Completions contract.

## Setup

Requirements:

- Docker with Compose
- NVIDIA DGX Spark or a compatible GB10 runtime
- a Python 3.12 virtual environment with the platform-specific vLLM 0.29.0 and
  PyTorch build
- Codex CLI credentials for the Luna translator

Create the protected environment file and replace every placeholder:

```sh
cp .env.example .env
chmod 600 .env
```

Keep OAuth credentials, LiteLLM keys, service keys, and the Hugging Face cache
outside the repository. The public OmniLion release does not need an HF token.

Start the core stack:

```sh
scripts/luna-oauth-login.sh
scripts/luna-up.sh
scripts/luna-create-key.sh
```

Start the Cloudflare edge as well:

```sh
./build.sh
```

Stop containers and native vLLM without deleting the PostgreSQL volume:

```sh
scripts/luna-down.sh
```

## Smoke tests

Luna's live smoke consumes subscription quota:

```sh
scripts/luna-smoke.sh
```

OmniLion smokes use local GPU inference. Their wrappers read keys from protected
files or pipe them through stdin; they do not put key values in process
arguments or print transcript content.

```sh
scripts/omnilion-backend-smoke.sh /path/to/audio.wav
scripts/omnilion-smoke.sh /path/to/audio.wav
scripts/luna-models-curl.sh
scripts/omnilion-curl-smoke.sh /path/to/audio.wav
```

Run through the public hostname by setting `LUNA_URL`:

```sh
LUNA_URL=https://gb10.hoshinoht.dev scripts/luna-models-curl.sh
LUNA_URL=https://gb10.hoshinoht.dev \
  scripts/omnilion-curl-smoke.sh /path/to/audio.wav
```

## Validation

```sh
bash -n build.sh scripts/*.sh
python3 -m py_compile scripts/*.py
docker compose -f compose.luna.yml --env-file .env config --quiet
git diff --check
```
