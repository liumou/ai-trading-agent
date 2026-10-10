"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Brain, CheckCircle2, AlertTriangle, Lightbulb, Target, ShieldAlert, Sparkles, MessageSquare, Loader2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { toDate } from "@/lib/format";
import { showSuccess, showError } from "@/lib/toast";
import {
  type TradeReview, type TradeReviewClassification,
  getTradeReviewSummary, triggerTradeReview,
} from "@/lib/api";

/** 四分类徽章配色：skilled_win 绿 / correct_process 蓝 / lucky_win 琥珀 / real_mistake 红。 */
export const REVIEW_CLASS_COLORS: Record<string, string> = {
  skilled_win: "bg-green-500/15 text-green-600 dark:text-green-400 border-green-500/30",
  correct_process: "bg-blue-500/15 text-blue-600 dark:text-blue-400 border-blue-500/30",
  lucky_win: "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30",
  real_mistake: "bg-destructive/10 text-destructive border-destructive/30",
};

/** 四分类翻译 key：class{SkilledWin}Desc → tradeReview.json。 */
type ClassKey = "skilled_win" | "correct_process" | "lucky_win" | "real_mistake";
const CLASS_DESC_KEYS: Record<ClassKey, string> = {
  skilled_win: "classSkilledWinDesc",
  correct_process: "classCorrectProcessDesc",
  lucky_win: "classLuckyWinDesc",
  real_mistake: "classRealMistakeDesc",
};
function classificationDescKey(cls: string): string {
  return CLASS_DESC_KEYS[cls as ClassKey] ?? "classSkilledWinDesc";
}

/** snake_case 分类值 → PascalCase（skilled_win → SkilledWin），用于拼接翻译 key。
 *
 * 后端 classification 是 snake_case（如 skilled_win），翻译文件 key 是驼峰
 * （classShortSkilledWin）。直接 `首字母大写 + 原样` 会生成 classShortSkilled_win
 * 找不到 key → next-intl 渲染原始 key 字符串（历史页复盘列曾显示
 * `tradeReview.classShortSkilled_win`）。
 */
export function classKeyPascal(classification: string): string {
  return classification
    .split("_")
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join("");
}

function ClassificationBadge({ classification, flagged }: { classification: string | null; flagged?: boolean }) {
  const t = useTranslations("tradeReview");
  if (!classification) return null;
  const key = `class${classKeyPascal(classification)}` as const;
  return (
    <Badge className={cn("border", REVIEW_CLASS_COLORS[classification] ?? "border-border text-muted-foreground")}>
      {t(key)}
    </Badge>
  );
}

interface TradeReviewDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 复盘记录（null = 尚未触发 / 触发后轮询填充） */
  review: TradeReview | null;
  /** 触发请求体：trade_id（bot 单）或 ticket+account_login（手动/统一） */
  triggerBody: { trade_id?: number; ticket?: number; account_login?: string } | null;
  /** 触发后回调（父组件负责轮询刷新） */
  onTriggered?: () => void;
  /** 直接渲染一条既有复盘（查看模式，由 /history 传入） */
  initial?: TradeReview | null;
  /** 外部轮询状态：true 表示分析中 */
  polling?: boolean;
}

/** 历史订单 AI 深度复盘弹窗。
 *
 * 三态：未复盘 → 触发确认；运行中 → 轮询中状态；已完成 → 四分类徽章 + 根因/教训/建议
 * + 跨单模式统计行（近 N 天第几次 real_mistake）+ 用户心态备注（存本地）。
 *
 * 备注未接入后端持久化（后端 trade_reviews 无 note 列），暂存 localStorage：
 * key = `trade_review_note:{account_login}:{ticket}`，单机体验一致，跨端不强制同步。
 */
