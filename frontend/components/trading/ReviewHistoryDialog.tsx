"use client";

import type { ReactNode } from "react";
import { useTranslations } from "next-intl";
import { ShieldCheck } from "lucide-react";
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { ManualReview } from "@/lib/api";
import { toDate } from "@/lib/format";
import { StatusBadge, VerdictBadge } from "./ReviewBadges";
import { cn } from "@/lib/utils";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-2 text-xs">
      <span className="w-28 shrink-0 text-muted-foreground">{label}</span>
      <span className="min-w-0 flex-1 break-words text-foreground">{children}</span>
    </div>
  );
}

function SectionTitle({ children }: { children: ReactNode }) {
  return <div className="mb-2 text-xs font-semibold text-muted-foreground">{children}</div>;
}

/** 规则检查的 choice 语义 → 徽章配色（clear/aligned/favorable 绿，partial/caution 黄，block/conflict 红）。 */
const CHOICE_OK = new Set(["clear", "aligned", "favorable", "approved", "ok"]);
const CHOICE_MID = new Set(["partial", "mixed", "neutral", "caution", "warn"]);
const CHOICE_BAD = new Set(["insufficient", "conflict", "adverse", "block", "rejected"]);

function ChoiceChip({ choice }: { choice?: string }) {
  const c = (choice ?? "").toLowerCase();
  const cls = CHOICE_OK.has(c)
    ? "bg-green-500/15 text-green-600 dark:text-green-400"
    : CHOICE_BAD.has(c)
      ? "bg-destructive/10 text-destructive"
      : CHOICE_MID.has(c)
        ? "bg-yellow-500/15 text-yellow-600 dark:text-yellow-400"
        : "bg-muted text-muted-foreground";
  return (
    <span className={cn("inline-block rounded-md px-1.5 py-0.5 text-xs font-medium whitespace-nowrap", cls)}>
      {choice || "—"}
    </span>
  );
}

/** 历史审查详情（只读）。不复用 ReviewResultCard —— 那是带确认/重试按钮的实时卡片。
 *
 * 布局：头部（品种/方向/ID/结论徽章 + 状态/引擎/置信度/时间元信息条）→
 * 双栏（订单上下文 | 审查要点）→ 全宽审查理由 → 全宽标记（风险/情绪/规则）→
 * 全宽规则检查明细表（证据列占主要宽度）。
 */
