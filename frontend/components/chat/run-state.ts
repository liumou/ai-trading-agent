import type { AgentChatEvent, AgentChatRun } from "@/lib/api";

export const isActiveRun = (run: Pick<AgentChatRun, "status">) =>
  run.status === "queued" || run.status === "running";

export function mergeEvents(previous: AgentChatEvent[], incoming: AgentChatEvent[]) {
  const bySequence = new Map(previous.map((event) => [event.sequence, event]));
  incoming.forEach((event) => bySequence.set(event.sequence, event));
  return [...bySequence.values()].sort((a, b) => a.sequence - b.sequence);
}

// `after` is exclusive: retain the last seen sequence, never add one.
export function eventCursor(after: number, events: AgentChatEvent[], next?: number | null) {
  return Math.max(after, next ?? 0, ...events.map((event) => event.sequence));
}

export function createRequestId() {
  // getRandomValues also works in non-HTTPS local deployments where randomUUID is absent.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

const KEY = "agent-chat:selection:v2";
export function saveSelection(sessionId: number | null, runId: string | null) {
  try {
    if (sessionId === null) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, JSON.stringify({ v: 2, sessionId, runId }));
  } catch { /* Storage may be unavailable; in-memory use still works. */ }
}

export function loadSelection(): { sessionId: number; runId: string | null } | null {
  try {
    const value = JSON.parse(localStorage.getItem(KEY) || "null");
    if (value?.v === 2 && Number.isSafeInteger(value.sessionId) && value.sessionId > 0) {
      return { sessionId: value.sessionId, runId: typeof value.runId === "string" ? value.runId : null };
    }
  } catch { /* Ignore stale or invalid references. */ }
  return null;
}
