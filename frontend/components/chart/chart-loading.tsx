"use client";

import { useTranslations } from "next-intl";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

interface ChartLoadingProps {
  className?: string;
}

export function ChartLoading({ className }: ChartLoadingProps) {
  const t = useTranslations("charts");
  return (
    <div
      className={cn(
        "relative flex items-center justify-center rounded-xl overflow-hidden",
        className
      )}
    >
      <Skeleton className="absolute inset-0 rounded-xl" />
      <p className="relative text-xs text-muted-foreground font-medium z-10">
        {t("loading")}
      </p>
    </div>
  );
}
