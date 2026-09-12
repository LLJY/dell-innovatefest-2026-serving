import type { ChatRequest, CompletionState, FunctionCall, JsonObject } from "./types";

const asObject = (value: unknown): JsonObject | undefined =>
  typeof value === "object" && value !== null && !Array.isArray(value) ? (value as JsonObject) : undefined;

export class RequestError extends Error {
  constructor(message: string, readonly code = "invalid_request_error") {
    super(message);
  }
}

export function parseChatRequest(value: unknown): ChatRequest {
  const body = asObject(value);
  if (!body) throw new RequestError("request body must be a JSON object");
  if (typeof body.model !== "string" || !body.model.trim()) throw new RequestError("model must be a non-empty string");
  if (!Array.isArray(body.messages) || body.messages.length === 0) throw new RequestError("messages must be a non-empty array");
  const messages = body.messages.map((message, index) => {
    const item = asObject(message);
    if (!item || typeof item.role !== "string") throw new RequestError(`messages[${index}] must include a role`);
    if (!["system", "developer", "user", "assistant", "tool"].includes(item.role)) {
      throw new RequestError(`messages[${index}].role is unsupported`);
    }
    return item;
  });
  if (body.tools !== undefined && !Array.isArray(body.tools)) throw new RequestError("tools must be an array");
  if (body.stream !== undefined && typeof body.stream !== "boolean") throw new RequestError("stream must be a boolean");
  const streamOptions = asObject(body.stream_options);
  if (body.stream_options !== undefined && (!streamOptions || (streamOptions.include_usage !== undefined && typeof streamOptions.include_usage !== "boolean"))) {
    throw new RequestError("stream_options.include_usage must be a boolean");
  }
  return {
    model: body.model,
    messages,
    tools: body.tools as JsonObject[] | undefined,
    tool_choice: body.tool_choice,
    stream: body.stream,
    stream_options: streamOptions ? { include_usage: streamOptions.include_usage as boolean | undefined } : undefined,
  };
}

function textContent(content: unknown, where: string): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map((part, index) => {
      const item = asObject(part);
      if (!item || !["text", "input_text", "output_text"].includes(String(item.type)) || typeof item.text !== "string") {
        throw new RequestError(`${where}[${index}] must be a text content part`);
      }
      return item.text;
    }).join("");
  }
  if (content === null || content === undefined) return "";
  throw new RequestError(`${where} must be text content`);
}

function normalizeToolChoice(value: unknown): unknown {
  if (value === undefined || typeof value === "string") return value;
  const choice = asObject(value);
  const functionObject = choice && asObject(choice.function);
  if (choice?.type === "function" && functionObject && typeof functionObject.name === "string") {
    return { type: "function", name: functionObject.name };
  }
  throw new RequestError("tool_choice is unsupported");
}

function mapTools(tools: JsonObject[] | undefined): JsonObject[] | undefined {
  if (!tools) return undefined;
  return tools.map((tool, index) => {
    const functionObject = asObject(tool.function);
    if (tool.type !== "function" || !functionObject || typeof functionObject.name !== "string") {
      throw new RequestError(`tools[${index}] must be an OpenAI function tool`);
    }
    const mapped: JsonObject = { type: "function", name: functionObject.name };
    for (const field of ["description", "parameters", "strict"]) {
      if (functionObject[field] !== undefined) mapped[field] = functionObject[field];
    }
    return mapped;
  });
}

