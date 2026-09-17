"use client";

import { useTranslations } from "next-intl";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Ban, Check, Loader2, RotateCw, Wrench, X } from "lucide-react";
import type { AgentChatEvent, AgentChatRun, AgentChatRunDetail } from "@/lib/api";
import { isActiveRun } from "./run-state";

const STATUS_KEY: Record<string, string> = {
  queued: "statusQueued", running: "statusRunning", completed: "statusCompleted",
  incomplete: "statusIncomplete", failed: "statusFailed", timed_out: "statusTimedOut",
  cancelled: "statusCancelled", interrupted: "statusInterrupted",
};

const REASON_KEY: Record<string, string> = {
  total_timeout: "reasonTotalTimeout", max_turns: "reasonMaxTurns",
  llm_request_timeout: "reasonLlmRequestTimeout", tool_timeout: "reasonToolTimeout",
  provider_error: "reasonProviderError", empty_response: "reasonEmptyResponse",
  circuit_open: "reasonCircuitOpen", cancelled_by_user: "reasonCancelled",
  worker_restart: "reasonInterrupted", audit_error: "reasonUnrecoverable",
  lease_lost: "reasonLeaseLost",
};

const EVENT_KEY: Record<string, string> = {
  tool_started: "toolStart", tool_finished: "toolEnd", tool_error: "toolError",
  assistant_text: "agentText",
};

const tone = (status: string) =>
  status === "completed"
    ? "text-green-600 dark:text-green-400"
    : isActiveRun({ status })
      ? "text-blue-600 dark:text-blue-400"
      : "text-red-600 dark:text-red-400";

function EventRow({ event }: { event: AgentChatEvent }) {
  const t = useTranslations("agentChat");
  const payload = (event.payload || {}) as Record<string, unknown>;
  const failed = payload.status === "failed" || Boolean(payload.reason_code);
  const label = t(EVENT_KEY[event.event_type] || "executionTrace");
  const name = (payload.tool || payload.agent_id) as string | undefined;
  const output = typeof payload.output === "string" ? payload.output : "";
  const text = typeof payload.text === "string" ? payload.text : "";
  return (
    <div className="flex gap-2 border-l-2 border-muted pl-2 py-0.5 text-[11px]">
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


export function RunPanel({
  detail,
  config,
  disconnected,
  retryable,
  onCancel,
  onRetry,
}: {
  detail: AgentChatRunDetail | null;
  config: Record<string, number | undefined> | null;
  disconnected: boolean;
  retryable: boolean;
  onCancel: () => void;
  onRetry: () => void;
}) {
  const t = useTranslations("agentChat");
  if (!detail) return <p className="text-xs text-muted-foreground pt-2">{t("newRunHint")}</p>;
  const run: AgentChatRun = detail.run;
  const status = String(run.status || "");
  const reason = run.reason_code
    ? `${t(REASON_KEY[run.reason_code] || "reasonCodePrefix")} (${run.reason_code})`
    : "";
  const events = [...(detail.events || [])].reverse();
  const agents = detail.agents || [];
  const active = isActiveRun({ status });

  return (
    <div className="rounded-xl border p-3 space-y-3 text-xs">
      <div className="flex items-center gap-2 flex-wrap">
        <Badge variant="outline" className={tone(status)}>
          {active && <Loader2 className="size-3 mr-1 animate-spin" />}
          {t(STATUS_KEY[status] || "statusUnknown")}
        </Badge>
        {run.mode === "experts" && <Badge variant="secondary">{t("modeExperts")}</Badge>}
        <span className="text-muted-foreground">
          {t("turnsLabel")} {run.turns ?? 0} · {t("durationLabel")} {(run.duration_s ?? 0).toFixed(1)}s
        </span>
        {active && (
          <Button size="sm" variant="ghost" className="h-6 px-2" onClick={onCancel}>
            <Ban className="size-3 mr-1" /> {t("cancel")}
          </Button>
        )}
        {!active && typeof run.message === "string" && run.message && (
          <Button size="sm" variant="ghost" className="h-6 px-2" onClick={onRetry} disabled={!retryable}>
            <RotateCw className="size-3 mr-1" /> {t("retry")}
          </Button>
        )}
        {disconnected && <span className="text-amber-600 dark:text-amber-400">{t("loadFailed")}</span>}
      </div>

      {reason && <p className="text-red-600 dark:text-red-400">{t("runFailed")}: {reason}</p>}

      {run.response && <div className="whitespace-pre-wrap leading-relaxed">{run.response}</div>}

      {!run.response && run.partial_response && (
        <div className="rounded-lg bg-amber-500/10 p-2 whitespace-pre-wrap">
          <p className="font-medium mb-1">{t("partialResponse")}</p>
          {run.partial_response}
        </div>
      )}

      <div>
        <p className="font-medium mb-1">{t("agentsLabel")}</p>
        {agents.length === 0 ? (
          <p className="text-muted-foreground">{t("noAgentsYet")}</p>
        ) : (
          <ul className="space-y-1">
            {agents.map((agent, index) => (
              <li key={String(agent.execution_id || agent.agent_id || index)} className="rounded-lg bg-muted/50 p-2">
                <div className="flex items-center gap-1">
                  <span className="font-medium">{agent.agent_id}</span>
                  {agent.status === "completed" ? (
                    <Check className="size-3 text-green-600" />
                  ) : agent.status === "running" ? (
                    <Loader2 className="size-3 animate-spin" />
                  ) : (
                    <X className="size-3 text-red-600" />
                  )}
                  {agent.model ? <span className="text-muted-foreground">· {agent.model}</span> : null}
                </div>
                {(agent.report || agent.summary) && (
                  <p className="text-muted-foreground whitespace-pre-wrap mt-0.5">{agent.report || agent.summary}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {events.length > 0 && (
        <div>
          <p className="font-medium mb-1 flex items-center gap-1">
            <Wrench className="size-3" /> {t("executionTrace")}
            <span className="text-muted-foreground">({events.length})</span>
          </p>
          <div className="max-h-44 overflow-y-auto space-y-0.5">
            {events.map((event) => (
              <EventRow key={event.sequence} event={event} />
            ))}
          </div>
        </div>
      )}

      {config && (
        <p className="text-[10px] text-muted-foreground">
          {t("budgetTitle")}: {t("budgetTotal")} {config.total_timeout_s}s · {t("budgetRequest")}{" "}
          {config.request_timeout_s}s · {t("budgetTool")} {config.tool_timeout_s}s · {t("budgetMaxTurns")}{" "}
          {config.max_turns}
        </p>
      )}
    </div>
  );
}
