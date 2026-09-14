"use client";

import { useState, useRef, useEffect } from "react";
import { Bell, AlertCircle, TrendingUp, Zap, Info } from "lucide-react";
import { useTranslations, useLocale } from "next-intl";
import { useBotStore } from "@/store/botStore";
import type { BotEvent } from "@/store/botStore";
import { cn } from "@/lib/utils";
import { translateServerText } from "@/lib/serverText";

const eventIcons: Record<string, typeof Info> = {
  signal: TrendingUp,
  trade: TrendingUp,
  error: AlertCircle,
  warning: AlertCircle,
  system: Zap,
  TRADE_OPENED: TrendingUp,
  TRADE_CLOSED: TrendingUp,
  SIGNAL_DETECTED: TrendingUp,
  ORDER_FAILED: AlertCircle,
  ERROR: AlertCircle,
  AI_AGENT_ERROR: AlertCircle,
  CIRCUIT_BREAKER: AlertCircle,
};

const eventColors: Record<string, string> = {
  signal: "text-primary",
  trade: "text-success",
  error: "text-destructive",
  warning: "text-warning",
  system: "text-muted-foreground",
  TRADE_OPENED: "text-success",
  TRADE_CLOSED: "text-success",
  SIGNAL_DETECTED: "text-primary",
  ORDER_FAILED: "text-destructive",
  ERROR: "text-destructive",
  AI_AGENT_ERROR: "text-destructive",
  CIRCUIT_BREAKER: "text-destructive",
};

function getTimeAgo(dateStr: string, t: ReturnType<typeof useTranslations>): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return t("justNow");
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return t("minutesAgo", { count: minutes });
  const hours = Math.floor(minutes / 60);
  return t("hoursAgo", { count: hours });
}

export function NotificationCenter() {
  const t = useTranslations("ui");
  const locale = useLocale();
  const [open, setOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const events = useBotStore((s) => s.events);
  const unreadCount = useBotStore((s) => s.unreadEventCount);
  const markRead = useBotStore((s) => s.markEventsRead);

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const handleToggle = () => {
    if (!open) {
      markRead();
    }
    setOpen((prev) => !prev);
  };

  const displayEvents = events.slice(0, 20);

  return (
    <div className="relative" ref={panelRef}>
      <button
        type="button"
        onClick={handleToggle}
        className="relative flex items-center justify-center size-8 rounded-full text-muted-foreground hover:text-foreground hover:bg-sidebar-accent transition-colors"
        aria-label={t("notifications")}
      >
        <Bell className="size-4" />
        {unreadCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 flex items-center justify-center min-w-[16px] h-4 rounded-full bg-destructive text-[9px] font-bold text-white px-1 animate-badge-in">
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="fixed left-4 bottom-36 w-72 sm:w-80 rounded-xl border border-border bg-card shadow-xl z-200 animate-in fade-in-0 zoom-in-95 slide-in-from-bottom-2 duration-150">
          <div className="flex items-center justify-between px-3 py-2.5 border-b border-border">
            <span className="text-xs font-semibold text-foreground">
              {t("notifications")}
            </span>
            <span className="text-[10px] text-muted-foreground">
              {t("eventsCount", { count: events.length })}
            </span>
          </div>

          <div className="max-h-64 overflow-y-auto">
            {displayEvents.length === 0 ? (
              <p className="text-xs text-muted-foreground text-center py-6">
                {t("noEventsYet")}
              </p>
            ) : (
              displayEvents.map((event: BotEvent, i: number) => {
                const Icon =
                  eventIcons[event.type] || Info;
                const color =
                  eventColors[event.type] || "text-muted-foreground";
                return (
                  <div
                    key={i}
                    className="flex items-start gap-2.5 px-3 py-2 hover:bg-muted/50 transition-colors"
                  >
                    <Icon className={cn("size-3.5 mt-0.5 shrink-0", color)} />
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-foreground truncate">
                        {translateServerText(event.message, locale)}
                      </p>
                      <p className="text-[10px] text-muted-foreground">
                        {getTimeAgo(event.timestamp, t)}
                      </p>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
