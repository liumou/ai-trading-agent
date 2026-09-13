"use client";

import { useMemo, useRef, useState } from "react";
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
import {
  ASSET_CLASSES,
  type AssetClass,
  type BrokerCatalogItem,
  type SymbolConfig,
  type SymbolConfigInput,
} from "@/lib/api";

const TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"] as const;

interface SymbolFormProps {
  initial?: SymbolConfig;
  onSubmit: (input: SymbolConfigInput) => Promise<void>;
  onCancel: () => void;
  onValidateAlias?: (alias: string) => Promise<void>;
  submitting?: boolean;
  brokerCatalog?: BrokerCatalogItem[];
  catalogError?: string | null;
  catalogLoading?: boolean;
}

type FormState = {
  symbol: string;
  display_name: string;
  broker_alias: string;
  asset_class: AssetClass;
  default_timeframe: string;
  pip_value: string;
  default_lot: string;
  max_lot: string;
  price_decimals: string;
  sl_atr_mult: string;
  tp_atr_mult: string;
  contract_size: string;
  ml_tp_pips: string;
  ml_sl_pips: string;
  ml_forward_bars: string;
  ml_timeframe: string;
};

function toFormState(cfg?: SymbolConfig): FormState {
  return {
    symbol: cfg?.symbol ?? "",
    display_name: cfg?.display_name ?? "",
    broker_alias: cfg?.broker_alias ?? "",
    asset_class: cfg?.asset_class ?? "forex",
    default_timeframe: cfg?.default_timeframe ?? "M15",
    pip_value: String(cfg?.pip_value ?? ""),
    default_lot: String(cfg?.default_lot ?? ""),
    max_lot: String(cfg?.max_lot ?? ""),
    price_decimals: String(cfg?.price_decimals ?? 2),
    sl_atr_mult: String(cfg?.sl_atr_mult ?? 1.5),
    tp_atr_mult: String(cfg?.tp_atr_mult ?? 2.0),
    contract_size: String(cfg?.contract_size ?? 1),
    ml_tp_pips: String(cfg?.ml_tp_pips ?? ""),
    ml_sl_pips: String(cfg?.ml_sl_pips ?? ""),
    ml_forward_bars: String(cfg?.ml_forward_bars ?? 10),
    ml_timeframe: cfg?.ml_timeframe ?? "M15",
  };
}

function toPayload(state: FormState, picked: BrokerCatalogItem | null): SymbolConfigInput {
  return {
    // 目录驱动的创建流程：省略规范名 —— 由服务端从券商品种名派生
    // （仅保留字母数字）并校验唯一性。
    symbol: picked ? null : state.symbol.trim(),
    display_name: state.display_name.trim(),
    broker_alias: state.broker_alias.trim() || null,
    asset_class: state.asset_class,
    default_timeframe: state.default_timeframe,
    pip_value: Number(state.pip_value),
    default_lot: Number(state.default_lot),
    max_lot: Number(state.max_lot),
    price_decimals: Number(state.price_decimals),
    sl_atr_mult: Number(state.sl_atr_mult),
    tp_atr_mult: Number(state.tp_atr_mult),
    contract_size: Number(state.contract_size),
    ml_tp_pips: Number(state.ml_tp_pips),
    ml_sl_pips: Number(state.ml_sl_pips),
    ml_forward_bars: Number(state.ml_forward_bars),
    ml_timeframe: state.ml_timeframe,
  };
}

type ValidateT = (key: "errSymbol" | "errDisplayName" | "errFieldGt0" | "errLotOrder", values?: Record<string, string>) => string;

function validate(state: FormState, t: ValidateT, picked: BrokerCatalogItem | null): string | null {
  // 选中目录项后规范名由服务端派生，本地正则校验不再适用。
  if (!picked && !state.symbol.match(/^[A-Za-z0-9._-]{2,32}$/)) return t("errSymbol");
  if (!state.display_name) return t("errDisplayName");
  const nums = [
    ["pip_value", state.pip_value],
    ["default_lot", state.default_lot],
    ["max_lot", state.max_lot],
    ["contract_size", state.contract_size],
    ["ml_tp_pips", state.ml_tp_pips],
    ["ml_sl_pips", state.ml_sl_pips],
  ] as const;
  for (const [field, raw] of nums) {
    const n = Number(raw);
    if (!Number.isFinite(n) || n <= 0) return t("errFieldGt0", { field });
  }
  if (Number(state.default_lot) > Number(state.max_lot)) {
    return t("errLotOrder");
  }
  return null;
}

