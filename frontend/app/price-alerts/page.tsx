"use client";

import { useCallback, useEffect, useState } from "react";
import { AxiosError } from "axios";
import { BellRing, Loader2, Plus, Send } from "lucide-react";
import { useTranslations } from "next-intl";
import { PageHeader } from "@/components/layout/PageHeader";
import { PageInstructions } from "@/components/layout/PageInstructions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  createPriceAlert,
  deletePriceAlert,
  listPriceAlerts,
  listSymbolConfigs,
  testPriceAlert,
  togglePriceAlert,
  updatePriceAlert,
  type PriceAlert,
  type PriceAlertInput,
  type SymbolConfig,
} from "@/lib/api";

type AlertForm = {
  symbol: string;
  condition: "above" | "below";
  trigger_price: string;
  duration_seconds: string;
  max_notifications: string;
  note: string;
};

const EMPTY_FORM: AlertForm = {
  symbol: "",
  condition: "above",
  trigger_price: "",
  duration_seconds: "60",
  max_notifications: "1",
  note: "",
};

function toForm(a?: PriceAlert): AlertForm {
  if (!a) return EMPTY_FORM;
  return {
    symbol: a.symbol,
    condition: a.condition,
    trigger_price: String(a.trigger_price),
    duration_seconds: String(a.duration_seconds),
    max_notifications: String(a.max_notifications),
    note: a.note ?? "",
  };
}

function fromForm(f: AlertForm): PriceAlertInput {
  return {
    symbol: f.symbol,
    condition: f.condition,
    trigger_price: Number(f.trigger_price),
    duration_seconds: Number(f.duration_seconds),
    max_notifications: Number(f.max_notifications),
    note: f.note.trim() || null,
  };
}

function errorText(err: unknown): string {
  if (err instanceof AxiosError) {
    const detail = err.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length) return detail[0]?.msg ?? err.message;
    return err.message;
  }
  if (err instanceof Error) return err.message;
  return "error";
}

