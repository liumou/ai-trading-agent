"use client";

import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";

/** 审查结论徽章配色：APPROVED 绿 / CAUTION 黄 / REJECTED 红。 */
export const VERDICT_COLORS: Record<string, string> = {
  APPROVED: "bg-green-500/15 text-green-600 dark:text-green-400 border-green-500/30",
  CAUTION: "bg-yellow-500/15 text-yellow-600 dark:text-yellow-400 border-yellow-500/30",
  REJECTED: "bg-destructive/10 text-destructive border-destructive/30",
};

/** 生命周期状态徽章配色（与 ReviewResultCard 语义一致）。 */
export const STATUS_COLORS: Record<string, string> = {
  PENDING_REVIEW: "bg-blue-500/15 text-blue-600 dark:text-blue-400 border-blue-500/30",
  PENDING_CONFIRM: "bg-yellow-500/15 text-yellow-600 dark:text-yellow-400 border-yellow-500/30",
  EXECUTED: "bg-green-500/15 text-green-600 dark:text-green-400 border-green-500/30",
  FILLED: "bg-green-500/15 text-green-600 dark:text-green-400 border-green-500/30",
  REJECTED: "bg-destructive/10 text-destructive border-destructive/30",
  FAILED: "bg-destructive/10 text-destructive border-destructive/30",
  ERROR: "bg-destructive/10 text-destructive border-destructive/30",
  EXPIRED: "bg-muted text-muted-foreground border-border",
  CANCELLED: "bg-muted text-muted-foreground border-border",
  TIMEOUT: "bg-muted text-muted-foreground border-border",
};

export function VerdictBadge({ verdict }: { verdict?: string | null }) {
  const t = useTranslations("manual-reviews");
  if (!verdict) return <span className="text-xs text-muted-foreground">—</span>;
  const label =
    verdict === "APPROVED" ? t("verdictApproved")
    : verdict === "CAUTION" ? t("verdictCaution")
    : t("verdictRejected");
  return <Badge className={cn("border", VERDICT_COLORS[verdict] ?? "border-border text-muted-foreground")}>{label}</Badge>;
}

export function StatusBadge({ status }: { status?: string }) {
  if (!status) return <span className="text-xs text-muted-foreground">—</span>;
  return (
    <Badge className={cn("border font-mono", STATUS_COLORS[status] ?? "border-border text-muted-foreground")}>
      {status}
    </Badge>
  );
}
