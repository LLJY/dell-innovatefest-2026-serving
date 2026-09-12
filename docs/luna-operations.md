# Luna + OmniLion operations

The active hackathon stack is the Luna translator, PostgreSQL, LiteLLM, and one
native OmniLion vLLM process. OmniLion runs from the pinned host virtual
environment under a transient user-systemd unit; LiteLLM reaches it through the
Docker host gateway. Do not start the legacy combined Compose file or the
deferred standalone Qwen-ASR service. Keep `.env` private and replace all
example values.

## Start and inspect

For the guided live test, prepare `.env` with mode `0600` and set
`OPENAI_CREDENTIALS_HOST_DIR` to an absolute directory outside this repository.
Set `TRANSLATOR_UID=$(id -u)` and `TRANSLATOR_GID=$(id -g)` in `.env`; both the
login script and startup preflight verify ownership, and Compose runs the
translator with that identity so its `0700`/`0600` credentials remain usable.
Use the official Codex device-code flow to create the mode-`0600`
`refresh-token` and `account-id` files without running a browser on the GB10:

```sh
codex --version
scripts/luna-oauth-login.sh
scripts/luna-up.sh
scripts/omnilion-service.sh status
scripts/luna-create-key.sh
scripts/luna-smoke.sh
# Later, stop both Compose and native vLLM without deleting data volumes:
scripts/luna-down.sh
```

For an OmniLion-only deployment without the OpenAI translator or PostgreSQL,
use the standalone wrapper. Select either immutable public release; it downloads
the selected revision, installs its bundled vLLM plugin, and starts native vLLM
plus a stateless LiteLLM container:

```sh
scripts/omnilion-stack.sh up bf16
# Or use NVFP4 weights, BF16 activations, and vLLM's Marlin backend:
scripts/omnilion-stack.sh up nvfp4
scripts/omnilion-stack.sh status
scripts/omnilion-stack.sh down
```

The presets are pinned to:

```text
bf16   LLJYY/OmniLion@405ef8e1d21224edca0bdff6499389d4260c17e2
nvfp4  LLJYY/OmniLion-NVFP4-W4A16@cc2628352cf7eb76c93b992c4f3079f4b3bc9498
```

`up <variant>` inspects the active vLLM process and switches it when either the
repository or revision differs. Use `OMNILION_VARIANT=bf16|nvfp4` in `.env` for
the full Luna launcher. The `custom` variant preserves explicitly configured
`OMNILION_MODEL` and `OMNILION_REVISION` values.

Do not run the standalone wrapper on the same `LITELLM_HOST_PORT` as the full
Luna stack; choose another port in `.env` when both are needed concurrently.

`luna-oauth-login.sh` runs `codex login --device-auth`. Open the displayed URL
on another trusted device and enter the one-time code. The script uses a
temporary isolated `CODEX_HOME`, extracts only the refresh token and account ID,
stores them in the external credential directory, and deletes the temporary
Codex auth store. It never accepts or automates your ChatGPT password. Install a
current official Codex CLI first if `codex` is unavailable. Use
`scripts/luna-oauth-login.sh --replace` only to intentionally rotate existing
credentials.

The key script stores the deployment key under
`~/.config/gb10-serving/luna-deployment-key` with mode `0600` and never prints
it. Override `ENV_FILE`, `LUNA_URL`, `LUNA_KEY_FILE`, or `LUNA_KEY_DURATION` when
needed, but credential and key paths are rejected if they resolve inside the
repository. The smoke script makes live requests that consume subscription quota.

```sh
docker compose -f compose.luna.yml --env-file .env config --quiet
docker compose -f compose.luna.yml --env-file .env up -d
docker compose -f compose.luna.yml --env-file .env ps
curl -fsS http://127.0.0.1:4000/health/liveliness
curl -fsS -H "Authorization: Bearer $LITELLM_MASTER_KEY" http://127.0.0.1:4000/v1/models
```

The OmniLion service wrapper verifies that `OMNILION_BIND_HOST` is exactly
Docker's current default-bridge gateway and that `OMNILION_API_BASE` is
`http://host.docker.internal:<OMNILION_PORT>/v1`; arbitrary LAN addresses and
wildcards are rejected. Its `/v1` routes also require `OMNILION_API_KEY`.
LiteLLM is the only intended client-facing model port. Inspect or restart the
model without disturbing PostgreSQL:

```sh
scripts/omnilion-service.sh status
scripts/omnilion-service.sh logs
journalctl --user -u omnilion-vllm -n 200 --no-pager
scripts/omnilion-service.sh restart
```

