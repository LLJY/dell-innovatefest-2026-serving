const serviceKey = requiredEnv("SERVICE_KEY");
const backendApiKey = requiredEnv("OMNILION_API_KEY");
const backendApiBase = requiredUrl("OMNILION_API_BASE");
const backendModel = process.env.OMNILION_SERVED_MODEL || "OmniLion";
const port = positiveInteger(process.env.PORT || "8790", "PORT");
const maxAudioBytes = positiveInteger(
  process.env.MAX_AUDIO_BYTES || String(25 * 1024 * 1024),
  "MAX_AUDIO_BYTES",
);
const backendTimeoutMs = positiveInteger(
  process.env.BACKEND_TIMEOUT_MS || "300000",
  "BACKEND_TIMEOUT_MS",
);

const extensionByMime = new Map([
  ["audio/flac", "flac"],
  ["audio/m4a", "m4a"],
  ["audio/mp3", "mp3"],
  ["audio/mp4", "mp4"],
  ["audio/mpeg", "mp3"],
  ["audio/ogg", "ogg"],
  ["audio/wav", "wav"],
  ["audio/wave", "wav"],
  ["audio/webm", "webm"],
  ["audio/x-m4a", "m4a"],
  ["audio/x-wav", "wav"],
]);

function requiredEnv(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
}

function requiredUrl(name: string): string {
  const value = requiredEnv(name).replace(/\/+$/, "");
  const parsed = new URL(value);
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error(`${name} must use http or https`);
  }
  return value;
}

function positiveInteger(value: string, name: string): number {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    throw new Error(`${name} must be a positive integer`);
  }
  return parsed;
}

function json(body: unknown, status = 200): Response {
  return Response.json(body, {
    status,
    headers: { "Cache-Control": "no-store" },
  });
}

function apiError(message: string, status: number, code: string): Response {
  return json({ error: { message, type: "invalid_request_error", code } }, status);
}

function isAuthorized(request: Request): boolean {
  return request.headers.get("authorization") === `Bearer ${serviceKey}`;
}

function audioFormat(file: File): string | null {
  const mime = file.type.toLowerCase().split(";", 1)[0];
  const fromMime = extensionByMime.get(mime);
  if (fromMime) return fromMime;
  const match = file.name.toLowerCase().match(/\.([a-z0-9]+)$/);
  const extension = match?.[1];
  return extension && ["flac", "m4a", "mp3", "mp4", "mpeg", "mpga", "ogg", "wav", "webm"].includes(extension)
    ? extension
    : null;
}

function transcriptionInstruction(form: FormData): string {
  const language = String(form.get("language") || "").trim().slice(0, 80);
  const prompt = String(form.get("prompt") || "").trim().slice(0, 1000);
  const parts = ["Transcribe this audio faithfully. Return only the transcription."];
  if (language) parts.push(`The requested language hint is: ${language}.`);
  if (prompt) parts.push(`Use this spelling/context hint when relevant: ${prompt}`);
  return parts.join(" ");
}

async function backendHealth(): Promise<boolean> {
  const healthUrl = `${backendApiBase.replace(/\/v1$/, "")}/health`;
  try {
    const response = await fetch(healthUrl, {
      headers: { Authorization: `Bearer ${backendApiKey}` },
      signal: AbortSignal.timeout(5000),
    });
    return response.ok;
  } catch {
    return false;
  }
}

async function transcribe(request: Request): Promise<Response> {
  if (!request.headers.get("content-type")?.toLowerCase().startsWith("multipart/form-data")) {
    return apiError("content-type must be multipart/form-data", 415, "unsupported_media_type");
  }

  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return apiError("invalid multipart form data", 400, "invalid_multipart");
  }

  const model = String(form.get("model") || "");
  if (model !== backendModel && model !== "omnilion") {
    return apiError(`unknown model: ${model || "(missing)"}`, 400, "model_not_found");
  }

  const file = form.get("file");
  if (!(file instanceof File)) {
    return apiError("file is required", 400, "missing_file");
  }
  if (file.size === 0) return apiError("file is empty", 400, "empty_file");
  if (file.size > maxAudioBytes) {
    return apiError(`file exceeds the ${maxAudioBytes}-byte limit`, 413, "file_too_large");
  }

  const format = audioFormat(file);
  if (!format) return apiError("unsupported audio format", 415, "unsupported_audio_format");
  const responseFormat = String(form.get("response_format") || "json");
  if (!["json", "text", "verbose_json"].includes(responseFormat)) {
    return apiError("response_format must be json, text, or verbose_json", 400, "unsupported_response_format");
  }

  const audio = Buffer.from(await file.arrayBuffer()).toString("base64");
  const body = {
    model: backendModel,
    messages: [{
      role: "user",
      content: [
        { type: "input_audio", input_audio: { data: audio, format } },
        { type: "text", text: transcriptionInstruction(form) },
      ],
    }],
    max_tokens: 512,
    temperature: 0,
    chat_template_kwargs: { enable_thinking: false },
  };

  let upstream: Response;
  try {
    upstream = await fetch(`${backendApiBase}/chat/completions`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${backendApiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(backendTimeoutMs),
    });
  } catch {
    return apiError("OmniLion backend is unavailable", 502, "backend_unavailable");
  }

  let result: any;
  try {
    result = await upstream.json();
  } catch {
    return apiError("OmniLion backend returned invalid JSON", 502, "invalid_backend_response");
  }
  if (!upstream.ok) {
    const message = result?.error?.message;
    return apiError(
      typeof message === "string" ? message : "OmniLion inference failed",
      502,
      "backend_error",
    );
  }

  const text = result?.choices?.[0]?.message?.content;
  if (typeof text !== "string" || !text.trim()) {
    return apiError("OmniLion returned an empty transcription", 502, "empty_transcription");
  }
  const transcription = text.trim();
  if (responseFormat === "text") {
    return new Response(transcription, {
      headers: { "Cache-Control": "no-store", "Content-Type": "text/plain; charset=utf-8" },
    });
  }
  return json({ text: transcription });
}

const server = Bun.serve({
  hostname: "0.0.0.0",
  port,
  maxRequestBodySize: maxAudioBytes + 1024 * 1024,
  async fetch(request) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/healthz") {
      return (await backendHealth()) ? json({ status: "ok" }) : json({ status: "unavailable" }, 503);
    }
    if (!isAuthorized(request)) return apiError("invalid API key", 401, "invalid_api_key");
    if (request.method === "GET" && url.pathname === "/v1/models") {
      return json({ object: "list", data: [{ id: backendModel, object: "model", owned_by: "local" }] });
    }
    if (request.method === "POST" && url.pathname === "/v1/audio/transcriptions") {
      return transcribe(request);
    }
    return apiError("not found", 404, "not_found");
  },
});

console.log(`OmniLion transcription adapter listening on port ${server.port}`);
