"use client";

import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";

interface SignalConfidenceBarProps {
  confirmations: { name: string; passed: boolean; confidence: number }[];
  required?: number;
  className?: string;
}

export function SignalConfidenceBar({
  confirmations,
  required = 3,
  className,
}: SignalConfidenceBarProps) {
  const t = useTranslations("quant");
  const passed = confirmations.filter((c) => c.passed).length;
  const total = confirmations.length;

  return (
    <div className={cn("space-y-1", className)}>
      <div className="flex items-center justify-between">
        <span className="text-[10px] text-muted-foreground font-medium">
          {t("confirmations")}
        </span>
        <span
          className={cn(
            "text-[10px] font-bold",
            passed >= required ? "text-green-400" : "text-yellow-400",
          )}
        >
          {t("confirmationCount", { passed, total, required })}
        </span>
      </div>
      <div className="flex gap-0.5">
        {confirmations.map((c) => (
          <div
            key={c.name}
            className={cn(
              "h-1.5 rounded-full flex-1 transition-colors",
              c.passed ? "bg-green-500" : "bg-muted",
            )}
            title={t("confirmationTooltip", {
              name: c.name,
              status: t(`confirmationStatus.${c.passed ? "pass" : "fail"}`),
              pct: Math.round(c.confidence * 100),
            })}
          />
        ))}
      </div>
    </div>
  );
}
