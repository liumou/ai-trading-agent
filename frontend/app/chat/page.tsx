"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Menu, Plus } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
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
import { ConversationThread } from "@/components/chat/conversation-thread";
import { MessageBubble } from "@/components/chat/message-bubble";
import { SessionList, type SessionListItem } from "@/components/chat/session-list";
import { ChatToolbar } from "@/components/chat/chat-toolbar";
import { InputComposer } from "@/components/chat/input-composer";
import { ChatEmptyState } from "@/components/chat/chat-empty-state";
import { useRunDetail } from "@/components/chat/use-run-detail";
import { createRequestId, isActiveRun, loadSelection, saveSelection } from "@/components/chat/run-state";
import { showError, showSuccess } from "@/lib/toast";

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
  // 轮询驱动的滚动信号：每次 run 状态更新递增，供消息流自动滚到底部
  const [scrollSignal, setScrollSignal] = useState(0);
  const [focusSignal, setFocusSignal] = useState(0);

  const onRunUpdate = useCallback((run: AgentChatRun) => {
    setScrollSignal((n) => n + 1);
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
      // session.mode 已废弃、不承载 UI 语义（真实模式在 run.mode）。
      // 后端 SessionCreateRequest 只接受 free/trading_plan/report，传 "free" 避免 422。
      const res = await createChatSession({ symbol, timeframe, mode: "free" });
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
        // session.mode 已废弃、不承载 UI 语义（真实模式在 run.mode），传 "free" 避免 422。
        const res = await createChatSession({ symbol, timeframe, mode: "free" });
        sid = res.data.session.id;
        setActiveId(sid);
        await refreshSessions();
      }
      // 幂等键：网络歧义时前端重试可复用同一 request_id，后端不产生重复任务
      const requestId = createRequestId();
      const res = await startChatRun(sid as number, { ...payload, mode, request_id: requestId });
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

  /** 空态建议卡：填入输入框而非直接发送，保留用户确认权；聚焦让光标就位。 */
  const handleSuggest = (text: string) => {
    setInput(text);
    setFocusSignal((n) => n + 1);
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
    const message = detail?.run.message ?? undefined;
    const preset = detail?.run.preset as AgentChatPreset | undefined;
    if ((!message && !preset) || submitting) return;
    void submit({ message, preset });
  };

  return (
    <div className="flex flex-col h-[calc(100dvh-4rem)]">
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <div className="flex flex-1 min-h-0 gap-4">
        {/* 桌面端会话列表 */}
        <aside className="hidden md:flex w-56 flex-col gap-2 border-r pr-3 overflow-y-auto">
          <Button variant="outline" size="sm" onClick={newSession} disabled={submitting}>
            <Plus className="size-4 mr-1" /> {t("newSession")}
          </Button>
          <SessionList
            sessions={sessions}
            activeId={activeId}
            loading={loading}
            onSelect={openSession}
            onDelete={removeSession}
          />
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
              <SessionList
                sessions={sessions}
                activeId={activeId}
                loading={loading}
                onSelect={openSession}
                onDelete={removeSession}
              />
            </aside>
          </div>
        )}

        {/* 聊天区 */}
        <section className="flex flex-1 min-h-0 flex-col">
          <ChatToolbar
            symbols={symbols}
            symbol={symbol}
            timeframe={timeframe}
            mode={mode}
            submitting={submitting}
            leading={
              <Button variant="ghost" size="icon" className="md:hidden" onClick={() => setHistoryOpen(true)} aria-label={t("history")}>
                <Menu className="size-4" />
              </Button>
            }
            onSymbolChange={(v) => v && setSymbol(v)}
            onTimeframeChange={(v) => v && setTimeframe(v)}
            onModeChange={(v) => setMode(v)}
            onPreset={handlePreset}
          />

          {/* 消息流 + 运行面板 */}
          <ConversationThread
            showEmpty={messages.length === 0 && !detail && !submitting}
            empty={<ChatEmptyState onSuggest={handleSuggest} />}
            thinking={submitting}
            thinkingText={t("thinking")}
            messages={messages.map((m) => <MessageBubble key={m.id} msg={m} />)}
            scrollSignal={scrollSignal}
          >
            <RunPanel
              detail={detail}
              config={config}
              disconnected={disconnected}
              retryable={Boolean(detail?.run.message)}
              onCancel={handleCancel}
              onRetry={handleRetry}
            />
          </ConversationThread>

          {/* 输入区 */}
          <InputComposer
            value={input}
            placeholder={t("inputPlaceholder")}
            submitting={submitting}
            onChange={setInput}
            onSend={handleSend}
            focusSignal={focusSignal}
          />
          <div className="flex items-center gap-2 pt-1 text-[10px] text-muted-foreground">
            <span className="caption shrink-0">{t("sendHint")}</span>
            <span className="opacity-40 select-none">·</span>
            <span className="min-w-0 truncate">{t("disclaimer")}</span>
          </div>
        </section>
      </div>
    </div>
  );
}
