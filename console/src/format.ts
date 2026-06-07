import type { TraceRecord } from "./types";

export function asString(value: unknown): string {
  return typeof value === "string" ? value : value === undefined || value === null ? "" : String(value);
}

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

export function compactJson(value: unknown): string {
  const text = JSON.stringify(value);
  return text.length > 240 ? `${text.slice(0, 237)}...` : text;
}

export function traceKey(record: TraceRecord): string {
  return `${record.trace_id}:${record.timestamp || ""}:${record.event}:${JSON.stringify(record.payload)}`;
}

export function traceSummary(record: TraceRecord): string {
  const payload = record.payload || {};
  return (
    asString(payload.tool) ||
    asString(payload.mode) ||
    asString(payload.status) ||
    asString(payload.decision) ||
    asString(payload.pending_tool_call_id) ||
    ""
  );
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
