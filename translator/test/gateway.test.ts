import { afterEach, describe, expect, test } from "bun:test";
import { mkdtemp, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createGateway } from "../src/gateway";
import type { GatewayConfig, JsonObject } from "../src/types";

type Scenario = "text" | "tool" | "unauthorized-once" | "rate-limit" | "failure";

interface FakeUpstream {
  url: string;
  state: { scenario: Scenario; oauthCalls: number; codexCalls: number; requests: JsonObject[]; headers: { authorization: string | null; accountId: string | null }[] };
  stop(): void;
}

const servers: ReturnType<typeof Bun.serve>[] = [];

afterEach(() => {
  while (servers.length) servers.pop()!.stop(true);
});

function sse(events: JsonObject[]): Response {
  return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""), {
    headers: { "Content-Type": "text/event-stream" },
  });
}

function textEvents(text = "hello"): JsonObject[] {
  return [
    { type: "response.created", response: { id: "resp_text", created_at: 1, model: "gpt-test" } },
    { type: "response.output_text.delta", delta: text },
    { type: "response.completed", response: { id: "resp_text", created_at: 1, model: "gpt-test", output: [{ type: "message", content: [{ type: "output_text", text }] }], usage: { input_tokens: 2, output_tokens: 1, total_tokens: 3 } } },
  ];
}

function toolEvents(): JsonObject[] {
  return [
    { type: "response.created", response: { id: "resp_tool", created_at: 2, model: "gpt-test" } },
    { type: "response.output_item.added", output_index: 0, item: { type: "function_call", id: "fc_1", call_id: "call_weather", name: "weather", arguments: "" } },
    { type: "response.function_call_arguments.delta", output_index: 0, delta: "{" },
    { type: "response.function_call_arguments.delta", output_index: 0, delta: "\"city\":" },
    { type: "response.function_call_arguments.done", output_index: 0, arguments: "{\"city\":\"Singapore\"}" },
    { type: "response.completed", response: { id: "resp_tool", created_at: 2, model: "gpt-test", output: [{ type: "function_call", id: "fc_1", call_id: "call_weather", name: "weather", arguments: "{\"city\":\"Singapore\"}" }] } },
  ];
}

function startFake(scenario: Scenario = "text"): FakeUpstream {
  const state = { scenario, oauthCalls: 0, codexCalls: 0, requests: [] as JsonObject[], headers: [] as { authorization: string | null; accountId: string | null }[] };
  const server = Bun.serve({
    port: 0,
    async fetch(request) {
      const path = new URL(request.url).pathname;
      if (path === "/oauth/token") {
        state.oauthCalls++;
        return Response.json({ access_token: `access-${state.oauthCalls}`, refresh_token: `refresh-${state.oauthCalls}`, expires_in: 3600 });
      }
      if (path !== "/responses") return new Response("not found", { status: 404 });
      state.codexCalls++;
      state.requests.push(await request.json() as JsonObject);
      state.headers.push({ authorization: request.headers.get("authorization"), accountId: request.headers.get("chatgpt-account-id") });
      if (state.scenario === "rate-limit") return new Response("too many", { status: 429, headers: { "Retry-After": "17" } });
      if (state.scenario === "failure") return new Response("access-secret refresh-secret service-secret", { status: 500 });
      if (state.scenario === "unauthorized-once" && state.codexCalls === 1) return new Response("stale", { status: 401 });
      return sse(state.scenario === "tool" ? toolEvents() : textEvents());
    },
  });
  servers.push(server);
  return { url: `http://127.0.0.1:${server.port}`, state, stop: () => server.stop(true) };
}

async function startGateway(fake: FakeUpstream, extra: Partial<GatewayConfig> = {}) {
  const gateway = await createGateway({
    serviceKey: "service-secret",
    codexBaseUrl: `${fake.url}/responses`,
    oauthTokenUrl: `${fake.url}/oauth/token`,
    oauthClientId: "test-client",
    refreshToken: "refresh-secret",
    accountId: "account-test",
    ...extra,
  });
  const server = Bun.serve({ port: 0, fetch: gateway.fetch });
  servers.push(server);
  return `http://127.0.0.1:${server.port}`;
}

