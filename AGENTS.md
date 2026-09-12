# Repository Instructions

## Active architecture

- Treat `compose.luna.yml` as the active deployment definition.
- The core stack is PostgreSQL, the Luna translator, the private OmniLion transcription adapter, and LiteLLM. `cloudflared` is enabled only through the `edge` Compose profile.
- LiteLLM is the only public model gateway. PostgreSQL, the translator, the OmniLion adapter, and native vLLM must remain private.
- The public text model is `gpt-5.6-luna`. Do not reintroduce `gpt-5.6-sol` into active configuration, scripts, or examples unless explicitly requested.
- The translator adapts OpenAI Chat Completions to the Codex Responses backend. It relays tool calls but never executes tools.
- The public ASR alias is `omnilion`. Native vLLM binds only to the Docker bridge, and the internal adapter converts `/v1/audio/transcriptions` to OmniLion's audio Chat Completions contract. Include `omnilion` in the deployment-key allowlist and never expose vLLM or the adapter directly.
- Treat the Qwen, legacy vLLM, audio, and combined-stack sections of `docs/hackathon-deployment-plan.md` as historical design.

## Secrets and access

- Never print, log, commit, or paste `.env`, OAuth tokens, Cloudflare tokens, LiteLLM keys, OmniLion service keys, or translator service keys.
- Keep `.env` at mode `0600` or `0400`.
- Keep OAuth credentials outside the repository. The directory must be mode `0700`; `refresh-token` and `account-id` must be mode `0600`.
- `LITELLM_MASTER_KEY` is administrator-only. `TRANSLATOR_SERVICE_KEY` and `OMNILION_API_KEY` are internal-only. External clients receive only an expiring LiteLLM deployment key.
- Preserve the network boundary: only LiteLLM publishes a host port. The Cloudflare origin service is `http://litellm:4000`.
- Do not revoke, rotate, remove, or display an existing key without confirming the exact target and impact.

## Working conventions

- Preserve unrelated user changes in the working tree.
- Prefer the existing scripts over duplicating setup or secret-handling logic.
- Use `./build.sh` to validate, build, and start the core plus Cloudflare Tunnel.
- Use `scripts/luna-oauth-login.sh` only as the configured non-root translator user; never run it with `sudo`.
- Use `scripts/luna-create-key.sh` to issue the client deployment key, `scripts/luna-smoke.sh` for Luna, and `scripts/omnilion-smoke.sh` for ASR.
- Live Luna smoke tests consume model quota. State that before running them. OmniLion smoke tests use only the local GPU.
- Keep Cloudflare routing and public API examples on `https://gb10.hoshinoht.dev` unless the deployment hostname is explicitly changed.

## Required checks

- For shell changes, run `bash -n build.sh scripts/*.sh` and `git diff --check`.
- For Compose or environment-shape changes, run `docker compose -f compose.luna.yml --env-file .env config --quiet` without printing the rendered config.
- For translator changes, run `bun run typecheck`, `bun test`, and `bun run smoke` from `translator/` when Bun development dependencies are available. The smoke test is fixture-only and must not contact live OAuth.
- For routing changes, verify `/health/liveliness`, authenticated `/v1/models`, and the affected endpoint. Never include secret values in command output or process arguments.
- Do not claim ASR is available until an authenticated `/v1/audio/transcriptions` request succeeds against the exact custom checkpoint.

## Code review rules

- Block changes that expose the translator, PostgreSQL, OAuth files, the OmniLion adapter, or native vLLM directly to the host or Internet.
- Block changes that put master/service credentials into client examples or source-controlled files.
- Flag model aliases that are not included in both `litellm-config.yaml` and the deployment-key model allowlist.
- Flag any translator code that dispatches tools; tool execution belongs to the external agent loop.
- Keep mock output clearly labelled and never present fixtures as live results.
