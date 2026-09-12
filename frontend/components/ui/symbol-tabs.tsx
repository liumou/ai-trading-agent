"use client";

import { type ReactNode } from "react";
import { useTranslations } from "next-intl";

type SymbolInfo = {
  symbol: string;
  display_name: string;
  state?: string;
};

type SymbolTabsProps = {
  symbols: SymbolInfo[];
  active: string;
  onSelect: (symbol: string) => void;
  showAll?: boolean;
  allLabel?: string;
  children?: ReactNode;
};

export function SymbolTabs({ symbols, active, onSelect, showAll, allLabel, children }: SymbolTabsProps) {
  const t = useTranslations("ui");
  if (symbols.length <= 1 && !showAll) return null;

  return (
    <div className="flex gap-2 overflow-x-auto pb-1">
      {showAll && (
        <button
          type="button"
          onClick={() => onSelect("all")}
          className={`flex items-center gap-2 min-h-[44px] px-4 py-2.5 rounded-xl border-2 text-xs font-semibold transition-all whitespace-nowrap ${
            active === "all"
              ? "bg-card text-primary border-primary"
              : "bg-card text-foreground border-border hover:border-primary/50"
          }`}
        >
          {allLabel ?? t("all")}
        </button>
      )}
      {symbols.map((s) => {
        const isActive = s.symbol === active;
        return (
          <button
            key={s.symbol}
            type="button"
            onClick={() => onSelect(s.symbol)}
            className={`flex items-center gap-2.5 min-h-[44px] px-4 py-2.5 rounded-xl border-2 text-xs font-semibold transition-all whitespace-nowrap ${
              isActive
                ? "bg-card text-primary border-primary"
                : "bg-card text-foreground border-border hover:border-primary/50"
            }`}
          >
            {s.state !== undefined && (
              <span
                className={`size-2 rounded-full shrink-0 ${
                  s.state === "RUNNING"
                    ? "bg-green-400"
                    : "bg-muted-foreground/30"
                }`}
              />
            )}
            <span>{s.display_name}</span>
          </button>
        );
      })}
      {children}
    </div>
  );
}
