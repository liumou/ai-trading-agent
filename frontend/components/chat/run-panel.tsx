"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  Ban, Check, ChevronDown, Clock, Loader2, RotateCw, X, WifiOff,
} from "lucide-react";
import type { AgentChatRun, AgentChatRunAgent, AgentChatRunDetail } from "@/lib/api";
import { isActiveRun } from "./run-state";
import { RunEvents } from "./run-events";

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

/** 状态分层着色：完成=绿，运行中=蓝，失败/中断/超时=红。 */
const tone = (status: string) =>
  status === "completed"
    ? "text-green-600 dark:text-green-400"
    : isActiveRun({ status })
      ? "text-blue-600 dark:text-blue-400"
      : "text-red-600 dark:text-red-400";

/** 单个 Agent 贡献卡：默认显示角色/状态/模型/summary，展开才 mount report。 */
function AgentCard({ agent }: { agent: AgentChatRunAgent }) {
  const t = useTranslations("agentChat");
  const [open, setOpen] = useState(false);
  const id = String(agent.agent_id || agent.role || t("agentLabel"));
  const hasReport = typeof agent.report === "string" && agent.report.length > 0;
  const summary = typeof agent.summary === "string" && agent.summary.length > 0
    ? agent.summary
    : hasReport
      ? undefined
      : t("agentStepWaiting");

  return (
    <li className="rounded-lg bg-muted/50 p-2">
      <div className="flex items-center gap-1.5">
        <span className="font-medium truncate">{id}</span>
        {agent.status === "completed" ? (
          <Check className="size-3 text-green-600 dark:text-green-400 shrink-0" />
        ) : agent.status === "running" || agent.status === "queued" ? (
          <Loader2 className="size-3 animate-spin text-blue-600 dark:text-blue-400 shrink-0" />
        ) : (
          <X className="size-3 text-red-600 dark:text-red-400 shrink-0" />
        )}
        {agent.model ? <span className="text-muted-foreground truncate">· {agent.model}</span> : null}
        {hasReport && (
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            className="ml-auto flex items-center gap-1 text-[11px] text-muted-foreground hover:text-primary transition-colors shrink-0"
          >
            <ChevronDown className={`size-3 transition-transform ${open ? "rotate-180" : ""}`} />
            {open ? t("collapseAgents") : t("expandAgents")}
          </button>
        )}
      </div>
      {summary && <p className="text-muted-foreground whitespace-pre-wrap mt-0.5">{summary}</p>}
      {open && hasReport && (
        <p className="whitespace-pre-wrap mt-1.5 animate-fade-in">{agent.report}</p>
      )}
    </li>
  );
}

/** 运行预算说明：徽章 + Tooltip 展开，不再平铺一行 10px 数字。 */
function BudgetBadge({ config }: { config: Record<string, number | undefined> | null }) {
  const t = useTranslations("agentChat");
  if (!config) return null;
  const items: Array<[string, string]> = [
    [t("budgetTotal"), `${config.total_timeout_s ?? "?"} ${t("secondsUnit")}`],
    [t("budgetRequest"), `${config.request_timeout_s ?? "?"} ${t("secondsUnit")}`],
    [t("budgetTool"), `${config.tool_timeout_s ?? "?"} ${t("secondsUnit")}`],
    [t("budgetHeavyTool"), `${config.heavy_tool_timeout_s ?? "?"} ${t("secondsUnit")}`],
    [t("budgetMaxTurns"), `${config.max_turns ?? "?"} ${t("turnsUnit")}`],
    [t("budgetMaxRetries"), `${config.max_retries ?? "?"} ${t("timesUnit")}`],
  ];
  return (
    <Tooltip>
      <TooltipTrigger>
        <button
          type="button"
          aria-label={t("budgetTitle")}
          className="inline-flex items-center gap-1 rounded-md border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground hover:text-foreground transition-colors"
        >
          <Clock className="size-3" />
          {t("budgetTitle")}
        </button>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="flex !w-44 flex-col items-stretch gap-1 py-2">
        <span className="font-medium mb-0.5">{t("budgetTitle")}</span>
        {items.map(([label, value]) => (
          <span key={label} className="flex justify-between gap-2 text-[11px]">
            <span className="opacity-70">{label}</span>
            <span>{value}</span>
          </span>
        ))}
      </TooltipContent>
    </Tooltip>
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
  // 无 run 时不渲染：空态已给出引导，newRunHint 与 EmptyState 叠加自相矛盾。
  if (!detail) return null;

  const run: AgentChatRun = detail.run;
  const status = String(run.status || "");
  const active = isActiveRun({ status });
  const failed = Boolean(run.reason_code);
  const reason = run.reason_code
    ? `${t(REASON_KEY[run.reason_code] || "reasonCodePrefix")} (${run.reason_code})`
    : "";
  const events = [...(detail.events || [])].toReversed();
  const agents = detail.agents || [];

  return (
    <div className="rounded-xl border p-3 space-y-3 text-xs animate-fade-in">
      <div className="flex items-center gap-2 flex-wrap">
        <Badge variant="outline" className={tone(status)}>
          {active && <Loader2 className="size-3 mr-1 animate-spin" />}
          {t(STATUS_KEY[status] || "statusUnknown")}
        </Badge>
        {run.mode === "experts" && <Badge variant="secondary">{t("modeExperts")}</Badge>}
        <span className="text-muted-foreground">
          {t("turnsLabel")} {run.turns ?? 0} · {t("durationLabel")} {(run.duration_s ?? 0).toFixed(1)}s
        </span>
        <BudgetBadge config={config} />
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
      </div>

      {/* 断线单独分层：轮询/网络失败，与「取消/中断」语义区分 */}
      {disconnected && (
        <div className="flex items-center gap-1.5 rounded-lg border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-amber-600 dark:text-amber-400">
          <WifiOff className="size-3.5" />
          {t("loadFailed")}
        </div>
      )}

      {reason && <p className="text-red-600 dark:text-red-400">{t("runFailed")}: {reason}</p>}

      {run.response && <div className="whitespace-pre-wrap leading-relaxed">{run.response}</div>}

      {!run.response && run.partial_response && (
        <div className="rounded-lg bg-amber-500/10 p-2 whitespace-pre-wrap">
          <p className="font-medium mb-1">{t("partialResponse")}</p>
          {run.partial_response}
        </div>
      )}

      {agents.length > 0 && (
        <AgentsSection agents={agents} />
      )}

      <RunEvents events={events} defaultOpen={active || failed} />
    </div>
  );
}

/** 参与 Agent 区：整体折叠，展开才 mount 卡片列表。 */
function AgentsSection({ agents }: { agents: AgentChatRunAgent[] }) {
  const t = useTranslations("agentChat");
  const [open, setOpen] = useState(false);
  const done = agents.filter((a) => a.status === "completed").length;
  return (
    <div className="space-y-1.5">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 text-left text-xs font-medium hover:text-primary transition-colors"
      >
        {t("agentsLabel")}
        <span className="text-muted-foreground">({done}/{agents.length})</span>
        <span className="text-muted-foreground ml-auto">{open ? t("collapseAgents") : t("expandAgents")}</span>
        <ChevronDown className={`size-3.5 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <ul className="space-y-1 animate-fade-in">
          {agents.map((agent, index) => (
            <AgentCard key={String(agent.execution_id || agent.agent_id || index)} agent={agent} />
          ))}
        </ul>
      )}
    </div>
  );
}
