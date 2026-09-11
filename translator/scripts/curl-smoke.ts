import { createGateway } from "../src/gateway";
import type { JsonObject } from "../src/types";

function sse(events: JsonObject[]): Response {
  return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""), { headers: { "Content-Type": "text/event-stream" } });
}

const upstream = Bun.serve({
  port: 0,
  async fetch(request) {
    const path = new URL(request.url).pathname;
    if (path === "/oauth/token") return Response.json({ access_token: "fixture-access", expires_in: 3600 });
    if (path !== "/responses") return new Response("not found", { status: 404 });
    const body = await request.json() as JsonObject;
    const tool = Array.isArray(body.tools);
    if (tool) {
      return sse([
        { type: "response.created", response: { id: "resp_smoke_tool", created_at: 1, model: "gpt-fixture" } },
        { type: "response.output_item.added", output_index: 0, item: { type: "function_call", call_id: "call_fixture", name: "fixture_tool", arguments: "" } },
        { type: "response.function_call_arguments.delta", output_index: 0, delta: "{\"ok\":true}" },
        { type: "response.completed", response: { id: "resp_smoke_tool", created_at: 1, model: "gpt-fixture", output: [{ type: "function_call", call_id: "call_fixture", name: "fixture_tool", arguments: "{\"ok\":true}" }] } },
      ]);
    }
    return sse([
      { type: "response.created", response: { id: "resp_smoke", created_at: 1, model: "gpt-fixture" } },
      { type: "response.output_text.delta", delta: "fixture hello" },
      { type: "response.completed", response: { id: "resp_smoke", created_at: 1, model: "gpt-fixture", output: [{ type: "message", content: [{ type: "output_text", text: "fixture hello" }] }] } },
    ]);
  },
});

const gateway = await createGateway({
  serviceKey: "fixture-service-key",
  codexBaseUrl: `http://127.0.0.1:${upstream.port}/responses`,
  oauthTokenUrl: `http://127.0.0.1:${upstream.port}/oauth/token`,
  oauthClientId: "fixture-client",
  refreshToken: "fixture-refresh-token",
  accountId: "fixture-account",
});
const server = Bun.serve({ port: 0, fetch: gateway.fetch });
const base = `http://127.0.0.1:${server.port}`;

async function curl(label: string, args: string[]): Promise<void> {
  const process = Bun.spawn(["curl", "--silent", "--show-error", ...args], { stdout: "pipe", stderr: "pipe" });
  const stdout = await new Response(process.stdout).text();
  const stderr = await new Response(process.stderr).text();
  const exitCode = await process.exited;
  console.log(`${label} (exit ${exitCode}):`);
  process.stdout && console.log(stdout.trimEnd());
  if (stderr) console.error(stderr.trimEnd());
  if (exitCode !== 0) throw new Error(`${label} curl failed with exit ${exitCode}`);
}

try {
  await curl("health", [`${base}/healthz`]);
  await curl("unauthorized", ["-o", "/dev/null", "-w", "%{http_code}", "-X", "POST", `${base}/v1/chat/completions`]);
  await curl("non-stream text", ["-H", "Authorization: Bearer fixture-service-key", "-H", "Content-Type: application/json", "-d", '{"model":"openai/gpt-fixture-fast","messages":[{"role":"user","content":"hello"}]}', `${base}/v1/chat/completions`]);
  await curl("stream tool call", ["-N", "-H", "Authorization: Bearer fixture-service-key", "-H", "Content-Type: application/json", "-d", '{"model":"gpt-fixture","stream":true,"messages":[{"role":"user","content":"call tool"}],"tools":[{"type":"function","function":{"name":"fixture_tool","parameters":{"type":"object"}}}]}', `${base}/v1/chat/completions`]);
} finally {
  server.stop(true);
  upstream.stop(true);
}
