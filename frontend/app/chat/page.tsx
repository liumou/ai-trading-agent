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
import { FileText, Loader2, Plus, Send, Trash2, Wrench } from "lucide-react";
import {
  createChatSession,
  deleteChatSession,
  getChatSession,
  getSymbols,
  listChatSessions,
  sendChatMessage,
  sendChatPreset,
  type AgentChatMessage as ChatMsg,
} from "@/lib/api";
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
        {!isUser && msg.tool_calls && msg.tool_calls.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-1 text-[10px] text-muted-foreground">
            <Wrench className="inline size-3" />
            {(msg.tool_calls as { tool?: string; name?: string }[]).map((tc, i) => (
              <span key={i} className="rounded bg-background/60 px-1.5 py-0.5">
                {tc.tool || tc.name || "tool"}
              </span>
            ))}
          </div>
        )}
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
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);

  const refreshSessions = useCallback(async () => {
    try {
      const res = await listChatSessions();
      setSessions(res.data.sessions);
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
      } catch {
        /* keep default GOLD */
      }
      setLoading(false);
    })();
  }, [refreshSessions]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  const openSession = async (id: number) => {
    setActiveId(id);
    try {
      const res = await getChatSession(id);
      setMessages(res.data.messages);
      setSymbol(res.data.session.symbol);
    } catch {
      showError(t("loadFailed"));
    }
  };

  const newSession = async () => {
    try {
      const res = await createChatSession({ symbol });
      await refreshSessions();
      setActiveId(res.data.session.id);
      setMessages([]);
      showSuccess(t("sessionCreated"));
    } catch {
      showError(t("loadFailed"));
    }
  };

  const removeSession = async (id: number) => {
    try {
      await deleteChatSession(id);
      if (activeId === id) {
        setActiveId(null);
        setMessages([]);
      }
      await refreshSessions();
    } catch {
      showError(t("loadFailed"));
    }
  };

  const handleSend = async () => {
    const text = input.trim();
    if (!text || sending) return;
    let sessionId = activeId;
    if (sessionId === null) {
      try {
        const res = await createChatSession({ symbol });
        sessionId = res.data.session.id;
        setActiveId(sessionId);
        await refreshSessions();
      } catch {
        showError(t("loadFailed"));
        return;
      }
    }
    setInput("");
    setMessages((prev) => [
      ...prev,
      { id: Date.now() - 1, role: "user", content: text, tool_calls: null, duration_s: null, created_at: new Date().toISOString() },
    ]);
    const id = sessionId as number;
    setSending(true);
    try {
      const res = await sendChatMessage(id, text);
      setMessages((prev) => [
        ...prev,
        { id: Date.now(), role: "assistant", content: res.data.reply, tool_calls: res.data.tool_calls, duration_s: null, created_at: new Date().toISOString() },
      ]);
      await refreshSessions();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      showError(detail || t("sendFailed"));
    } finally {
      setSending(false);
    }
  };

  const handlePreset = async (preset: "trading_plan" | "report") => {
    if (sending) return;
    let sessionId = activeId;
    if (sessionId === null) {
      try {
        const res = await createChatSession({ symbol, mode: preset });
        sessionId = res.data.session.id;
        setActiveId(sessionId);
        await refreshSessions();
      } catch {
        showError(t("loadFailed"));
        return;
      }
    }
    setSending(true);
    try {
      const res = await sendChatPreset(sessionId as number, preset);
      setMessages((prev) => [
        ...prev,
        { id: Date.now(), role: "assistant", content: res.data.reply, tool_calls: res.data.tool_calls, duration_s: null, created_at: new Date().toISOString() },
      ]);
      await refreshSessions();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      showError(detail || t("sendFailed"));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)]">
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <div className="flex flex-1 min-h-0 gap-4">
        <aside className="hidden md:flex w-56 flex-col gap-2 border-r pr-3 overflow-y-auto">
          <Button variant="outline" size="sm" onClick={newSession}>
            <Plus className="size-4 mr-1" /> {t("newSession")}
          </Button>
          {loading ? (
            <Skeleton className="h-16" />
          ) : (
            sessions.map((s) => (
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
            ))
          )}
        </aside>

        <section className="flex flex-1 min-h-0 flex-col">
          <div className="flex items-center gap-2 pb-3 flex-wrap">
            <Select value={symbol} onValueChange={(v: string | null) => { if (v) setSymbol(v); }}>
              <SelectTrigger className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(symbols.length > 0 ? symbols : ["GOLD"]).map((s) => (
                  <SelectItem key={s} value={s}>{s}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button variant="secondary" size="sm" onClick={() => handlePreset("trading_plan")} disabled={sending}>
              <FileText className="size-4 mr-1" /> {t("tradingPlan")}
            </Button>
            <Button variant="secondary" size="sm" onClick={() => handlePreset("report")} disabled={sending}>
              <FileText className="size-4 mr-1" /> {t("report")}
            </Button>
            <Button variant="outline" size="sm" onClick={newSession} className="md:hidden">
              <Plus className="size-4" />
            </Button>
          </div>

          <div className="flex-1 min-h-0 overflow-y-auto space-y-3 rounded-xl border p-4">
            {messages.length === 0 && !sending && (
              <p className="text-sm text-muted-foreground text-center pt-10">{t("emptyHint")}</p>
            )}
            {messages.map((m) => (
              <ChatMessageBubble key={m.id} msg={m} />
            ))}
            {sending && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" /> {t("thinking")}
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          <div className="flex gap-2 pt-3">
            <Input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.nativeEvent.isComposing) handleSend();
              }}
              placeholder={t("inputPlaceholder")}
              disabled={sending}
            />
            <Button onClick={handleSend} disabled={sending || !input.trim()}>
              <Send className="size-4" />
            </Button>
          </div>
          <p className="pt-1 text-[10px] text-muted-foreground">{t("disclaimer")}</p>
        </section>
      </div>
    </div>
  );
}