export function toResponsesRequest(request: ChatRequest): JsonObject {
  const instructions: string[] = [];
  const input: JsonObject[] = [];
  for (const [index, message] of request.messages.entries()) {
    const role = message.role as string;
    if (role === "system" || role === "developer") {
      instructions.push(textContent(message.content, `messages[${index}].content`));
      continue;
    }
    if (role === "tool") {
      if (typeof message.tool_call_id !== "string" || !message.tool_call_id) throw new RequestError(`messages[${index}].tool_call_id is required`);
      input.push({ type: "function_call_output", call_id: message.tool_call_id, output: textContent(message.content, `messages[${index}].content`) });
      continue;
    }
    if (role === "assistant" && Array.isArray(message.tool_calls)) {
      const content = textContent(message.content, `messages[${index}].content`);
      if (content) input.push({ type: "message", role: "assistant", content: [{ type: "input_text", text: content }] });
      for (const [callIndex, callValue] of message.tool_calls.entries()) {
        const call = asObject(callValue);
        const fn = call && asObject(call.function);
        if (call?.type !== "function" || typeof call.id !== "string" || !fn || typeof fn.name !== "string" || typeof fn.arguments !== "string") {
          throw new RequestError(`messages[${index}].tool_calls[${callIndex}] is malformed`);
        }
        input.push({ type: "function_call", call_id: call.id, name: fn.name, arguments: fn.arguments });
      }
      continue;
    }
    input.push({ type: "message", role, content: [{ type: "input_text", text: textContent(message.content, `messages[${index}].content`) }] });
  }
  const result: JsonObject = { model: normalizeModel(request.model), input, reasoning: { effort: "high" }, store: false, stream: true };
  if (instructions.length > 0) result.instructions = instructions.join("\n\n");
  const tools = mapTools(request.tools);
  if (tools) result.tools = tools;
  const toolChoice = normalizeToolChoice(request.tool_choice);
  if (toolChoice !== undefined) result.tool_choice = toolChoice;
  return result;
}

