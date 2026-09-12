"use client";

import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";

const CLASSIFICATIONS = {
  skilled_win: { key: "skilledWin", color: "text-green-500", bg: "bg-green-500/10" },
  correct_process: { key: "correctProcess", color: "text-blue-500", bg: "bg-blue-500/10" },
  lucky_win: { key: "luckyWin", color: "text-yellow-500", bg: "bg-yellow-500/10" },
  real_mistake: { key: "realMistake", color: "text-red-500", bg: "bg-red-500/10" },
} as const;

interface AccountabilityBadgeProps {
  classification: string;
  className?: string;
}

export function AccountabilityBadge({ classification, className }: AccountabilityBadgeProps) {
  const t = useTranslations("quant.accountability");
  const config = CLASSIFICATIONS[classification as keyof typeof CLASSIFICATIONS] || CLASSIFICATIONS.correct_process;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold",
        config.bg,
        config.color,
        className,
      )}
    >
      {t.has(config.key) ? t(config.key) : classification}
    </span>
  );
}
