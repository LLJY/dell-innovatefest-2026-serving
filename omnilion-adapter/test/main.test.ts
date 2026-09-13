import { afterAll, expect, test } from "bun:test";

process.env.SERVICE_KEY = "test-service-key";
process.env.OMNILION_API_KEY = "test-backend-key";
process.env.OMNILION_API_BASE = "http://backend.test/v1";
process.env.OMNILION_SERVED_MODEL = "OmniLion";

const { handleRequest } = await import("../src/main.ts");
const originalFetch = globalThis.fetch;

afterAll(() => {
  globalThis.fetch = originalFetch;
});

function request(payload: unknown): Request {
  return new Request("http://adapter.test/v1/chat/completions", {
    method: "POST",
    headers: {
      Authorization: "Bearer test-service-key",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

test("proxies an omnimodal chat completion unchanged", async () => {
  const payload = {
    model: "OmniLion",
    messages: [{
      role: "user",
      content: [
        { type: "text", text: "What is happening across these inputs?" },
        { type: "input_audio", input_audio: { data: "YXVkaW8=", format: "wav" } },
        { type: "image_url", image_url: { url: "data:image/png;base64,aW1hZ2U=" } },
        { type: "video_url", video_url: { url: "data:video/mp4;base64,dmlkZW8=" } },
      ],
    }],
  };
  let forwarded: Request | undefined;
  globalThis.fetch = async (input, init) => {
    forwarded = new Request(input, init);
    return Response.json({ choices: [{ message: { content: "hello" } }] });
  };

  const response = await handleRequest(request(payload));

  expect(response.status).toBe(200);
  expect(await response.json()).toMatchObject({ choices: [{ message: { content: "hello" } }] });
  expect(forwarded?.url).toBe("http://backend.test/v1/chat/completions");
  expect(forwarded?.headers.get("authorization")).toBe("Bearer test-backend-key");
  expect(await forwarded?.json()).toEqual(payload);
});

test("preserves an OmniLion SSE chat completion response", async () => {
  const payload = {
    model: "OmniLion",
    messages: [{ role: "user", content: "Say hello." }],
    stream: true,
  };
  const stream = 'data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n';
  globalThis.fetch = async () => new Response(stream, {
    headers: { "Content-Type": "text/event-stream" },
  });

  const response = await handleRequest(request(payload));

  expect(response.status).toBe(200);
  expect(response.headers.get("content-type")).toContain("text/event-stream");
  expect(await response.text()).toBe(stream);
});

test("rejects remote media URLs before contacting vLLM", async () => {
  const remoteParts = [
    { type: "image_url", image_url: { url: "http://127.0.0.1/private" } },
    { image_url: "http://127.0.0.1/private" },
    { video_url: "https://example.invalid/private.mp4" },
    { audio_url: { url: "http://169.254.169.254/latest/meta-data" } },
    { type: "input_image", image_url: "http://localhost/private" },
  ];

  for (const part of remoteParts) {
    let backendCalled = false;
    globalThis.fetch = async () => {
      backendCalled = true;
      return Response.json({ choices: [] });
    };

    const response = await handleRequest(request({
      model: "OmniLion",
      messages: [{ role: "user", content: [part] }],
    }));

    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({
      error: { code: "remote_media_url_not_allowed" },
    });
    expect(backendCalled).toBe(false);
  }
});

test("forwards the original JSON bytes without rounding large integers", async () => {
  const raw = '{"model":"OmniLion","seed":9223372036854775807,"messages":[{"role":"user","content":"hello"}]}';
  let forwardedBody = "";
  globalThis.fetch = async (input, init) => {
    forwardedBody = await new Request(input, init).text();
    return Response.json({ choices: [] });
  };

  const response = await handleRequest(new Request("http://adapter.test/v1/chat/completions", {
    method: "POST",
    headers: {
      Authorization: "Bearer test-service-key",
      "Content-Type": "application/json",
    },
    body: raw,
  }));

  expect(response.status).toBe(200);
  expect(forwardedBody).toBe(raw);
});
