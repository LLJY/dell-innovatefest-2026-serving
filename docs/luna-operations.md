# Luna operations

This is the Luna-only stack. Do not start the legacy Compose file or any Qwen,
P21, vLLM, or audio service. Keep `.env` private and replace all example values.

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
scripts/luna-create-key.sh
scripts/luna-smoke.sh
```

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

Only LiteLLM publishes port 4000; translator and PostgreSQL are internal.
Required private inputs are `POSTGRES_PASSWORD`, `LITELLM_MASTER_KEY`,
`LITELLM_SALT_KEY`, `TRANSLATOR_SERVICE_KEY`,
`OPENAI_CREDENTIALS_HOST_DIR`, and (only for edge)
`CLOUDFLARE_TUNNEL_TOKEN`. The credential directory must contain a file named
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
  -d '{"models":["gpt-5.6-sol"],"key_alias":"luna-deployment","user_id":"openshift","duration":"24h"}'
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

A reset is destructive: `docker compose -f compose.luna.yml --env-file .env
down`, then `docker volume rm gb10-serving_luna-postgres-data`, and start
again; then generate one new expiring key.

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
