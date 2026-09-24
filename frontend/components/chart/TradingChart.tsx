"use client";

// 手动交易页图表：蜡烛 + SMA55/EMA20/EMA50 + RSI + MACD + Ichimoku + 实时 tick。
// 多 pane 用 lightweight-charts v5 原生 addPane/paneIndex/setStretchFactor。
// 指标数据由后端 /ohlcv 的 indicators 数组提供（与 candles 对齐），前端不再自算，
// 保证与后端/ML 链路一致。位移/预热产生的 NaN 槽在前端统一归一化为 WhitespaceData。

import { useEffect, useRef, useState } from "react";
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
  ISeriesApi,
  LineData,
  UTCTimestamp,
  WhitespaceData,
} from "lightweight-charts";
import { getOHLCV } from "@/lib/api";
import type { ChartIndicatorRow } from "@/lib/api";

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

export default function TradingChart({ symbol, timeframe, tick, height = 460 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const lineSeriesRef = useRef<Record<string, ISeriesApi<"Line"> | null>>({});
  const cloudSeriesRef = useRef<ISeriesApi<"Area"> | null>(null);
  const macdHistRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const [loading, setLoading] = useState(true);
  const lastCandleRef = useRef<CandlestickData | null>(null);
  const initialLoadRef = useRef(true);
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";
  const c = isDark ? THEME.dark : THEME.light;

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

    // ─── pane 1：RSI（独立 priceScaleId + 30/70 参考线，评审 M4：0/100 贴边不画）───
    const rsiPane = chart.addPane();
    line("rsi", c.rsi);
    const rsiSeries = lineSeriesRef.current.rsi!;
    rsiSeries.applyOptions({ priceScaleId: "rsi" });
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
    chart.priceScale("rsi", 1).applyOptions({ scaleMargins: { top: 0.15, bottom: 0.15 } });

    // ─── pane 2：MACD（快线/慢线 + 柱状，base:0 正绿负红）───
    const macdPane = chart.addPane();
    line("macd", c.macdLine);
    line("macdSignal", c.macdSignal);
    lineSeriesRef.current.macd?.applyOptions({ priceScaleId: "macd" });
    lineSeriesRef.current.macdSignal?.applyOptions({ priceScaleId: "macd" });
    macdHistRef.current = chart.addSeries(
      HistogramSeries,
      { priceScaleId: "macd", base: 0, priceLineVisible: false, lastValueVisible: false },
      2,
    );
    chart.priceScale("macd", 2).applyOptions({ scaleMargins: { top: 0.15, bottom: 0.15 } });

    // pane 高度权重：主图 0.6 / RSI 0.2 / MACD 0.2（评审 H1）
    rsiPane.setStretchFactor(0.2);
    macdPane.setStretchFactor(0.2);

    chartRef.current = chart;

    const handleResize = () => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    };
    const resizeObserver = new ResizeObserver(handleResize);
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      lineSeriesRef.current = {};
      cloudSeriesRef.current = null;
      macdHistRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDark]);

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

        candleSeriesRef.current.setData(viewCandles);
        lastCandleRef.current = viewCandles[viewCandles.length - 1] ?? null;

        const times = viewCandles.map((cd) => cd.time as number);
        const rows = viewRows as { time: number } & ChartIndicatorRow[];

        // 均线
        lineSeriesRef.current.sma55?.setData(rows.map((r, i) => seriesPoint(times[i], r.sma55)));
        lineSeriesRef.current.ema20?.setData(rows.map((r, i) => seriesPoint(times[i], r.ema20)));
        lineSeriesRef.current.ema50?.setData(rows.map((r, i) => seriesPoint(times[i], r.ema50)));

        // Ichimoku 线
        lineSeriesRef.current.ichimokuTenkan?.setData(rows.map((r, i) => seriesPoint(times[i], r.ichimoku_tenkan)));
        lineSeriesRef.current.ichimokuKijun?.setData(rows.map((r, i) => seriesPoint(times[i], r.ichimoku_kijun)));
        lineSeriesRef.current.ichimokuSenkouA?.setData(rows.map((r, i) => seriesPoint(times[i], r.ichimoku_senkou_a)));
        lineSeriesRef.current.ichimokuSenkouB?.setData(rows.map((r, i) => seriesPoint(times[i], r.ichimoku_senkou_b)));

        // 云 Area：取 senkou_a/b 均值，空值用最近有效值保持连续（避免 Area 断线）
        if (cloudSeriesRef.current) {
          const cloudData: (AreaData<UTCTimestamp> | WhitespaceData<UTCTimestamp>)[] = [];
          let lastValid: number | null = null;
          rows.forEach((r, i) => {
            const a = r.ichimoku_senkou_a;
            const b = r.ichimoku_senkou_b;
            const mid = a != null && b != null ? (a + b) / 2 : null;
            if (mid != null) lastValid = mid;
            cloudData.push(mid != null || lastValid != null ? { time: times[i] as UTCTimestamp, value: mid ?? lastValid! } : { time: times[i] as UTCTimestamp });
          });
          cloudSeriesRef.current.setData(cloudData);
        }

        // RSI
        lineSeriesRef.current.rsi?.setData(rows.map((r, i) => seriesPoint(times[i], r.rsi14)));

        // MACD
        lineSeriesRef.current.macd?.setData(rows.map((r, i) => seriesPoint(times[i], r.macd)));
        lineSeriesRef.current.macdSignal?.setData(rows.map((r, i) => seriesPoint(times[i], r.macd_signal)));
        if (macdHistRef.current) {
          const histData: (HistogramData<UTCTimestamp> | WhitespaceData<UTCTimestamp>)[] = rows.map((r, i) => {
            const v = r.macd_histogram;
            if (v == null || Number.isNaN(v)) return { time: times[i] as UTCTimestamp };
            return { time: times[i] as UTCTimestamp, value: v, color: v >= 0 ? c.macdPos : c.macdNeg };
          });
          macdHistRef.current.setData(histData);
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

  return (
    <div className="relative w-full" style={{ height }}>
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center z-10">
          <span className="text-muted-foreground text-sm font-medium">Loading {symbol} chart...</span>
        </div>
      )}
      <div ref={containerRef} className="w-full h-full animate-fade-in" />
    </div>
  );
}