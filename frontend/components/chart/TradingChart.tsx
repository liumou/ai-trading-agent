"use client";

// 手动交易页图表：蜡烛 + SMA55/EMA20/EMA50 + RSI + MACD + Ichimoku + 实时 tick。
// 多 pane 用 lightweight-charts v5 原生 addPane/paneIndex/setStretchFactor。
// 指标数据由后端 /ohlcv 的 indicators 数组提供（与 candles 对齐），前端不再自算，
// 保证与后端/ML 链路一致。位移/预热产生的 NaN 槽在前端统一归一化为 WhitespaceData。
//
// 本次扩展（指标开关 + 数值条 + 鼠标滑动联动）：
// - 每个指标一个开关（SMA55/EMA20/EMA50/RSI/MACD/Ichimoku），默认全开，受控于父级。
// - 主图系列（蜡烛/均线/Ichimoku）用 applyOptions({ visible }) 动态显隐（v5 文档：隐藏≠删除，
//   不影响时间线、零开销），不重建 series。
// - 子图（RSI/MACD）是独立 pane：关闭时销毁 series 并 removePane 收起空 pane，开启时重建
//   pane + series（用 pane.addSeries 显式指定 paneIndex，规避默认 pane0 的坑）。
// - 图表上方渲染指标数值条：默认展示最新一根 K 线的指标值；鼠标滑动（crosshair）时按
//   param.time 查该时间点指标行联动展示，鼠标离开图表回落到最新值。

import { useEffect, useMemo, useRef, useState } from "react";
import { useTheme } from "next-themes";
import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  createChart,
} from "lightweight-charts";
import type {
  AreaData,
  CandlestickData,
  HistogramData,
  IChartApi,
  IPaneApi,
  ISeriesApi,
  LineData,
  MouseEventParams,
  Time,
  UTCTimestamp,
  WhitespaceData,
} from "lightweight-charts";
import { getOHLCV } from "@/lib/api";
import type { ChartIndicatorRow } from "@/lib/api";

// 指标开关 key：主图均线 + Ichimoku + 两个子图。与数值条条目一一对应。
export type IndicatorKey = "sma55" | "ema20" | "ema50" | "rsi" | "macd" | "ichimoku";
export const INDICATOR_KEYS: readonly IndicatorKey[] = [
  "sma55",
  "ema20",
  "ema50",
  "rsi",
  "macd",
  "ichimoku",
];

/** 全开默认值：用户要求"默认开启"。 */
export function allIndicatorsOn(): Record<IndicatorKey, boolean> {
  return { sma55: true, ema20: true, ema50: true, rsi: true, macd: true, ichimoku: true };
}

// 显示根数：200；预热（SMA55≈55 / Ichimoku senkou_b≈78）约 80 根。
// 请求 count = 显示数 + 预热，后端全量算指标、前端截取后 DISPLAY_COUNT 根展示，
// 避免图左段大面积 NaN 起线（评审 A3）。
const DISPLAY_COUNT = 200;
const WARMUP_COUNT = 80;
const FETCH_COUNT = DISPLAY_COUNT + WARMUP_COUNT;

// 指标线配色（浅色/深色主题各一套）
const THEME = {
  dark: {
    candleUp: "#4ade80",
    candleDown: "#d03238",
    sma55: "#f472b6", // 粉
    ema20: "#60a5fa", // 蓝
    ema50: "#fbbf24", // 黄
    tenkan: "#22d3ee", // 青
    kijun: "#e879f9", // 紫
    senkouA: "#4ade80", // 云上沿
    senkouB: "#d03238", // 云下沿
    cloud: "rgba(100,200,140,0.10)", // 云背景（极淡）
    macdLine: "#60a5fa",
    macdSignal: "#fbbf24",
    macdPos: "#4ade80",
    macdNeg: "#d03238",
    rsi: "#c084fc",
    text: "#868685",
  },
  light: {
    candleUp: "#054d28",
    candleDown: "#d03238",
    sma55: "#db2777",
    ema20: "#2563eb",
    ema50: "#d97706",
    tenkan: "#0891b2",
    kijun: "#a21caf",
    senkouA: "#15803d",
    senkouB: "#b91c1c",
    cloud: "rgba(20,120,80,0.08)",
    macdLine: "#2563eb",
    macdSignal: "#d97706",
    macdPos: "#15803d",
    macdNeg: "#b91c1c",
    rsi: "#7c3aed",
    text: "#454745",
  },
};

