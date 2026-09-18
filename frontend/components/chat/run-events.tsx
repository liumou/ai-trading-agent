"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { ChevronDown, Wrench } from "lucide-react";
import type { AgentChatEvent } from "@/lib/api";

interface RunEventsProps {
  events: AgentChatEvent[];
  /** 运行中 / 失败时默认展开，便于及时排查；完成后保持用户手动控制。 */
  defaultOpen: boolean;
}

const EVENT_KEY: Record<string, string> = {
  tool_started: "toolStart", tool_finished: "toolEnd", tool_error: "toolError",
  assistant_text: "agentText",
};

/** 单条事件行。payload 结构由后端约定，按可选字段容错读取。 */
function EventRow({ event }: { event: AgentChatEvent }) {
  const t = useTranslations("agentChat");
  const payload = (event.payload || {}) as Record<string, unknown>;
  const failed = payload.status === "failed" || Boolean(payload.reason_code);
  const label = t(EVENT_KEY[event.event_type] || "executionTrace");
  const name = (payload.tool || payload.agent_id) as string | undefined;
  const output = typeof payload.output === "string" ? payload.output : "";
  const text = typeof payload.text === "string" ? payload.text : "";
  return (
    <div className="flex gap-2 border-l-2 border-primary/30 pl-2 py-0.5 text-[11px]">
      <span className="text-muted-foreground shrink-0">#{event.sequence}</span>
      <span className={failed ? "text-red-600 dark:text-red-400" : "text-foreground"}>
        {label}
        {name ? ` · ${name}` : ""}
        {typeof payload.duration_s === "number" ? ` · ${payload.duration_s.toFixed(2)}s` : ""}
      </span>
      {(text || output) && (
        <span className="text-muted-foreground truncate max-w-[38ch]">
          {text || output}
          {payload.truncated ? ` · ${t("truncated")}` : ""}
        </span>
      )}
    </div>
  );
}

/** 执行事件时间线：默认折叠为一行，展开才 mount 列表（条件渲染降 DOM）。 */
export function RunEvents({ events, defaultOpen }: RunEventsProps) {
  const t = useTranslations("agentChat");
  const [open, setOpen] = useState(defaultOpen);

  // 运行进入 active / failed 时自动展开一次；用户手动收起后不再强制回弹。
  useEffect(() => {
    if (defaultOpen) setOpen(true);
  }, [defaultOpen]);

  if (events.length === 0) return null;

  return (
    <div className="space-y-1.5">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 text-left text-xs font-medium hover:text-primary transition-colors"
      >
        <Wrench className="size-3" />
        {t("executionTrace")}
        <span className="text-muted-foreground">({events.length})</span>
        <span className="text-muted-foreground ml-auto">{open ? t("collapseTrace") : t("expandTrace")}</span>
        <ChevronDown className={`size-3.5 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="max-h-44 overflow-y-auto space-y-0.5 animate-fade-in">
          {events.map((event) => (
            <EventRow key={event.sequence} event={event} />
          ))}
        </div>
      )}
    </div>
  );
}
