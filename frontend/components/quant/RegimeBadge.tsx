"use client";

import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";

const REGIME_CONFIG: Record<string, { key: string; color: string; bg: string }> = {
  trending_high_vol: { key: "trending", color: "text-orange-500", bg: "bg-orange-500/10" },
  trending_low_vol: { key: "trending", color: "text-blue-500", bg: "bg-blue-500/10" },
  ranging: { key: "ranging", color: "text-yellow-500", bg: "bg-yellow-500/10" },
  normal: { key: "normal", color: "text-muted-foreground", bg: "bg-muted" },
};

interface RegimeBadgeProps {
  regime: string;
  probability?: number;
  size?: "sm" | "md";
  className?: string;
}

export function RegimeBadge({ regime, probability, size = "sm", className }: RegimeBadgeProps) {
  const t = useTranslations("quant.regime");
  const config = REGIME_CONFIG[regime] || REGIME_CONFIG.normal;
  const isSmall = size === "sm";

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full font-semibold",
        config.bg,
        config.color,
        isSmall ? "px-2 py-0.5 text-[10px]" : "px-3 py-1 text-xs",
        className,
      )}
    >
      <span className={cn("size-1.5 rounded-full", config.color.replace("text-", "bg-"))} />
      {t.has(config.key) ? t(config.key) : regime}
      {probability !== undefined && (
        <span className="opacity-70">{Math.round(probability * 100)}%</span>
      )}
    </span>
  );
}
