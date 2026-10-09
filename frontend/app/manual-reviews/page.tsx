"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { ShieldCheck, RefreshCw, ChevronLeft, ChevronRight, Eye } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { PageInstructions } from "@/components/layout/PageInstructions";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { EmptyState } from "@/components/ui/empty-state";
import { getManualReviews, type ManualReview } from "@/lib/api";
import { showError } from "@/lib/toast";
import { toDate } from "@/lib/format";
import { ReviewHistoryDialog } from "@/components/trading/ReviewHistoryDialog";
import { StatusBadge, VerdictBadge } from "@/components/trading/ReviewBadges";

const PAGE_SIZE = 20;
const DAY_OPTIONS = [7, 30, 90, 365];
const STATUS_OPTIONS = [
  "PENDING_REVIEW", "PENDING_CONFIRM", "EXECUTED", "REJECTED", "EXPIRED", "FAILED", "CANCELLED",
];

export default function ManualReviewsPage() {
  const t = useTranslations("manual-reviews");

  const [days, setDays] = useState(30);
  const [status, setStatus] = useState("");
  const [verdict, setVerdict] = useState("");
  const [symbol, setSymbol] = useState("");
  const [symbols, setSymbols] = useState<string[]>([]);
  const [offset, setOffset] = useState(0);
  const [reviews, setReviews] = useState<ManualReview[]>([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState({ approved: 0, caution: 0, rejected: 0 });
  const [loading, setLoading] = useState(true);
  const [detail, setDetail] = useState<ManualReview | null>(null);

  // 品种下拉：一次性拉全量窗口收集去重品种（200 条足够枚举）
  useEffect(() => {
    getManualReviews({ days: 365, limit: 200 })
      .then((res) => {
        const seen = new Set<string>();
        (res.data.reviews ?? []).forEach((r) => {
          if (r.symbol) seen.add(r.symbol);
        });
        setSymbols([...seen].sort());
      })
      .catch(() => {});
  }, []);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const res = await getManualReviews({
        days, limit: PAGE_SIZE, offset,
        status: status || undefined,
        verdict: verdict || undefined,
        symbol: symbol || undefined,
      });
      setReviews(res.data.reviews ?? []);
      setTotal(res.data.total ?? 0);
      setStats(res.data.stats ?? { approved: 0, caution: 0, rejected: 0 });
    } catch (e) {
      console.error(e);
      showError(t("loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [days, status, verdict, symbol, offset, t]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // 过滤条件变化时回到第一页（React 批量更新，effect 只跑一次）
  const applyFilter = (kind: "days" | "status" | "verdict" | "symbol", value: string | number) => {
    setOffset(0);
    if (kind === "days") setDays(value as number);
    if (kind === "status") setStatus(value as string);
    if (kind === "verdict") setVerdict(value as string);
    if (kind === "symbol") setSymbol(value as string);
  };

  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="p-4 sm:p-6 xl:p-8 space-y-5 sm:space-y-6 page-enter">
      <PageHeader title={t("title")} subtitle={t("subtitle")}>
        <div className="flex gap-1 border border-border rounded-2xl p-1 bg-card">
          {DAY_OPTIONS.map((d) => (
            <Button
              key={d}
              variant={days === d ? "default" : "ghost"}
              size="sm"
              onClick={() => applyFilter("days", d)}
              className={`rounded-xl ${days === d ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}
            >
              {t("dayOption", { days: d })}
            </Button>
          ))}
        </div>
        <select
          value={status}
          onChange={(e) => applyFilter("status", e.target.value)}
          className="rounded-md border border-border bg-background px-3 py-1.5 text-sm"
        >
          <option value="">{t("statusAll")}</option>
          {STATUS_OPTIONS.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select
          value={verdict}
          onChange={(e) => applyFilter("verdict", e.target.value)}
          className="rounded-md border border-border bg-background px-3 py-1.5 text-sm"
        >
          <option value="">{t("verdictAll")}</option>
          <option value="APPROVED">{t("verdictApproved")}</option>
          <option value="CAUTION">{t("verdictCaution")}</option>
          <option value="REJECTED">{t("verdictRejected")}</option>
        </select>
        <select
          value={symbol}
          onChange={(e) => applyFilter("symbol", e.target.value)}
          className="rounded-md border border-border bg-background px-3 py-1.5 text-sm"
        >
          <option value="">{t("symbolAll")}</option>
          {symbols.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <Button variant="outline" size="sm" onClick={() => fetchData()} className="rounded-xl">
          <RefreshCw className="size-3.5 mr-1.5" />
          {t("refresh")}
        </Button>
      </PageHeader>

      <PageInstructions items={[t("instruction1"), t("instruction2")]} />

      <Card>
        <CardContent className="pt-6">
          {loading ? (
            <div className="space-y-3 py-4">
              {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
            </div>
          ) : reviews.length > 0 ? (
            <>
              <ScrollArea className="h-[440px]">
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="text-xs">{t("thTime")}</TableHead>
                        <TableHead className="text-xs">{t("thId")}</TableHead>
                        <TableHead className="text-xs">{t("thSymbol")}</TableHead>
                        <TableHead className="text-xs">{t("thDirection")}</TableHead>
                        <TableHead className="text-xs text-right">{t("thLot")}</TableHead>
                        <TableHead className="text-xs">{t("thStatus")}</TableHead>
                        <TableHead className="text-xs">{t("thVerdict")}</TableHead>
                        <TableHead className="text-xs">{t("thProvider")}</TableHead>
                        <TableHead className="text-xs text-right">{t("thConfidence")}</TableHead>
                        <TableHead className="text-xs">{t("thReason")}</TableHead>
                        <TableHead className="text-xs">{t("thActions")}</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {reviews.map((r) => {
                        const reason = r.reason ?? r.review?.llm?.reasoning ?? "";
                        return (
                          <TableRow key={r.id} className="hover:bg-muted/30 transition-colors">
                            <TableCell className="text-muted-foreground text-xs whitespace-nowrap">
                              {r.created_at ? toDate(r.created_at).toLocaleString() : "—"}
                            </TableCell>
                            <TableCell className="text-xs font-mono">{r.id}</TableCell>
                            <TableCell className="text-xs font-medium">{r.symbol}</TableCell>
                            <TableCell className={`text-xs font-semibold ${r.order_type === "BUY" ? "text-success dark:text-green-400" : "text-destructive"}`}>
                              {r.order_type ?? "—"}
                            </TableCell>
                            <TableCell className="text-right text-xs font-mono">{r.requested_lot ?? "—"}</TableCell>
                            <TableCell><StatusBadge status={r.status} /></TableCell>
                            <TableCell><VerdictBadge verdict={r.verdict} /></TableCell>
                            <TableCell className="text-xs font-mono text-muted-foreground">{r.provider ?? "—"}</TableCell>
                            <TableCell className="text-right text-xs font-mono">
                              {typeof r.confidence === "number" ? `${(r.confidence * 100).toFixed(0)}%` : "—"}
                            </TableCell>
                            <TableCell className="text-xs text-muted-foreground max-w-[220px] truncate" title={reason || undefined}>
                              {reason || "—"}
                            </TableCell>
                            <TableCell>
                              <Button variant="ghost" size="sm" className="rounded-lg" onClick={() => setDetail(r)}>
                                <Eye className="size-3.5 mr-1" />
                                {t("detail")}
                              </Button>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
              </ScrollArea>

              {/* 统计摘要 + 分页 */}
              <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-t border-border bg-muted/20 rounded-b-xl">
                <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground font-medium">
                  <span>{t("totalCount", { total })}</span>
                  <span className="inline-flex items-center gap-1">
                    <span className="size-1.5 rounded-full bg-green-500" />
                    {t("statsApproved")} {stats.approved}
                  </span>
                  <span className="inline-flex items-center gap-1">
                    <span className="size-1.5 rounded-full bg-amber-500" />
                    {t("statsCaution")} {stats.caution}
                  </span>
                  <span className="inline-flex items-center gap-1">
                    <span className="size-1.5 rounded-full bg-destructive" />
                    {t("statsRejected")} {stats.rejected}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline" size="sm" className="rounded-xl"
                    disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                  >
                    <ChevronLeft className="size-3.5 mr-1" />
                    {t("prevPage")}
                  </Button>
                  <span className="text-xs text-muted-foreground">{t("pageInfo", { page, totalPages })}</span>
                  <Button
                    variant="outline" size="sm" className="rounded-xl"
                    disabled={offset + PAGE_SIZE >= total}
                    onClick={() => setOffset(offset + PAGE_SIZE)}
                  >
                    {t("nextPage")}
                    <ChevronRight className="size-3.5 ml-1" />
                  </Button>
                </div>
              </div>
            </>
          ) : (
            <EmptyState
              icon={ShieldCheck}
              heading={t("noDataHeading")}
              description={t("noDataDescription")}
            />
          )}
        </CardContent>
      </Card>

      <ReviewHistoryDialog
        review={detail}
        open={!!detail}
        onOpenChange={(o) => { if (!o) setDetail(null); }}
      />
    </div>
  );
}
