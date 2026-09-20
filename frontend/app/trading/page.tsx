"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { RefreshCw } from "lucide-react";
import api, {
  cancelPendingOrder,
  closePositionGated,
  confirmManualOrder,
  getManualReview,
  getPendingOrders,
  getRolloutMode,
  modifyPendingOrder,
  modifyPositionSltp,
  submitManualOrder,
  type ManualOrderRequest,
  type ManualReview,
  type PendingOrder,
} from "@/lib/api";
import { useBotStore } from "@/store/botStore";
import { PageHeader } from "@/components/layout/PageHeader";
import { PageInstructions } from "@/components/layout/PageInstructions";
import { PositionsTable } from "@/components/trading/PositionsTable";
import { ReviewResultCard, type ReviewState } from "@/components/trading/ReviewResultCard";
import { showError, showSuccess } from "@/lib/toast";

const PENDING_TYPES = ["BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP"] as const;
type PendingType = (typeof PENDING_TYPES)[number];

const REVIEW_POLL_MS = 2000;
const REVIEW_POLL_MAX = 20; // 2s × 20 = 40s > LLM 25s 预算

export default function TradingPage() {
  const t = useTranslations("trading");
  const positions = useBotStore((s) => s.positions);

  const [rolloutMode, setRolloutMode] = useState<string | null>(null);
  const [orderKind, setOrderKind] = useState<"market" | "pending">("market");
  const [orderType, setOrderType] = useState<"BUY" | "SELL" | PendingType>("BUY");
  const [symbol, setSymbol] = useState("");
  const [lot, setLot] = useState("0.10");
  const [price, setPrice] = useState("");
  const [sl, setSl] = useState("");
  const [tp, setTp] = useState("");
  const [comment, setComment] = useState("");

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

  const symbols = useBotStore((s) => s.symbols);
  const activeSymbol = useBotStore((s) => s.activeSymbol);
  useEffect(() => {
    if (!symbol) {
      setSymbol(symbols.length > 0 ? symbols[0].symbol : activeSymbol || "");
    }
  }, [symbols, activeSymbol, symbol]);

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

  useEffect(() => {
    fetchPendingOrders();
    fetchRollout();
  }, [fetchPendingOrders, fetchRollout]);

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
          if (r.status === "EXECUTED") showSuccess(t("executed"));
          if (r.status === "FAILED") showError(r.reason || t("failed"));
        }
      } catch { /* keep polling */ }
    }, REVIEW_POLL_MS);
  }, [fetchPendingOrders, t]);

  const submit = async () => {
    if (!symbol || Number(lot) <= 0) return;
    if (orderKind === "pending" && !Number(price)) return;
    setSubmitting(true);
    setReview(null);
    const payload = {
      symbol: symbol.toUpperCase(),
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
      else await fetchPendingOrders();
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
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail;
      showError(detail || t("failed"));
    } finally { setBusyTicket(null); }
  };

  const isBuySide = orderType.startsWith("BUY");

  return (
    <div className="p-4 sm:p-6 xl:p-8 space-y-5 sm:space-y-6 page-enter">
      <PageHeader title={t("title")} subtitle={t("subtitle")}>
        <button
          type="button"
          onClick={() => { fetchPendingOrders(); fetchRollout(); }}
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
              <input
                type="text" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                placeholder="GOLD"
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">{t("lot")}</label>
              <input
                type="number" step="0.01" min="0.01" value={lot} onChange={(e) => setLot(e.target.value)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            {orderKind === "pending" && (
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-1">{t("price")}</label>
                <input
                  type="number" step="0.01" value={price} onChange={(e) => setPrice(e.target.value)}
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary"
                />
              </div>
            )}
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">{t("sl")}</label>
              <input
                type="number" step="0.01" value={sl} onChange={(e) => setSl(e.target.value)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">{t("tp")}</label>
              <input
                type="number" step="0.01" value={tp} onChange={(e) => setTp(e.target.value)}
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
