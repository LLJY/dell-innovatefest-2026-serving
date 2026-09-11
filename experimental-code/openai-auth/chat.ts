/**
 * SPIKE — minimal ChatGPT-subscription prompt sender (personal use).
 *
 * Reuses the OAuth tokens opencode2 already stores for YOUR account
 * (~/.local/share/opencode/auth.json). Read-only on that file; tokens are
 * kept in memory and never printed or written anywhere.
 *
 * Protocol reference: openai/codex (codex-rs/login: auth.openai.com OAuth
 * PKCE + refresh; codex-rs/model-provider: Bearer + ChatGPT-Account-ID
 * headers against chatgpt.com/backend-api/codex/responses).
 *
 * Usage:
 *   bun chat.ts "reply with exactly: hi" [--model openai/gpt-5.6-sol] [--raw]
 *
 * Note: subscription tokens in third-party clients are a ToS gray zone;
 * keep this to personal use.
 */

const ISSUER = "https://auth.openai.com";
const TOKEN_URL = `${ISSUER}/oauth/token`;
// Public Codex client ID (embedded in the open-source Codex CLI).
const CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann";
const CODEX_URL = "https://chatgpt.com/backend-api/codex/responses";

interface StoredAuth {
  refresh?: string;
  access?: string;
  expires?: number;
  accountId?: string;
}

async function loadStoredAuth(): Promise<StoredAuth> {
  const home = process.env.HOME;
  if (!home) throw new Error("HOME is not set");
  const raw = await Bun.file(`${home}/.local/share/opencode/auth.json`).text();
  const doc = JSON.parse(raw) as Record<string, StoredAuth>;
  const openai = doc.openai;
  if (!openai?.refresh) throw new Error("no stored openai oauth refresh token found");
  if (!openai.accountId) throw new Error("no stored openai accountId found");
  return openai;
}

async function refreshAccess(refreshToken: string): Promise<{ access?: string; refresh?: string }> {
  const res = await fetch(TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      client_id: CLIENT_ID,
      grant_type: "refresh_token",
      refresh_token: refreshToken,
    }),
    signal: AbortSignal.timeout(30_000),
  });
  if (!res.ok) throw new Error(`token refresh failed: HTTP ${res.status}`);
  const data = (await res.json()) as { access_token?: string; refresh_token?: string };
  if (!data.access_token) throw new Error("token refresh returned no access_token");
  return { access: data.access_token, refresh: data.refresh_token };
}

async function postPrompt(
  access: string,
  accountId: string,
  model: string,
  prompt: string,
): Promise<{ status: number; sse: string }> {
  const res = await fetch(CODEX_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      Authorization: `Bearer ${access}`,
      "ChatGPT-Account-ID": accountId,
    },
    body: JSON.stringify({
      model,
      // Codex requires input as a list of items (not a bare string),
      // store:false, and SSE streaming.
      input: [{ type: "message", role: "user", content: [{ type: "input_text", text: prompt }] }],
      store: false,
      stream: true,
    }),
    signal: AbortSignal.timeout(120_000),
  });
  const sse = await res.text();
  return { status: res.status, sse };
}

function extractText(body: unknown): string {
  const output = (body as { output?: unknown[] })?.output ?? [];
  const chunks: string[] = [];
  for (const item of output) {
    const rec = item as { type?: string; content?: { type?: string; text?: string }[] };
    if (rec.type !== "message" || !Array.isArray(rec.content)) continue;
    for (const part of rec.content) {
      if (part.type === "output_text" && typeof part.text === "string") chunks.push(part.text);
    }
  }
  return chunks.join("");
}

/** Collect assistant text from a Responses-API SSE stream. */
function extractStreamText(sse: string): string {
  const chunks: string[] = [];
  let completed: unknown = undefined;
  for (const line of sse.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed.startsWith("data:")) continue;
    const payload = trimmed.slice(5).trim();
    if (payload === "[DONE]" || payload === "") continue;
    let evt: Record<string, unknown>;
    try {
      evt = JSON.parse(payload) as Record<string, unknown>;
    } catch {
      continue;
    }
    if (evt.type === "response.output_text.delta" && typeof evt.delta === "string") {
      chunks.push(evt.delta);
    } else if (evt.type === "response.completed") {
      completed = (evt as { response?: unknown }).response;
    }
  }
  if (chunks.length > 0) return chunks.join("");
  if (completed) return extractText(completed);
  return "";
}

const args = process.argv.slice(2);
const rawMode = args.includes("--raw");
const modelFlag = args.indexOf("--model");
const model =
  modelFlag >= 0 && args[modelFlag + 1]
    ? args[modelFlag + 1]!
        .replace(/^openai\//, "")
        // Alias to the upstream ID like our V2 plugin does: -1m variants
        // don't exist backend-side, and neither do -fast IDs (the Codex
        // backend only knows base IDs; priority tier rides service_tier,
        // which this spike doesn't set).
        .replace(/-1m$/, "")
        .replace(/-fast$/, "")
    : "gpt-5.6-sol";
const skip = new Set<string>();
if (modelFlag >= 0 && args[modelFlag + 1] && !args[modelFlag + 1]!.startsWith("--")) {
  skip.add(args[modelFlag + 1]!);
}
const prompt = args.find((a) => !a.startsWith("--") && !skip.has(a));
if (!prompt) {
  console.error('usage: bun chat.ts "your prompt" [--model openai/gpt-5.6-sol] [--raw]');
  process.exit(2);
}

const stored = await loadStoredAuth();
let access = stored.access;
let refreshToken = stored.refresh!;
// Fast path: stored access token with >60s of life left (expires is epoch seconds).
if (!access || typeof stored.expires !== "number" || stored.expires * 1000 - Date.now() < 60_000) {
  const rotated = await refreshAccess(refreshToken);
  access = rotated.access!;
  if (rotated.refresh) refreshToken = rotated.refresh;
}

let attempt = await postPrompt(access!, stored.accountId!, model, prompt);
if (attempt.status === 401) {
  // Slow path: access was stale despite bookkeeping — refresh once and retry.
  const rotated = await refreshAccess(refreshToken);
  attempt = await postPrompt(rotated.access!, stored.accountId!, model, prompt);
}

if (attempt.status < 200 || attempt.status >= 300) {
  console.error(`request failed: HTTP ${attempt.status}`);
  console.error(attempt.sse.slice(0, 500));
  process.exit(1);
}
if (rawMode) {
  console.log(attempt.sse);
} else {
  const text = extractStreamText(attempt.sse);
  if (!text) {
    console.error("no message text in response; rerun with --raw to inspect");
    process.exit(1);
  }
  console.log(text);
}
