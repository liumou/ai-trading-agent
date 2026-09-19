"use client";

import { useTranslations } from "next-intl";
import { AlertTriangle, Ban, CheckCircle2, Loader2, ShieldCheck, XCircle } from "lucide-react";

export interface ReviewState {
  status: string; // PENDING_REVIEW / PENDING_CONFIRM / REJECTED / EXPIRED / EXECUTED / FAILED
  kind?: string;
  reason?: string;
  reviewId?: number;
  ruleFlags?: { flag: string; severity: string; detail?: string }[];
  llm?: {
    verdict?: string;
    confidence?: number;
    reasoning?: string;
    risk_flags?: string[];
    emotional_indicators?: string[];
  };
}

export function ReviewResultCard({
  review,
  onConfirm,
  onResubmit,
  confirming,
}: {
  review: ReviewState;
  onConfirm?: () => void;
  onResubmit?: () => void;
  confirming?: boolean;
}) {
  const t = useTranslations("trading");
  const llm = review.llm;
  const kindLabel = review.kind ? ` (${review.kind})` : "";

  if (review.status === "PENDING_REVIEW") {
    return (
      <div className="rounded-xl border border-blue-500/30 bg-blue-500/5 p-4 flex items-center gap-3">
        <Loader2 className="size-5 animate-spin text-blue-500" />
        <div>
          <div className="text-sm font-medium">{t("reviewing")}</div>
          {review.ruleFlags && review.ruleFlags.length > 0 && (
            <div className="mt-1 space-y-0.5">
              {review.ruleFlags.map((f) => (
                <div key={f.flag} className="text-xs text-muted-foreground">
                  · {f.flag}
                  {f.detail ? ` — ${f.detail}` : ""}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  if (review.status === "REJECTED") {
    return (
      <div className="rounded-xl border border-destructive/40 bg-destructive/5 p-4 space-y-2">
        <div className="flex items-center gap-2 text-destructive font-semibold">
          <Ban className="size-5" />
          {t("verdictRejected")}
          {kindLabel && <span className="text-xs font-normal text-muted-foreground">{kindLabel}</span>}
        </div>
        <div className="text-sm">
          <span className="text-muted-foreground">{t("blockedReason")}: </span>
          {review.reason}
        </div>
        {llm?.emotional_indicators && llm.emotional_indicators.length > 0 && (
          <div className="text-xs text-muted-foreground">
            {t("emotionalIndicators")}: {llm.emotional_indicators.join("; ")}
          </div>
        )}
        {review.kind === "llm_unavailable" && onResubmit && (
          <button
            type="button"
            onClick={onResubmit}
            className="rounded-md border border-border px-3 py-1.5 text-xs font-medium hover:bg-muted"
          >
            {t("resubmit")}
          </button>
        )}
      </div>
    );
  }

  if (review.status === "PENDING_CONFIRM") {
    return (
      <div className="rounded-xl border border-yellow-500/40 bg-yellow-500/5 p-4 space-y-2">
        <div className="flex items-center gap-2 text-yellow-600 dark:text-yellow-400 font-semibold">
          <AlertTriangle className="size-5" />
          {t("verdictCaution")}
        </div>
        {llm?.reasoning && <div className="text-sm">{llm.reasoning}</div>}
        {llm?.risk_flags && llm.risk_flags.length > 0 && (
          <div className="text-xs text-muted-foreground">{t("riskFlags")}: {llm.risk_flags.join(", ")}</div>
        )}
        {onConfirm && (
          <button
            type="button"
            onClick={onConfirm}
            disabled={confirming}
            className="rounded-md bg-yellow-600 px-4 py-2 text-sm font-medium text-white hover:bg-yellow-600/90 disabled:opacity-50"
          >
            {confirming ? t("confirming") : t("confirmBtn")}
          </button>
        )}
      </div>
    );
  }

  if (review.status === "EXECUTED") {
    return (
      <div className="rounded-xl border border-green-500/40 bg-green-500/5 p-4 space-y-1">
        <div className="flex items-center gap-2 text-green-600 dark:text-green-400 font-semibold">
          <CheckCircle2 className="size-5" />
          {t("executed")}
        </div>
        {llm?.reasoning && (
          <div className="text-xs text-muted-foreground">
            <ShieldCheck className="mr-1 inline size-3.5" />
            {t("reasoning")}: {llm.reasoning}
          </div>
        )}
      </div>
    );
  }

  if (review.status === "FAILED") {
    return (
      <div className="rounded-xl border border-destructive/40 bg-destructive/5 p-4">
        <div className="flex items-center gap-2 text-destructive font-semibold">
          <XCircle className="size-5" />
          {t("failed")}
        </div>
        <div className="text-sm text-muted-foreground">{review.reason}</div>
      </div>
    );
  }

  if (review.status === "EXPIRED") {
    return (
      <div className="rounded-xl border border-border bg-card p-4 space-y-2">
        <div className="flex items-center gap-2 font-semibold">
          <AlertTriangle className="size-5 text-muted-foreground" />
          {t("expired")}
        </div>
        {onResubmit && (
          <button
            type="button"
            onClick={onResubmit}
            className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90"
          >
            {t("resubmit")}
          </button>
        )}
      </div>
    );
  }

  return null;
}