export default function PriceAlertsPage() {
  const t = useTranslations("priceAlerts");
  const [alerts, setAlerts] = useState<PriceAlert[]>([]);
  const [symbols, setSymbols] = useState<SymbolConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<PriceAlert | null>(null);
  const [form, setForm] = useState<AlertForm>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [testingId, setTestingId] = useState<number | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("");

  const load = useCallback(async () => {
    try {
      const [alertsRes, symbolsRes] = await Promise.all([
        listPriceAlerts().catch(() => ({ data: [] as PriceAlert[] })),
        listSymbolConfigs().catch(() => ({ data: [] as SymbolConfig[] })),
      ]);
      setAlerts(alertsRes.data);
      setSymbols(symbolsRes.data);
      setError(null);
    } catch {
      setError(t("errorLoad"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    load();
  }, [load]);

  const openCreate = () => {
    setEditing(null);
    setForm(EMPTY_FORM);
    setDialogOpen(true);
  };

  const openEdit = (a: PriceAlert) => {
    setEditing(a);
    setForm(toForm(a));
    setDialogOpen(true);
  };

  const validateForm = (): string | null => {
    const trigger = Number(form.trigger_price);
    const dur = Number(form.duration_seconds);
    const max = Number(form.max_notifications);
    if (!form.symbol) return t("formErrorTriggerPrice");
    if (!Number.isFinite(trigger) || trigger <= 0) return t("formErrorTriggerPrice");
    if (!Number.isFinite(dur) || dur < 1) return t("formErrorDuration");
    if (!Number.isFinite(max) || max < 1) return t("formErrorMax");
    return null;
  };

  const handleSave = async () => {
    const err = validateForm();
    if (err) {
      setError(err);
      return;
    }
    setSaving(true);
    try {
      const payload = fromForm(form);
      if (editing) {
        await updatePriceAlert(editing.id, payload);
      } else {
        await createPriceAlert(payload);
      }
      setDialogOpen(false);
      await load();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (a: PriceAlert) => {
    if (!window.confirm(t("deleteConfirm"))) return;
    try {
      await deletePriceAlert(a.id);
      await load();
    } catch {
      setError(t("errorDelete"));
    }
  };

  const handleToggle = async (a: PriceAlert) => {
    try {
      await togglePriceAlert(a.id);
      await load();
    } catch {
      setError(t("errorToggle"));
    }
  };

  const handleTest = async (a: PriceAlert) => {
    setTestingId(a.id);
    try {
      await testPriceAlert(a.symbol);
      window.alert(t("testSent"));
    } catch {
      window.alert(t("testFailed"));
    } finally {
      setTestingId(null);
    }
  };

  const availableSymbols = symbols.length > 0 ? symbols : [];

  // 状态分类计数 —— 与 notifications 页筛选条一致的中间内容层
  const counts = alerts.reduce(
    (acc, a) => {
      if (a.is_active) acc.active += 1;
      else if (!a.is_active && a.sent_count >= a.max_notifications) acc.saturated += 1;
      else acc.inactive += 1;
      return acc;
    },
    { active: 0, inactive: 0, saturated: 0 },
  );

  const visibleAlerts = statusFilter
    ? alerts.filter((a) => {
        if (statusFilter === "active") return a.is_active;
        if (statusFilter === "saturated") return !a.is_active && a.sent_count >= a.max_notifications;
        if (statusFilter === "inactive") return !a.is_active && a.sent_count < a.max_notifications;
        return true;
      })
    : alerts;

  return (
    <div className="p-4 sm:p-6 xl:p-8 space-y-5 sm:space-y-6 page-enter">
      <PageHeader title={t("title")} subtitle={t("subtitle")}>
        <Button onClick={openCreate}>
          <Plus className="size-4" />
          {t("addAlert")}
        </Button>
      </PageHeader>

      <PageInstructions items={[t("instruction1"), t("instruction2"), t("instruction3")]} />

      {error && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-2 text-sm text-destructive">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-16 text-muted-foreground">
          <Loader2 className="size-5 animate-spin" />
          <span className="ml-2">{t("loading")}</span>
        </div>
      ) : alerts.length === 0 ? (
        <EmptyState
          icon={BellRing}
          heading={t("emptyHeading")}
          description={t("emptyDescription")}
          action={{ label: t("addAlert"), onClick: openCreate }}
        />
      ) : (
        <>
          {/* 状态摘要 + 筛选条 —— 对齐 notifications 页的 flex flex-wrap gap-3 中间层 */}
          <div className="flex flex-wrap gap-3">
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="rounded-md border border-border bg-background px-3 py-1.5 text-sm"
            >
              <option value="">{t("statusAll")}</option>
              <option value="active">{t("statusActive")}</option>
              <option value="saturated">{t("statusSaturated")}</option>
              <option value="inactive">{t("statusInactive")}</option>
            </select>
            <div className="flex items-center gap-2 text-xs text-muted-foreground ml-auto self-center">
              <span className="inline-flex items-center gap-1">
                <span className="size-1.5 rounded-full bg-emerald-500" />
                {t("statusActive")} {counts.active}
              </span>
              <span className="inline-flex items-center gap-1">
                <span className="size-1.5 rounded-full bg-amber-500" />
                {t("statusSaturated")} {counts.saturated}
              </span>
              <span className="inline-flex items-center gap-1">
                <span className="size-1.5 rounded-full bg-muted-foreground/40" />
                {t("statusInactive")} {counts.inactive}
              </span>
            </div>
          </div>

          {/* 表格容器 —— 对齐 symbols/accounts：rounded-xl border border-border bg-card overflow-hidden */}
          <div className="rounded-xl border border-border bg-card overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-xs uppercase tracking-wider text-muted-foreground bg-muted/30">
                    <th className="px-4 py-3 font-medium">{t("colSymbol")}</th>
                    <th className="px-4 py-3 font-medium">{t("colCondition")}</th>
                    <th className="px-4 py-3 font-medium">{t("colTrigger")}</th>
                    <th className="px-4 py-3 font-medium">{t("colDuration")}</th>
                    <th className="px-4 py-3 font-medium">{t("colSent")}</th>
                    <th className="px-4 py-3 font-medium">{t("colStatus")}</th>
                    <th className="px-4 py-3 font-medium text-right">{t("colActions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleAlerts.map((a) => {
                    const saturated = !a.is_active && a.sent_count >= a.max_notifications;
                    return (
                      <tr
                        key={a.id}
                        className="border-b border-border/50 last:border-b-0 hover:bg-muted/20 transition-colors"
                      >
                        <td className="px-4 py-3 font-mono font-semibold">{a.symbol}</td>
                        <td className="px-4 py-3">
                          <Badge variant={a.condition === "above" ? "default" : "secondary"}>
                            {a.condition === "above" ? t("conditionAbove") : t("conditionBelow")}
                          </Badge>
                        </td>
                        <td className="px-4 py-3 tabular-nums">{a.trigger_price.toFixed(2)}</td>
                        <td className="px-4 py-3 text-muted-foreground">
                          {a.duration_seconds} {t("seconds")}
                        </td>
                        <td className="px-4 py-3 tabular-nums">
                          {t("sentOf", { sent: a.sent_count, max: a.max_notifications })}
                        </td>
                        <td className="px-4 py-3">
                          {a.is_active ? (
                            <Badge className="bg-emerald-500/10 text-emerald-500 border-emerald-500/20">
                              {t("statusActive")}
                            </Badge>
                          ) : saturated ? (
                            <Badge className="bg-amber-500/10 text-amber-600 border-amber-500/20">
                              {t("statusSaturated")}
                            </Badge>
                          ) : (
                            <Badge variant="secondary">{t("statusInactive")}</Badge>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex items-center justify-end gap-2">
                            <Switch
                              checked={a.is_active}
                              onCheckedChange={() => handleToggle(a)}
                              aria-label={a.is_active ? t("toggleOff") : t("toggleOn")}
                            />
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => handleTest(a)}
                              disabled={testingId === a.id}
                            >
                              {testingId === a.id ? (
                                <Loader2 className="size-3 animate-spin" />
                              ) : (
                                <Send className="size-3" />
                              )}
                            </Button>
                            <Button variant="ghost" size="sm" onClick={() => openEdit(a)}>
                              {t("edit")}
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="text-destructive hover:text-destructive"
                              onClick={() => handleDelete(a)}
                            >
                              {t("delete")}
                            </Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? t("formEditTitle") : t("formTitle")}</DialogTitle>
            <DialogDescription>{t("subtitle")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div>
              <label className="mb-1 block text-sm font-medium">{t("formSymbol")}</label>
              <Select
                value={form.symbol}
                onValueChange={(v) => setForm((f) => ({ ...f, symbol: v ?? "" }))}
              >
                <SelectTrigger className="w-full">
                  <SelectValue placeholder={t("formSymbolPh")} />
                </SelectTrigger>
                <SelectContent>
                  {(availableSymbols.length > 0
                    ? availableSymbols.map((s) => ({ value: s.symbol, label: s.display_name || s.symbol }))
                    : []
                  ).map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div>
              <label className="mb-1 block text-sm font-medium">{t("formCondition")}</label>
              <Select
                value={form.condition}
                onValueChange={(v) =>
                  setForm((f) => ({ ...f, condition: v as "above" | "below" }))
                }
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="above">{t("conditionAbove")}</SelectItem>
                  <SelectItem value="below">{t("conditionBelow")}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div>
              <label className="mb-1 block text-sm font-medium">{t("formTriggerPrice")}</label>
              <Input
                type="number"
                step="any"
                value={form.trigger_price}
                onChange={(e) => setForm((f) => ({ ...f, trigger_price: e.target.value }))}
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-sm font-medium">{t("formDuration")}</label>
                <Input
                  type="number"
                  value={form.duration_seconds}
                  onChange={(e) => setForm((f) => ({ ...f, duration_seconds: e.target.value }))}
                />
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium">{t("formMaxNotifications")}</label>
                <Input
                  type="number"
                  value={form.max_notifications}
                  onChange={(e) => setForm((f) => ({ ...f, max_notifications: e.target.value }))}
                />
              </div>
            </div>

            <div>
              <label className="mb-1 block text-sm font-medium">{t("formNote")}</label>
              <Input
                value={form.note}
                placeholder={t("formNotePh")}
                onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)} disabled={saving}>
              {t("formCancel")}
            </Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving ? <Loader2 className="size-4 animate-spin" /> : t("formSave")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
