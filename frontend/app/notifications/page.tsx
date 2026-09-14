"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslations, useLocale } from "next-intl";
import { getBotEvents } from "@/lib/api";
import { Bell } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { PageInstructions } from "@/components/layout/PageInstructions";
import { EmptyState } from "@/components/ui/empty-state";
import { formatDate } from "@/lib/format";
import { translateServerText } from "@/lib/serverText";

interface BotEvent {
  id: number;
  type: string;
  message: string;
  created_at: string;
}

const EVENT_COLORS: Record<string, string> = {
  TRADE_OPENED: "text-green-600 dark:text-green-400",
  TRADE_CLOSED: "text-amber-600 dark:text-amber-400",
  SIGNAL_DETECTED: "text-blue-600 dark:text-blue-400",
  TRADE_BLOCKED: "text-orange-600 dark:text-orange-400",
  ORDER_FAILED: "text-red-600 dark:text-red-400",
  ERROR: "text-red-600 dark:text-red-400",
  AI_AGENT_ERROR: "text-red-600 dark:text-red-400",
  CIRCUIT_BREAKER: "text-red-700 dark:text-red-300",
  SETTINGS_CHANGED: "text-purple-600 dark:text-purple-400",
  STRATEGY_CHANGED: "text-purple-600 dark:text-purple-400",
  STARTED: "text-green-600 dark:text-green-400",
  STOPPED: "text-gray-600 dark:text-gray-400",
};

const PAGE_SIZE = 30;

export default function NotificationsPage() {
  const t = useTranslations("notifications");
  const locale = useLocale();
  const [events, setEvents] = useState<BotEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(7);
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const sentinelRef = useRef<HTMLTableRowElement | null>(null);

  useEffect(() => {
    const fetchEvents = async () => {
      setLoading(true);
      try {
        const res = await getBotEvents({ days, event_type: typeFilter || undefined, limit: 500 });
        setEvents(res.data.events || []);
        setVisibleCount(PAGE_SIZE);
      } catch {
        setEvents([]);
      } finally {
        setLoading(false);
      }
    };
    fetchEvents();
  }, [days, typeFilter]);

  const visibleEvents = useMemo(
    () => events.slice(0, visibleCount),
    [events, visibleCount],
  );
  const hasMore = visibleCount < events.length;

  useEffect(() => {
    if (!hasMore) return;
    const node = sentinelRef.current;
    if (!node) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisibleCount((n) => Math.min(n + PAGE_SIZE, events.length));
        }
      },
      { rootMargin: "200px" },
    );
    io.observe(node);
    return () => io.disconnect();
  }, [hasMore, events.length]);

  const eventTypes = [
    "", "STARTED", "STOPPED", "TRADE_OPENED", "TRADE_CLOSED",
    "SIGNAL_DETECTED", "TRADE_BLOCKED", "ORDER_FAILED", "ERROR",
    "AI_AGENT_ERROR", "CIRCUIT_BREAKER", "SETTINGS_CHANGED", "STRATEGY_CHANGED",
  ];

  return (
    <div className="p-4 sm:p-6 xl:p-8 space-y-5 sm:space-y-6 page-enter">
      <PageHeader title={t("title")} subtitle={t("subtitle")} />

      <PageInstructions
        items={[
          t("instructions.item1"),
          t("instructions.item2"),
        ]}
      />

      <div className="flex flex-wrap gap-3">
        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="rounded-md border border-border bg-background px-3 py-1.5 text-sm"
        >
          <option value={1}>{t("last24h")}</option>
          <option value={3}>{t("last3days")}</option>
          <option value={7}>{t("last7days")}</option>
          <option value={14}>{t("last14days")}</option>
          <option value={30}>{t("last30days")}</option>
        </select>

        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          className="rounded-md border border-border bg-background px-3 py-1.5 text-sm"
        >
          <option value="">{t("allTypes")}</option>
          {eventTypes.filter(Boolean).map((type) => (
            <option key={type} value={type}>{t.has(`eventTypes.${type}`) ? t(`eventTypes.${type}`) : type.replace(/_/g, " ")}</option>
          ))}
        </select>

        <span className="text-xs text-muted-foreground self-center ml-auto">
          {t("eventsCount", { visible: visibleEvents.length, total: events.length })}
        </span>
      </div>

      {loading ? (
        <div className="text-center py-12 text-muted-foreground">{t("loading")}</div>
      ) : events.length === 0 ? (
        <EmptyState icon={Bell} heading={t("noEvents")} description={t("noEventsDesc")} />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                <th className="pb-2 pr-4 font-medium">{t("colTime")}</th>
                <th className="pb-2 pr-4 font-medium">{t("colType")}</th>
                <th className="pb-2 font-medium">{t("colMessage")}</th>
              </tr>
            </thead>
            <tbody>
              {visibleEvents.map((e) => (
                <tr key={e.id} className="border-b border-border/50">
                  <td className="py-2 pr-4 whitespace-nowrap text-muted-foreground">
                    {formatDate(e.created_at, locale)}
                  </td>
                  <td className={`py-2 pr-4 whitespace-nowrap font-medium ${EVENT_COLORS[e.type] || ""}`}>
                    {t.has(`eventTypes.${e.type}`) ? t(`eventTypes.${e.type}`) : e.type.replace(/_/g, " ")}
                  </td>
                  <td className="py-2">{translateServerText(e.message, locale)}</td>
                </tr>
              ))}
              {hasMore && (
                <tr ref={sentinelRef}>
                  <td colSpan={3} className="py-4 text-center text-xs text-muted-foreground">
                    {t("loadingMore")}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
