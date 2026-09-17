"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageHeader } from "@/components/layout/PageHeader";
import { Skeleton } from "@/components/ui/skeleton";
import { FileText, Loader2, Menu, Plus, Send, Trash2, Users } from "lucide-react";
import {
  cancelChatRun,
  createChatSession,
  deleteChatSession,
  getChatRunConfig,
  getChatSession,
  getSymbols,
  listChatSessions,
  startChatRun,
  type AgentChatBudget,
  type AgentChatMode,
  type AgentChatPreset,
  type AgentChatRun,
  type AgentChatMessage as ChatMsg,
} from "@/lib/api";
import { RunPanel } from "@/components/chat/run-panel";
import { useRunDetail } from "@/components/chat/use-run-detail";
import { createRequestId, isActiveRun, loadSelection, saveSelection } from "@/components/chat/run-state";
import { showError, showSuccess } from "@/lib/toast";

interface SessionListItem {
  id: number;
  title: string;
  symbol: string;
  updated_at: string | null;
}

function ChatMessageBubble({ msg }: { msg: ChatMsg }) {
  const isUser = msg.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm whitespace-pre-wrap leading-relaxed ${
          isUser ? "bg-primary text-primary-foreground" : "bg-muted text-foreground"
        }`}
      >
        {msg.content}
      </div>
    </div>
  );
}

export default function AgentChatPage() {
  const t = useTranslations("agentChat");
  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [symbol, setSymbol] = useState("GOLD");
  const [timeframe, setTimeframe] = useState("M15");
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<AgentChatMode>("single");
  const [submitting, setSubmitting] = useState(false);
  const [loading, setLoading] = useState(true);
  const [runId, setRunId] = useState<string | null>(null);
  const [config, setConfig] = useState<Record<string, number | undefined> | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const pendingRef = useRef<{ requestId: string } | null>(null);

  const onRunUpdate = useCallback((run: AgentChatRun) => {
    if (!isActiveRun(run)) {
      // 终态：刷新消息列表，把最终报告并入会话
      setMessages((prev) => (run.response
        ? [...prev.filter((m) => m.content !== run.response), {
            id: -Date.now(), role: "assistant", content: run.response,
            tool_calls: null, duration_s: run.duration_s ?? null, created_at: new Date().toISOString(),
          }]
        : prev));
    }
  }, []);
  const { detail, disconnected } = useRunDetail(runId, onRunUpdate);

  const refreshSessions = useCallback(async () => {
    try {
      const res = await listChatSessions();
      setSessions(res.data.sessions);
    } catch {
      showError(t("loadFailed"));
    }
  }, [t]);

  const loadMessages = useCallback(async (id: number) => {
    try {
      const res = await getChatSession(id);
      setMessages(res.data.messages);
      setSymbol(res.data.session.symbol);
      setTimeframe(res.data.session.timeframe);
    } catch {
      showError(t("loadFailed"));
    }
  }, [t]);

  useEffect(() => {
    (async () => {
      await refreshSessions();
      try {
        const res = await getSymbols();
        const list: string[] = (res.data.symbols || []).map((s: { symbol: string }) => s.symbol);
        if (list.length > 0) setSymbols(list);
      } catch { /* keep default symbol */ }
      try {
        const res = await getChatRunConfig();
        const data = res.data as { budget?: AgentChatBudget } | AgentChatBudget;
        setConfig((("budget" in data ? data.budget : data) || null) as Record<string, number | undefined> | null);
      } catch { /* budget panel is optional */ }
      const restored = loadSelection();
      if (restored) {
        setActiveId(restored.sessionId);
        setRunId(restored.runId);
        await loadMessages(restored.sessionId).catch(() => saveSelection(null, null));
      }
      setLoading(false);
    })();
  }, [refreshSessions, loadMessages]);


  const openSession = async (id: number) => {
    setActiveId(id);
    setRunId(null);
    saveSelection(id, null);
    await loadMessages(id);
    setHistoryOpen(false);
  };

  const newSession = async () => {
    if (submitting) return;
    setSubmitting(true);
    try {
      const res = await createChatSession({ symbol, timeframe, mode });
      await refreshSessions();
      setActiveId(res.data.session.id);
      setRunId(null);
      saveSelection(res.data.session.id, null);
      setMessages([]);
      showSuccess(t("sessionCreated"));
    } catch {
      showError(t("loadFailed"));
    } finally {
      setSubmitting(false);
    }
  };

  const removeSession = async (id: number) => {
    if (!window.confirm(t("deleteConfirm"))) return;
    try {
      await deleteChatSession(id);
      if (activeId === id) {
        setActiveId(null);
        setRunId(null);
        saveSelection(null, null);
        setMessages([]);
      }
      await refreshSessions();
      showSuccess(t("archived"));
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      showError(status === 409 ? t("deleteConflict") : t("loadFailed"));
    }
  };

  const submit = async (payload: { message?: string; preset?: AgentChatPreset }) => {
    if (submitting) return;
    setSubmitting(true);
    try {
      let sid = activeId;
      if (sid === null) {
        const res = await createChatSession({ symbol, timeframe, mode });
        sid = res.data.session.id;
        setActiveId(sid);
        await refreshSessions();
      }
      // 幂等键：网络歧义时前端重试可复用同一 request_id，后端不产生重复任务
      const requestId = createRequestId();
      pendingRef.current = { requestId };
      const res = await startChatRun(sid as number, { ...payload, mode, request_id: requestId });
      pendingRef.current = null;
      setRunId(res.data.run.id);
      saveSelection(sid as number, res.data.run.id);
      await loadMessages(sid as number);
      showSuccess(t("submittedRun"));
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      showError(status === 429 ? detail || t("sendFailed") : detail || t("sendFailed"));
    } finally {
      setSubmitting(false);
    }
  };

  const handleSend = () => {
    const text = input.trim();
    if (!text || submitting) return;
    setInput("");
    setMessages((prev) => [
      ...prev,
      { id: -Date.now(), role: "user", content: text, tool_calls: null, duration_s: null, created_at: new Date().toISOString() },
    ]);
    void submit({ message: text });
  };

  const handlePreset = (preset: AgentChatPreset) => {
    if (submitting) return;
    void submit({ preset });
  };

  const handleCancel = async () => {
    if (!runId) return;
    try {
      await cancelChatRun(runId);
      showSuccess(t("cancelRequested"));
    } catch {
      showError(t("cancelFailed"));
    }
  };

  const handleRetry = () => {
    const message = detail?.run.message;
    if (!message || submitting) return;
    void submit({ message });
  };


  const sessionItems = sessions.map((s) => (
    <div
      key={s.id}
      className={`group flex items-center justify-between rounded-lg px-3 py-2 cursor-pointer text-sm ${
        activeId === s.id ? "bg-primary/10 font-medium" : "hover:bg-muted"
      }`}
      onClick={() => openSession(s.id)}
    >
      <div className="min-w-0">
        <div className="truncate">{s.title}</div>
        <div className="text-[10px] text-muted-foreground">{s.symbol}</div>
      </div>
      <button
        type="button"
        aria-label={t("delete")}
        className="opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-red-500"
        onClick={(e) => {
          e.stopPropagation();
          removeSession(s.id);
        }}
      >
        <Trash2 className="size-4" />
      </button>
    </div>
  ));

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)]">
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <div className="flex flex-1 min-h-0 gap-4">
        {/* 桌面端会话列表 */}
        <aside className="hidden md:flex w-56 flex-col gap-2 border-r pr-3 overflow-y-auto">
          <Button variant="outline" size="sm" onClick={newSession} disabled={submitting}>
            <Plus className="size-4 mr-1" /> {t("newSession")}
          </Button>
          {loading ? <Skeleton className="h-16" /> : sessionItems}
        </aside>

        {/* 移动端会话历史抽屉 */}
        {historyOpen && (
          <div className="fixed inset-0 z-40 md:hidden" onClick={() => setHistoryOpen(false)}>
            <div className="absolute inset-0 bg-black/40" />
            <aside
              className="absolute left-0 top-0 bottom-0 w-64 bg-sidebar border-r p-3 flex flex-col gap-2 overflow-y-auto"
              onClick={(e) => e.stopPropagation()}
            >
              <Button variant="outline" size="sm" onClick={newSession} disabled={submitting}>
                <Plus className="size-4 mr-1" /> {t("newSession")}
              </Button>
              <button type="button" className="text-left text-sm text-muted-foreground mb-1" onClick={() => setHistoryOpen(false)}>
                {t("closeHistory")}
              </button>
              {loading ? <Skeleton className="h-16" /> : sessionItems}
            </aside>
          </div>
        )}

        {/* 聊天区 */}
        <section className="flex flex-1 min-h-0 flex-col">
          <div className="flex items-center gap-2 pb-3 flex-wrap">
            <Button variant="ghost" size="icon" className="md:hidden" onClick={() => setHistoryOpen(true)} aria-label={t("history")}>
              <Menu className="size-4" />
            </Button>
            <Select value={symbol} onValueChange={(v) => v && setSymbol(v)}>
              <SelectTrigger className="w-28">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(symbols.length ? symbols : ["GOLD"]).map((s) => (
                  <SelectItem key={s} value={s}>{s}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={timeframe} onValueChange={(v) => v && setTimeframe(v)}>
              <SelectTrigger className="w-24">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {["M15", "H1", "H4", "D1"].map((tf) => (
                  <SelectItem key={tf} value={tf}>{tf}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={mode} onValueChange={(v) => v && setMode(v as AgentChatMode)}>
              <SelectTrigger className="w-28">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="single">{t("modeSingle")}</SelectItem>
                <SelectItem value="experts">{t("modeExperts")}</SelectItem>
              </SelectContent>
            </Select>
            <Button variant="secondary" size="sm" onClick={() => handlePreset("trading_plan")} disabled={submitting}>
              <FileText className="size-4 mr-1" /> {t("tradingPlan")}
            </Button>
            <Button variant="secondary" size="sm" onClick={() => handlePreset("report")} disabled={submitting}>
              <FileText className="size-4 mr-1" /> {t("report")}
            </Button>
          </div>
          {mode === "experts" && <p className="text-[11px] text-muted-foreground pb-2">{t("expertsHint")}</p>}


          {/* 消息流 + 运行面板 */}
          <div className="flex-1 min-h-0 overflow-y-auto space-y-3 rounded-xl border p-4">
            {messages.length === 0 && !detail && !submitting && (
              <p className="text-sm text-muted-foreground text-center pt-10">{t("emptyHint")}</p>
            )}
            {messages.map((m) => (
              <ChatMessageBubble key={m.id} msg={m} />
            ))}
            <RunPanel
              detail={detail}
              config={config}
              disconnected={disconnected}
              retryable={Boolean(detail?.run.message)}
              onCancel={handleCancel}
              onRetry={handleRetry}
            />
            {submitting && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" /> {t("thinking")}
              </div>
            )}
          </div>

          {/* 输入区 */}
          <div className="flex gap-2 pt-3">
            <Input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.nativeEvent.isComposing) handleSend();
              }}
              placeholder={t("inputPlaceholder")}
              disabled={submitting}
            />
            <Button onClick={handleSend} disabled={submitting || !input.trim()}>
              {submitting ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
            </Button>
          </div>
          <p className="pt-1 text-[10px] text-muted-foreground">{t("disclaimer")}</p>
        </section>
      </div>
    </div>
  );
}

