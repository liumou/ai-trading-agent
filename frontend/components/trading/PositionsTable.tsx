"use client";

import { useTranslations } from "next-intl";

export interface TradingPosition {
  ticket: number;
  symbol: string;
  type: string;
  lot: number;
  open_price: number;
  current_price: number;
  sl: number;
  tp: number;
  profit: number;
  open_time: string;
}

export function PositionsTable({
  positions,
  onClose,
  onEditSlTp,
  busyTicket,
  showActions = true,
}: {
  positions: TradingPosition[];
  onClose?: (ticket: number) => void;
  onEditSlTp?: (ticket: number, sl: number, tp: number) => void;
  busyTicket?: number | null;
  showActions?: boolean;
}) {
  const t = useTranslations("trading");
  if (positions.length === 0) {
    return (
      <div className="text-center text-muted-foreground py-8 border border-dashed border-border rounded-xl text-sm">
        {t("noPositions")}
      </div>
    );
  }
  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border/50 text-left text-xs text-muted-foreground">
            <th className="px-4 py-3 font-medium">{t("colTicket")}</th>
            <th className="px-4 py-3 font-medium">{t("colSymbol")}</th>
            <th className="px-4 py-3 font-medium">{t("colType")}</th>
            <th className="px-4 py-3 text-right font-medium">{t("colLots")}</th>
            <th className="px-4 py-3 text-right font-medium">{t("colPrice")}</th>
            <th className="px-4 py-3 text-right font-medium">{t("colSl")}</th>
            <th className="px-4 py-3 text-right font-medium">{t("colTp")}</th>
            <th className="px-4 py-3 text-right font-medium">{t("colPnl")}</th>
            {showActions && <th className="px-4 py-3 text-right font-medium">{t("colActions")}</th>}
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr key={p.ticket} className="border-b border-border/40 last:border-b-0">
              <td className="px-4 py-3 font-mono text-xs">{p.ticket}</td>
              <td className="px-4 py-3 font-medium text-xs">{p.symbol}</td>
              <td className={`px-4 py-3 font-semibold ${p.type === "BUY" ? "text-success dark:text-green-400" : "text-destructive"}`}>
                {p.type}
              </td>
              <td className="px-4 py-3 text-right font-mono">{p.lot}</td>
              <td className="px-4 py-3 text-right font-mono">{p.open_price?.toFixed(2)}</td>
              <td className="px-4 py-3 text-right font-mono text-muted-foreground">{p.sl?.toFixed(2)}</td>
              <td className="px-4 py-3 text-right font-mono text-muted-foreground">{p.tp?.toFixed(2)}</td>
              <td className={`px-4 py-3 text-right font-mono font-semibold ${p.profit >= 0 ? "text-success dark:text-green-400" : "text-destructive"}`}>
                {p.profit >= 0 ? "+" : ""}{p.profit?.toFixed(2)}
              </td>
              {showActions && (
                <td className="px-4 py-3">
                  <div className="flex items-center justify-end gap-2">
                    {onEditSlTp && (
                      <button
                        type="button"
                        onClick={() => {
                          const input = window.prompt(t("editSlTp"), `${p.sl},${p.tp}`);
                          if (!input) return;
                          const [sl, tp] = input.split(",").map((v) => Number(v.trim()));
                          onEditSlTp(p.ticket, sl, tp);
                        }}
                        disabled={busyTicket === p.ticket}
                        className="rounded-md px-3 py-1.5 text-xs border border-border hover:bg-muted disabled:opacity-50"
                      >
                        {t("editSlTp")}
                      </button>
                    )}
                    {onClose && (
                      <button
                        type="button"
                        onClick={() => onClose(p.ticket)}
                        disabled={busyTicket === p.ticket}
                        className="rounded-md px-3 py-1.5 text-xs text-red-500 hover:bg-red-500/10 disabled:opacity-50"
                      >
                        {t("close")}
                      </button>
                    )}
                  </div>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
