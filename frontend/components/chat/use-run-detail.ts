"use client";

import { useEffect, useRef, useState } from "react";
import { getChatRun, type AgentChatRun, type AgentChatRunDetail } from "@/lib/api";
import { eventCursor, isActiveRun, mergeEvents } from "./run-state";

export function useRunDetail(runId: string | null, onUpdate: (run: AgentChatRun) => void) {
  const [detail, setDetail] = useState<AgentChatRunDetail | null>(null);
  const [disconnected, setDisconnected] = useState(false);
  const updateRef = useRef(onUpdate);
  useEffect(() => { updateRef.current = onUpdate; }, [onUpdate]);

  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cursor = 0;
    let accumulated: AgentChatRunDetail["events"] = [];
    let failures = 0;
    const MAX_FAILURES = 20; // ~5 min at exponential backoff before giving up
    const poll = async () => {
      try {
        const { data } = await getChatRun(runId, cursor, 100, controller.signal);
        if (controller.signal.aborted) return;
        const next = eventCursor(cursor, data.events, data.next_cursor);
        const advanced = next > cursor;
        cursor = next;
        accumulated = mergeEvents(accumulated, data.events);
        setDetail({ ...data, events: accumulated });
        setDisconnected(false);
        failures = 0;
        updateRef.current(data.run);
        // A terminal snapshot can still have many unseen pages. Drain until an empty
        // page, not merely until status becomes terminal or a page is short.
        if (advanced || isActiveRun(data.run)) {
          timer = setTimeout(poll, advanced ? 0 : 2000);
        }
      } catch {
        if (controller.signal.aborted) return;
        setDisconnected(true);
        failures += 1;
        if (failures >= MAX_FAILURES) {
          console.warn("[useRunDetail] Max polling failures reached, stopping.");
          return;
        }
        timer = setTimeout(poll, Math.min(15000, 2000 * failures));
      }
    };
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [runId]);

  return { detail: detail?.run.id === runId ? detail : null, disconnected };
}
