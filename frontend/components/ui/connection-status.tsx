"use client";

import { useTranslations } from "next-intl";
import { useBotStore } from "@/store/botStore";
import { cn } from "@/lib/utils";

export function ConnectionStatus() {
  const t = useTranslations("ui");
  const wsConnected = useBotStore((s) => s.wsConnected);
  const lastSyncAt = useBotStore((s) => s.lastSyncAt);

  const statusColor = wsConnected
    ? "bg-emerald-500"
    : "bg-destructive";

  const statusLabel = wsConnected ? t("live") : t("offline");

  const timeAgo = lastSyncAt ? getTimeAgo(lastSyncAt, t) : null;

  return (
    <div className="flex items-center gap-2 min-w-0" title={timeAgo ? t("lastSync", { time: timeAgo }) : statusLabel}>
      <span className="relative flex size-2">
        <span
          className={cn(
            "absolute inline-flex size-full rounded-full opacity-75",
            wsConnected && "animate-ping",
            statusColor
          )}
        />
        <span
          className={cn("relative inline-flex size-2 rounded-full", statusColor)}
        />
      </span>
      <span className="text-[10px] font-semibold text-muted-foreground">
        {statusLabel}
      </span>
      {timeAgo && (
        <span className="text-[10px] text-muted-foreground/50 truncate">
          {timeAgo}
        </span>
      )}
    </div>
  );
}

type UITranslations = ReturnType<typeof useTranslations>;

function getTimeAgo(dateStr: string, t: UITranslations): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const seconds = Math.floor(diff / 1000);
  if (seconds < 5) return t("justNow");
  if (seconds < 60) return t("secondsAgo", { count: seconds });
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return t("minutesAgo", { count: minutes });
  return t("hoursAgo", { count: Math.floor(minutes / 60) });
}
