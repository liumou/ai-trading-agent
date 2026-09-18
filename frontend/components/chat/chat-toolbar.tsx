"use client";

import { type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { FileText } from "lucide-react";
import type { AgentChatMode, AgentChatPreset } from "@/lib/api";

interface ChatToolbarProps {
  symbols: string[];
  symbol: string;
  timeframe: string;
  mode: AgentChatMode;
  submitting: boolean;
  /** 可选：渲染在工具栏行首（如移动端历史抽屉开关）。 */
  leading?: ReactNode;
  onSymbolChange: (v: string | null) => void;
  onTimeframeChange: (v: string | null) => void;
  onModeChange: (v: AgentChatMode) => void;
  onPreset: (preset: AgentChatPreset) => void;
}

/** 顶部工具栏：symbol/timeframe/mode Select + preset 按钮 + experts 提示。纯展示。 */
export function ChatToolbar({
  symbols, symbol, timeframe, mode, submitting, leading,
  onSymbolChange, onTimeframeChange, onModeChange, onPreset,
}: ChatToolbarProps) {
  const t = useTranslations("agentChat");
  return (
    <>
      <div className="flex items-center gap-2 pb-3 flex-wrap">
        {leading}
        <Select value={symbol} onValueChange={onSymbolChange}>
          <SelectTrigger className="w-28">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(symbols.length ? symbols : ["GOLD"]).map((s) => (
              <SelectItem key={s} value={s}>{s}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={timeframe} onValueChange={onTimeframeChange}>
          <SelectTrigger className="w-24">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {["M15", "H1", "H4", "D1"].map((tf) => (
              <SelectItem key={tf} value={tf}>{tf}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={mode} onValueChange={(v) => v && onModeChange(v as AgentChatMode)}>
          <SelectTrigger className="w-28">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="single">{t("modeSingle")}</SelectItem>
            <SelectItem value="experts">{t("modeExperts")}</SelectItem>
          </SelectContent>
        </Select>
        <Button variant="secondary" size="sm" onClick={() => onPreset("trading_plan")} disabled={submitting}>
          <FileText className="size-4 mr-1" /> {t("tradingPlan")}
        </Button>
        <Button variant="secondary" size="sm" onClick={() => onPreset("report")} disabled={submitting}>
          <FileText className="size-4 mr-1" /> {t("report")}
        </Button>
      </div>
      {mode === "experts" && <p className="text-[11px] text-muted-foreground pb-2">{t("expertsHint")}</p>}
    </>
  );
}
