"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { RefreshCw } from "lucide-react";
import {
  cancelPendingOrder,
  closePositionGated,
  confirmManualOrder,
  getDayRange,
  getManualReview,
  getPendingOrders,
  getPositions,
  getRolloutMode,
  getTick,
  modifyPendingOrder,
  modifyPositionSltp,
  submitManualOrder,
  type DayRange,
  type ManualOrderRequest,
  type ManualReview,
  type PendingOrder,
  type TickQuote,
} from "@/lib/api";
import { useBotStore } from "@/store/botStore";
import { useWebSocket } from "@/lib/websocket";
import { PageHeader } from "@/components/layout/PageHeader";
import { PageInstructions } from "@/components/layout/PageInstructions";
import { PositionsTable, type TradingPosition } from "@/components/trading/PositionsTable";
import { ReviewResultCard, type ReviewState } from "@/components/trading/ReviewResultCard";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { TimeframeSelector } from "@/components/ui/timeframe-selector";
import { showError, showSuccess } from "@/lib/toast";

// lightweight-charts 依赖 window，必须 ssr:false 动态加载（评审 H5）
const TradingChart = dynamic(() => import("@/components/chart/TradingChart"), {
  ssr: false,
  loading: () => (
    <div className="h-full flex items-center justify-center text-sm text-muted-foreground">
      Loading chart…
    </div>
  ),
});

const PENDING_TYPES = ["BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP"] as const;
type PendingType = (typeof PENDING_TYPES)[number];

const REVIEW_POLL_MS = 2000;
const REVIEW_POLL_MAX = 20; // 2s × 20 = 40s > LLM 25s 预算
const POSITIONS_POLL_MS = 10000; // 持仓全量快照周期（品种停跑后 WS 不再推送持仓）
const QUOTE_POLL_MS = 2000; // WS 断开时的报价兜底周期
const STALE_TICK_MS = 30000; // 超过此时长未收到新报价 → 标记"延迟"（对齐后端 MAX_TICK_AGE_SECONDS）

