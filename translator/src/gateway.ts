import { CredentialError, CredentialManager } from "./credentials";
import {
  RequestError,
  chatChunk,
  chatCompletion,
  chatUsage,
  consumeResponseEvent,
  isResponseFailure,
  newCompletionState,
  parseChatRequest,
  toResponsesRequest,
} from "./protocol";
import type { CompletionState, GatewayConfig, JsonObject } from "./types";

const encoder = new TextEncoder();
const decoder = new TextDecoder();

export interface Gateway {
  fetch(request: Request): Promise<Response>;
}

export async function createGateway(config: GatewayConfig): Promise<Gateway> {
  if (!config.serviceKey) throw new CredentialError("missing SERVICE_KEY");
  const credentials = new CredentialManager(config);
  await credentials.initialize();

  return {
    async fetch(request: Request): Promise<Response> {
      const url = new URL(request.url);
      if (request.method === "GET" && url.pathname === "/healthz") return new Response("ok\n");
      if (url.pathname !== "/v1/chat/completions") return errorResponse(404, "not_found", "unsupported route");
      if (request.method !== "POST") return errorResponse(405, "method_not_allowed", "method not allowed");
      if (request.headers.get("authorization") !== `Bearer ${config.serviceKey}`) {
        return errorResponse(401, "invalid_api_key", "invalid service key");
      }
      let chat;
      try {
        chat = parseChatRequest(await request.json());
      } catch (error) {
        return requestErrorResponse(error);
      }
      let upstream: Response;
      try {
        upstream = await requestCodex(credentials, config, toResponsesRequest(chat), request.signal);
      } catch (error) {
        return gatewayErrorResponse(error);
      }
      if (!upstream.ok) return upstreamErrorResponse(upstream);
      if (!upstream.body) return errorResponse(502, "upstream_invalid_response", "upstream returned no response body");
      const state = newCompletionState(chat.model);
      return chat.stream ? streamingResponse(upstream.body, request.signal, state, chat.stream_options?.include_usage === true) : collectResponse(upstream.body, state);
    },
  };
}

async function requestCodex(credentials: CredentialManager, config: GatewayConfig, payload: JsonObject, signal: AbortSignal): Promise<Response> {
  let retried = false;
  while (true) {
    const access = await credentials.getAccessToken();
    let response: Response;
    try {
      response = await fetch(config.codexBaseUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
          Authorization: `Bearer ${access}`,
          "ChatGPT-Account-ID": credentials.getAccountId(),
        },
        body: JSON.stringify(payload),
        signal,
      });
    } catch (error) {
      if (signal.aborted) throw new Error("client disconnected");
      throw error;
    }
    if (response.status !== 401 || retried) return response;
    retried = true;
    await credentials.refresh();
  }
}

function streamingResponse(body: ReadableStream<Uint8Array>, requestSignal: AbortSignal, state: CompletionState, includeUsage: boolean): Response {
  const upstreamAbort = new AbortController();
  const abort = () => upstreamAbort.abort();
  requestSignal.addEventListener("abort", abort, { once: true });
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      try {
        for await (const event of parseSse(body, upstreamAbort.signal)) {
          if (isResponseFailure(event)) throw new Error("upstream response failed");
          for (const converted of consumeResponseEvent(state, event)) {
            if (converted.delta) controller.enqueue(sse(chatChunk(state, converted.delta)));
            if (converted.completed) {
              controller.enqueue(sse(chatChunk(state, {}, state.finishReason)));
              const usage = includeUsage ? chatUsage(state.usage) : undefined;
              if (usage) controller.enqueue(sse({ id: state.id, object: "chat.completion.chunk", created: state.created, model: state.model, choices: [], usage }));
            }
          }
        }
        controller.enqueue(sse("[DONE]"));
        controller.close();
      } catch (error) {
        if (!upstreamAbort.signal.aborted) controller.enqueue(sse({ error: structuredError(error) }));
        controller.enqueue(sse("[DONE]"));
        controller.close();
      } finally {
        requestSignal.removeEventListener("abort", abort);
      }
    },
    cancel() {
      upstreamAbort.abort();
      requestSignal.removeEventListener("abort", abort);
    },
  });
  return new Response(stream, { headers: sseHeaders() });
}

async function collectResponse(body: ReadableStream<Uint8Array>, state: CompletionState): Promise<Response> {
  try {
    for await (const event of parseSse(body)) {
      if (isResponseFailure(event)) throw new Error("upstream response failed");
      consumeResponseEvent(state, event);
    }
    return jsonResponse(200, chatCompletion(state));
  } catch (error) {
    return gatewayErrorResponse(error);
  }
}

async function* parseSse(body: ReadableStream<Uint8Array>, signal?: AbortSignal): AsyncGenerator<unknown> {
  const reader = body.getReader();
  let buffer = "";
  try {
    while (true) {
      if (signal?.aborted) throw new Error("client disconnected");
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = frame.split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
        if (!data || data === "[DONE]") continue;
        try {
          yield JSON.parse(data);
        } catch {
          // Ignore malformed event frames; the completed event remains authoritative.
        }
      }
    }
  } finally {
    if (signal?.aborted) await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

function sse(value: JsonObject | "[DONE]"): Uint8Array {
  return encoder.encode(`data: ${typeof value === "string" ? value : JSON.stringify(value)}\n\n`);
}

function sseHeaders(): HeadersInit {
  return { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache", Connection: "keep-alive" };
}

function jsonResponse(status: number, body: JsonObject): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function errorResponse(status: number, code: string, message: string, headers?: HeadersInit): Response {
  return jsonResponseWithHeaders(status, { error: { message, type: "gateway_error", param: null, code } }, headers);
}

function jsonResponseWithHeaders(status: number, body: JsonObject, headers?: HeadersInit): Response {
  const result = new Headers(headers);
  result.set("Content-Type", "application/json");
  return new Response(JSON.stringify(body), { status, headers: result });
}

function requestErrorResponse(error: unknown): Response {
  if (error instanceof RequestError) return errorResponse(400, error.code, error.message);
  return errorResponse(400, "invalid_json", "request body must be valid JSON");
}

function gatewayErrorResponse(error: unknown): Response {
  const disconnected = error instanceof Error && error.message === "client disconnected";
  return errorResponse(disconnected ? 499 : 502, disconnected ? "client_disconnected" : "upstream_unavailable", disconnected ? "client disconnected" : "upstream request failed");
}

function upstreamErrorResponse(upstream: Response): Response {
  const retryAfter = upstream.headers.get("retry-after");
  if (upstream.status === 429) return errorResponse(429, "rate_limit_exceeded", "upstream rate limit exceeded", retryAfter ? { "Retry-After": retryAfter } : undefined);
  if (upstream.status === 401) return errorResponse(502, "upstream_unauthorized", "upstream authorization failed");
  return errorResponse(502, "upstream_error", "upstream request failed");
}

function structuredError(error: unknown): JsonObject {
  const message = error instanceof Error ? error.message : "upstream request failed";
  return { message: redact(message), type: "gateway_error", code: "stream_error" };
}

function redact(value: string): string {
  return value
    .replace(/Bearer\s+\S+/gi, "Bearer [REDACTED]")
    .replace(/(access|refresh|service)[_-]?token\s*[=:]\s*[^\s,}"']+/gi, "$1_token=[REDACTED]");
}