export function SymbolForm({
  initial,
  onSubmit,
  onCancel,
  onValidateAlias,
  submitting,
  brokerCatalog,
  catalogError,
  catalogLoading,
}: SymbolFormProps) {
  const t = useTranslations("symbols.form");
  const tAssetClass = useTranslations("symbols.assetClass");
  const [state, setState] = useState<FormState>(toFormState(initial));
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<BrokerCatalogItem | null>(null);

  const isEdit = Boolean(initial);

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setState((prev) => ({ ...prev, [key]: value }));

  const applyCatalogItem = (item: BrokerCatalogItem) => {
    setPicked(item);
    setState((prev) => ({
      ...prev,
      symbol: prev.symbol || item.symbol.replace(/[^A-Za-z0-9]/g, ""),
      display_name: prev.display_name || item.description || item.symbol,
      broker_alias: item.symbol,
      asset_class: item.asset_class,
      price_decimals: String(item.price_decimals),
      pip_value: String(item.pip_value),
      contract_size: String(item.contract_size),
      default_lot: String(item.volume_min),
      max_lot: String(Math.min(item.volume_max, 100)),
    }));
    setError(null);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const err = validate(state, t, picked);
    if (err) {
      setError(err);
      return;
    }
    setError(null);
    await onSubmit(toPayload(state, picked));
  };

  const handleValidate = async () => {
    if (!onValidateAlias) return;
    const alias = state.broker_alias.trim() || state.symbol.trim();
    if (!alias) {
      setError(t("errAliasFirst"));
      return;
    }
    await onValidateAlias(alias);
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {!isEdit && (
        <BrokerCatalogPicker
          items={brokerCatalog}
          loading={catalogLoading}
          error={catalogError}
          onSelect={applyCatalogItem}
        />
      )}
      {!isEdit && picked && (
        <p className="text-xs text-emerald-600 dark:text-emerald-400">{t("brokerValidated")}</p>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <Field label={t("labelSymbol")} required>
          <Input
            value={state.symbol}
            onChange={(e) => update("symbol", e.target.value)}
            disabled={isEdit || picked !== null}
            placeholder={t("phSymbol")}
          />
          {picked && (
            <span className="text-xs text-muted-foreground">
              {t("symbolAutoDerived", { broker: picked.symbol })}
            </span>
          )}
        </Field>
        <Field label={t("labelDisplayName")} required>
          <Input
            value={state.display_name}
            onChange={(e) => update("display_name", e.target.value)}
            placeholder={t("phDisplay")}
          />
        </Field>
        <Field label={t("labelBrokerAlias")}>
          <div className="flex gap-2">
            <Input
              value={state.broker_alias}
              onChange={(e) => update("broker_alias", e.target.value)}
              placeholder={t("phOptional")}
            />
            {onValidateAlias && (
              <Button type="button" variant="outline" size="sm" onClick={handleValidate}>
                {t("validate")}
              </Button>
            )}
          </div>
        </Field>
        <Field label={t("labelDefaultTf")}>
          <Select
            value={state.default_timeframe}
            onValueChange={(v) => update("default_timeframe", v as string)}
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TIMEFRAMES.map((tf) => (
                <SelectItem key={tf} value={tf}>
                  {tf}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field label={t("labelAssetClass")} required>
          <Select
            value={state.asset_class}
            onValueChange={(v) => update("asset_class", v as AssetClass)}
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ASSET_CLASSES.map((cls) => (
                <SelectItem key={cls} value={cls}>
                  {tAssetClass(cls)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field label={t("labelPipValue")} required>
          <Input
            type="number"
            step="any"
            value={state.pip_value}
            onChange={(e) => update("pip_value", e.target.value)}
          />
        </Field>
        <Field label={t("labelPriceDecimals")}>
          <Input
            type="number"
            value={state.price_decimals}
            onChange={(e) => update("price_decimals", e.target.value)}
          />
        </Field>
        <Field label={t("labelDefaultLot")} required>
          <Input
            type="number"
            step="any"
            value={state.default_lot}
            onChange={(e) => update("default_lot", e.target.value)}
          />
        </Field>
        <Field label={t("labelMaxLot")} required>
          <Input
            type="number"
            step="any"
            value={state.max_lot}
            onChange={(e) => update("max_lot", e.target.value)}
          />
        </Field>
        <Field label={t("labelSlAtr")}>
          <Input
            type="number"
            step="any"
            value={state.sl_atr_mult}
            onChange={(e) => update("sl_atr_mult", e.target.value)}
          />
        </Field>
        <Field label={t("labelTpAtr")}>
          <Input
            type="number"
            step="any"
            value={state.tp_atr_mult}
            onChange={(e) => update("tp_atr_mult", e.target.value)}
          />
        </Field>
        <Field label={t("labelContractSize")} required>
          <Input
            type="number"
            step="any"
            value={state.contract_size}
            onChange={(e) => update("contract_size", e.target.value)}
          />
        </Field>
        <Field label={t("labelMlTf")}>
          <Select
            value={state.ml_timeframe}
            onValueChange={(v) => update("ml_timeframe", v as string)}
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TIMEFRAMES.map((tf) => (
                <SelectItem key={tf} value={tf}>
                  {tf}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field label={t("labelMlTp")} required>
          <Input
            type="number"
            step="any"
            value={state.ml_tp_pips}
            onChange={(e) => update("ml_tp_pips", e.target.value)}
          />
        </Field>
        <Field label={t("labelMlSl")} required>
          <Input
            type="number"
            step="any"
            value={state.ml_sl_pips}
            onChange={(e) => update("ml_sl_pips", e.target.value)}
          />
        </Field>
        <Field label={t("labelMlForward")}>
          <Input
            type="number"
            value={state.ml_forward_bars}
            onChange={(e) => update("ml_forward_bars", e.target.value)}
          />
        </Field>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={onCancel} disabled={submitting}>
          {t("cancel")}
        </Button>
        <Button type="submit" disabled={submitting}>
          {submitting ? t("saving") : isEdit ? t("saveChanges") : t("createSymbol")}
        </Button>
      </div>
    </form>
  );
}

function Field({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-xs font-medium text-muted-foreground">
        {label}
        {required && <span className="text-destructive"> *</span>}
      </span>
      {children}
    </label>
  );
}

function BrokerCatalogPicker({
  items,
  loading,
  error,
  onSelect,
}: {
  items?: BrokerCatalogItem[];
  loading?: boolean;
  error?: string | null;
  onSelect: (item: BrokerCatalogItem) => void;
}) {
  const t = useTranslations("symbols.form");
  const tAssetClass = useTranslations("symbols.assetClass");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const filtered = useMemo(() => {
    if (!items || items.length === 0) return [];
    const q = query.trim().toLowerCase();
    if (!q) return items.slice(0, 50);
    return items
      .filter(
        (it) =>
          it.symbol.toLowerCase().includes(q) ||
          it.description.toLowerCase().includes(q),
      )
      .slice(0, 50);
  }, [items, query]);

  if (error || (!loading && (!items || items.length === 0))) {
    return (
      <div className="rounded border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-600 dark:text-amber-400">
        {t("catalogUnavailable", { error: error ? `: ${error}` : "" })}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1 text-sm" ref={containerRef}>
      <span className="text-xs font-medium text-muted-foreground">
        {t("pickFromBroker")}
      </span>
      <div className="relative">
        <Input
          placeholder={loading ? t("loadingCatalog") : t("searchPh")}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          disabled={loading}
        />
        {open && filtered.length > 0 && (
          <ul className="absolute z-20 mt-1 max-h-64 w-full overflow-auto rounded-md border bg-popover shadow-md">
            {filtered.map((it) => (
              <li key={it.symbol}>
                <button
                  type="button"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => {
                    onSelect(it);
                    setQuery(it.symbol);
                    setOpen(false);
                  }}
                  className="flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left text-xs hover:bg-accent"
                >
                  <span className="font-mono font-semibold">{it.symbol}</span>
                  <span className="text-muted-foreground">
                    {it.description || it.path} ·{" "}
                    {t("itemMeta", {
                      assetClass: tAssetClass(it.asset_class),
                      min: String(it.volume_min),
                      max: String(it.volume_max),
                    })}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
