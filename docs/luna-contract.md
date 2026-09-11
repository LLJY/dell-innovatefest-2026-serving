# Luna contract

## Hops

| Hop | Route and trust boundary |
|---|---|
| Client/loop → LiteLLM | `POST /v1/chat/completions`, bearer deployment key |
| LiteLLM → translator | Compose network, `http://translator:8787/v1`, service key |
| Translator → Codex | OAuth access bearer and account ID, outbound HTTPS |
| Optional edge | cloudflared → LiteLLM only; token is an environment secret |

Translator converts OpenAI chat messages into Responses instructions/input,
converts function tools into Responses function tools, and converts Responses
SSE back to Chat Completions chunks. Function calls and tool outputs are
relayed; no component in this stack executes a tool.

## Errors and routes

Unauthenticated or wrong translator service credentials are `401`. Upstream
rate limits are `429` with `Retry-After` preserved where available. Other
upstream or translation failures are structured errors and never include
credentials. LiteLLM performs client-facing key authentication and may return
its normal `401`, `429`, or `5xx` responses.

Supported: `GET /healthz` internally, `POST /v1/chat/completions` (streaming
and non-streaming), function-tool declaration/relay, and LiteLLM `/v1/models`.
Unsupported: `/v1/responses`, audio routes, image/embedding routes, arbitrary
proxy routes, and tool execution.

The active build has no Qwen, P21, vLLM, or audio route. Those services and
routes, and sibling-repository agent-loop work, are deferred.
