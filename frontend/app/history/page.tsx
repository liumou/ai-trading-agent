"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useTranslations, useLocale } from "next-intl";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  Download, BarChart3, TrendingUp, DollarSign, Target, History, Archive,
  ChevronLeft, ChevronRight, Sparkles, Loader2, RefreshCw,
} from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { PageInstructions } from "@/components/layout/PageInstructions";
import { StatCard } from "@/components/ui/stat-card";
import SentimentBadge from "@/components/ai/SentimentBadge";
import {
  getTradeHistory, getPerformance, getSymbols, archiveTrades,
  getLatestTradeReviewByTicket, type TradeReview,
} from "@/lib/api";
import { showSuccess, showError } from "@/lib/toast";
import { toDate } from "@/lib/format";
import { TradeReviewDialog, REVIEW_CLASS_COLORS } from "@/components/trading/TradeReviewDialog";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import { SymbolTabs } from "@/components/ui/symbol-tabs";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine, ResponsiveContainer,
} from "recharts";

type Trade = {
  id: number | null; ticket: number; symbol: string; type: string; lot: number;
  open_price: number; close_price: number | null; sl: number; tp: number;
  open_time: string; close_time: string | null; profit: number | null;
  strategy_name: string; ai_sentiment_label: string | null; ai_sentiment_score: number | null;
  trade_reason: string | null;
  pre_trade_snapshot: Record<string, unknown> | null;
  post_trade_analysis: { exit_reason: string; duration_hours: number | null; outcome: string; profit_usd: number; entry_regime: string; exit_regime: string | null; summary_th: string } | null;
  source: "bot" | "mt5";
  account_login: string;
};

/** 复盘操作单元格：无复盘 → 高亮「复盘」按钮；有复盘 → 分类徽章 + 查看/重审；加载中 → 灰态。 */
function ReviewCell({
  trade, review, loading, onOpen, onTriggered,
}: {
  trade: Trade;
  review: TradeReview | null;
  loading: boolean;
  onOpen: (t: Trade) => void;
  onTriggered: (t: Trade) => void;
}) {
  const t = useTranslations("tradeReview");
  const key = `${trade.source}-${trade.ticket}`;
  const completed = review?.status === "completed";

  if (loading) {
    return <Loader2 className="mx-auto size-3.5 animate-spin text-muted-foreground" />;
  }
  if (completed && review?.classification) {
    const shortKey = `classShort${review.classification.charAt(0).toUpperCase()}${review.classification.slice(1)}`;
    return (
      <div className="flex items-center justify-center gap-1.5">
        <Badge className={cn("border cursor-pointer hover:opacity-80", REVIEW_CLASS_COLORS[review.classification] ?? "border-border text-muted-foreground")} onClick={() => onOpen(trade)}>
          {t(shortKey)}
        </Badge>
      </div>
    );
  }
  if (review?.status === "failed") {
    return (
      <Button variant="outline" size="sm" className="h-6 px-2 text-xs text-destructive border-destructive/30 hover:bg-destructive/10" onClick={() => onOpen(trade)}>
        <RefreshCw className="size-3 mr-1" />
        {t("retry")}
      </Button>
    );
  }
  if (review?.status === "running" || review?.status === "pending") {
    return (
      <Button variant="ghost" size="sm" className="h-6 px-2 text-xs text-muted-foreground" disabled>
        <Loader2 className="size-3 animate-spin mr-1" />
        {t("running")}
      </Button>
    );
  }
  // 无复盘 → 高亮触发
  return (
    <Button variant="outline" size="sm" className="h-6 px-2 text-xs text-primary border-primary/30 hover:bg-primary/10" onClick={() => onOpen(trade)}>
      <Sparkles className="size-3 mr-1" />
      {t("trigger")}
    </Button>
  );
}

