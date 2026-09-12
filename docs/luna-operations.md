# Luna and OmniLion operations

This stack serves `gpt-5.6-luna` and the pinned public OmniLion NVFP4 W4A16 model
through one LiteLLM endpoint. Do not start the legacy Compose file or historical
Qwen/P21 services. Keep `.env` private and replace all example values.

Client-facing behavior is documented in [`api.md`](api.md).

## Start and inspect

Prepare `.env` with mode `0600`. Set `OPENAI_CREDENTIALS_HOST_DIR` to an
absolute directory outside this repository, `TRANSLATOR_UID=$(id -u)`, and
`TRANSLATOR_GID=$(id -g)`. Set `OMNILION_VENV` to the verified vLLM 0.29.0
environment and `HF_HOME` to an absolute cache directory outside the repository.

```sh
codex --version
scripts/luna-oauth-login.sh
scripts/luna-up.sh
scripts/luna-create-key.sh
```

`luna-oauth-login.sh` runs the official `codex login --device-auth` flow. It
uses a temporary isolated `CODEX_HOME`, extracts only the refresh token and
account ID, stores them in the external credential directory, and deletes the
temporary auth store. Never run it with `sudo`.

The key script stores the deployment key under
`~/.config/gb10-serving/luna-deployment-key` with mode `0600` and never prints
it. Credential and key paths are rejected if they resolve inside the repository.

```sh
docker compose -f compose.luna.yml --env-file .env config --quiet
docker compose -f compose.luna.yml --env-file .env ps
curl -fsS http://127.0.0.1:4000/health/liveliness
```

Only LiteLLM publishes port 4000. PostgreSQL, the translator, and the OmniLion
adapter expose ports only on the Compose network. Native OmniLion vLLM binds to
Docker's current default bridge gateway at port 8002 and requires its internal
key. Startup rejects wildcard, loopback, and arbitrary LAN bindings, and also
requires the adapter URL to be `http://host.docker.internal:8002/v1`.
Cloudflare uses `http://litellm:4000` as its origin.

Start the optional edge separately:

```sh
docker compose -f compose.luna.yml --env-file .env --profile edge up -d cloudflared
```

## OmniLion runtime

`scripts/luna-up.sh` invokes `scripts/omnilion-runtime.sh up`. It downloads the
exact anonymous public Hub revision into `HF_HOME`, verifies the release-manifest
SHA-256, installs the bundled plugin into `OMNILION_VENV`, and manages native
vLLM through the non-root user systemd manager.

```text
model: LLJYY/OmniLion-NVFP4-W4A16
revision: cc2628352cf7eb76c93b992c4f3079f4b3bc9498
manifest SHA-256: b69cec09f3e6dfbf713485eb637c2909ed43704791f4ecb208b0b69bb0e7d8e0
```

The virtualenv must contain the platform-specific vLLM/PyTorch build and the
vLLM audio dependencies `av`, `scipy`, `soundfile`, and `soxr`. No Hugging Face
token is used.

```sh
scripts/omnilion-runtime.sh status
scripts/omnilion-runtime.sh logs
scripts/omnilion-runtime.sh down
```

## Keys and smoke tests

New deployment keys allow `gpt-5.6-luna` and `omnilion`. Add OmniLion to an
existing deployment key in place with:

```sh
scripts/luna-allow-omnilion.sh
```

The wrapper pipes the master and deployment keys to Python through stdin and
removes both variables from the Python process environment. It preserves every
existing model permission and adds the two active aliases.

The Luna smoke makes live requests that consume subscription quota:

```sh
scripts/luna-smoke.sh
```

The OmniLion tests use only the local GPU. They pipe their key through stdin,
remove it from the Python process environment, and never print transcription
content:

```sh
scripts/omnilion-backend-smoke.sh /path/to/audio.wav
scripts/omnilion-smoke.sh /path/to/audio.wav
LUNA_URL=https://gb10.hoshinoht.dev scripts/omnilion-smoke.sh /path/to/audio.wav
```

Safe curl-based model discovery and transcription checks are documented in
[`api.md`](api.md).

## Persistence, backup, and rotation

Stop both layers without deleting the PostgreSQL volume:

```sh
scripts/luna-down.sh
```

Restarting containers preserves LiteLLM keys and spend data in the
`luna-postgres-data` volume. Back up with `pg_dump` from PostgreSQL before
upgrades. Never write a dump containing production data into source control.

A database reset is destructive: confirm the exact volume and impact, stop the
stack, remove only the `luna-postgres-data` volume, start again, and issue a
replacement deployment key.

Rotate in this order: revoke/replace the OAuth grant and refresh-token file;
restart translator; replace the translator and OmniLion service keys and
restart their services; replace LiteLLM salt/master keys only with a planned key
reissuance; revoke the old deployment key and issue one replacement. Never log
or transmit the master key.
