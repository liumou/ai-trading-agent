"use client";

import { useEffect, useState, useCallback } from "react";
import Image from "next/image";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from "@/components/ui/dialog";
import { Save, RotateCcw, RefreshCw } from "lucide-react";
import { getAgentPrompts, updateAgentPrompt, resetAgentPrompt } from "@/lib/api";
import { showSuccess, showError } from "@/lib/toast";

interface AgentPrompt {
  id: string;
  name: string;
  model: string;
  description: string;
  default_prompt: string;
  active_prompt: string;
  is_customized: boolean;
}

interface AgentConfig {
  character: string;
  roleKey: string;
  accent: string;
  accentBorder: string;
  accentBg: string;
  accentText: string;
  glow: string;
  floorKey: string;
}

const MODEL_BADGE: Record<string, { label: string; color: string }> = {
  "claude-sonnet-4-20250514": { label: "Sonnet", color: "text-blue-400 border-blue-500/40 bg-blue-500/10" },
  "claude-haiku-4-5-20251001": { label: "Haiku", color: "text-amber-400 border-amber-500/40 bg-amber-500/10" },
};

const AGENT_CONFIG: Record<string, AgentConfig> = {
  orchestrator: {
    character: "/agent-characters/Orchestrator.png",
    roleKey: "roles.headTrader",
    accent: "blue",
    accentBorder: "border-blue-500/50",
    accentBg: "bg-blue-500/5",
    accentText: "text-blue-400",
    glow: "hover:shadow-blue-500/30",
    floorKey: "roles.headOfficeSuite",
  },
  technical_analyst: {
    character: "/agent-characters/Technical Analyst.png",
    roleKey: "roles.chartAnalyst",
    accent: "green",
    accentBorder: "border-green-500/50",
    accentBg: "bg-green-500/5",
    accentText: "text-green-400",
    glow: "hover:shadow-green-500/30",
    floorKey: "roles.analysisDesk1",
  },
  fundamental_analyst: {
    character: "/agent-characters/Fundamental Analyst.png",
    roleKey: "roles.marketResearcher",
    accent: "purple",
    accentBorder: "border-purple-500/50",
    accentBg: "bg-purple-500/5",
    accentText: "text-purple-400",
    glow: "hover:shadow-purple-500/30",
    floorKey: "roles.analysisDesk2",
  },
  risk_analyst: {
    character: "/agent-characters/Risk Analyst.png",
    roleKey: "roles.riskManager",
    accent: "red",
    accentBorder: "border-red-500/50",
    accentBg: "bg-red-500/5",
    accentText: "text-red-400",
    glow: "hover:shadow-red-500/30",
    floorKey: "roles.analysisDesk3",
  },
  reflector: {
    character: "/agent-characters/Reflector.png",
    roleKey: "roles.tradeReviewer",
    accent: "amber",
    accentBorder: "border-amber-500/50",
    accentBg: "bg-amber-500/5",
    accentText: "text-amber-400",
    glow: "hover:shadow-amber-500/30",
    floorKey: "roles.reviewOffice",
  },
  sentiment: {
    character: "/agent-characters/Sentiment Analyzer.png",
    roleKey: "roles.sentimentAnalyzer",
    accent: "pink",
    accentBorder: "border-pink-500/50",
    accentBg: "bg-pink-500/5",
    accentText: "text-pink-400",
    glow: "hover:shadow-pink-500/30",
    floorKey: "roles.newsDesk",
  },
  optimization: {
    character: "/agent-characters/Strategy Optimizer.png",
    roleKey: "roles.strategyOptimizer",
    accent: "cyan",
    accentBorder: "border-cyan-500/50",
    accentBg: "bg-cyan-500/5",
    accentText: "text-cyan-400",
    glow: "hover:shadow-cyan-500/30",
    floorKey: "roles.optimizationLab",
  },
  single_agent: {
    character: "/agent-characters/Single Agent Agent.png",
    roleKey: "roles.soloTrader",
    accent: "zinc",
    accentBorder: "border-zinc-500/50",
    accentBg: "bg-zinc-500/5",
    accentText: "text-zinc-300",
    glow: "hover:shadow-zinc-500/30",
    floorKey: "roles.backupDesk",
  },
};