type Props = {
  symbol: string;
  timeframe: string;
  tick?: { bid: number; ask: number; time?: string } | null;
  height?: number;
  /** 各指标显隐开关（受控，父级持有 state）。 */
  indicatorVisibility: Record<IndicatorKey, boolean>;
  /** 开关变化回调（Phase 2 由父级 UI 触发）。 */
  onIndicatorVisibilityChange?: (key: IndicatorKey, on: boolean) => void;
  /** 数值条标签（i18n，父级传入）。 */
  labels?: Record<string, string>;
};

// 归一化：把 {time, value} 或 {time, value:null} 转成 lightweight-charts 可接受的数据。
// v5 的 setData 不接受 value:null/NaN —— 未定义槽必须用 WhitespaceData {time}（仅占位，评审 B2）。
function seriesPoint(
  time: number,
  value: number | null | undefined,
): LineData<UTCTimestamp> | WhitespaceData<UTCTimestamp> {
  if (value == null || Number.isNaN(value)) {
    return { time: time as UTCTimestamp };
  }
  return { time: time as UTCTimestamp, value };
}

/**
 * 数值条：图表上方一行指标数值。默认展示最新 K 的指标值；hover 时展示该时间点数值。
 * 只展示开关开启的指标（SMA55/EMA20/EMA50/RSI/MACD；Ichimoku 线多值密不进条）。
 */
function IndicatorValueBar({
  labels,
  colors,
  values,
}: {
  labels: Record<string, string>;
  colors: Record<string, string>;
  values: { key: string; value: string }[];
}) {
  if (values.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 pb-2 text-xs">
      {values.map((v) => (
        <span key={v.key} className="inline-flex items-center gap-1.5 whitespace-nowrap">
          <span className="text-muted-foreground">{labels[v.key] ?? v.key}</span>
          <span className="font-mono font-semibold" style={{ color: colors[v.key] ?? "inherit" }}>
            {v.value}
          </span>
        </span>
      ))}
    </div>
  );
}

