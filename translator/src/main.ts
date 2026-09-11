import { createGateway } from "./gateway";
import type { GatewayConfig } from "./types";

const DEFAULT_TOKEN_URL = "https://auth.openai.com/oauth/token";
const DEFAULT_CODEX_URL = "https://chatgpt.com/backend-api/codex/responses";
const CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann";

function configFromEnv(env: NodeJS.ProcessEnv = process.env): GatewayConfig {
  return {
    serviceKey: env.SERVICE_KEY ?? "",
    codexBaseUrl: env.CODEX_BASE_URL ?? DEFAULT_CODEX_URL,
    oauthTokenUrl: env.OPENAI_OAUTH_TOKEN_URL ?? DEFAULT_TOKEN_URL,
    oauthClientId: env.OPENAI_OAUTH_CLIENT_ID ?? CODEX_CLIENT_ID,
    refreshToken: env.OPENAI_REFRESH_TOKEN,
    refreshTokenFile: env.OPENAI_REFRESH_TOKEN_FILE,
    refreshTokenWriteFile: env.OPENAI_REFRESH_TOKEN_WRITE_FILE,
    accountId: env.OPENAI_ACCOUNT_ID,
    accountIdFile: env.OPENAI_ACCOUNT_ID_FILE,
  };
}

const gateway = await createGateway(configFromEnv());
const port = Number(process.env.PORT ?? "8787");
Bun.serve({ port, idleTimeout: 0, fetch: gateway.fetch });
