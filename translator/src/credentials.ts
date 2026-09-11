import { dirname, basename, join } from "node:path";
import { open, rename, unlink } from "node:fs/promises";
import type { GatewayConfig } from "./types";

const REFRESH_SKEW_MS = 60_000;

export class CredentialError extends Error {}

export class CredentialManager {
  private refreshToken = "";
  private accessToken = "";
  private expiresAt = 0;
  private accountId = "";
  private refreshing?: Promise<void>;

  constructor(private readonly config: GatewayConfig) {}

  async initialize(): Promise<void> {
    this.refreshToken = await this.readSecret(this.config.refreshToken, this.config.refreshTokenFile, "refresh token");
    this.accountId = await this.readSecret(this.config.accountId, this.config.accountIdFile, "account id");
    await this.refresh();
  }

  getAccountId(): string {
    return this.accountId;
  }

  async getAccessToken(): Promise<string> {
    if (!this.accessToken || this.expiresAt - Date.now() < REFRESH_SKEW_MS) await this.refresh();
    return this.accessToken;
  }

  async refresh(): Promise<void> {
    if (!this.refreshing) {
      this.refreshing = this.refreshInner().finally(() => {
        this.refreshing = undefined;
      });
    }
    await this.refreshing;
  }

  private async refreshInner(): Promise<void> {
    let response: Response;
    try {
      response = await fetch(this.config.oauthTokenUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          client_id: this.config.oauthClientId,
          grant_type: "refresh_token",
          refresh_token: this.refreshToken,
        }),
        signal: AbortSignal.timeout(30_000),
      });
    } catch {
      throw new CredentialError("OAuth token refresh request failed");
    }
    if (!response.ok) throw new CredentialError(`OAuth token refresh failed (HTTP ${response.status})`);
    let body: Record<string, unknown>;
    try {
      body = (await response.json()) as Record<string, unknown>;
    } catch {
      throw new CredentialError("OAuth token refresh returned invalid JSON");
    }
    if (typeof body.access_token !== "string" || body.access_token.length === 0) {
      throw new CredentialError("OAuth token refresh returned no access token");
    }
    this.accessToken = body.access_token;
    const expiresIn = typeof body.expires_in === "number" && body.expires_in > 0 ? body.expires_in : 300;
    this.expiresAt = Date.now() + expiresIn * 1000;
    if (typeof body.refresh_token === "string" && body.refresh_token.length > 0) {
      this.refreshToken = body.refresh_token;
      if (this.config.refreshTokenWriteFile) await this.persistRotatedRefreshToken(body.refresh_token);
    }
  }

  private async readSecret(value: string | undefined, file: string | undefined, label: string): Promise<string> {
    const secret = value ?? (file ? await Bun.file(file).text().catch(() => "") : "");
    const trimmed = secret.trim();
    if (!trimmed) throw new CredentialError(`missing OAuth ${label}`);
    return trimmed;
  }

  private async persistRotatedRefreshToken(token: string): Promise<void> {
    const destination = this.config.refreshTokenWriteFile!;
    const temporary = join(dirname(destination), `.${basename(destination)}.${crypto.randomUUID()}.tmp`);
    let file;
    try {
      file = await open(temporary, "wx", 0o600);
      await file.writeFile(`${token}\n`);
      await file.sync();
      await file.close();
      file = undefined;
      await rename(temporary, destination);
    } catch {
      await file?.close().catch(() => undefined);
      await unlink(temporary).catch(() => undefined);
      throw new CredentialError("could not persist rotated OAuth refresh token");
    }
  }
}
