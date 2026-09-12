"use client";

import { useTranslations } from "next-intl";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface ErrorStateProps {
  message?: string;
  onRetry?: () => void;
  className?: string;
}

export function ErrorState({
  message,
  onRetry,
  className,
}: ErrorStateProps) {
  const t = useTranslations("ui");
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center py-8 px-4 text-center animate-fade-in",
        className
      )}
    >
      <div className="size-10 rounded-xl bg-destructive/10 flex items-center justify-center mb-3">
        <AlertTriangle className="size-5 text-destructive" />
      </div>
      <p className="text-sm font-semibold text-foreground mb-1">{t("error")}</p>
      <p className="text-xs text-muted-foreground max-w-xs mb-4">{message ?? t("failedToLoad")}</p>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RotateCcw className="size-3.5" />
          {t("retry")}
        </Button>
      )}
    </div>
  );
}
