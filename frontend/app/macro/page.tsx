"use client";

import { useEffect, useState, useCallback } from "react";
import { useTranslations } from "next-intl";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Globe, Calendar, RefreshCw, ArrowRightLeft } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { PageInstructions } from "@/components/layout/PageInstructions";
import { getMacroLatest, getMacroCorrelations, getMacroEvents, collectMacro } from "@/lib/api";
import { showSuccess, showError } from "@/lib/toast";
import { EmptyState } from "@/components/ui/empty-state";

export default function MacroPage() {
  const t = useTranslations("macro");
  const [snapshot, setSnapshot] = useState<Record<string, { name: string; value: number; date: string }>>({});
  const [correlations, setCorrelations] = useState<Record<string, { name: string; correlation: number; data_points: number }>>({});
  const [events, setEvents] = useState<{ type: string; name: string; date: string; impact: string; note: string }[]>([]);
  const [loading, setLoading] = useState(true);
  const [collecting, setCollecting] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const [snapRes, corrRes, eventRes] = await Promise.all([
        getMacroLatest().catch(() => null),
        getMacroCorrelations().catch(() => null),
        getMacroEvents(30).catch(() => null),
      ]);
      if (snapRes?.data) setSnapshot(snapRes.data);
      if (corrRes?.data && !corrRes.data.error) setCorrelations(corrRes.data);
      if (eventRes?.data) setEvents(Array.isArray(eventRes.data) ? eventRes.data : []);
    } catch (e) { console.error(e); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleCollect = async () => {
    setCollecting(true);
    try {
      await collectMacro();
      await fetchData();
      showSuccess(t("collected"));
    } catch (e) {
      console.error(e);
      showError(t("collectFailed"));
    }
    finally { setCollecting(false); }
  };

  if (loading) {
    return (
      <div className="p-4 sm:p-6 xl:p-8 space-y-5 sm:space-y-6">
        <Skeleton className="h-8 w-48" />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Skeleton className="h-60 rounded-2xl" />
          <Skeleton className="h-60 rounded-2xl" />
        </div>
      </div>
    );
  }

  const snapEntries = Object.entries(snapshot);
  const corrEntries = Object.entries(correlations);

  return (
    <div className="p-4 sm:p-6 xl:p-8 space-y-5 sm:space-y-6 page-enter">
      <PageHeader title={t("title")} subtitle={t("subtitle")}>
        <Button onClick={handleCollect} disabled={collecting} variant="outline" size="sm" className="rounded-full">
          <RefreshCw className={`size-4 mr-1.5 ${collecting ? "animate-spin" : ""}`} />
          {collecting ? t("collecting") : t("refreshData")}
        </Button>
      </PageHeader>

      <PageInstructions

        items={[
          t("instructions.item1"),
          t("instructions.item2"),
        ]}
      />

      {/* Macro Snapshot */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-bold flex items-center gap-2">
            <Globe className="size-4 text-primary-foreground dark:text-primary" />
            {t("indicators")}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {snapEntries.length > 0 ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {snapEntries.map(([id, data]) => (
                <div key={id} className="border border-border rounded-2xl p-4 space-y-1">
                  <p className="text-xs text-muted-foreground font-medium">{data.name}</p>
                  <p className="text-xl font-black">{typeof data.value === "number" ? data.value.toFixed(2) : data.value}</p>
                  <p className="text-xs text-muted-foreground font-medium">{data.date}</p>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState icon={Globe} heading={t("noMacroData")} description={t("noMacroDataDesc")} />
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Correlations */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-bold flex items-center gap-2">
              <ArrowRightLeft className="size-4 text-primary-foreground dark:text-primary" />
              {t("correlations")}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {corrEntries.length > 0 ? (
              <div className="space-y-3">
                {corrEntries.map(([id, data]) => {
                  const corr = data.correlation;
                  const isNeg = corr < 0;
                  const absCorr = Math.abs(corr);
                  const strength = absCorr > 0.7 ? t("strengthStrong") : absCorr > 0.4 ? t("strengthModerate") : t("strengthWeak");
                  return (
                    <div key={id} className="space-y-1">
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-semibold">{data.name}</span>
                        <div className="flex items-center gap-2">
                          <Badge variant="outline" className="text-xs rounded-full">{t("pts", { count: data.data_points })}</Badge>
                          <span className={`text-sm font-mono font-bold ${isNeg ? "text-destructive" : "text-success dark:text-green-400"}`}>
                            {corr > 0 ? "+" : ""}{corr.toFixed(3)}
                          </span>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
                          <div
                            className={`h-full rounded-full ${isNeg ? "bg-destructive" : "bg-success dark:bg-green-400"}`}
                            style={{ width: `${absCorr * 100}%` }}
                          />
                        </div>
                        <span className="text-xs text-muted-foreground w-16 text-right font-medium">{strength}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground text-center py-8 font-medium">{t("collectForCorrelations")}</p>
            )}
          </CardContent>
        </Card>

        {/* Events */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-bold flex items-center gap-2">
              <Calendar className="size-4 text-primary-foreground dark:text-primary" />
              {t("upcomingEvents")}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {events.length > 0 ? (
              <div className="space-y-3">
                {events.map((event, i) => {
                  const daysUntil = Math.ceil(
                    (new Date(event.date).getTime() - Date.now()) / (1000 * 60 * 60 * 24)
                  );
                  return (
                    <div key={i} className="border border-border rounded-2xl p-3 space-y-1">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <Badge className={`rounded-full font-semibold ${
                            event.type === "FOMC" ? "bg-purple-100 text-purple-700 dark:bg-purple-500/20 dark:text-purple-400" :
                            event.type === "NFP" ? "bg-blue-100 text-blue-700 dark:bg-blue-500/20 dark:text-blue-400" :
                            "bg-orange-100 text-orange-700 dark:bg-orange-500/20 dark:text-orange-400"
                          }`}>
                            {event.type}
                          </Badge>
                          <span className="text-sm font-semibold">{event.name}</span>
                        </div>
                        <span className="text-xs text-muted-foreground font-medium">
                          {daysUntil <= 0 ? t("today") : daysUntil === 1 ? t("tomorrow") : t("inDays", { count: daysUntil })}
                        </span>
                      </div>
                      <p className="text-[11px] text-muted-foreground font-medium">{event.note}</p>
                      <p className="text-xs text-muted-foreground/60 font-medium">{event.date}</p>
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground text-center py-8 font-medium">{t("noUpcomingEvents")}</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