A full load takes roughly three minutes for BF16 and four minutes for NVFP4 on
the GB10. The standalone
`scripts/omnilion-stack.sh` downloads `OMNILION_MODEL` at the exact
`OMNILION_REVISION` into the Hugging Face cache and installs the plugin wheel
bundled in that immutable snapshot; subsequent starts reuse the cache. Both
variants use BF16 activations, `max-model-len=8192`, one concurrent sequence,
65% GPU-memory utilization, and 30 video frames. NVFP4 execution selects Marlin
from the checkpoint metadata. Text, image, video, audio, and
one video plus one audio item are accepted. Through LiteLLM, use
`model="OmniLion"`; content parts follow the OpenAI-compatible vLLM shapes
`image_url`, `video_url`, and `input_audio`.

Only LiteLLM publishes port 4000; translator and PostgreSQL are internal.
Required private inputs are `POSTGRES_PASSWORD`, `LITELLM_MASTER_KEY`,
`LITELLM_SALT_KEY`, `TRANSLATOR_SERVICE_KEY`, `OMNILION_API_KEY`, and
`OPENAI_CREDENTIALS_HOST_DIR`. `LLJYY/OmniLion` is public, so its pinned BF16
revision needs no `HF_TOKEN`. Set a read-only token only for a future private
model derivative or where the Hub requires authenticated downloads.
`CLOUDFLARE_TUNNEL_TOKEN` is required only for the edge profile. The credential
`refresh-token` and `account-id`, be owned by `TRANSLATOR_UID:TRANSLATOR_GID`,
be writable by that identity for atomic OAuth token rotation, and remain outside
source control with host mode `0700` and file mode `0600`. `.env.example`
contains placeholders, not secrets.
The optional edge is separate and does not run in the core command:

```sh
docker compose -f compose.luna.yml --env-file .env --profile edge up -d cloudflared
```

Generate exactly one deployment key, with expiry, for OpenShift/clients. The
master key stays on GB10 and is never copied to a client or OpenShift:

```sh
curl -fsS http://127.0.0.1:4000/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H 'Content-Type: application/json' \
  -d '{"models":["gpt-5.6-sol","OmniLion"],"key_alias":"luna-deployment","user_id":"openshift","duration":"24h"}'
```

Per-team authentication remains in the external loop; do not create one LiteLLM
key per phone/team in this deployment.

## Request smoke tests

Set `LUNA_KEY` to the returned key (never put it in source control).

```sh
curl -fsS http://127.0.0.1:4000/v1/chat/completions -H "Authorization: Bearer $LUNA_KEY" -H 'Content-Type: application/json' -d '{"model":"gpt-5.6-sol","messages":[{"role":"user","content":"Reply with one short sentence."}]}'
curl -N http://127.0.0.1:4000/v1/chat/completions -H "Authorization: Bearer $LUNA_KEY" -H 'Content-Type: application/json' -d '{"model":"gpt-5.6-sol","stream":true,"messages":[{"role":"user","content":"Stream a short greeting."}]}'
curl -fsS http://127.0.0.1:4000/v1/chat/completions -H "Authorization: Bearer $LUNA_KEY" -H 'Content-Type: application/json' -d '{"model":"gpt-5.6-sol","messages":[{"role":"user","content":"Use the lookup tool."}],"tools":[{"type":"function","function":{"name":"lookup","description":"Look up a value","parameters":{"type":"object","properties":{"q":{"type":"string"}},"required":["q"]}}}]}'
```

The tool call is a relay round trip: Luna emits a function call; the external
loop executes approved tools and sends the tool output in a later request.
Translator never executes tools.

## Persistence, backup, and rotation

Restarting containers preserves LiteLLM keys and spend data in the
`luna-postgres-data` volume. Back up with `pg_dump` from the PostgreSQL
container before upgrades:

```sh
docker compose -f compose.luna.yml --env-file .env exec -T postgres \
  pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > luna-postgres.sql
```

A normal shutdown is `scripts/luna-down.sh`; it stops Compose and native
OmniLion while preserving named volumes. A reset is destructive: run
`scripts/luna-down.sh`, then `docker volume rm
gb10-serving_luna-postgres-data`, and start again; then generate one new
expiring key.

Rotate in this order: revoke/replace the OAuth grant and refresh-token file;
restart translator; replace the translator service key in `.env` and restart
the stack; replace the LiteLLM salt/master keys only with a planned key
reissuance; revoke the old deployment key and issue one replacement. Never log
or transmit the master key. Compose defaults `OPENAI_REFRESH_TOKEN_WRITE_FILE`
atomically replaces `/run/credentials/refresh-token` in the writable credential
directory. To require manual reseeding instead, set that variable empty and
change the credential mount to read-only in a local Compose override.

Live OAuth testing is operator-only, requires account authorization, and is
pending that authorization. Do not contact live OAuth as part of config checks.