function authHeaders(): HeadersInit {
  return { Authorization: "Bearer service-secret", "Content-Type": "application/json" };
}

function requestBody(overrides: JsonObject = {}): JsonObject {
  return { model: "openai/gpt-test-fast", messages: [{ role: "system", content: "be concise" }, { role: "user", content: "hi" }], ...overrides };
}

function streamedToolArguments(output: string): string {
  type Chunk = { choices?: { delta?: { tool_calls?: { function?: { arguments?: string } }[] } }[] };
  return output.split("\n\n")
    .filter((frame) => frame.startsWith("data: {")).map((frame) => JSON.parse(frame.slice(6)) as Chunk)
    .flatMap((chunk) => chunk.choices ?? [])
    .flatMap((choice) => choice.delta?.tool_calls ?? [])
    .map((call) => call.function?.arguments ?? "")
    .join("");
}

describe("Luna Chat Completions adapter", () => {
  test("health is public", async () => {
    const fake = startFake();
    const url = await startGateway(fake);
    const response = await fetch(`${url}/healthz`);
    expect(response.status).toBe(200);
    expect(await response.text()).toBe("ok\n");
  });

  test("wrong auth is rejected before upstream traffic", async () => {
    const fake = startFake();
    const url = await startGateway(fake);
    const response = await fetch(`${url}/v1/chat/completions`, { method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer wrong" }, body: JSON.stringify(requestBody()) });
    expect(response.status).toBe(401);
    expect(fake.state.codexCalls).toBe(0);
  });

  test("streams text as Chat Completions SSE", async () => {
    const fake = startFake();
    const url = await startGateway(fake);
    const response = await fetch(`${url}/v1/chat/completions`, { method: "POST", headers: authHeaders(), body: JSON.stringify(requestBody({ stream: true, stream_options: { include_usage: true } })) });
    const output = await response.text();
    expect(response.headers.get("content-type")).toContain("text/event-stream");
    expect(output).toContain('"object":"chat.completion.chunk"');
    expect(output).toContain('"content":"hello"');
    expect(output).toContain('"finish_reason":"stop"');
    expect(output).toContain('"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}');
    expect(output).toContain("data: [DONE]");
    expect(fake.state.requests[0]).toMatchObject({ model: "gpt-test", store: false, stream: true, instructions: "be concise" });
    expect(fake.state.headers[0]).toEqual({ authorization: "Bearer access-1", accountId: "account-test" });
  });

  test("aggregates text for non-stream callers", async () => {
    const fake = startFake();
    const url = await startGateway(fake);
    const response = await fetch(`${url}/v1/chat/completions`, { method: "POST", headers: authHeaders(), body: JSON.stringify(requestBody()) });
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({ object: "chat.completion", choices: [{ message: { content: "hello" }, finish_reason: "stop" }], usage: { prompt_tokens: 2, completion_tokens: 1, total_tokens: 3 } });
  });

  test("relays multi-turn function calls and tool outputs without executing", async () => {
    const fake = startFake();
    const url = await startGateway(fake);
    const body = requestBody({
      tools: [{ type: "function", function: { name: "weather", description: "weather", parameters: { type: "object" } } }],
      tool_choice: { type: "function", function: { name: "weather" } },
      messages: [
        { role: "developer", content: "use tools" },
        { role: "assistant", content: null, tool_calls: [{ id: "call_previous", type: "function", function: { name: "weather", arguments: "{\"city\":\"Paris\"}" } }] },
        { role: "tool", tool_call_id: "call_previous", content: "sunny" },
        { role: "user", content: "and tomorrow?" },
      ],
    });
    const response = await fetch(`${url}/v1/chat/completions`, { method: "POST", headers: authHeaders(), body: JSON.stringify(body) });
    expect(response.status).toBe(200);
    const sent = fake.state.requests[0]!;
    expect(sent.tools).toEqual([{ type: "function", name: "weather", description: "weather", parameters: { type: "object" } }]);
    expect(sent.tool_choice).toEqual({ type: "function", name: "weather" });
    expect(sent.input).toEqual(expect.arrayContaining([
      { type: "function_call", call_id: "call_previous", name: "weather", arguments: "{\"city\":\"Paris\"}" },
      { type: "function_call_output", call_id: "call_previous", output: "sunny" },
    ]));
  });

  test("converts streaming function calls with preserved IDs and arguments", async () => {
    const fake = startFake("tool");
    const url = await startGateway(fake);
    const response = await fetch(`${url}/v1/chat/completions`, { method: "POST", headers: authHeaders(), body: JSON.stringify(requestBody({ stream: true })) });
    const output = await response.text();
    expect(output).toContain('"id":"call_weather"');
    expect(output).toContain('"name":"weather"');
    expect(output).toContain('"finish_reason":"tool_calls"');
    expect(streamedToolArguments(output)).toBe("{\"city\":\"Singapore\"}");
  });

  test("refreshes once after an upstream 401", async () => {
    const fake = startFake("unauthorized-once");
    const url = await startGateway(fake);
    const response = await fetch(`${url}/v1/chat/completions`, { method: "POST", headers: authHeaders(), body: JSON.stringify(requestBody()) });
    expect(response.status).toBe(200);
    expect(fake.state.oauthCalls).toBe(2); // startup plus the one permitted 401 retry
    expect(fake.state.codexCalls).toBe(2);
  });

  test("propagates upstream 429 and Retry-After", async () => {
    const fake = startFake("rate-limit");
    const url = await startGateway(fake);
    const response = await fetch(`${url}/v1/chat/completions`, { method: "POST", headers: authHeaders(), body: JSON.stringify(requestBody()) });
    expect(response.status).toBe(429);
    expect(response.headers.get("retry-after")).toBe("17");
  });

  test("rejects malformed input cleanly", async () => {
    const fake = startFake();
    const url = await startGateway(fake);
    const response = await fetch(`${url}/v1/chat/completions`, { method: "POST", headers: authHeaders(), body: JSON.stringify({ model: "gpt-test", messages: "not-an-array" }) });
    expect(response.status).toBe(400);
    expect(fake.state.codexCalls).toBe(0);
  });

  test("redacts credentials from upstream failures", async () => {
    const fake = startFake("failure");
    const url = await startGateway(fake);
    const response = await fetch(`${url}/v1/chat/completions`, { method: "POST", headers: authHeaders(), body: JSON.stringify(requestBody()) });
    const body = await response.text();
    expect(response.status).toBe(502);
    expect(body).not.toContain("access-secret");
    expect(body).not.toContain("refresh-secret");
    expect(body).not.toContain("service-secret");
  });

  test("loads container credential files and atomically persists a rotated refresh token when configured", async () => {
    const fake = startFake();
    const directory = await mkdtemp(join(tmpdir(), "luna-translator-"));
    const refreshFile = join(directory, "refresh");
    const accountFile = join(directory, "account");
    const rotatedFile = join(directory, "rotated-refresh");
    await Bun.write(refreshFile, "refresh-from-file\n");
    await Bun.write(accountFile, "account-from-file\n");
    try {
      await createGateway({
        serviceKey: "service-secret",
        codexBaseUrl: `${fake.url}/responses`,
        oauthTokenUrl: `${fake.url}/oauth/token`,
        oauthClientId: "test-client",
        refreshTokenFile: refreshFile,
        refreshTokenWriteFile: rotatedFile,
        accountIdFile: accountFile,
      });
      expect(await Bun.file(rotatedFile).text()).toBe("refresh-1\n");
      expect((await stat(rotatedFile)).mode & 0o777).toBe(0o600);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });
});