const FALLBACK_CONFIG: AgentConfig = {
  character: "/agent-characters/Single Agent Agent.png",
  roleKey: "roles.agent",
  accent: "zinc",
  accentBorder: "border-zinc-500/50",
  accentBg: "bg-zinc-500/5",
  accentText: "text-zinc-300",
  glow: "hover:shadow-zinc-500/30",
  floorKey: "roles.agent",
};

function TickerTape() {
  const tickers = [
    "GOLD ▲ 3,342.10", "BTC ▲ 84,250", "OIL ▼ 63.42",
    "USDJPY ▲ 142.88", "SP500 ▲ 5,218", "VIX ▼ 18.2",
    "GOLD ▲ 3,342.10", "BTC ▲ 84,250", "OIL ▼ 63.42",
  ];
  return (
    <div className="overflow-hidden border-b border-green-500/20 bg-black/40 py-1.5">
      <div className="flex gap-8 animate-[ticker_20s_linear_infinite] whitespace-nowrap">
        {tickers.map((t, i) => (
          <span key={i} className={`text-[11px] font-mono font-bold ${t.includes("▲") ? "text-green-400" : "text-red-400"}`}>
            {t}
          </span>
        ))}
      </div>
    </div>
  );
}

interface AgentDeskProps {
  agent: AgentPrompt;
  onClick: () => void;
  isActive: boolean;
  size?: "lg" | "md";
}

function AgentDesk({ agent, onClick, isActive, size = "md" }: AgentDeskProps) {
  const t = useTranslations("agentPrompts");
  const cfg = AGENT_CONFIG[agent.id] ?? FALLBACK_CONFIG;
  const badge = MODEL_BADGE[agent.model] ?? { label: agent.model, color: "text-muted-foreground border-border bg-muted" };
  const portraitClass = size === "lg" ? "size-44" : "size-36";
  const portraitSizeAttr = size === "lg" ? "180px" : "140px";

  return (
    <button
      type="button"
      onClick={onClick}
      className={`
        relative group text-left w-full h-full rounded-none border-2 p-4
        transition-all duration-200 cursor-pointer overflow-hidden
        ${cfg.accentBorder} ${cfg.accentBg}
        hover:scale-[1.02] hover:shadow-lg ${cfg.glow}
        active:scale-[0.98]
        ${isActive ? "ring-2 ring-primary/60" : ""}
      `}
    >
      {/* Floor area label */}
      <p className={`text-[9px] font-mono uppercase tracking-widest mb-2 opacity-70 ${cfg.accentText}`}>
        {t(cfg.floorKey)}
      </p>

      <div className={`flex gap-4 ${size === "lg" ? "items-center" : "items-start"}`}>
        {/* Character portrait */}
        <div
          className={`relative shrink-0 ${cfg.accentBg} border ${cfg.accentBorder} rounded-sm overflow-hidden ${portraitClass}`}
        >
          <Image
            src={cfg.character}
            alt={agent.name}
            fill
            sizes={portraitSizeAttr}
            className="object-contain p-1 group-hover:scale-105 transition-transform duration-300"
            priority={size === "lg"}
          />
          {/* Scanline overlay */}
          <div className="absolute inset-0 pointer-events-none bg-[linear-gradient(transparent_50%,rgba(0,0,0,0.08)_50%)] bg-size-[100%_3px] opacity-40" />
        </div>

        {/* Name plate + info */}
        <div className="flex-1 min-w-0 flex flex-col gap-1.5">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className={`font-bold text-foreground ${size === "lg" ? "text-lg" : "text-sm"}`}>
              {agent.name}
            </span>
            <Badge variant="outline" className={`text-[9px] px-1.5 py-0 ${badge.color}`}>
              {badge.label}
            </Badge>
            {agent.is_customized && (
              <Badge variant="outline" className="text-[9px] px-1.5 py-0 bg-primary/10 text-primary border-primary/30">
                {t("custom")}
              </Badge>
            )}
          </div>
          <p className={`text-xs font-mono ${cfg.accentText} opacity-90`}>
            {t(cfg.roleKey)}
          </p>
          <p className={`text-[11px] text-muted-foreground ${size === "lg" ? "line-clamp-3" : "line-clamp-2"}`}>
            {agent.description}
          </p>

          {/* Status bar */}
          <div className="mt-auto pt-2 flex items-center gap-2">
            <span className="size-1.5 rounded-full bg-green-400 animate-pulse" />
            <span className="text-[10px] font-mono text-green-400">{t("online")}</span>
            <span className="text-[10px] text-muted-foreground ml-auto group-hover:text-primary transition-colors">
              {t("configure")}
            </span>
          </div>
        </div>
      </div>
    </button>
  );
}