export function ReviewHistoryDialog({
  review, open, onOpenChange,
}: {
  review: ManualReview | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useTranslations("manual-reviews");
  if (!review) return null;

  const llm = review.review?.llm;
  const systemone = review.review?.systemone;
  const riskFlags = llm?.risk_flags ?? [];
  const emotional = llm?.emotional_indicators ?? [];
  const ruleFlags = review.review?.rule_flags ?? review.rule_flags ?? [];
  const checks = systemone?.checks ?? [];
  const hasChecks = Array.isArray(checks) && checks.length > 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* 基类 DialogContent 自带 sm:max-w-sm，必须用 ! 压过它，否则桌面端被锁回 512px */}
      <DialogContent className="sm:max-w-4xl! max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex flex-wrap items-center gap-2 text-base">
            <span className="font-mono">{review.symbol}</span>
            <span className={review.order_type === "BUY" ? "text-success dark:text-green-400" : "text-destructive"}>
              {review.order_type}
            </span>
            <span className="text-muted-foreground font-normal">#{review.id}</span>
            <VerdictBadge verdict={review.verdict} />
          </DialogTitle>
          <DialogDescription className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <StatusBadge status={review.status} />
            {review.provider && <span className="font-mono text-xs">{review.provider}</span>}
            {typeof review.confidence === "number" && (
              <span className="text-xs">{t("confidence")}: {(review.confidence * 100).toFixed(0)}%</span>
            )}
            {review.created_at && (
              <span className="text-xs text-muted-foreground">{toDate(review.created_at).toLocaleString()}</span>
            )}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* 双栏：订单上下文 | 审查要点 */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <section className="rounded-xl border border-border bg-card p-4 space-y-1.5">
              <SectionTitle>{t("orderInfo")}</SectionTitle>
              <Row label={t("orderKind")}>{review.order_kind ?? "—"}</Row>
              <Row label={t("lot")}>{review.requested_lot ?? "—"}</Row>
              <Row label={t("sl")}>{review.requested_sl ?? "—"}</Row>
              <Row label={t("tp")}>{review.requested_tp ?? "—"}</Row>
              {review.order_price != null && <Row label={t("orderPrice")}>{review.order_price}</Row>}
              {review.ticket != null && <Row label={t("ticket")}>{review.ticket}</Row>}
              {review.fill_price != null && <Row label={t("fillPrice")}>{review.fill_price}</Row>}
            </section>

            <section className="rounded-xl border border-border bg-card p-4 space-y-1.5">
              <SectionTitle>{t("verdictSection")}</SectionTitle>
              {review.reason && <Row label={t("blockedReason")}>{review.reason}</Row>}
              {review.kind && <Row label={t("rejectKind")}>{review.kind}</Row>}
              {typeof review.retryable === "boolean" && (
                <Row label={t("retryable")}>{review.retryable ? t("yes") : t("no")}</Row>
              )}
              {review.review?.confirm_expires_at && (
                <Row label={t("confirmExpires")}>{new Date(review.review.confirm_expires_at).toLocaleString()}</Row>
              )}
              {!review.reason && !review.kind && typeof review.retryable !== "boolean" && !review.review?.confirm_expires_at && (
                <div className="text-xs text-muted-foreground">{t("noVerdictDetail")}</div>
              )}
            </section>
          </div>

          {/* 全宽：审查理由 */}
          {llm?.reasoning && (
            <section className="rounded-xl border border-border bg-card p-4 space-y-1.5">
              <SectionTitle>{t("reasoning")}</SectionTitle>
              <div className="text-sm leading-relaxed text-foreground">{llm.reasoning}</div>
            </section>
          )}

          {/* 全宽：标记（风险 / 情绪 / 规则） */}
          {(riskFlags.length > 0 || emotional.length > 0 || ruleFlags.length > 0) && (
            <section className="rounded-xl border border-border bg-card p-4 space-y-2">
              {riskFlags.length > 0 && (
                <Row label={t("riskFlags")}>
                  {riskFlags.map((f) => (
                    <span key={f} className="inline-block mr-1.5 rounded-md bg-destructive/10 px-1.5 py-0.5 text-xs">{f}</span>
                  ))}
                </Row>
              )}
              {emotional.length > 0 && <Row label={t("emotionalIndicators")}>{emotional.join("; ")}</Row>}
              {ruleFlags.length > 0 && (
                <Row label={t("ruleFlags")}>
                  {ruleFlags.map((f) => (
                    <span key={`${f.flag}-${f.severity}`} className="inline-block mr-1.5 mb-0.5 rounded-md bg-muted px-1.5 py-0.5 text-xs">
                      {f.flag}
                      {f.severity ? ` [${f.severity}]` : ""}
                      {f.detail ? ` — ${f.detail}` : ""}
                    </span>
                  ))}
                </Row>
              )}
            </section>
          )}

          {/* 全宽：规则检查明细 */}
          <section className="rounded-xl border border-border bg-card p-4">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-xs font-semibold text-muted-foreground">
              <ShieldCheck className="size-3.5" />
              {t("systemChecks")}
              {systemone?.provider && <span className="font-mono">{systemone.provider}</span>}
              {typeof systemone?.latency_ms === "number" && <span className="font-mono">({systemone.latency_ms}ms)</span>}
              {typeof systemone?.degraded === "boolean" && systemone.degraded && (
                <span className="text-amber-500">{t("degraded")}</span>
              )}
            </div>
            {hasChecks ? (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="text-xs w-36">{t("checkName")}</TableHead>
                      <TableHead className="text-xs w-28">{t("checkChoice")}</TableHead>
                      <TableHead className="text-xs w-24 text-right">{t("checkConfidence")}</TableHead>
                      <TableHead className="text-xs">{t("checkEvidence")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {checks.map((c) => (
                      <TableRow key={`${c.name}-${c.choice}`} className="align-top">
                        <TableCell className="text-xs font-medium whitespace-nowrap">{c.name}</TableCell>
                        <TableCell><ChoiceChip choice={c.choice} /></TableCell>
                        <TableCell className="text-right text-xs font-mono whitespace-nowrap">
                          {typeof c.confidence === "number" ? c.confidence.toFixed(2) : "—"}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground break-words">{c.evidence ?? "—"}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            ) : (
              <div className="text-xs text-muted-foreground">{t("noChecks")}</div>
            )}
          </section>
        </div>
      </DialogContent>
    </Dialog>
  );
}
