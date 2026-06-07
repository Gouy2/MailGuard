import type { AgentMode, CleanerPreview, CleanRule, Health, PendingTool, SseMessage, TraceRecord } from "./types";

export type ApiConfig = {
  apiBase: string;
  token: string;
  sessionId: string;
  systemPrompt: string;
};

export async function getHealth(config: ApiConfig): Promise<Health> {
  return requestJson<Health>(config, "/health");
}

export async function getPending(config: ApiConfig): Promise<PendingTool[]> {
  const result = await requestJson<{ pending: PendingTool[] }>(config, "/tools/pending");
  return result.pending ?? [];
}

export async function approvePending(config: ApiConfig, pendingId: string): Promise<Record<string, unknown>> {
  return requestJson(config, "/tools/approve", {
    method: "POST",
    body: { pending_tool_call_id: pendingId },
  });
}

export async function rejectPending(config: ApiConfig, pendingId: string): Promise<Record<string, unknown>> {
  return requestJson(config, "/tools/reject", {
    method: "POST",
    body: { pending_tool_call_id: pendingId },
  });
}

export async function getTrace(config: ApiConfig, traceId: string): Promise<TraceRecord[]> {
  const result = await requestJson<{ events: TraceRecord[] }>(config, `/traces/${encodeURIComponent(traceId)}`);
  return result.events ?? [];
}

export async function teachCleaner(config: ApiConfig, instruction: string): Promise<Record<string, unknown>> {
  return requestJson(config, "/cleaner/teach", {
    method: "POST",
    body: {
      session_id: config.sessionId,
      instruction,
      limit: 30,
    },
  });
}

export async function listRules(config: ApiConfig): Promise<CleanRule[]> {
  const result = await requestJson<{ rules: CleanRule[] }>(
    config,
    `/cleaner/rules?session_id=${encodeURIComponent(config.sessionId)}&limit=100`,
  );
  return result.rules ?? [];
}

export async function approveRule(config: ApiConfig, ruleId: string): Promise<Record<string, unknown>> {
  return requestJson(config, `/cleaner/rules/${encodeURIComponent(ruleId)}/approve`, {
    method: "POST",
    body: { session_id: config.sessionId },
  });
}

export async function disableRule(config: ApiConfig, ruleId: string): Promise<Record<string, unknown>> {
  return requestJson(config, `/cleaner/rules/${encodeURIComponent(ruleId)}/disable`, {
    method: "POST",
    body: { session_id: config.sessionId },
  });
}

export async function runCleanerPreview(config: ApiConfig): Promise<CleanerPreview> {
  return requestJson<CleanerPreview>(config, "/cleaner/preview", {
    method: "POST",
    body: {
      session_id: config.sessionId,
      limit: 50,
    },
  });
}

export async function getCleanerPolicy(config: ApiConfig): Promise<Record<string, unknown>> {
  return requestJson(config, `/cleaner/policy?session_id=${encodeURIComponent(config.sessionId)}`);
}

export async function getCleanerAudit(config: ApiConfig): Promise<Record<string, unknown>> {
  return requestJson(config, `/cleaner/audit?session_id=${encodeURIComponent(config.sessionId)}&limit=20`);
}

export async function streamChat(
  config: ApiConfig,
  mode: AgentMode,
  message: string,
  onEvent: (event: SseMessage) => void,
): Promise<void> {
  const path = mode === "agent_readonly" ? "/chat/readonly" : mode === "simple" ? "/chat/simple" : "/chat";
  const response = await fetch(joinUrl(config.apiBase, path), {
    method: "POST",
    headers: headers(config, true),
    body: JSON.stringify({ session_id: config.sessionId, message, system_prompt: config.systemPrompt }),
  });
  if (!response.ok) {
    throw new Error(await errorText(response));
  }
  if (!response.body) {
    throw new Error("Streaming response body is unavailable.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split(/\n\n/);
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const parsed = parseSseFrame(frame);
      if (parsed) onEvent(parsed);
    }
  }
  buffer += decoder.decode();
  const parsed = parseSseFrame(buffer);
  if (parsed) onEvent(parsed);
}

async function requestJson<T>(
  config: ApiConfig,
  path: string,
  options: { method?: string; body?: Record<string, unknown> } = {},
): Promise<T> {
  const response = await fetch(joinUrl(config.apiBase, path), {
    method: options.method ?? "GET",
    headers: headers(config, Boolean(options.body)),
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (!response.ok) {
    throw new Error(await errorText(response));
  }
  return (await response.json()) as T;
}

function headers(config: ApiConfig, includeJson: boolean): HeadersInit {
  const result: Record<string, string> = {};
  if (includeJson) result["Content-Type"] = "application/json";
  if (config.token.trim()) result.Authorization = `Bearer ${config.token.trim()}`;
  return result;
}

function joinUrl(base: string, path: string): string {
  if (!base || base === "/") return path;
  return `${base.replace(/\/$/, "")}${path}`;
}

function parseSseFrame(frame: string): SseMessage | null {
  const lines = frame.split(/\r?\n/);
  let event = "";
  const data: string[] = [];
  for (const line of lines) {
    if (line.startsWith("event:")) event = line.slice("event:".length).trim();
    if (line.startsWith("data:")) data.push(line.slice("data:".length).trim());
  }
  if (!event || data.length === 0) return null;
  try {
    return { event, data: JSON.parse(data.join("\n")) as Record<string, unknown> };
  } catch {
    return { event, data: { raw: data.join("\n") } };
  }
}

async function errorText(response: Response): Promise<string> {
  const text = await response.text();
  if (!text) return `HTTP ${response.status}`;
  try {
    const data = JSON.parse(text) as { detail?: string };
    return data.detail || text;
  } catch {
    return text;
  }
}