export default function AgentPromptsPage() {
  const t = useTranslations("agentPrompts");
  const tc = useTranslations("common");
  const [agents, setAgents] = useState<AgentPrompt[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<AgentPrompt | null>(null);
  const [editValue, setEditValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const res = await getAgentPrompts();
      setAgents(res.data.agents || []);
    } catch {
      showError(t("loadFailed"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const openDesk = (agent: AgentPrompt) => {
    setSelected(agent);
    setEditValue(agent.active_prompt);
  };

  const handleSave = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      await updateAgentPrompt(selected.id, editValue);
      await fetchData();
      showSuccess(t("promptSaved"));
      setSelected(s => s ? { ...s, active_prompt: editValue, is_customized: true } : null);
    } catch { showError(t("promptSaveFailed")); }
    finally { setSaving(false); }
  };

  const handleReset = async () => {
    if (!selected) return;
    if (!confirm(t("resetConfirm"))) return;
    setResetting(true);
    try {
      await resetAgentPrompt(selected.id);
      await fetchData();
      showSuccess(t("promptReset"));
      setSelected(s => s ? { ...s, active_prompt: s.default_prompt, is_customized: false } : null);
      setEditValue(selected.default_prompt);
    } catch { showError(t("resetFailed")); }
    finally { setResetting(false); }
  };

  const isDirty = selected && editValue !== selected.active_prompt;

  const orchestrator = agents.filter(a => a.id === "orchestrator");
  const analysts = agents.filter(a => ["technical_analyst", "fundamental_analyst", "risk_analyst"].includes(a.id));
  const others = agents.filter(a => !["orchestrator", "technical_analyst", "fundamental_analyst", "risk_analyst"].includes(a.id));

  if (loading) {
    return (
      <div className="p-4 sm:p-6 xl:p-8 space-y-4">
        <Skeleton className="h-8 w-64" />
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-48 rounded-none" />)}
        </div>
      </div>
    );
  }

  return (
    <div className="p-4 sm:p-6 xl:p-8 space-y-0 page-enter">
      {/* Floor header */}
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold tracking-tight font-mono">
            {t("title")}
          </h1>
          <p className="text-xs text-muted-foreground font-mono mt-0.5">
            {t("agentsOnline", { count: agents.length })}
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={fetchData}
          className="text-xs rounded-none font-mono gap-1.5"
        >
          <RefreshCw className="size-3" />
          {t("refresh")}
        </Button>
      </div>

      <TickerTape />

      {/* Trading floor grid */}
      <div className="border-2 border-border trading-floor-grid">
        <div className="border-b border-green-500/20 bg-green-500/5 px-4 py-1.5">
          <p className="text-[10px] font-mono text-green-500/70 uppercase tracking-widest">{t("tradingFloorActive")}</p>
        </div>

        {/* Orchestrator — full width top */}
        {orchestrator.length > 0 && (
          <div className="border-b-2 border-border">
            <div className="px-3 py-1 bg-blue-500/5 border-b border-blue-500/20">
              <p className="text-[9px] font-mono text-blue-400/70 uppercase tracking-widest">{t("headOffice")}</p>
            </div>
            <div className="p-2">
              {orchestrator.map(a => (
                <AgentDesk
                  key={a.id}
                  agent={a}
                  onClick={() => openDesk(a)}
                  isActive={selected?.id === a.id}
                  size="lg"
                />
              ))}
            </div>
          </div>
        )}

        {/* Analysts — 3 columns */}
        {analysts.length > 0 && (
          <div className="border-b-2 border-border">
            <div className="px-3 py-1 bg-muted/30 border-b border-border">
              <p className="text-[9px] font-mono text-muted-foreground uppercase tracking-widest">{t("analysisRoom")}</p>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 divide-y md:divide-y-0 md:divide-x-2 divide-border">
              {analysts.map(a => (
                <div key={a.id} className="p-2">
                  <AgentDesk agent={a} onClick={() => openDesk(a)} isActive={selected?.id === a.id} />
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Support desks */}
        {others.length > 0 && (
          <div>
            <div className="px-3 py-1 bg-muted/20 border-b border-border">
              <p className="text-[9px] font-mono text-muted-foreground uppercase tracking-widest">{t("supportDesks")}</p>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 divide-y xl:divide-y-0 md:divide-x-2 xl:divide-x-2 divide-border">
              {others.map(a => (
                <div key={a.id} className="p-2">
                  <AgentDesk agent={a} onClick={() => openDesk(a)} isActive={selected?.id === a.id} />
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Bottom status bar */}
      <div className="border-2 border-t-0 border-border bg-black/60 px-4 py-2 flex items-center gap-4 font-mono text-[10px]">
        <span className="text-green-400">{t("systemOnline")}</span>
        <span className="text-muted-foreground">|</span>
        <span className="text-muted-foreground">
          {t("customPromptsActive", { count: agents.filter(a => a.is_customized).length })}
        </span>
        <span className="text-muted-foreground ml-auto">
          {t("changesApplyNote")}
        </span>
      </div>

      {/* Prompt editor dialog */}
      <Dialog open={!!selected} onOpenChange={(o) => { if (!o) setSelected(null); }}>
        <DialogContent className="max-w-3xl rounded-none border-2">
          {selected && (() => {
            const cfg = AGENT_CONFIG[selected.id] ?? FALLBACK_CONFIG;
            const badge = MODEL_BADGE[selected.model] ?? { label: selected.model, color: "text-muted-foreground" };
            return (
              <>
                <DialogHeader>
                  <DialogTitle className="flex items-center gap-3 font-mono">
                    <div className={`relative shrink-0 ${cfg.accentBg} border ${cfg.accentBorder} rounded-sm overflow-hidden size-14`}>
                      <Image
                        src={cfg.character}
                        alt={selected.name}
                        fill
                        sizes="56px"
                        className="object-contain p-0.5"
                      />
                    </div>
                    <div>
                      <div className="flex items-center gap-2 flex-wrap">
                        <span>{selected.name}</span>
                        <Badge variant="outline" className={`text-[9px] ${badge.color}`}>{badge.label}</Badge>
                        {selected.is_customized && (
                          <Badge variant="outline" className="text-[9px] bg-primary/10 text-primary border-primary/30">{t("custom")}</Badge>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground font-normal mt-0.5">{selected.description}</p>
                    </div>
                  </DialogTitle>
                  <DialogDescription className={`text-[10px] font-mono ${cfg.accentText} opacity-80`}>
                    {t(cfg.floorKey)} · {t(cfg.roleKey)}
                  </DialogDescription>
                </DialogHeader>

                <textarea
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  className="w-full h-72 border border-border bg-black/60 p-3 text-xs font-mono leading-relaxed resize-y focus:outline-none focus:ring-2 focus:ring-primary/50 rounded-none"
                  spellCheck={false}
                  aria-label={t("systemPromptFor", { name: selected.name })}
                />

                <div className="flex items-center justify-between">
                  <span className="text-[11px] font-mono text-muted-foreground">
                    {t("chars", { count: editValue.length.toLocaleString() })}
                    {isDirty && <span className="text-amber-400 ml-2">{t("unsaved")}</span>}
                  </span>
                  <div className="flex gap-2">
                    {selected.is_customized && (
                      <Button variant="outline" size="sm" onClick={handleReset} disabled={resetting} className="rounded-none text-xs font-mono">
                        <RotateCcw className="size-3 mr-1" />
                        {resetting ? t("resetting") : t("resetDefault")}
                      </Button>
                    )}
                    <Button size="sm" onClick={handleSave} disabled={!isDirty || saving || editValue.length < 10} className="rounded-none text-xs font-mono">
                      <Save className="size-3 mr-1" />
                      {saving ? t("saving") : t("savePrompt")}
                    </Button>
                  </div>
                </div>
              </>
            );
          })()}
        </DialogContent>
      </Dialog>
    </div>
  );
}
