"use client";

import { useTranslations } from "next-intl";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";
import { cn } from "@/lib/utils";

type Props = {
  label: string;
  score: number;
  confidence?: number;
  size?: "sm" | "md" | "lg";
  /** 情绪判定来源：laya（预筛命中）| llm（Claude 深析）。显示一个小标记。 */
  engine?: string;
};

const colorMap: Record<string, string> = {
  bullish: "bg-success/10 text-success dark:bg-green-500/10 dark:text-green-400 border-success/20 dark:border-green-500/20",
  bearish: "bg-destructive/10 text-destructive border-destructive/20",
  neutral: "bg-muted text-muted-foreground border-border",
};

const iconMap: Record<string, typeof TrendingUp> = {
  bullish: TrendingUp,
  bearish: TrendingDown,
  neutral: Minus,
};

const sizeMap = {
  sm: { badge: "text-[11px] px-2 py-0.5 gap-1", icon: "size-3" },
  md: { badge: "text-xs px-2.5 py-1 gap-1.5", icon: "size-3.5" },
  lg: { badge: "text-sm px-3 py-1.5 gap-2", icon: "size-4" },
};

export default function SentimentBadge({ label, score, confidence, size = "md", engine }: Props) {
  const t = useTranslations("ai.sentiment");
  const color = colorMap[label] || colorMap.neutral;
  const Icon = iconMap[label] || iconMap.neutral;
  const s = sizeMap[size];

  return (
    <span className={cn("inline-flex items-center rounded-full border font-semibold", color, s.badge)}>
      <Icon className={s.icon} />
      <span>{t.has(label) ? t(label) : label}</span>
      <span className="opacity-60">
        {score > 0 ? "+" : ""}
        {score.toFixed(2)}
      </span>
      {confidence !== undefined && (
        <span className="opacity-40">{(confidence * 100).toFixed(0)}%</span>
      )}
      {engine && (
        <span className={cn("opacity-50 uppercase tracking-wide", engine === "laya" ? "text-info" : "")}>
          {engine}
        </span>
      )}
    </span>
  );
}
