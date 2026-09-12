# Luna Codex translator

This Bun sidecar accepts only OpenAI Chat Completions at `POST /v1/chat/completions` and adapts them to the ChatGPT Codex Responses backend. It is intended for the private compose network behind LiteLLM, not as a public service.

## Contract and boundaries

- `GET /healthz` is unauthenticated. The Chat Completions route requires an exact `Authorization: Bearer $SERVICE_KEY` before it contacts Codex. Every other route is `404`.
- The adapter sends `reasoning: { effort: "high" }`, `store:false`, and `stream:true` upstream, normalizes `openai/` prefixes and `-1m` / `-fast` suffixes, and adds OAuth Bearer plus `ChatGPT-Account-ID` headers.
- System and developer messages become Responses `instructions`. User/assistant messages, prior function calls, and tool outputs become Responses input items. OpenAI function tools become Responses function tools.
- It relays tool calls and tool outputs only. It never dispatches or executes a tool.
- Upstream Responses SSE becomes Chat Completions SSE (`chat.completion.chunk` and `[DONE]`), or an aggregated `chat.completion` JSON object when `stream` is absent/false.
- The only supported model route is Chat Completions. There is no `/v1/responses`, audio, or tool-execution endpoint.

## Environment

| Variable | Required | Purpose |
| --- | --- | --- |
| `SERVICE_KEY` | yes | Compose-network bearer key accepted from LiteLLM. |
| `OPENAI_REFRESH_TOKEN_FILE` or `OPENAI_REFRESH_TOKEN` | yes | Container-mounted refresh-token file (preferred) or explicitly supplied runtime secret. This code never reads an OpenCode auth file. |
| `OPENAI_ACCOUNT_ID` or `OPENAI_ACCOUNT_ID_FILE` | yes | ChatGPT account ID. |
| `CODEX_BASE_URL` | no | Responses endpoint; defaults to the ChatGPT Codex endpoint. Use a local fake upstream in tests. |
| `OPENAI_OAUTH_TOKEN_URL` | no | OAuth token endpoint override for tests. |
| `OPENAI_OAUTH_CLIENT_ID` | no | Codex OAuth client ID override. |
| `OPENAI_REFRESH_TOKEN_WRITE_FILE` | no | Writable credential-file target for atomic rotated-token persistence. |
| `PORT` | no | Listener port, default `8787`. |

The gateway refreshes access tokens on startup, caches access tokens only in memory, and performs one refresh/retry on an upstream `401`. If OAuth returns a rotated refresh token, it is retained in memory. It is atomically written only when `OPENAI_REFRESH_TOKEN_WRITE_FILE` is deliberately configured to a writable file; otherwise the operator must update the mounted secret before a restart.

Errors are structured and redact credentials. Upstream `429` is returned with its `Retry-After`. A client disconnect cancels the upstream response stream.

## Local checks

```sh
bun install --frozen-lockfile
bun run typecheck
bun test
bun run smoke
```

`bun run smoke` starts a fixture-only local OAuth/Responses server and this gateway, then makes real local `curl` requests for health, auth rejection, non-stream text, and streaming tool-call relay. It does not use live credentials or contact OpenAI.

## Container

`Dockerfile` uses the official ARM64-capable Bun image and switches to an unprivileged `gateway` user. Mounted credential files must be readable by that user; mount the refresh-token file read-only unless rotated-token persistence is intentionally enabled on a separate writable credential volume.