export default function TradingPage() {
  const t = useTranslations("trading");
  const positions = useBotStore((s) => s.positions);
  const setPositions = useBotStore((s) => s.setPositions);
  const setTick = useBotStore((s) => s.setTick);
  const { isConnected, subscribe } = useWebSocket();

  const [rolloutMode, setRolloutMode] = useState<string | null>(null);
  const [orderKind, setOrderKind] = useState<"market" | "pending">("market");
  const [orderType, setOrderType] = useState<"BUY" | "SELL" | PendingType>("BUY");
  const [symbol, setSymbol] = useState("");
  const [lot, setLot] = useState("0.10");
  const [price, setPrice] = useState("");
  const [sl, setSl] = useState("");
  const [tp, setTp] = useState("");
  const [comment, setComment] = useState("");
  // REST 报价（WS price_update 的兜底）。带 symbol 一起存：切品种后旧报价不会短暂串台
  const [quote, setQuote] = useState<{ symbol: string; tick: TickQuote | null } | null>(null);
  const [quoteNonce, setQuoteNonce] = useState(0); // 手动"刷新"时重新取价
  // 报价新鲜度只认本地收到的时刻：桥返回的 time 是终端本地时间，跨时区不可比
  const lastQuoteAt = useRef(0);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const positionsEmptyStreak = useRef(0);

  const [submitting, setSubmitting] = useState(false);
  const [review, setReview] = useState<ReviewState | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [lastOrder, setLastOrder] = useState<ManualOrderRequest | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const [pendingOrders, setPendingOrders] = useState<PendingOrder[]>([]);
  const [loadingPending, setLoadingPending] = useState(true);
  const [busyTicket, setBusyTicket] = useState<number | null>(null);
  const [modifyTicket, setModifyTicket] = useState<number | null>(null);
  const [modifyPrice, setModifyPrice] = useState("");
  // 图表周期（默认承接该品种默认周期，不硬编码 M15——评审 H4）
  const [timeframe, setTimeframe] = useState<string>("M15");
  // 当日最高/最低（读 D1 最新 K，随品种/周期变化重取，独立于 tick）
  const [dayRange, setDayRange] = useState<DayRange | null>(null);

  const symbols = useBotStore((s) => s.symbols);
  const activeSymbol = useBotStore((s) => s.activeSymbol);
  const symbolInfo = symbols.find((s) => s.symbol === symbol);
  const priceDecimals = symbolInfo?.price_decimals ?? 2;
  const priceStep = 1 / 10 ** priceDecimals;
  // 下拉选项：value 用规范品种名（后端严格精确匹配），label 用 display_name
  const symbolItems = symbols.map((s) => ({ value: s.symbol, label: s.display_name || s.symbol }));
  // 只订阅当前品种的 tick：其它品种更新时返回的引用不变，不会触发多余的渲染
  const wsTick = useBotStore((s) => (symbol ? s.ticks[symbol] : undefined));
  const restTick = quote && quote.symbol === symbol ? quote.tick : null;
  const liveTick = wsTick ?? restTick ?? null;
  const priceStale =
    !!liveTick && (lastQuoteAt.current === 0 || nowMs - lastQuoteAt.current > STALE_TICK_MS);

  /** 默认品种：优先 store 的 activeSymbol（须在已配置列表内），否则第一个。 */
  useEffect(() => {
    if (symbol || symbols.length === 0) return;
    const initial = symbols.some((s) => s.symbol === activeSymbol)
      ? activeSymbol
      : symbols[0].symbol;
    setSymbol(initial);
    const info = symbols.find((s) => s.symbol === initial);
    setLot(String(info?.default_lot ?? 0.1));
    // 图表周期默认承接该品种默认周期
    setTimeframe(info?.timeframe || "M15");
  }, [symbol, symbols, activeSymbol]);

  const fetchPendingOrders = useCallback(async () => {
    try {
      const res = await getPendingOrders();
      setPendingOrders(res.data.orders || []);
    } catch { /* handled */ } finally { setLoadingPending(false); }
  }, []);

  const fetchRollout = useCallback(async () => {
    try {
      const res = await getRolloutMode();
      setRolloutMode(res.data?.mode ?? null);
    } catch { setRolloutMode(null); }
  }, []);

  /**
   * 持仓刷新：一次取全账户快照（不传 symbol），整体替换 store。
   *
   * 为什么不用"按 ticket 合并"：本页展示全部持仓，快照就是完整视图，合并会残留
   * 已平仓的行。为什么空快照要连续两次才清空：MT5 bridge 超时同样返回空数组
   * （engine.sync_positions 有同款守卫），单次空直接清空会让持仓"闪没"。
   */
  const refreshPositions = useCallback(async () => {
    try {
      const res = await getPositions();
      const list = res.data?.positions;
      if (!Array.isArray(list)) return; // 结构异常 → 保留现有数据
      if (list.length === 0 && useBotStore.getState().positions.length > 0) {
        positionsEmptyStreak.current += 1;
        if (positionsEmptyStreak.current < 2) return;
      } else {
        positionsEmptyStreak.current = 0;
      }
      setPositions(list);
    } catch { /* 保留现有数据，等下一次刷新 */ }
  }, [setPositions]);

  /** WS position_update：每个引擎只推自己的持仓 → 按 ticket 合并（与 dashboard 一致）。 */
  const mergePositions = useCallback((data: unknown) => {
    const incoming = (data as { positions?: TradingPosition[] } | null)?.positions;
    if (!incoming) return;
    const merged = new Map(useBotStore.getState().positions.map((p) => [p.ticket, p]));
    incoming.forEach((p) => merged.set(p.ticket, p));
    setPositions([...merged.values()]);
  }, [setPositions]);

  /** 切换品种：清掉上一品种的价格/SL/TP，手数回到该品种默认值，图表周期回到该品种默认，并立即刷新持仓与报价。 */
  const handleSymbolChange = (next: string) => {
    if (!next || next === symbol) return;
    setSymbol(next);
    setPrice(""); setSl(""); setTp("");
    setReview(null);
    const info = symbols.find((s) => s.symbol === next);
    if (info) setLot(String(info.default_lot));
    if (info?.timeframe) setTimeframe(info.timeframe);
    lastQuoteAt.current = 0;
    setQuoteNonce((n) => n + 1);
    void refreshPositions();
  };

  useEffect(() => {
    fetchPendingOrders();
    fetchRollout();
  }, [fetchPendingOrders, fetchRollout]);

  // 报价：挂载/切品种/手动刷新时取一次；WS 断开期间每 2s 轮询兜底（WS 恢复即停）
  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    const fetchOnce = async () => {
      try {
        const res = await getTick(symbol);
        if (cancelled) return;
        const tick = res.data?.tick ?? null;
        setQuote({ symbol, tick });
        if (tick) lastQuoteAt.current = Date.now();
      } catch {
        if (!cancelled) setQuote({ symbol, tick: null });
      }
    };
    fetchOnce();
    if (isConnected) return () => { cancelled = true; };
    const timer = setInterval(fetchOnce, QUOTE_POLL_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, [symbol, isConnected, quoteNonce]);

  // WS 订阅：报价 + 持仓推送（持仓作为 10s 快照之间的补充）
  useEffect(() => {
    subscribe("price_update", (data) => {
      if (!data) return;
      const tick = data as NonNullable<typeof wsTick>;
      if (tick.symbol === symbol) lastQuoteAt.current = Date.now();
      setTick(tick);
    });
    subscribe("position_update", (data) => mergePositions(data));
  }, [subscribe, setTick, mergePositions, symbol]);

  // 持仓：挂载 + 切品种立即刷新一次
  useEffect(() => {
    refreshPositions();
  }, [refreshPositions, symbol]);

  // 当日最高/最低：随品种变化拉取（读 D1 最新 K）。独立于 tick（tick 1Hz，
  // 避免每秒触发），失败时隐藏不阻塞页面（评审 H3/架构建议）。
  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    const fetchDayRange = async () => {
      try {
        const res = await getDayRange(symbol);
        if (!cancelled) setDayRange(res.data ?? null);
      } catch {
        if (!cancelled) setDayRange(null);
      }
    };
    fetchDayRange();
    return () => { cancelled = true; };
  }, [symbol]);

  // 持仓轮询：页面可见时每 10s 全量刷新（品种停跑后 WS 不再推持仓，只能靠 REST）；
  // 同一条心跳顺便驱动"报价是否延迟"的重算。
  useEffect(() => {
    const timer = setInterval(() => {
      setNowMs(Date.now());
      if (document.visibilityState === "visible") refreshPositions();
    }, POSITIONS_POLL_MS);
    return () => clearInterval(timer);
  }, [refreshPositions]);

  // 清理轮询
  useEffect(() => () => { if (pollTimer.current) clearInterval(pollTimer.current); }, []);

  const stopPolling = () => {
    if (pollTimer.current) { clearInterval(pollTimer.current); pollTimer.current = null; }
  };

  const pollReview = useCallback((reviewId: number) => {
    stopPolling();
    let attempts = 0;
    pollTimer.current = setInterval(async () => {
      attempts += 1;
      if (attempts > REVIEW_POLL_MAX) { stopPolling(); setSubmitting(false); return; }
      try {
        const res = await getManualReview(reviewId);
        const r = res.data;
        setReview(toReviewState(r));
        if (r.status !== "PENDING_REVIEW") {
          stopPolling();
          setSubmitting(false);
          await fetchPendingOrders();
          await refreshPositions();
          if (r.status === "EXECUTED") showSuccess(t("executed"));
          if (r.status === "FAILED") showError(r.reason || t("failed"));
        }
      } catch { /* keep polling */ }
    }, REVIEW_POLL_MS);
  }, [fetchPendingOrders, refreshPositions, t]);

  const submit = async () => {
    if (!symbol || Number(lot) <= 0) return;
    if (orderKind === "pending" && !Number(price)) return;
    setSubmitting(true);
    setReview(null);
    const payload = {
      // 必须原样发送规范品种名：后端严格精确匹配（OILCash 不能变成 OILCASH）
      symbol,
      order_kind: orderKind,
      order_type: orderType,
      lot: Number(lot),
      sl: sl ? Number(sl) : undefined,
      tp: tp ? Number(tp) : undefined,
      price: orderKind === "pending" ? Number(price) : undefined,
      comment: comment || undefined,
    } as const;
    try {
      const res = await submitManualOrder(payload);
      const r = res.data;
      setLastOrder({ ...payload });
      setReview(toReviewState(r));
      if (r.status === "PENDING_REVIEW") {
        pollReview(r.review_id as number);
      } else {
        setSubmitting(false);
        if (r.status === "REJECTED") { /* 拦截是业务结果,面板展示 */ }
        await fetchPendingOrders();
        await refreshPositions();
      }
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail;
      showError(detail || t("failed"));
      setSubmitting(false);
    }
  };

  const handleConfirm = async () => {
    if (!review?.reviewId) return;
    setConfirming(true);
    try {
      const res = await confirmManualOrder(review.reviewId);
      const r: ManualReview = res.data;
      if (r.status === "EXECUTED") {
        showSuccess(t("executed"));
        setReview({ status: "EXECUTED", reviewId: review.reviewId, llm: review.llm });
        await fetchPendingOrders();
        await refreshPositions();
      } else if (r.status === "EXPIRED") {
        setReview({ status: "EXPIRED", reviewId: review.reviewId });
      } else {
        setReview({ status: "REJECTED", reviewId: review.reviewId, reason: r.reason });
      }
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail;
      showError(detail || t("failed"));
    } finally { setConfirming(false); }
  };

  const handleResubmit = async () => {
    if (lastOrder) { await submit(); }
  };

  const handleCancel = async (ticket: number) => {
    if (!window.confirm(t("cancelConfirm", { ticket }))) return;
    setBusyTicket(ticket);
    try {
      await cancelPendingOrder(ticket);
      showSuccess(t("cancel"));
      await fetchPendingOrders();
      await refreshPositions();
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail;
      showError(detail || t("failed"));
    } finally { setBusyTicket(null); }
  };

  const handleModifyPrice = async (ticket: number) => {
    if (!Number(modifyPrice)) return;
    setBusyTicket(ticket);
    setSubmitting(true);
    setReview(null);
    try {
      const res = await modifyPendingOrder(ticket, { price: Number(modifyPrice) });
      const r = res.data;
      setModifyTicket(null);
      setModifyPrice("");
      setReview(toReviewState(r));
      if (r.status === "PENDING_REVIEW") pollReview(r.review_id as number);
      else { await fetchPendingOrders(); await refreshPositions(); }
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail;
      showError(detail || t("failed"));
    } finally { setBusyTicket(null); setSubmitting(false); }
  };

  const handleClose = async (ticket: number) => {
    if (!window.confirm(t("closeConfirm", { ticket }))) return;
    setBusyTicket(ticket);
    try {
      await closePositionGated(ticket);
      showSuccess(t("close"));
      await refreshPositions();
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail;
      showError(detail || t("failed"));
    } finally { setBusyTicket(null); }
  };

  const handleEditSlTp = async (ticket: number, newSl: number, newTp: number) => {
    setBusyTicket(ticket);
    try {
      const res = await modifyPositionSltp(ticket, { sl: newSl, tp: newTp });
      if (res.data?.modified === false && res.data?.rejected) {
        showError(res.data.reason || t("failed"));
      } else {
        showSuccess(t("editSlTp"));
      }
      await refreshPositions();
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail;
      showError(detail || t("failed"));
    } finally { setBusyTicket(null); }
  };

  const isBuySide = orderType.startsWith("BUY");

  return (
    <div className="p-4 sm:p-6 xl:p-8 space-y-5 sm:space-y-6 page-enter">
      <PageHeader title={t("title")} subtitle={t("subtitle")}>
        {liveTick ? (
          <div
            className={`border rounded-full px-3 py-1.5 sm:px-4 sm:py-2 flex items-center gap-2 sm:gap-3 bg-card ${
              priceStale ? "border-amber-500/60" : "border-border"
            }`}
          >
            <span className="text-xs text-muted-foreground font-medium">{symbol}</span>
            <span className="text-xs sm:text-sm font-mono font-bold text-foreground" title={t("bid")}>
              {liveTick.bid?.toFixed(priceDecimals)}
            </span>
            <span className="text-xs text-muted-foreground">/</span>
            <span className="text-xs sm:text-sm font-mono text-muted-foreground" title={t("ask")}>
              {liveTick.ask?.toFixed(priceDecimals)}
            </span>
            <span className="hidden sm:inline text-xs text-muted-foreground font-medium">
              {t("spread")}: {liveTick.spread?.toFixed(1)}
            </span>
            {priceStale && (
              <span className="text-[10px] font-semibold text-amber-600 dark:text-amber-400">
                {t("stalePrice")}
              </span>
            )}
          </div>
        ) : (
          symbol && (
            <div className="border border-dashed border-border rounded-full px-3 py-1.5 sm:px-4 sm:py-2 text-xs text-muted-foreground">
              {t("waitingTick")}
            </div>
          )
        )}
        <button
          type="button"
          onClick={() => {
            fetchPendingOrders();
            fetchRollout();
            refreshPositions();
            setQuoteNonce((n) => n + 1);
          }}
          className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-sm hover:bg-muted"
        >
          <RefreshCw className="size-4" />{t("refresh")}
        </button>
      </PageHeader>

      <PageInstructions items={[t("firewallNote")]} />

      {rolloutMode && rolloutMode !== "live" && rolloutMode !== "micro" && (
        <div className="rounded-xl border border-yellow-500/40 bg-yellow-500/10 px-4 py-3 text-sm text-yellow-700 dark:text-yellow-400">
          {t("shadowModeBanner", { mode: rolloutMode })}
        </div>
      )}

      {/* ─── 行情图表（全宽独立行；含周期切换 + 当日高低）─── */}
      {symbol && (
        <div className="rounded-xl border border-border bg-card p-4 sm:p-5 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3 flex-wrap">
              <span className="text-sm font-semibold">{t("chartTitle")}</span>
              {/* 当日最高/最低（is_current=false 时标注"上一交易日"，null 隐藏） */}
              {dayRange?.day_range ? (
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span className="font-mono text-green-600 dark:text-green-400">
                    {t("todayHigh")} {dayRange.day_range.high.toFixed(priceDecimals)}
                  </span>
                  <span className="font-mono text-red-600 dark:text-red-400">
                    {t("todayLow")} {dayRange.day_range.low.toFixed(priceDecimals)}
                  </span>
                  {!dayRange.is_current && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted">{t("prevTradingDay")}</span>
                  )}
                </div>
              ) : (
                <span className="text-xs text-muted-foreground">{t("noDayRange")}</span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">{t("timeframe")}</span>
              <TimeframeSelector value={timeframe} onChange={setTimeframe} size="sm" />
            </div>
          </div>
          <TradingChart symbol={symbol} timeframe={timeframe} tick={liveTick} height={460} />
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* ─── 下单表单 ─── */}
        <div className="rounded-xl border border-border bg-card p-5 space-y-4">
          <div className="text-sm font-semibold">{t("orderForm")}</div>

          <div className="flex gap-2">
            {(["market", "pending"] as const).map((k) => (
              <button
                key={k}
                type="button"
                onClick={() => { setOrderKind(k); setOrderType(k === "market" ? (isBuySide ? "BUY" : "SELL") : "BUY_LIMIT"); }}
                className={`flex-1 rounded-md px-3 py-2 text-sm font-medium border ${orderKind === k ? "bg-primary text-primary-foreground border-primary" : "border-border hover:bg-muted"}`}
              >
                {t(k)}
              </button>
            ))}
          </div>

          {orderKind === "market" ? (
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => setOrderType("BUY")}
                className={`rounded-md px-3 py-2.5 text-sm font-semibold border ${orderType === "BUY" ? "bg-green-600 text-white border-green-600" : "border-border hover:bg-muted"}`}
              >
                {t("buy")}
              </button>
              <button
                type="button"
                onClick={() => setOrderType("SELL")}
                className={`rounded-md px-3 py-2.5 text-sm font-semibold border ${orderType === "SELL" ? "bg-red-600 text-white border-red-600" : "border-border hover:bg-muted"}`}
              >
                {t("sell")}
              </button>
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              {PENDING_TYPES.map((pt) => (
                <button
                  key={pt}
                  type="button"
                  onClick={() => setOrderType(pt)}
                  className={`rounded-md px-3 py-2 text-xs font-semibold border ${orderType === pt ? "bg-primary text-primary-foreground border-primary" : "border-border hover:bg-muted"}`}
                >
                  {pt}
                </button>
              ))}
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">{t("symbol")}</label>
              <Select
                value={symbol || null}
                onValueChange={(v) => v && handleSymbolChange(v)}
                items={symbolItems}
              >
                <SelectTrigger className="w-full" aria-label={t("symbol")}>
                  <SelectValue placeholder={t("symbolPlaceholder")} />
                </SelectTrigger>
                <SelectContent>
                  {symbolItems.length > 0 ? (
                    symbolItems.map((s) => (
                      <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>
                    ))
                  ) : (
                    <SelectItem value="__none__" disabled>{t("noSymbols")}</SelectItem>
                  )}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">{t("lot")}</label>
              <input
                type="number" step="0.01" min="0.01" max={symbolInfo?.max_lot} value={lot} onChange={(e) => setLot(e.target.value)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            {orderKind === "pending" && (
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-1">{t("price")}</label>
                <input
                  type="number" step={priceStep} value={price} onChange={(e) => setPrice(e.target.value)}
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary"
                />
              </div>
            )}
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">{t("sl")}</label>
              <input
                type="number" step={priceStep} value={sl} onChange={(e) => setSl(e.target.value)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">{t("tp")}</label>
              <input
                type="number" step={priceStep} value={tp} onChange={(e) => setTp(e.target.value)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">{t("comment")}</label>
              <input
                type="text" value={comment} onChange={(e) => setComment(e.target.value)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
          </div>

          <button
            type="button"
            onClick={submit}
            disabled={submitting || !symbol || Number(lot) <= 0 || (orderKind === "pending" && !Number(price))}
            className={`w-full rounded-md px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50 ${isBuySide ? "bg-green-600 hover:bg-green-600/90" : "bg-red-600 hover:bg-red-600/90"}`}
          >
            {submitting ? t("submitting") : t("submit")}
          </button>
        </div>

        {/* ─── 审查结果 ─── */}
        <div className="rounded-xl border border-border bg-card p-5 space-y-3">
          <div className="text-sm font-semibold">{t("reviewResult")}</div>
          {review ? (
            <ReviewResultCard
              review={review}
              onConfirm={handleConfirm}
              onResubmit={handleResubmit}
              confirming={confirming}
            />
          ) : (
            <div className="text-center text-muted-foreground py-10 border border-dashed border-border rounded-xl text-sm">
              {t("firewallNote")}
            </div>
          )}
        </div>
      </div>

      {/* ─── 挂单 ─── */}
      <div className="space-y-3">
        <div className="text-sm font-semibold">{t("pendingOrders")}</div>
        {loadingPending ? (
          <div className="text-center text-muted-foreground py-6 text-sm">{t("loading")}</div>
        ) : pendingOrders.length === 0 ? (
          <div className="text-center text-muted-foreground py-8 border border-dashed border-border rounded-xl text-sm">
            {t("noPendingOrders")}
          </div>
        ) : (
          <div className="rounded-xl border border-border bg-card overflow-hidden overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border/50 text-left text-xs text-muted-foreground">
                  <th className="px-4 py-3 font-medium">{t("colTicket")}</th>
                  <th className="px-4 py-3 font-medium">{t("colType")}</th>
                  <th className="px-4 py-3 font-medium">{t("colSymbol")}</th>
                  <th className="px-4 py-3 text-right font-medium">{t("colLots")}</th>
                  <th className="px-4 py-3 text-right font-medium">{t("colPrice")}</th>
                  <th className="px-4 py-3 text-right font-medium">{t("colSl")}</th>
                  <th className="px-4 py-3 text-right font-medium">{t("colTp")}</th>
                  <th className="px-4 py-3 text-right font-medium">{t("colActions")}</th>
                </tr>
              </thead>
              <tbody>
                {pendingOrders.map((o) => (
                  <tr key={o.ticket} className="border-b border-border/40 last:border-b-0">
                    <td className="px-4 py-3 font-mono text-xs">{o.ticket}</td>
                    <td className="px-4 py-3 text-xs font-medium">{o.type}</td>
                    <td className="px-4 py-3 font-medium text-xs">{o.symbol}</td>
                    <td className="px-4 py-3 text-right font-mono">{o.lot}</td>
                    <td className="px-4 py-3 text-right font-mono">
                      {modifyTicket === o.ticket ? (
                        <input
                          type="number" step="0.01" autoFocus value={modifyPrice}
                          onChange={(e) => setModifyPrice(e.target.value)}
                          className="w-24 rounded border border-border bg-background px-2 py-1 text-right font-mono text-xs"
                        />
                      ) : (
                        o.price_open?.toFixed(2)
                      )}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-muted-foreground">{o.sl?.toFixed(2)}</td>
                    <td className="px-4 py-3 text-right font-mono text-muted-foreground">{o.tp?.toFixed(2)}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-2">
                        {modifyTicket === o.ticket ? (
                          <>
                            <button
                              type="button" onClick={() => handleModifyPrice(o.ticket)}
                              disabled={busyTicket === o.ticket || !Number(modifyPrice)}
                              className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50"
                              title={t("modifyNote")}
                            >
                              OK
                            </button>
                            <button
                              type="button" onClick={() => { setModifyTicket(null); setModifyPrice(""); }}
                              className="rounded-md px-2 py-1.5 text-xs text-muted-foreground"
                            >
                              ✕
                            </button>
                          </>
                        ) : (
                          <button
                            type="button" onClick={() => { setModifyTicket(o.ticket); setModifyPrice(""); }}
                            disabled={busyTicket === o.ticket}
                            className="rounded-md px-3 py-1.5 text-xs border border-border hover:bg-muted disabled:opacity-50"
                            title={t("modifyNote")}
                          >
                            {t("modify")}
                          </button>
                        )}
                        <button
                          type="button" onClick={() => handleCancel(o.ticket)}
                          disabled={busyTicket === o.ticket}
                          className="rounded-md px-3 py-1.5 text-xs text-red-500 hover:bg-red-500/10 disabled:opacity-50"
                        >
                          {t("cancel")}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ─── 持仓 ─── */}
      <div className="space-y-3">
        <div className="text-sm font-semibold">{t("positions")}</div>
        <PositionsTable
          positions={positions}
          onClose={handleClose}
          onEditSlTp={handleEditSlTp}
          busyTicket={busyTicket}
        />
      </div>
    </div>
  );
}

function toReviewState(r: ManualReview): ReviewState {
  return {
    status: r.status,
    kind: r.kind,
    reason: r.reason ?? r.error_message,
    reviewId: r.review_id as number | undefined,
    ruleFlags: r.rule_flags as ReviewState["ruleFlags"],
    llm: r.review?.llm,
  };
}
