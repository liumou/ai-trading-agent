"use client";

import { useTranslations } from "next-intl";
import { Shield, Activity, TrendingUp, GitBranch, PieChart } from "lucide-react";
import { cn } from "@/lib/utils";

interface QuantSummaryStripProps {
  var95?: number;
  regime?: string;
  regimeProb?: number;
  volLevel?: "low" | "normal" | "high";
  correlationStatus?: "stable" | "shifting" | "breakdown";
  portfolioStatus?: "balanced" | "concentrated" | "unknown";
  className?: string;
}

export function QuantSummaryStrip({
  var95,
  regime,
  regimeProb,
  volLevel,
  correlationStatus,
  portfolioStatus,
  className,
}: QuantSummaryStripProps) {
  const t = useTranslations("quant");
  const tVolLevel = useTranslations("quant.volLevel");
  const tCorrStatus = useTranslations("quant.correlationStatus");
  const tPortfolioStatus = useTranslations("quant.portfolioStatus");
  const items = [
    {
      icon: Shield,
      label: t("summary.var"),
      value: var95 !== undefined ? `${(var95 * 100).toFixed(1)}%` : "--",
      color: var95 && var95 > 0.03 ? "text-red-400" : "text-green-400",
    },
    {
      icon: TrendingUp,
      label: t("summary.regime"),
      value: regime
        ? `${regime.replace("trending_", "T-").replace("_vol", "")} ${regimeProb ? Math.round(regimeProb * 100) + "%" : ""}`
        : "--",
      color: regime?.includes("trending") ? "text-blue-400" : "text-yellow-400",
    },
    {
      icon: Activity,
      label: t("summary.vol"),
      value: volLevel ? (tVolLevel.has(volLevel) ? tVolLevel(volLevel) : volLevel) : "--",
      color: volLevel === "high" ? "text-orange-400" : volLevel === "low" ? "text-cyan-400" : "text-muted-foreground",
    },
    {
      icon: GitBranch,
      label: t("summary.corr"),
      value: correlationStatus ? (tCorrStatus.has(correlationStatus) ? tCorrStatus(correlationStatus) : correlationStatus) : "--",
      color: correlationStatus === "stable" ? "text-green-400" : correlationStatus === "breakdown" ? "text-red-400" : "text-yellow-400",
    },
    {
      icon: PieChart,
      label: t("summary.portfolio"),
      value: portfolioStatus ? (tPortfolioStatus.has(portfolioStatus) ? tPortfolioStatus(portfolioStatus) : portfolioStatus) : "--",
      color: portfolioStatus === "balanced" ? "text-green-400" : "text-yellow-400",
    },
  ];

  return (
    <div className={cn("flex gap-2 overflow-x-auto pb-1", className)}>
      {items.map((item) => (
        <div
          key={item.label}
          className="flex items-center gap-1.5 rounded-lg border border-border bg-card px-2.5 py-1.5 min-w-fit"
        >
          <item.icon className="size-3 text-muted-foreground" />
          <span className="text-[10px] text-muted-foreground font-medium">{item.label}:</span>
          <span className={cn("text-[10px] font-bold", item.color)}>{item.value}</span>
        </div>
      ))}
    </div>
  );
}
