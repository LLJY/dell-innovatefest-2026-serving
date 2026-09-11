export type JsonObject = Record<string, unknown>;

export interface GatewayConfig {
  serviceKey: string;
  codexBaseUrl: string;
  oauthTokenUrl: string;
  oauthClientId: string;
  refreshToken?: string;
  refreshTokenFile?: string;
  refreshTokenWriteFile?: string;
  accountId?: string;
  accountIdFile?: string;
}

export interface ChatRequest {
  model: string;
  messages: JsonObject[];
  tools?: JsonObject[];
  tool_choice?: unknown;
  stream?: boolean;
  stream_options?: { include_usage?: boolean };
}

export interface FunctionCall {
  id: string;
  name: string;
  arguments: string;
  emittedArguments: string;
  outputIndex: number;
}

export interface CompletionState {
  id: string;
  created: number;
  model: string;
  text: string;
  calls: Map<number, FunctionCall>;
  emittedText: boolean;
  emittedCalls: Set<number>;
  roleEmitted: boolean;
  finishReason: "stop" | "length" | "tool_calls";
  usage?: JsonObject;
}