export function TradeReviewDialog({
  open, onOpenChange, review, triggerBody, onTriggered, initial, polling,
}: TradeReviewDialogProps) {
  const t = useTranslations("tradeReview");
  const [submitting, setSubmitting] = useState(false);
  const [summary, setSummary] = useState<{ real_mistakes: number; window_days: number } | null>(null);
  const [note, setNote] = useState("");
  const [noteSaved, setNoteSaved] = useState(false);
  const loadedNoteKey = useRef<string | null>(null);

  // 由触发结果（review）或外部 initial 决定当前展示
  const activeReview = review ?? initial ?? null;
  const accountLogin = triggerBody?.account_login ?? "0";

  // 加载备注（单 ticket）
  useEffect(() => {
    if (!open || !activeReview?.ticket) return;
    const key = `trade_review_note:${accountLogin}:${activeReview.ticket}`;
    if (loadedNoteKey.current !== key) {
      loadedNoteKey.current = key;
      setNote(localStorage.getItem(key) ?? "");
      setNoteSaved(false);
    }
  }, [open, activeReview?.ticket, accountLogin]);

  // 跨单模式统计：近 30 天 real_mistake 次数
  useEffect(() => {
    if (!open || !activeReview?.ticket || !accountLogin) return;
    let cancelled = false;
    getTradeReviewSummary(accountLogin, 30)
      .then((res) => {
        if (!cancelled && res.data) {
          setSummary({
            real_mistakes: res.data.real_mistakes ?? 0,
            window_days: res.data.window_days ?? 30,
          });
        }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [open, activeReview?.ticket, accountLogin]);

  const handleTrigger = async () => {
    if (!triggerBody) return;
    setSubmitting(true);
    try {
      await triggerTradeReview({ ...triggerBody, force: false });
      showSuccess(t("resultReady"), t("thinking"));
      onTriggered?.();
    } catch (e: unknown) {
      // 429 = 配额超限
      const status = (e as { response?: { status?: number } }).response?.status;
      showError(t("errorTrigger"), status === 429 ? t("quotaExceeded") : undefined);
    } finally {
      setSubmitting(false);
    }
  };

  const handleSaveNote = () => {
    if (!activeReview?.ticket) return;
    try {
      localStorage.setItem(`trade_review_note:${accountLogin}:${activeReview.ticket}`, note);
      setNoteSaved(true);
      showSuccess(t("noteSaved"));
    } catch {
      showError(t("noteSavedFailed"));
    }
  };

  const reviewData = activeReview?.review;
  const lessons = reviewData?.lessons ?? [];
  const improvementActions = reviewData?.improvement_actions ?? [];
  const lossCauses = reviewData?.loss_causes ?? [];
  const winCauses = reviewData?.win_causes ?? [];
  const causes = [...lossCauses, ...winCauses];
  const classification = activeReview?.classification ?? null;
  const isRunning = activeReview?.status === "running" || activeReview?.status === "pending" || !!polling;
  const isFailed = activeReview?.status === "failed";
  const isCompleted = activeReview?.status === "completed";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-4xl! max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex flex-wrap items-center gap-2 text-base">
            <Brain className="size-4 text-primary" />
            {t("title")}
            {classification && <ClassificationBadge classification={classification} flagged={activeReview?.flagged} />}
            {activeReview?.flagged && (
              <Badge variant="destructive" className="gap-1">
                <ShieldAlert className="size-3" />
                {t("flagged")}
              </Badge>
            )}
            {activeReview && (
              <span className="text-xs font-mono text-muted-foreground">
                #{activeReview.ticket}
              </span>
            )}
          </DialogTitle>
          <DialogDescription className="text-xs">
            {t("subtitle")}
          </DialogDescription>
        </DialogHeader>

        {/* 状态：运行中 / 失败 / 未触发 */}
        {isRunning && (
          <div className="flex flex-col items-center gap-3 py-8">
            <Loader2 className="size-8 animate-spin text-primary" />
            <p className="text-sm text-muted-foreground">{t("thinking")}</p>
          </div>
        )}

        {isFailed && (
          <div className="flex flex-col items-center gap-3 py-8">
            <AlertTriangle className="size-8 text-destructive" />
            <p className="text-sm text-muted-foreground">{t("reviewFailedDesc")}</p>
            {triggerBody && (
              <Button variant="outline" size="sm" onClick={handleTrigger} disabled={submitting}>
                {submitting ? <Loader2 className="size-3 animate-spin" /> : null}
                {t("retry")}
              </Button>
            )}
          </div>
        )}

        {!isRunning && !isFailed && !isCompleted && (
          <div className="flex flex-col items-center gap-3 py-8">
            <Sparkles className="size-8 text-primary" />
            <p className="text-sm text-muted-foreground">{t("triggerMessage")}</p>
            {triggerBody && (
              <Button onClick={handleTrigger} disabled={submitting}>
                {submitting ? <Loader2 className="size-3 animate-spin" /> : <Brain className="size-3" />}
                {submitting ? t("thinking") : t("trigger")}
              </Button>
            )}
          </div>
        )}

        {/* 完成：复盘内容 */}
        {isCompleted && reviewData && (
          <div className="space-y-5">
            {/* 经验教训提示条 */}
            {lessons.length > 0 && (
              <div className="flex items-start gap-2 rounded-xl bg-primary/5 border border-primary/15 px-3 py-2 text-xs text-muted-foreground">
                <Lightbulb className="size-3.5 mt-0.5 shrink-0 text-amber-500" />
                <div className="flex flex-col gap-1">
                  <span className="font-semibold text-foreground">{t("lessons")}</span>
                  <ul className="list-disc pl-4">
                    {lessons.map((l, i) => <li key={i}>{l}</li>)}
                  </ul>
                </div>
              </div>
            )}

            {/* 分类卡片 */}
            {classification && (
              <div className={cn(
                "rounded-xl border p-4 flex flex-col gap-2",
                REVIEW_CLASS_COLORS[classification]?.split(" ")[0],
              )}>
                <div className="flex items-center gap-2">
                  <ClassificationBadge classification={classification} flagged={activeReview?.flagged} />
                  <span className="text-xs font-semibold">{t(classificationDescKey(classification))}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className={cn("inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-semibold",
                    reviewData.reasoning_correct
                      ? "bg-green-500/15 text-green-600 dark:text-green-400"
                      : "bg-destructive/10 text-destructive")}>
                    {reviewData.reasoning_correct ? <CheckCircle2 className="size-3" /> : <AlertTriangle className="size-3" />}
                    {reviewData.reasoning_correct ? t("reasoningCorrect") : t("reasoningIncorrect")}
                  </span>
                  {activeReview?.confidence !== null && activeReview?.confidence !== undefined && (
                    <span className="text-xs text-muted-foreground">
                      {t("confidence")}: {Math.round((activeReview.confidence ?? 0) * 100)}%
                      {(activeReview.confidence ?? 1) < 0.5 && (
                        <Badge variant="outline" className="ml-1 border-amber-500/30 text-amber-500">{t("lowConfidence")}</Badge>
                      )}
                    </span>
                  )}
                </div>
              </div>
            )}

            {/* 跨单模式统计行 */}
            {summary && (
              <div className="flex items-center gap-2 rounded-xl border border-border bg-muted/20 px-3 py-2 text-xs">
                <Target className="size-3.5 shrink-0 text-primary" />
                <span className={cn(
                  "font-medium",
                  (summary.real_mistakes ?? 0) > 0 ? "text-destructive" : "text-muted-foreground",
                )}>
                  {(summary.real_mistakes ?? 0) > 0
                    ? t("patternBadge", { days: summary.window_days, count: summary.real_mistakes })
                    : t("patternNone", { days: summary.window_days })}
                </span>
              </div>
            )}

            {/* 根因 / 盈利原因 */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-2">
                <div className="text-xs font-semibold text-muted-foreground">
                  {classification === "skilled_win" || classification === "lucky_win"
                    ? t("winCauses")
                    : t("lossCauses")}
                </div>
                {causes.length > 0 ? (
                  <div className="flex flex-wrap gap-1.5">
                    {causes.map((c, i) => (
                      <Badge key={i} variant="secondary" className="font-normal">{c}</Badge>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">—</p>
                )}
              </div>
              <div className="space-y-2">
                <div className="text-xs font-semibold text-muted-foreground">{t("improvementActions")}</div>
                {improvementActions.length > 0 ? (
                  <ul className="list-disc pl-4 text-xs text-muted-foreground">
                    {improvementActions.map((a, i) => <li key={i}>{a}</li>)}
                  </ul>
                ) : (
                  <p className="text-xs text-muted-foreground">—</p>
                )}
              </div>
            </div>

            {/* 摘要 */}
            {reviewData.summary && (
              <div className="rounded-xl border border-border bg-card p-3">
                <div className="mb-1 text-xs font-semibold text-muted-foreground">{t("summaryLabel")}</div>
                <p className="text-sm leading-relaxed text-foreground/90">{reviewData.summary}</p>
              </div>
            )}

            {/* 用户心态备注 */}
            <div className="rounded-xl border border-border bg-card p-3">
              <div className="mb-1 flex items-center justify-between">
                <span className="text-xs font-semibold text-muted-foreground">
                  <MessageSquare className="size-3 inline mr-1" />
                  {t("myNote")}
                </span>
                {noteSaved && <span className="text-xs text-success">{t("noteSaved")}</span>}
              </div>
              <textarea
                value={note}
                onChange={(e) => { setNote(e.target.value); setNoteSaved(false); }}
                placeholder={t("myNotePlaceholder")}
                rows={3}
                className="w-full resize-y rounded-lg border border-border bg-black/40 p-3 text-xs leading-relaxed focus:outline-none focus:ring-2 focus:ring-primary/50"
                aria-label={t("myNote")}
              />
              <div className="mt-2 flex justify-end">
                <Button variant="outline" size="sm" onClick={handleSaveNote} disabled={!note.trim()}>
                  {t("saveNote")}
                </Button>
              </div>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
