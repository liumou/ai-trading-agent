"use client";

import { useEffect, useState, useCallback } from "react";
import { useTranslations } from "next-intl";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Shield, AlertTriangle, CheckCircle2, XCircle, Info, RefreshCw, Loader2,
} from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import {
  getBotStatus, updateSettings, updateStrategy, getRolloutMode, setRolloutMode, getRolloutReadiness, getAvailableStrategies,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { showSuccess, showError } from "@/lib/toast";

type RolloutMode = "shadow" | "paper" | "micro" | "live";
type Check = { name: string; status: string; detail: string };
type SymbolStatus = {
  symbol: string;
  state: string;
  strategy: string;
  timeframe: string;
  paper_trade: boolean;
  max_lot: number;
  fixed_lot: number | null;
  max_risk_per_trade: number;
  max_daily_loss: number;
  max_concurrent_trades: number;
};

const MODE_CONFIG: Record<RolloutMode, { labelKey: string; descriptionKey: string; color: string; icon: string }> = {
  shadow: {
    labelKey: "modeShadow",
    descriptionKey: "modeShadowDesc",
    color: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20",
    icon: "opacity-50",
  },
  paper: {
    labelKey: "modePaper",
    descriptionKey: "modePaperDesc",
    color: "bg-blue-500/10 text-blue-400 border-blue-500/20",
    icon: "text-blue-400",
  },
  micro: {
    labelKey: "modeMicro",
    descriptionKey: "modeMicroDesc",
    color: "bg-amber-500/10 text-amber-400 border-amber-500/20",
    icon: "text-amber-400",
  },
  live: {
    labelKey: "modeLive",
    descriptionKey: "modeLiveDesc",
    color: "bg-red-500/10 text-red-400 border-red-500/20",
    icon: "text-red-400",
  },
};

const MODE_ORDER: RolloutMode[] = ["shadow", "paper", "micro", "live"];

export default function SettingsPage() {
  const t = useTranslations("settings");
  const tc = useTranslations("common");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [rolloutMode, setRolloutModeState] = useState<RolloutMode>("shadow");
  const [symbolStatuses, setSymbolStatuses] = useState<Record<string, SymbolStatus>>({});
  const [checks, setChecks] = useState<Check[]>([]);
  const [readiness, setReadiness] = useState<{ ready: boolean; errors: number; warnings: number } | null>(null);
  const [confirmLive, setConfirmLive] = useState(false);
  const [strategies, setStrategies] = useState<{ name: string; worst_case: string }[]>([]);
  const [autoStrategySwitch, setAutoStrategySwitch] = useState(false);

  const fetchAll = useCallback(async () => {
    try {
      const [rolloutRes, statusRes, readinessRes, stratRes] = await Promise.all([
        getRolloutMode().catch(() => null),
        getBotStatus().catch(() => null),
        getRolloutReadiness().catch(() => null),
        getAvailableStrategies().catch(() => null),
      ]);

      if (rolloutRes?.data) {
        setRolloutModeState(rolloutRes.data.mode);
      }
      if (statusRes?.data?.symbols) {
        setSymbolStatuses(statusRes.data.symbols);
      }
      if (statusRes?.data?.enable_auto_strategy_switch !== undefined) {
        setAutoStrategySwitch(statusRes.data.enable_auto_strategy_switch);
      }
      if (readinessRes?.data) {
        setChecks(readinessRes.data.checks || []);
        setReadiness({
          ready: readinessRes.data.ready,
          errors: readinessRes.data.errors,
          warnings: readinessRes.data.warnings,
        });
      }
      if (stratRes?.data?.strategies) {
        setStrategies(stratRes.data.strategies);
      }
    } catch {
      /* handled */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const handleModeChange = async (mode: RolloutMode) => {
    if (mode === "live" && !confirmLive) {
      setConfirmLive(true);
      return;
    }
    setSaving(true);
    setConfirmLive(false);
    try {
      await setRolloutMode(mode);
      setRolloutModeState(mode);
      showSuccess(t("switchedTo", { mode: t(MODE_CONFIG[mode].labelKey) }));
      await fetchAll();
    } catch {
      showError(t("modeChangeFailed"));
    } finally {
      setSaving(false);
    }
  };

  const handleSettingChange = async (symbol: string, updates: Record<string, unknown>) => {
    try {
      await updateSettings({ symbol, ...updates });
      const res = await getBotStatus().catch(() => null);
      if (res?.data?.symbols) setSymbolStatuses(res.data.symbols);
      showSuccess(t("settingsUpdated"));
    } catch { showError(t("settingsUpdateFailed")); }
  };

  if (loading) {
    return (
      <div className="p-4 sm:p-6 xl:p-8 space-y-6">
        <Skeleton className="h-10 w-48" />
        <Skeleton className="h-48 w-full rounded-2xl" />
        <Skeleton className="h-64 w-full rounded-2xl" />
      </div>
    );
  }

  const currentMode = MODE_CONFIG[rolloutMode];

  return (
    <div className="p-4 sm:p-6 xl:p-8 space-y-5 sm:space-y-6 max-w-4xl page-enter">
      <PageHeader title={t("title")} subtitle={t("subtitle")} />

      {/* ── Decision Mode ────────────────────────────────────── */}
      <Card>
        <CardHeader className="p-4 sm:p-6">
          <CardTitle className="text-sm font-bold flex items-center gap-2">
            <Info className="size-4" />
            {t("decisionMode")}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-4 pt-0 sm:p-6 sm:pt-0">
          <div className="rounded-xl border border-green-500/30 bg-green-500/5 p-4">
            <p className="text-sm font-bold text-green-400">{t("strategyFirst")}</p>
            <p className="text-xs text-muted-foreground mt-1">
              {t("strategyFirstDesc")}
            </p>
          </div>
          <p className="text-[11px] text-muted-foreground mt-2">
            {t("strategyFlow")}
          </p>
        </CardContent>
      </Card>

      {/* ── Auto Strategy Switch ────────────────────────────── */}
      <Card>
        <CardHeader className="p-4 sm:p-6">
          <CardTitle className="text-sm font-bold flex items-center gap-2">
            <RefreshCw className="size-4" />
            {t("autoSwitch")}
            <Badge variant="outline" className="text-[10px]">Beta</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="p-4 pt-0 sm:p-6 sm:pt-0">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-sm">{t("autoSwitchDesc")}</p>
              <p className="text-xs text-muted-foreground mt-1">
                {t("autoSwitchNote")}
              </p>
            </div>
            <Switch
              checked={autoStrategySwitch}
              onCheckedChange={async (v) => {
                try {
                  await updateSettings({ enable_auto_strategy_switch: v });
                  setAutoStrategySwitch(v);
                  showSuccess(v ? t("autoSwitchEnabled") : t("autoSwitchDisabled"));
                } catch {
                  showError(t("settingUpdateFailed"));
                }
              }}
            />
          </div>
        </CardContent>
      </Card>

      {/* ── Rollout Mode ──────────────────────────────────────── */}
      <Card>
        <CardHeader className="p-4 sm:p-6">
          <CardTitle className="text-sm font-bold flex items-center gap-2">
            <Shield className="size-4" />
            {t("rolloutMode")}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-4 pt-0 sm:p-6 sm:pt-0 space-y-4">
          {/* Current mode banner */}
          <div className={cn("rounded-xl border p-4", currentMode.color)}>
            <div className="flex items-center justify-between">
              <div>
                <p className="text-lg font-bold">{t(currentMode.labelKey)}</p>
                <p className="text-sm opacity-80">{t(currentMode.descriptionKey)}</p>
              </div>
              {saving && <Loader2 className="size-5 animate-spin opacity-50" />}
            </div>
          </div>

          {/* Mode selector */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {MODE_ORDER.map((mode) => {
              const config = MODE_CONFIG[mode];
              const isActive = mode === rolloutMode;
              return (
                <button
                  key={mode}
                  onClick={() => handleModeChange(mode)}
                  disabled={saving}
                  className={cn(
                    "relative rounded-xl border-2 p-3 text-center transition-all",
                    isActive
                      ? "border-primary bg-primary/5"
                      : "border-border hover:border-muted-foreground/30 hover:bg-muted/30",
                  )}
                >
                  <p className={cn("text-sm font-bold", isActive ? "text-foreground" : "text-muted-foreground")}>
                    {t(config.labelKey)}
                  </p>
                  {isActive && (
                    <div className="absolute -top-1 -right-1 size-3 rounded-full bg-primary" />
                  )}
                </button>
              );
            })}
          </div>

          {/* Live mode confirmation */}
          {confirmLive && (
            <div className="rounded-xl border border-red-500/30 bg-red-500/5 p-4 space-y-3">
              <div className="flex items-start gap-2">
                <AlertTriangle className="size-5 text-red-500 shrink-0 mt-0.5" />
                <div>
                  <p className="text-sm font-bold text-red-500">{t("switchToLive")}</p>
                  <p className="text-xs text-muted-foreground mt-1">
                    {t("switchToLiveDesc")}
                  </p>
                </div>
              </div>
              <div className="flex gap-2 justify-end">
                <Button size="sm" variant="ghost" onClick={() => setConfirmLive(false)}>{tc("cancel")}</Button>
                <Button size="sm" variant="destructive" onClick={() => handleModeChange("live")}>
                  {t("confirmLive")}
                </Button>
              </div>
            </div>
          )}

          {/* Mode descriptions */}
          <div className="text-xs text-muted-foreground space-y-1.5 pt-1">
            {MODE_ORDER.map((mode) => (
              <div key={mode} className="flex gap-2">
                <span className="font-bold w-14 shrink-0">{t(MODE_CONFIG[mode].labelKey)}</span>
                <span className="opacity-70">{t(MODE_CONFIG[mode].descriptionKey)}</span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* ── Per-Symbol Settings ──────────────────────────────── */}
      <Card>
        <CardHeader className="p-4 sm:p-6">
            <CardTitle className="text-sm font-bold">{t("perSymbol")}</CardTitle>
        </CardHeader>
        <CardContent className="p-4 pt-0 sm:p-6 sm:pt-0 space-y-4">
          {Object.entries(symbolStatuses).map(([symbol, st]) => (
            <div key={symbol} className="rounded-xl border border-border p-4 space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-bold">{symbol}</span>
                  <Badge variant="outline" className="text-[10px]">
                    {st.state}
                  </Badge>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">{t("paper")}</span>
                  <Switch
                    checked={st.paper_trade}
                    onCheckedChange={(v) => handleSettingChange(symbol, { paper_trade: v })}
                  />
                </div>
              </div>

              <Separator />

              {/* Strategy selector + worst case */}
              {strategies.length > 0 && (
                <div className="space-y-1.5">
                  <div className="flex items-center gap-3">
                    <div className="space-y-1 flex-1">
                      <label className="text-xs text-muted-foreground font-medium">{t("strategyLabel")}</label>
                      <Select
                        value={st.strategy || "ai_autonomous"}
                        onValueChange={async (v) => {
                          if (!v) return;
                          try {
                            await updateStrategy(v, undefined, symbol || undefined);
                            const res = await getBotStatus().catch(() => null);
                            if (res?.data?.symbols) setSymbolStatuses(res.data.symbols);
                            showSuccess(t("strategyUpdated"));
                          } catch { showError(t("strategyUpdateFailed")); }
                        }}
                      >
                        <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="ai_autonomous">{t("aiAutonomous")}</SelectItem>
                          {strategies.map((s) => (
                            <SelectItem key={s.name} value={s.name}>{s.name}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                  {(() => {
                    const selected = strategies.find((s) => s.name === st.strategy);
                    return selected?.worst_case ? (
                      <p className="text-[11px] text-amber-500/80 leading-snug">
                        <AlertTriangle className="size-3 inline mr-1" />
                        {t("worstCase", { case: selected.worst_case })}
                      </p>
                    ) : null;
                  })()}
                </div>
              )}

              <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-3 sm:gap-x-6 gap-y-3 text-xs">
                {/* Lot Mode */}
                <div className="space-y-1">
                  <label className="text-muted-foreground font-medium">{t("lotMode")}</label>
                  <Select
                    value={st.fixed_lot != null ? "fixed" : "auto"}
                    onValueChange={(v) => {
                      if (v === "auto") {
                        handleSettingChange(symbol, { lot_mode: "auto" });
                      } else {
                        handleSettingChange(symbol, { lot_mode: "fixed", fixed_lot: st.fixed_lot ?? 0.1 });
                      }
                    }}
                  >
                    <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="auto">{t("lotAutoAi")}</SelectItem>
                      <SelectItem value="fixed">{t("lotFixed")}</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                {/* Fixed Lot / Max Lot */}
                <div className="space-y-1">
                  <label className="text-muted-foreground font-medium">
                    {st.fixed_lot != null ? t("fixedLot") : t("maxLot")}
                  </label>
                  <Input
                    type="number"
                    step="0.01"
                    min="0.01"
                    className="h-8 text-xs font-mono"
                    defaultValue={st.fixed_lot ?? st.max_lot}
                    key={`${symbol}-lot-${st.fixed_lot}-${st.max_lot}`}
                    onBlur={(e) => {
                      const v = parseFloat(e.target.value);
                      if (!v || v < 0.01) return;
                      if (st.fixed_lot != null) {
                        handleSettingChange(symbol, { lot_mode: "fixed", fixed_lot: v });
                      } else {
                        handleSettingChange(symbol, { max_lot: v });
                      }
                    }}
                  />
                </div>

                {/* Timeframe */}
                <div className="space-y-1">
                  <label className="text-muted-foreground font-medium">{t("timeframe")}</label>
                  <Select
                    value={st.timeframe || "M15"}
                    onValueChange={(v) => handleSettingChange(symbol, { timeframe: v })}
                  >
                    <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {["M1", "M5", "M15", "M30", "H1", "H4", "D1"].map((tf) => (
                        <SelectItem key={tf} value={tf}>{tf}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                {/* Max Risk */}
                <div className="space-y-1">
                  <label className="text-muted-foreground font-medium">{t("maxRisk")}</label>
                  <div className="flex items-center gap-1">
                    <Input
                      type="number"
                      step="0.1"
                      min="0.1"
                      max="10"
                      className="h-8 text-xs font-mono"
                      defaultValue={((st.max_risk_per_trade ?? 0.02) * 100).toFixed(1)}
                      key={`${symbol}-risk-${st.max_risk_per_trade}`}
                      onBlur={(e) => {
                        const v = parseFloat(e.target.value);
                        if (v >= 0.1 && v <= 10) handleSettingChange(symbol, { max_risk_per_trade: v / 100 });
                      }}
                    />
                    <span className="text-muted-foreground font-medium">%</span>
                  </div>
                </div>

                {/* Max Daily Loss */}
                <div className="space-y-1">
                  <label className="text-muted-foreground font-medium">{t("maxDailyLoss")}</label>
                  <div className="flex items-center gap-1">
                    <Input
                      type="number"
                      step="0.5"
                      min="1"
                      max="20"
                      className="h-8 text-xs font-mono"
                      defaultValue={((st.max_daily_loss ?? 0.05) * 100).toFixed(1)}
                      key={`${symbol}-daily-${st.max_daily_loss}`}
                      onBlur={(e) => {
                        const v = parseFloat(e.target.value);
                        if (v >= 1 && v <= 20) handleSettingChange(symbol, { max_daily_loss: v / 100 });
                      }}
                    />
                    <span className="text-muted-foreground font-medium">%</span>
                  </div>
                </div>

                {/* Max Concurrent */}
                <div className="space-y-1">
                  <label className="text-muted-foreground font-medium">{t("maxPositions")}</label>
                  <Input
                    type="number"
                    step="1"
                    min="1"
                    max="20"
                    className="h-8 text-xs font-mono"
                    defaultValue={st.max_concurrent_trades ?? 3}
                    key={`${symbol}-conc-${st.max_concurrent_trades}`}
                    onBlur={(e) => {
                      const v = parseInt(e.target.value);
                      if (v >= 1 && v <= 20) handleSettingChange(symbol, { max_concurrent_trades: v });
                    }}
                  />
                </div>
              </div>
            </div>
          ))}

          {Object.keys(symbolStatuses).length === 0 && (
            <p className="text-sm text-muted-foreground text-center py-6">
              {t("noActiveSymbols")}
            </p>
          )}
        </CardContent>
      </Card>

      {/* ── System Readiness ─────────────────────────────────── */}
      <Card>
        <CardHeader className="p-4 sm:p-6">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm font-bold flex items-center gap-2">
              {t("systemReadiness")}
              {readiness && (
                <Badge variant={readiness.ready ? "default" : "destructive"} className="text-[10px]">
                  {readiness.ready ? t("ready") : t("errorsCount", { count: readiness.errors })}
                </Badge>
              )}
            </CardTitle>
            <Button variant="ghost" size="sm" onClick={fetchAll} className="h-7 text-xs">
              <RefreshCw className="size-3 mr-1" />
              {tc("refresh")}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="p-4 pt-0 sm:p-6 sm:pt-0">
          <div className="space-y-1.5">
            {checks.map((check) => (
              <div
                key={check.name}
                className="flex items-center gap-3 py-2 px-3 rounded-lg hover:bg-muted/30 transition-colors"
              >
                {check.status === "ok" && <CheckCircle2 className="size-4 text-green-500 shrink-0" />}
                {check.status === "warn" && <AlertTriangle className="size-4 text-amber-500 shrink-0" />}
                {check.status === "error" && <XCircle className="size-4 text-red-500 shrink-0" />}
                <div className="flex-1 min-w-0">
                  <span className="text-xs font-semibold capitalize">
                    {check.name.replace(/_/g, " ")}
                  </span>
                </div>
                <span className={cn(
                  "text-xs font-mono truncate max-w-[280px]",
                  check.status === "ok" ? "text-muted-foreground" :
                  check.status === "warn" ? "text-amber-500" : "text-red-500",
                )}>
                  {check.detail}
                </span>
              </div>
            ))}
            {checks.length === 0 && (
              <div className="flex items-center gap-2 text-muted-foreground py-4 justify-center">
                <Info className="size-4" />
                <span className="text-sm">{t("unableToFetchChecks")}</span>
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