export default function TradingChart({
  symbol,
  timeframe,
  tick,
  height = 460,
  indicatorVisibility,
  onIndicatorVisibilityChange,
  labels: labelsProp,
}: Props) {
  void onIndicatorVisibilityChange; // Phase 2 由父级 UI 触发，此处保留回调契约
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  // 主图 series（pane0）：蜡烛 + 均线 + Ichimoku 线 + 云。用 visible 控制显隐，不重建。
  const lineSeriesRef = useRef<Record<string, ISeriesApi<"Line"> | null>>({});
  const cloudSeriesRef = useRef<ISeriesApi<"Area"> | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  // 子图 series + pane：RSI/MACD 独立 pane，开关变化时销毁/重建。
  const subSeriesRef = useRef<Record<string, ISeriesApi<"Line" | "Histogram"> | null>>({});
  // pane key 收窄为字面量联合，避免任意 string 失去类型约束（评审次要-5）
  const subPanesRef = useRef<Record<"rsi" | "macd", IPaneApi<Time> | null>>({
    rsi: null,
    macd: null,
  });
  // 已加载（截取后）的指标行 + time→行 索引，供数值条与 crosshair 联动。
  const rowsRef = useRef<ChartIndicatorRow[]>([]);
  const timeIndexRef = useRef<Map<number, ChartIndicatorRow>>(new Map());
  const timesRef = useRef<number[]>([]);
  // hover 的指标行（crosshair 所在时间点）；null 表示回落最新。
  const [hoverRow, setHoverRow] = useState<ChartIndicatorRow | null>(null);
  // hover 所在 time（供 60s 轮询后判断是否仍有效，避免鼠标未离开却回落——评审重要-2）
  const hoverTimeRef = useRef<number | null>(null);

  const [loading, setLoading] = useState(true);
  const lastCandleRef = useRef<CandlestickData | null>(null);
  const initialLoadRef = useRef(true);
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";
  const c = isDark ? THEME.dark : THEME.light;

  // 数值条标签：优先父级 i18n，默认英文兜底
  const labels = useMemo(
    () =>
      ({
        sma55: "SMA55",
        ema20: "EMA20",
        ema50: "EMA50",
        rsi: "RSI(14)",
        macd: "MACD",
        macdSignal: "Signal",
        macdHist: "Hist",
        ...labelsProp,
      }) as Record<string, string>,
    [labelsProp],
  );
  const colors = useMemo(
    () => ({
      sma55: c.sma55,
      ema20: c.ema20,
      ema50: c.ema50,
      rsi: c.rsi,
      macd: c.macdLine,
      macdSignal: c.macdSignal,
      macdHist: c.macdPos,
    }),
    [c],
  );

  // 创建图表（随主题重建，重置所有 series 引用）
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: c.text,
        fontSize: 11,
        attributionLogo: false, // v5 原生去水印（评审 H2）
        panes: { separatorColor: isDark ? "rgba(232,235,230,0.15)" : "rgba(14,15,12,0.12)" },
      },
      grid: {
        vertLines: { color: isDark ? "rgba(232,235,230,0.04)" : "rgba(14,15,12,0.06)" },
        horzLines: { color: isDark ? "rgba(232,235,230,0.04)" : "rgba(14,15,12,0.06)" },
      },
      crosshair: {
        vertLine: { labelBackgroundColor: "#9fe870" },
        horzLine: { labelBackgroundColor: "#9fe870" },
      },
      rightPriceScale: { borderColor: isDark ? "rgba(232,235,230,0.08)" : "rgba(14,15,12,0.08)" },
      timeScale: {
        borderColor: isDark ? "rgba(232,235,230,0.08)" : "rgba(14,15,12,0.08)",
        timeVisible: true,
        secondsVisible: false,
      },
    });

    // ─── pane 0：主图（蜡烛 + 均线 + Ichimoku）───
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: c.candleUp,
      downColor: c.candleDown,
      borderDownColor: c.candleDown,
      borderUpColor: c.candleUp,
      wickDownColor: c.candleDown,
      wickUpColor: c.candleUp,
    });
    candleSeriesRef.current = candleSeries;

    const line = (key: string, color: string) => {
      lineSeriesRef.current[key] = chart.addSeries(LineSeries, {
        color,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      });
    };
    // 均线
    line("sma55", c.sma55);
    line("ema20", c.ema20);
    line("ema50", c.ema50);
    // Ichimoku：转换线/基准线 Line + 领先 A/B 双 Line
    line("ichimokuTenkan", c.tenkan);
    line("ichimokuKijun", c.kijun);
    line("ichimokuSenkouA", c.senkouA);
    line("ichimokuSenkouB", c.senkouB);
    // 云背景：极淡单 Area（评审 B1 妥协，异色双色云需 IPanePrimitive，记为后续增强）
    cloudSeriesRef.current = chart.addSeries(AreaSeries, {
      topColor: c.cloud,
      bottomColor: c.cloud,
      lineColor: "transparent",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    chartRef.current = chart;

    // 主题重建后按受控 props 恢复主图 series 显隐（子图由 rebuildSubPanes 处理）
    applyMainSeriesVisibility();

    // crosshair 联动：滑动时按 time 查指标行 → hoverRow；鼠标离开（point undefined）回落最新。
    // 订阅随图表生命周期（theme 重建时重新订阅），不依赖独立 effect。
    const crosshairHandler = (param: MouseEventParams) => {
      if (param.point === undefined) {
        hoverTimeRef.current = null;
        setHoverRow(null);
        return;
      }
      if (typeof param.time !== "number") return; // 后端用 UTCTimestamp（秒级 number）；字符串/日期不做指标映射
      const t = param.time;
      hoverTimeRef.current = t;
      const row = timeIndexRef.current.get(t);
      setHoverRow((prev) => (prev === row ? prev : row ?? null));
    };
    chart.subscribeCrosshairMove(crosshairHandler);

    const handleResize = () => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    };
    const resizeObserver = new ResizeObserver(handleResize);
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.unsubscribeCrosshairMove(crosshairHandler);
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      lineSeriesRef.current = {};
      cloudSeriesRef.current = null;
      subSeriesRef.current = {};
      subPanesRef.current = { rsi: null, macd: null };
      lastCandleRef.current = null;
      // 注意：rowsRef / timeIndexRef / timesRef 是数据缓存，跨主题重建保留，
      // 供 rebuildSubPanes 在主题切换后立即重放子图数据（评审重要-3）。
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDark]);

  // 主图 series 显隐（均线 + Ichimoku 线 + 云）——只 applyOptions visible，不重建。
  // 子图（RSI/MACD）在 rebuildSubPanes 里按当前 visibility 销毁/重建。
  function applyMainSeriesVisibility() {
    const v = indicatorVisibility;
    const setLine = (key: string, on: boolean) =>
      lineSeriesRef.current[key]?.applyOptions({ visible: on });
    setLine("sma55", v.sma55);
    setLine("ema20", v.ema20);
    setLine("ema50", v.ema50);
    // Ichimoku 复合：转换/基准/先行A/B/云 一起开关
    setLine("ichimokuTenkan", v.ichimoku);
    setLine("ichimokuKijun", v.ichimoku);
    setLine("ichimokuSenkouA", v.ichimoku);
    setLine("ichimokuSenkouB", v.ichimoku);
    cloudSeriesRef.current?.applyOptions({ visible: v.ichimoku });
  }

  useEffect(() => {
    if (!chartRef.current) return;
    applyMainSeriesVisibility();
    // 子图按需重建
    rebuildSubPanes();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [indicatorVisibility, isDark]);

  // 子图（RSI/MACD）pane 重建：销毁现有 → 按当前 visibility 重建。
  // 固定顺序：RSI 恒在 MACD 上方。开启时 pane.addSeries 显式指定 paneIndex，规避默认 pane0。
  function rebuildSubPanes() {
    const chart = chartRef.current;
    if (!chart) return;

    // 1) 销毁现有子图 series + pane
    for (const key of ["macd", "macdSignal", "macdHist", "rsi"] as const) {
      const s = subSeriesRef.current[key];
      if (s) {
        chart.removeSeries(s);
        subSeriesRef.current[key] = null;
      }
    }
    // 销毁现有子图 pane（series 已移除，pane 变空；从后往前删避免索引漂移，保留 pane0）
    const panes = chart.panes();
    for (let i = panes.length - 1; i > 0; i -= 1) {
      chart.removePane(i);
    }
    subPanesRef.current = { rsi: null, macd: null };

    // 2) 按 visibility 重建
    const v = indicatorVisibility;
    const addSubLine = (
      key: string,
      pane: IPaneApi<Time>,
      color: string,
      priceScaleId: string,
    ): ISeriesApi<"Line"> => {
      const s = pane.addSeries(LineSeries, {
        color,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        priceScaleId,
      });
      subSeriesRef.current[key] = s;
      return s;
    };

    // RSI pane（恒在 MACD 上方）
    if (v.rsi) {
      const rsiPane = chart.addPane();
      const rsiSeries = addSubLine("rsi", rsiPane, c.rsi, "rsi");
      rsiSeries.createPriceLine({
        price: 70,
        color: isDark ? "rgba(232,235,230,0.3)" : "rgba(14,15,12,0.25)",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: false,
      });
      rsiSeries.createPriceLine({
        price: 30,
        color: isDark ? "rgba(232,235,230,0.3)" : "rgba(14,15,12,0.25)",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: false,
      });
      chart
        .priceScale("rsi", rsiPane.paneIndex())
        .applyOptions({ scaleMargins: { top: 0.15, bottom: 0.15 } });
      rsiPane.setStretchFactor(0.2);
      subPanesRef.current.rsi = rsiPane;
      // 已有数据则重放
      const rsiData = rowsRef.current.map((r, i) =>
        seriesPoint(timesRef.current[i] ?? 0, r.rsi14),
      );
      if (rsiData.length > 0) rsiSeries.setData(rsiData);
    }

    // MACD pane（恒在 RSI 下方）
    if (v.macd) {
      const macdPane = chart.addPane();
      addSubLine("macd", macdPane, c.macdLine, "macd");
      addSubLine("macdSignal", macdPane, c.macdSignal, "macd");
      const hist = macdPane.addSeries(HistogramSeries, {
        priceScaleId: "macd",
        base: 0,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      subSeriesRef.current.macdHist = hist;
      chart
        .priceScale("macd", macdPane.paneIndex())
        .applyOptions({ scaleMargins: { top: 0.15, bottom: 0.15 } });
      macdPane.setStretchFactor(0.2);
      subPanesRef.current.macd = macdPane;
      // 已有数据则重放
      const macdData = rowsRef.current.map((r, i) =>
        seriesPoint(timesRef.current[i] ?? 0, r.macd),
      );
      const signalData = rowsRef.current.map((r, i) =>
        seriesPoint(timesRef.current[i] ?? 0, r.macd_signal),
      );
      const histData: (HistogramData<UTCTimestamp> | WhitespaceData<UTCTimestamp>)[] =
        rowsRef.current.map((r, i) => {
          const val = r.macd_histogram;
          if (val == null || Number.isNaN(val)) return { time: timesRef.current[i] as UTCTimestamp };
          return {
            time: timesRef.current[i] as UTCTimestamp,
            value: val,
            color: val >= 0 ? c.macdPos : c.macdNeg,
          };
        });
      if (macdData.length > 0) {
        subSeriesRef.current.macd?.setData(macdData);
        subSeriesRef.current.macdSignal?.setData(signalData);
        hist.setData(histData);
      }
    }
  }

  // 拉取蜡烛 + 指标（随 symbol/timeframe 变化；60s 轮询兜底）
  useEffect(() => {
    let cancelled = false;

    const fetchData = async () => {
      try {
        const res = await getOHLCV(symbol, timeframe, FETCH_COUNT, true);
        if (cancelled || !candleSeriesRef.current) return;
        const candles = (res.data.candles ?? []) as CandlestickData[];
        const indicators = (res.data.indicators ?? []) as ChartIndicatorRow[];

        if (candles.length === 0) return;

        // 截取后 DISPLAY_COUNT 根（warmup 阶段已保证指标可用）
        const start = Math.max(0, candles.length - DISPLAY_COUNT);
        const viewCandles = candles.slice(start);
        const viewRows = indicators.slice(start);
        const times = viewCandles.map((cd) => cd.time as number);

        candleSeriesRef.current.setData(viewCandles);
        lastCandleRef.current = viewCandles[viewCandles.length - 1] ?? null;

        // 更新 rows 引用供数值条 / crosshair / 子图重放
        rowsRef.current = viewRows as ChartIndicatorRow[];
        timesRef.current = times;
        timeIndexRef.current = new Map();
        rowsRef.current.forEach((r, i) => timeIndexRef.current.set(times[i], r));

        const rows = rowsRef.current;
        // 主图均线
        lineSeriesRef.current.sma55?.setData(rows.map((r, i) => seriesPoint(times[i], r.sma55)));
        lineSeriesRef.current.ema20?.setData(rows.map((r, i) => seriesPoint(times[i], r.ema20)));
        lineSeriesRef.current.ema50?.setData(rows.map((r, i) => seriesPoint(times[i], r.ema50)));

        // Ichimoku 线
        lineSeriesRef.current.ichimokuTenkan?.setData(
          rows.map((r, i) => seriesPoint(times[i], r.ichimoku_tenkan)),
        );
        lineSeriesRef.current.ichimokuKijun?.setData(
          rows.map((r, i) => seriesPoint(times[i], r.ichimoku_kijun)),
        );
        lineSeriesRef.current.ichimokuSenkouA?.setData(
          rows.map((r, i) => seriesPoint(times[i], r.ichimoku_senkou_a)),
        );
        lineSeriesRef.current.ichimokuSenkouB?.setData(
          rows.map((r, i) => seriesPoint(times[i], r.ichimoku_senkou_b)),
        );

        // 云 Area：取 senkou_a/b 均值，空值用最近有效值保持连续（避免 Area 断线）
        if (cloudSeriesRef.current) {
          const cloudData: (AreaData<UTCTimestamp> | WhitespaceData<UTCTimestamp>)[] = [];
          let lastValid: number | null = null;
          rows.forEach((r, i) => {
            const a = r.ichimoku_senkou_a;
            const b = r.ichimoku_senkou_b;
            const mid = a != null && b != null ? (a + b) / 2 : null;
            if (mid != null) lastValid = mid;
            cloudData.push(
              mid != null || lastValid != null
                ? { time: times[i] as UTCTimestamp, value: mid ?? lastValid! }
                : { time: times[i] as UTCTimestamp },
            );
          });
          cloudSeriesRef.current.setData(cloudData);
        }

        // 子图（若已创建）重放数据
        subSeriesRef.current.rsi?.setData(rows.map((r, i) => seriesPoint(times[i], r.rsi14)));
        subSeriesRef.current.macd?.setData(rows.map((r, i) => seriesPoint(times[i], r.macd)));
        subSeriesRef.current.macdSignal?.setData(
          rows.map((r, i) => seriesPoint(times[i], r.macd_signal)),
        );
        if (subSeriesRef.current.macdHist) {
          const histData: (HistogramData<UTCTimestamp> | WhitespaceData<UTCTimestamp>)[] =
            rows.map((r, i) => {
              const val = r.macd_histogram;
              if (val == null || Number.isNaN(val)) return { time: times[i] as UTCTimestamp };
              return {
                time: times[i] as UTCTimestamp,
                value: val,
                color: val >= 0 ? c.macdPos : c.macdNeg,
              };
            });
          subSeriesRef.current.macdHist.setData(histData);
        }

        // 数据刷新后：仅当 hover 的时间点已不在新数据中才回落最新
        // （鼠标未离开时不应跳回最新值——评审重要-2；timeIndexRef 已按新数据重建）
        const ht = hoverTimeRef.current;
        if (ht != null && !timeIndexRef.current.has(ht)) {
          hoverTimeRef.current = null;
          setHoverRow(null);
        }

        if (initialLoadRef.current) {
          chartRef.current?.timeScale().fitContent();
          initialLoadRef.current = false;
        }
      } catch (e) {
        console.error("Failed to fetch chart data:", e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    fetchData();
    const interval = setInterval(fetchData, 60000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, timeframe]);

  // tick 只更新主图最后 K 线（子图指标不动，60s 轮询兜底；tick 不含指标值，
  // 前端不重算指标——评审 H3）
  useEffect(() => {
    if (!tick || !candleSeriesRef.current || !lastCandleRef.current) return;
    const price = tick.bid;
    const candle = lastCandleRef.current;
    const updated = {
      ...candle,
      high: Math.max(candle.high, price),
      low: Math.min(candle.low, price),
      close: price,
    };
    lastCandleRef.current = updated;
    try {
      candleSeriesRef.current.update(updated);
    } catch {
      // 忽略旧 tick 时间戳越界等边缘错误
    }
  }, [tick]);

  // 数值条数据：hover 行优先，否则最新一行。只显示开关开启的指标。
  const valueBarRows = useMemo(() => {
    const displayRow = hoverRow ?? rowsRef.current[rowsRef.current.length - 1] ?? null;
    if (!displayRow) return [];
    const v = indicatorVisibility;
    const fmt = (n: number | null | undefined, digits = 2) =>
      n == null || Number.isNaN(n) ? "—" : n.toFixed(digits);
    const items: { key: string; value: string }[] = [];
    if (v.sma55) items.push({ key: "sma55", value: fmt(displayRow.sma55) });
    if (v.ema20) items.push({ key: "ema20", value: fmt(displayRow.ema20) });
    if (v.ema50) items.push({ key: "ema50", value: fmt(displayRow.ema50) });
    if (v.rsi) items.push({ key: "rsi", value: fmt(displayRow.rsi14, 2) });
    if (v.macd) {
      items.push({ key: "macd", value: fmt(displayRow.macd) });
      items.push({ key: "macdSignal", value: fmt(displayRow.macd_signal) });
      items.push({ key: "macdHist", value: fmt(displayRow.macd_histogram) });
    }
    return items;
  }, [hoverRow, indicatorVisibility]);

  return (
    // flex 列布局：数值条自然高度，图表容器 flex-1 填满剩余空间
    // （数值条 wrap 换行时图表自动压缩，不再用固定 30px 减法——评审重要-1）
    <div className="flex flex-col w-full" style={{ height }}>
      <IndicatorValueBar labels={labels} colors={colors} values={valueBarRows} />
      <div className="relative w-full flex-1 min-h-0">
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center z-10">
            <span className="text-muted-foreground text-sm font-medium">Loading {symbol} chart...</span>
          </div>
        )}
        <div ref={containerRef} className="w-full h-full animate-fade-in" />
      </div>
    </div>
  );
}