export default function HistoryPage() {
  const t = useTranslations("history");
  const locale = useLocale();
  const dateLocale = locale === "zh" ? "zh-CN" : "en-GB";
  const [trades, setTrades] = useState<Trade[]>([]);
  const [performance, setPerformance] = useState<Record<string, unknown> | null>(null);
  const [days, setDays] = useState(30);
  const [symbolFilter, setSymbolFilter] = useState<string>("all");
  const [symbols, setSymbols] = useState<{symbol: string; display_name: string}[]>([]);
  const [loading, setLoading] = useState(true);
  const [archiving, setArchiving] = useState(false);
  // 分页：page 当前页（0-based），pageSize 每页条数。分页只影响表格视图，
  // 汇总/图表/CSV 导出均基于全量 trades，与分页状态解耦。
  const [page, setPage] = useState(0);
  const pageSize = 20;
  const totalPages = Math.max(1, Math.ceil(trades.length / pageSize));
  // 越界保护：归档/数据刷新后条数变少，page 可能超过最后一页 → 钳制到末页
  const safePage = Math.min(page, totalPages - 1);
  const pagedTrades = trades.slice(safePage * pageSize, (safePage + 1) * pageSize);

  useEffect(() => {
    getSymbols().then((res) => {
      if (res.data?.symbols) {
        setSymbols(res.data.symbols);
      }
    }).catch(() => {});
  }, []);

  const fetchData = useCallback(async () => {
    setLoading(true);
    const sym = symbolFilter === "all" ? undefined : symbolFilter;
    try {
      const [tradeRes, perfRes] = await Promise.all([
        getTradeHistory({ days, symbol: sym, limit: 200 }), getPerformance(days, sym),
      ]);
      setTrades(tradeRes.data.trades || []);
      setPerformance(perfRes.data);
    } catch (e) { console.error(e); showError(t("loadFailed")); } finally { setLoading(false); }
  }, [days, symbolFilter]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // 筛选条件（天数/品种）变化时回到第一页，避免停留在越界页码
  useEffect(() => { setPage(0); }, [days, symbolFilter]);

  const handleArchiveDemoTrades = async () => {
    const date = prompt(t("archivePrompt"), new Date().toISOString().slice(0, 10));
    if (!date) return;
    if (!confirm(t("archiveConfirm", { date }))) return;
    setArchiving(true);
    try {
      const res = await archiveTrades(date);
      showSuccess(t("archivedTitle"), t("archivedMessage", { count: res.data.archived }));
      await fetchData();
      setPage(0); // 归档后数据收缩，回到第一页
    } catch { showError(t("archiveFailed")); } finally { setArchiving(false); }
  };

  // ─── AI 深度复盘状态 ─────────────────────────────────────────────
  const [reviewDialogOpen, setReviewDialogOpen] = useState(false);
  const [selectedReview, setSelectedReview] = useState<TradeReview | null>(null);
  const [selectedTrigger, setSelectedTrigger] = useState<{ trade_id?: number; ticket?: number; account_login?: string } | null>(null);
  const [reviewByTicket, setReviewByTicket] = useState<Record<string, TradeReview>>({});
  const [loadingReviews, setLoadingReviews] = useState<Set<string>>(new Set());
  const pollTimersRef = useRef<Record<string, ReturnType<typeof setInterval>>>({});

  // 待复盘提示：已平仓（有 profit）且无 completed 复盘的交易
  const pendingReviewCount = trades.filter((t) => {
    if (t.profit === null) return false;
    const r = reviewByTicket[`${t.source}-${t.ticket}`];
    return !r || r.status !== "completed";
  }).length;

  // 按 (source, ticket) 查各交易的最新复盘（后端按 account_login 归属）
  const fetchReviewByTicket = useCallback(async (t: Trade) => {
    const key = `${t.source}-${t.ticket}`;
    setLoadingReviews((prev) => new Set(prev).add(key));
    try {
      const res = await getLatestTradeReviewByTicket(t.ticket, t.account_login);
      setReviewByTicket((prev) => ({ ...prev, [key]: res.data }));
    } catch { /* 无复盘记录（404）→ 保持未复盘状态 */ } finally {
      setLoadingReviews((prev) => { const next = new Set(prev); next.delete(key); return next; });
    }
  }, []);

  // trades 变化后，为所有已平仓交易补查复盘状态（只查一次，避免循环依赖）
  useEffect(() => {
    for (const t of trades) {
      if (t.profit === null) continue;
      void fetchReviewByTicket(t);
    }
  }, [trades, fetchReviewByTicket]);

  // 卸载时清理所有轮询定时器
  useEffect(() => () => {
    for (const k of Object.keys(pollTimersRef.current)) clearInterval(pollTimersRef.current[k]);
  }, []);

  // 打开复盘弹窗：已有复盘 → 查看；无 → 触发模式
  const openReview = (t: Trade) => {
    const key = `${t.source}-${t.ticket}`;
    const existing = reviewByTicket[key];
    setSelectedReview(existing?.status === "completed" ? existing : null);
    setSelectedTrigger({
      trade_id: t.source === "bot" && t.id ? t.id : undefined,
      ticket: t.ticket,
      account_login: t.account_login,
    });
    setReviewDialogOpen(true);
  };

  // 复盘触发后 → 每 2s 轮询该 ticket 的复盘直到终态
  const handleReviewTriggered = (t: Trade) => {
    const key = `${t.source}-${t.ticket}`;
    // 清理已有轮询
    if (pollTimersRef.current[key]) clearInterval(pollTimersRef.current[key]);
    pollTimersRef.current[key] = setInterval(async () => {
      try {
        const res = await getLatestTradeReviewByTicket(t.ticket, t.account_login);
        const status = res.data?.status;
        setReviewByTicket((prev) => ({ ...prev, [key]: res.data }));
        if (status === "completed" || status === "failed") {
          clearInterval(pollTimersRef.current[key]);
          delete pollTimersRef.current[key];
          setSelectedReview(res.data ?? null);
        }
      } catch {
        clearInterval(pollTimersRef.current[key]);
        delete pollTimersRef.current[key];
      }
    }, 2000);
    // 兜底：最长轮询 2 分钟自动停止
    window.setTimeout(() => {
      if (pollTimersRef.current[key]) {
        clearInterval(pollTimersRef.current[key]);
        delete pollTimersRef.current[key];
      }
    }, 120000);
  };

  const handleExportCSV = () => {
    const headers = "Ticket,Symbol,Type,Lot,Open Time,Close Time,Open Price,Close Price,SL,TP,Profit,Strategy,Sentiment\n";
    const rows = trades
      .map((t) =>
        `${t.ticket},${t.symbol},${t.type},${t.lot},${t.open_time},${t.close_time ?? ""},${t.open_price},${t.close_price ?? ""},${t.sl},${t.tp},${t.profit ?? ""},${t.strategy_name},${t.ai_sentiment_label ?? ""}`
      )
      .join("\n");
    const blob = new Blob([headers + rows], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `trades_${days}d.csv`;
    a.click();
    showSuccess(t("csvExported"), t("tradesExported", { count: trades.length }));
  };

  return (
    <div className="p-4 sm:p-6 xl:p-8 space-y-5 sm:space-y-6 page-enter">
      <PageHeader title={t("title")} subtitle={t("subtitle")}>
        <SymbolTabs
          symbols={symbols}
          active={symbolFilter}
          onSelect={setSymbolFilter}
          showAll
        />
        <div className="flex gap-1 border border-border rounded-2xl p-1 bg-card">
          {[7, 30, 90].map((d) => (
            <Button
              key={d}
              variant={days === d ? "default" : "ghost"}
              size="sm"
              onClick={() => setDays(d)}
              className={`rounded-xl ${days === d ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}
            >
              {t("dayOption", { days: d })}
            </Button>
          ))}
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={handleArchiveDemoTrades}
          disabled={archiving}
          className="rounded-xl text-amber-500 border-amber-500/30 hover:bg-amber-500/10"
          title={t("archiveButtonTitle")}
        >
          <Archive className="size-3.5 mr-1.5" />
          {archiving ? t("archiving") : t("archiveDemo")}
        </Button>
      </PageHeader>

      <PageInstructions

        items={[
          t("instruction1"),
          t("instruction2"),
        ]}
      />

      {/* 待复盘提示：有已平仓且未完成的复盘 */}
      {pendingReviewCount > 0 && (
        <div className="flex items-center gap-2 rounded-xl border border-primary/20 bg-primary/5 px-3 py-2 text-xs text-foreground/90">
          <Sparkles className="size-3.5 shrink-0 text-primary" />
          <span className="font-medium">
            {t("pendingReviews", { count: pendingReviewCount })}
          </span>
          <span className="text-muted-foreground">{t("pendingReviewsHint")}</span>
        </div>
      )}

      {/* AI 深度复盘弹窗 */}
      <TradeReviewDialog
        open={reviewDialogOpen}
        onOpenChange={setReviewDialogOpen}
        review={selectedReview}
        triggerBody={selectedTrigger}
        onTriggered={() => {
          if (selectedTrigger?.ticket) {
            const t = trades.find((x) => x.ticket === selectedTrigger.ticket);
            if (t) handleReviewTriggered(t);
          }
        }}
        polling={selectedReview?.status === "running" || selectedReview?.status === "pending"}
      />

      <Tabs defaultValue="trades">
        <TabsList>
          <TabsTrigger value="trades">{t("tradesTab")}</TabsTrigger>
          <TabsTrigger value="performance">{t("performanceTab")}</TabsTrigger>
        </TabsList>

        <TabsContent value="trades" className="mt-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="text-sm font-bold">
                {t("tradesCount", { count: trades.length })}
              </CardTitle>
              <Button variant="outline" size="sm" onClick={handleExportCSV} className="rounded-full">
                <Download className="size-3.5 mr-1.5" />
                {t("exportCsv")}
              </Button>
            </CardHeader>
            <CardContent>
              {loading ? (
                <div className="space-y-3 py-4">
                  {Array.from({ length: 5 }).map((_, i) => (
                    <Skeleton key={i} className="h-8 w-full" />
                  ))}
                </div>
              ) : trades.length > 0 ? (
                <>
                  {/* Detect which optional columns have data */}
                  {(() => {
                    const hasReason = trades.some((t) => t.trade_reason);
                    const hasSentiment = trades.some((t) => t.ai_sentiment_label);
                    const total = trades.reduce((s, t) => s + (t.profit ?? 0), 0);
                    const wins = trades.filter((t) => (t.profit ?? 0) > 0).length;
                    const losses = trades.length - wins;

                    return (
                      <>
                        <ScrollArea className="h-[400px] sm:h-[500px]">
                          <div className="overflow-x-auto">
                          <Table>
                            <TableHeader>
                              <TableRow>
                                <TableHead className="text-xs">{t("thOpenTime")}</TableHead>
                                <TableHead className="text-xs">{t("thCloseTime")}</TableHead>
                                <TableHead className="text-xs">{t("thSymbol")}</TableHead>
                                <TableHead className="text-xs">{t("thType")}</TableHead>
                                <TableHead className="text-xs text-right">{t("thLot")}</TableHead>
                                <TableHead className="text-xs text-right">{t("thOpen")}</TableHead>
                                <TableHead className="text-xs text-right">{t("thClose")}</TableHead>
                                <TableHead className="text-xs text-right">{t("thSl")}</TableHead>
                                <TableHead className="text-xs text-right">{t("thTp")}</TableHead>
                                <TableHead className="text-xs text-right">{t("thPnl")}</TableHead>
                                <TableHead className="text-xs">{t("thStrategy")}</TableHead>
                                {hasReason && <TableHead className="text-xs">{t("thReason")}</TableHead>}
                                {hasSentiment && <TableHead className="text-xs text-center">{t("thAi")}</TableHead>}
                                <TableHead className="text-xs text-center">{t("thReview")}</TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {pagedTrades.map((t) => (
                                <TableRow key={`${t.source}-${t.ticket}`} className="hover:bg-muted/30 transition-colors">
                                  <TableCell className="text-muted-foreground text-xs">
                                    {toDate(t.open_time).toLocaleDateString(dateLocale, { timeZone: "Asia/Shanghai" })}
                                  </TableCell>
                                  <TableCell className="text-muted-foreground text-xs">
                                    {t.close_time
                                      ? toDate(t.close_time).toLocaleDateString(dateLocale, { timeZone: "Asia/Shanghai" })
                                      : "—"}
                                  </TableCell>
                                  <TableCell className="text-xs font-medium">{t.symbol}</TableCell>
                                  <TableCell className={`text-xs font-semibold ${t.type === "BUY" ? "text-success dark:text-green-400" : "text-destructive"}`}>
                                    {t.type}
                                  </TableCell>
                                  <TableCell className="text-right text-xs font-mono">{t.lot}</TableCell>
                                  <TableCell className="text-right text-xs font-mono">{t.open_price.toFixed(2)}</TableCell>
                                  <TableCell className="text-right text-xs font-mono">{t.close_price?.toFixed(2) ?? "—"}</TableCell>
                                  <TableCell className="text-right text-xs font-mono">{t.sl > 0 ? t.sl.toFixed(2) : "—"}</TableCell>
                                  <TableCell className="text-right text-xs font-mono">{t.tp > 0 ? t.tp.toFixed(2) : "—"}</TableCell>
                                  <TableCell className={`text-right text-xs font-mono font-semibold ${(t.profit ?? 0) >= 0 ? "text-success dark:text-green-400" : "text-destructive"}`}>
                                    {t.profit !== null ? `${t.profit >= 0 ? "+" : ""}${t.profit.toFixed(2)}` : "—"}
                                  </TableCell>
                                  <TableCell className="text-xs text-muted-foreground">{t.strategy_name}</TableCell>
                                  {hasReason && (
                                    <TableCell className="text-xs text-muted-foreground max-w-[180px] truncate" title={t.trade_reason || undefined}>
                                      {t.trade_reason || "—"}
                                    </TableCell>
                                  )}
                                  {hasSentiment && (
                                    <TableCell className="text-center">
                                      {t.ai_sentiment_label ? (
                                        <SentimentBadge label={t.ai_sentiment_label} score={t.ai_sentiment_score || 0} size="sm" />
                                      ) : null}
                                    </TableCell>
                                  )}
                                  {/* 复盘操作列 */}
                                  {t.profit === null ? (
                                    <TableCell className="text-center" />
                                  ) : (
                                    <TableCell className="text-center">
                                      <ReviewCell
                                        trade={t}
                                        review={reviewByTicket[`${t.source}-${t.ticket}`] ?? null}
                                        loading={loadingReviews.has(`${t.source}-${t.ticket}`)}
                                        onOpen={openReview}
                                        onTriggered={handleReviewTriggered}
                                      />
                                    </TableCell>
                                  )}
                                </TableRow>
                              ))}
                            </TableBody>
                          </Table>
                          </div>
                        </ScrollArea>

                        {/* Summary bar at bottom */}
                        <div className="flex items-center justify-between px-4 py-3 border-t border-border bg-muted/20 rounded-b-xl">
                          <span className="text-xs text-muted-foreground font-medium">
                            {t("summaryStats", { total: trades.length, wins, losses, rate: trades.length > 0 ? ((wins / trades.length) * 100).toFixed(0) : 0 })}
                          </span>
                          <span className={`text-sm font-bold font-mono ${total >= 0 ? "text-success dark:text-green-400" : "text-destructive"}`}>
                            {total >= 0 ? "+" : ""}${Math.abs(total).toFixed(2)}
                          </span>
                        </div>

                        {/* Pagination bar */}
                        {trades.length > pageSize && (
                          <nav role="navigation" aria-label={t("paginationLabel")} className="flex items-center justify-between px-4 py-2 border-t border-border">
                            <span className="text-xs text-muted-foreground">
                              {t("paginationShowing", { from: safePage * pageSize + 1, to: Math.min((safePage + 1) * pageSize, trades.length), total: trades.length })}
                            </span>
                            <div className="flex items-center gap-1">
                              <Button
                                variant="outline"
                                size="icon-xs"
                                onClick={() => setPage((p) => Math.max(0, p - 1))}
                                disabled={safePage === 0}
                                aria-label={t("paginationPrev")}
                              >
                                <ChevronLeft className="size-3" />
                              </Button>
                              <span className="text-xs text-muted-foreground px-2" aria-current="page">
                                {t("paginationPage", { page: safePage + 1, pages: totalPages })}
                              </span>
                              <Button
                                variant="outline"
                                size="icon-xs"
                                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                                disabled={safePage >= totalPages - 1}
                                aria-label={t("paginationNext")}
                              >
                                <ChevronRight className="size-3" />
                              </Button>
                            </div>
                          </nav>
                        )}
                      </>
                    );
                  })()}
                </>
              ) : (
                <EmptyState icon={History} heading={t("noTradesHeading")} description={t("noTradesDescription")} action={{ label: t("goToDashboard"), href: "/dashboard" }} />
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="performance" className="mt-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <StatCard icon={BarChart3} label={t("totalTrades")} value={(performance?.total_trades as number) ?? 0} />
            <StatCard icon={TrendingUp} label={t("winRate")}
              value={`${(((performance?.win_rate as number) ?? 0) * 100).toFixed(1)}%`}
              variant={((performance?.win_rate as number) ?? 0) > 0.5 ? "success" : "danger"} />
            <StatCard icon={DollarSign} label={t("totalProfit")}
              value={`$${((performance?.total_profit as number) ?? 0).toFixed(2)}`}
              variant={((performance?.total_profit as number) ?? 0) > 0 ? "success" : "danger"} />
            <StatCard icon={Target} label={t("avgProfit")}
              value={`$${((performance?.avg_profit as number) ?? 0).toFixed(2)}`}
              variant={((performance?.avg_profit as number) ?? 0) > 0 ? "success" : "danger"} />
          </div>

          {trades.filter((t) => t.profit !== null).length > 0 && (
            <Card className="mt-4">
              <CardHeader>
                <CardTitle className="text-sm font-bold">{t("cumulativePnl")}</CardTitle>
              </CardHeader>
              <CardContent>
                <ErrorBoundary>
                <ResponsiveContainer width="100%" height={300}>
                  <AreaChart
                    data={trades
                      .filter((t) => t.profit !== null && t.close_time)
                      .sort((a, b) => toDate(a.close_time!).getTime() - toDate(b.close_time!).getTime())
                      .reduce<{ date: string; pnl: number }[]>((acc, t) => {
                        const prev = acc.length > 0 ? acc[acc.length - 1].pnl : 0;
                        acc.push({ date: toDate(t.close_time!).toLocaleDateString(dateLocale, { timeZone: "Asia/Shanghai" }), pnl: prev + (t.profit ?? 0) });
                        return acc;
                      }, [])}
                  >
                    <defs>
                      <linearGradient id="pnlGradient" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#9fe870" stopOpacity={0.3} />
                        <stop offset="100%" stopColor="#9fe870" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                    <XAxis dataKey="date" className="fill-muted-foreground" fontSize={10} />
                    <YAxis className="fill-muted-foreground" fontSize={10} />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: "var(--popover)",
                        border: "1px solid var(--border)",
                        borderRadius: "12px",
                        color: "var(--foreground)",
                      }}
                      formatter={(value) => [`$${Number(value).toFixed(2)}`, t("pnl")]}
                    />
                    <ReferenceLine y={0} className="stroke-muted-foreground" strokeDasharray="3 3" strokeOpacity={0.5} />
                    <Area type="monotone" dataKey="pnl" stroke="#9fe870" strokeWidth={2} fill="url(#pnlGradient)" />
                  </AreaChart>
                </ResponsiveContainer>
                </ErrorBoundary>
              </CardContent>
            </Card>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