export function normalizeModel(model: string): string {
  return model.replace(/^openai\//, "").replace(/-1m$/, "").replace(/-fast$/, "");
}

export function newCompletionState(model: string): CompletionState {
  return {
    id: `chatcmpl-${crypto.randomUUID()}`,
    created: Math.floor(Date.now() / 1000),
    model,
    text: "",
    calls: new Map(),
    emittedText: false,
    emittedCalls: new Set(),
    roleEmitted: false,
    finishReason: "stop",
  };
}

function readCall(item: JsonObject, outputIndex: number): FunctionCall | undefined {
  const id = typeof item.call_id === "string" ? item.call_id : typeof item.id === "string" ? item.id : undefined;
  const name = typeof item.name === "string" ? item.name : undefined;
  if (!id || !name) return undefined;
  return { id, name, arguments: typeof item.arguments === "string" ? item.arguments : "", emittedArguments: "", outputIndex };
}

export interface ConvertedEvent {
  delta?: JsonObject;
  completed?: boolean;
}

export function consumeResponseEvent(state: CompletionState, raw: unknown): ConvertedEvent[] {
  const event = asObject(raw);
  if (!event || typeof event.type !== "string") return [];
  const response = asObject(event.response);
  if (response) {
    if (typeof response.id === "string") state.id = `chatcmpl-${response.id}`;
    if (typeof response.created_at === "number") state.created = response.created_at;
    if (typeof response.model === "string") state.model = response.model;
    if (asObject(response.usage)) state.usage = response.usage as JsonObject;
  }
  const out: ConvertedEvent[] = [];
  const outputIndex = typeof event.output_index === "number" ? event.output_index : 0;
  const item = asObject(event.item);
  if ((event.type === "response.output_item.added" || event.type === "response.output_item.done") && item?.type === "function_call") {
    const call = readCall(item, outputIndex);
    if (call) {
      const old = state.calls.get(outputIndex);
      state.calls.set(outputIndex, { ...call, arguments: call.arguments || old?.arguments || "", emittedArguments: old?.emittedArguments ?? "" });
      const converted = toolDelta(state, outputIndex, event.type.endsWith("added") && !state.emittedCalls.has(outputIndex));
      if (converted) out.push(converted);
    }
  }
  if (event.type === "response.output_text.delta" && typeof event.delta === "string") {
    state.text += event.delta;
    state.emittedText = true;
    out.push({ delta: { content: event.delta } });
  }
  if (event.type === "response.output_text.done" && typeof event.text === "string" && !state.emittedText) {
    state.text = event.text;
    state.emittedText = true;
    out.push({ delta: { content: event.text } });
  }
  if (event.type === "response.function_call_arguments.delta" || event.type === "response.function_call_arguments.done") {
    const call = state.calls.get(outputIndex) ?? readCall(event, outputIndex);
    if (call) {
      if (typeof event.delta === "string") call.arguments += event.delta;
      else if (typeof event.arguments === "string") call.arguments = event.arguments;
      else return out;
      state.calls.set(outputIndex, call);
      const converted = toolDelta(state, outputIndex, !state.emittedCalls.has(outputIndex));
      if (converted) out.push(converted);
    }
  }
  if (event.type === "response.completed" || event.type === "response.incomplete") {
    if (event.type === "response.incomplete" || response?.status === "incomplete") state.finishReason = "length";
    const output = response?.output;
    if (Array.isArray(output)) {
      for (const [index, outputItem] of output.entries()) {
        const outputObject = asObject(outputItem);
        if (outputObject?.type === "function_call") {
          const call = readCall(outputObject, index);
          if (call) {
            const old = state.calls.get(index);
            state.calls.set(index, { ...call, emittedArguments: old?.emittedArguments ?? "" });
          }
        }
        if (outputObject?.type === "message" && Array.isArray(outputObject.content) && !state.text) {
          state.text = outputObject.content.map((part) => {
            const text = asObject(part);
            return text?.type === "output_text" && typeof text.text === "string" ? text.text : "";
          }).join("");
        }
      }
    }
    if (state.calls.size > 0) state.finishReason = "tool_calls";
    if (state.text && !state.emittedText) {
      state.emittedText = true;
      out.push({ delta: { content: state.text } });
    }
    for (const index of state.calls.keys()) {
      const converted = toolDelta(state, index, !state.emittedCalls.has(index));
      if (converted) out.push(converted);
    }
    out.push({ completed: true });
  }
  return out;
}

export function isResponseFailure(raw: unknown): boolean {
  const event = asObject(raw);
  return event?.type === "response.failed" || event?.type === "response.error";
}

function toolDelta(state: CompletionState, outputIndex: number, includeMetadata: boolean): ConvertedEvent | undefined {
  const call = state.calls.get(outputIndex)!;
  const argumentsDelta = call.arguments.slice(call.emittedArguments.length);
  if (!argumentsDelta && state.emittedCalls.has(outputIndex)) return undefined;
  const position = [...state.calls.keys()].sort((a, b) => a - b).indexOf(outputIndex);
  const functionPayload: JsonObject = includeMetadata
    ? { name: call.name, arguments: argumentsDelta }
    : { arguments: argumentsDelta };
  call.emittedArguments = call.arguments;
  state.emittedCalls.add(outputIndex);
  return { delta: { tool_calls: [{ index: position, ...(includeMetadata ? { id: call.id, type: "function" } : {}), function: functionPayload }] } };
}

export function chatChunk(state: CompletionState, delta: JsonObject, finishReason: CompletionState["finishReason"] | null = null): JsonObject {
  if (!state.roleEmitted && finishReason === null) {
    state.roleEmitted = true;
    delta = { role: "assistant", ...delta };
  }
  return { id: state.id, object: "chat.completion.chunk", created: state.created, model: state.model, choices: [{ index: 0, delta, finish_reason: finishReason }] };
}

export function chatCompletion(state: CompletionState): JsonObject {
  const toolCalls = [...state.calls.values()].sort((a, b) => a.outputIndex - b.outputIndex).map((call) => ({
    id: call.id, type: "function", function: { name: call.name, arguments: call.arguments },
  }));
  const message: JsonObject = { role: "assistant", content: state.text || null };
  if (toolCalls.length) message.tool_calls = toolCalls;
  const result: JsonObject = {
    id: state.id,
    object: "chat.completion",
    created: state.created,
    model: state.model,
    choices: [{ index: 0, message, finish_reason: state.finishReason }],
  };
  const usage = chatUsage(state.usage);
  if (usage) result.usage = usage;
  return result;
}

export function chatUsage(usage: JsonObject | undefined): JsonObject | undefined {
  if (!usage || typeof usage.input_tokens !== "number" || typeof usage.output_tokens !== "number" || typeof usage.total_tokens !== "number") return undefined;
  return { prompt_tokens: usage.input_tokens, completion_tokens: usage.output_tokens, total_tokens: usage.total_tokens };
}
