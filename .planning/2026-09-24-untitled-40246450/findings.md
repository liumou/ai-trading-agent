# Findings — 交易图表指标开关 + 指标数值条 + 鼠标滑动联动

> 调研时间：2026-09-24。所有结论均已通过实际读取代码/类型定义核实。
> 承接上一任务（commit 777226c「手动交易页面品种图表功能」已上线）。

## 需求

在手动交易页现有行情图表基础上：
1. 为各个指标增加**显示开关**，默认开启；开启→展示在图表上，关闭→不展示。
2. 图表**上方展示各指标的最新数据**（数值条，默认展示最新一根 K 线的指标值）。
3. **鼠标滑动**时，数值条展示该时间点的指标数据。

## 现状（已核实）

### 前端 — TradingChart.tsx（350 行，上一任务产物）
- 已绘制：主图 pane0（蜡烛 + SMA55/EMA20/EMA50 + Ichimoku tenkan/kijun/senkouA/B 线 + 极淡 Area 云）+ RSI pane1（+30/70 参考线）+ MACD pane2（快慢线 + Histogram 柱）。
- series 引用存在 `lineSeriesRef` 字典（sma55/ema20/ema50/ichimokuTenkan/ichimokuKijun/ichimokuSenkouA/ichimokuSenkouB/rsi/macd/macdSignal）；另有 `cloudSeriesRef`（Area 云）、`macdHistRef`（Histogram）、`candleSeriesRef`。
- **pane 引用（rsiPane/macdPane）是局部变量，未保存到 ref** → 动态收起子图时无法 removePane。
- 数据拉取：`getOHLCV(symbol, timeframe, FETCH_COUNT=280, true)` → `res.data.candles` + `res.data.indicators`，截取后 200 根展示，60s 轮询；tick 只更新最后 K 线（子图不动）。
- 数据归一化已存在：`seriesPoint()` 把 value==null/NaN → WhitespaceData `{time}`。

### 前端 — api.ts（已核实 L310-353）
- `ChartIndicatorRow` 类型已齐备：sma55/ema20/ema50/rsi14/macd/macd_signal/macd_histogram/ichimoku_tenkan/kijun/senkou_a/senkou_b/chikou。
- `getOHLCV` 返回 `{candles, indicators?}`；`getDayRange` 已存在。

### 前端 — trading/page.tsx（751 行）
- 图表卡在 PageHeader 与下单表单之间（L466-496）：标题栏含图表标题 + 当日高低徽标 + 周期切换 TimeframeSelector；下方 `<TradingChart symbol={symbol} timeframe={timeframe} tick={liveTick} height={460} />`。
- `useTranslations("trading")`；图表通用文案在 `charts.json`（zh/en），交易页专属在 `trading.json`（zh/en）。**项目无 th locale**。

### 图表库 — lightweight-charts v5.1.0（已查证 typings.d.ts）
- `SeriesOptionsCommon.visible: boolean`（L3920）：隐藏 series 连带隐藏 price lines/markers；**文档明确 hiding ≠ deleting，不影响时间线** → 最适合做开关（无需重建 series）。
- `chart.addPane()/removePane(index)`（L1689/1701）：可动态增删子图 pane。
- `chart.subscribeCrosshairMove(handler)` / `unsubscribeCrosshairMove`（L1639/1649）：handler 收到 `MouseEventParams`。
- `MouseEventParams`（L3302）：`time?`（数据时间，图表数据范围外为 undefined）、`point?`（鼠标位置，mouse leave 为 undefined）、`paneIndex?`、`seriesData`（Map<ISeriesApi, BarData|LineData|HistogramData>，**键是 series 实例**）。
- `IPaneApi.setStretchFactor(n)`（L2014）：动态调整 pane 高度权重。
- 多 series 共享 timeScale，time 集合一致或子集。

## 关键设计决策（推荐）

1. **开关 = series 级 `applyOptions({ visible })`**，不删除/重建 series：
   - 关闭某指标 → 对应 series `visible:false`；开启 → `visible:true`。
   - 优点：数据已 setData，切换零开销、不触发重新拉取、时间线不受影响（文档明示）。
2. **子图 pane 动态收起**：RSI/MACD 是独立 pane，若 RSI 与 MACD 同时关闭（或各自单独逻辑），空 pane 会留下空白条。
   - 方案：pane 引用保存到 ref；某 pane 的系列全部隐藏 → `chart.removePane(paneIndex)`；重新开启 → 重新 `addPane()` 并把对应 series `moveToPane()`（或重建）。
   - 简化：因为「MACD 关闭」只关 macd 快慢线+柱，「RSI 关闭」只关 rsi 线。pane 的增删仅在对应指标开关切换时发生一次。
   - **注意**：removePane 会改变后续 paneIndex。RSI pane1 / MACD pane2，若先删 pane1 则 MACD 变 pane1。需用 `paneIndex()` 动态获取或按固定顺序管理（RSI 永远在 MACD 上方，先删 RSI 时 MACD 上移）。
3. **数值条（图表上方）**：
   - 新增一行（或折叠展开的区块）展示各指标最新值：SMA55/EMA20/EMA50（主图，随价格）、RSI14、MACD/DIF/DEA/柱、Ichimoku 可选。
   - **默认展示最新一根 K 线的指标值**（`indicators` 数组最后一行的值）。
   - 数值条**随开关联动**：关掉的指标不显示其数值（避免"关了图上却还显示数字"的割裂）。
4. **鼠标滑动联动**：
   - `subscribeCrosshairMove(handler)`，handler 内取 `param.time`，在已加载的 `indicators` 行里查该时间戳对应的行，更新数值条为该时间点数据。
   - 鼠标离开图表（`param.point === undefined`）→ 数值条回到最新值。
   - 性能：handler 高频触发，但只是查 Map + setState；用 `useCallback`/ref 避免多余渲染。数据行存 ref（`rowsRef`），handler 读 ref 不依赖 state。
5. **开关放哪**：图表卡标题栏（与周期切换同一行，右侧），用小号 ToggleGroup/开关组。关闭主图均线系列时不收 pane0（pane0 还有蜡烛），只隐藏线。
6. **Ichimoku 是复合**：开关「Ichimoku」同时控制 tenkan/kijun/senkouA/senkouB/云 Area 全部。chikou 线现状代码未绘制，本次不新增。
7. **新增一个「全部」总开关**（可选）：一键全开/全关，便利性。

## 待定设计决策（需用户在计划确认时拍板）

- **开关粒度**：按「指标」分 6 个开关（SMA55 / EMA20 / EMA50 / RSI / MACD / Ichimoku）？还是更细（如把 EMA20/50 合并为「EMA」）？→ 推荐按 6 个，与数值条条目一一对应，直观。
- **数值条展示范围**：全部开启的指标都显示数值（可能拥挤）？还是只显示主图均线 + RSI + MACD（Ichimoku 值不在条上，因有 5 条线太密）？→ 推荐：数值条显示 SMA/EMA/RSI/MACD，Ichimoku 的 5 条线只在图上，值过多不放进数值条（可在计划里说明，让用户选择）。
- **开关默认值**：默认全开（用户明确要求"默认开启"）。

## 风险

- **removePane 动态调整**：v5 的 pane 增删 + moveToPane 组合需要实测；若 moveToPane 行为有坑，退路是「关闭子图指标时用 setStretchFactor 压成极小 + visible:false」或「重建 series 到默认 pane」。计划里标注主方案 + 退路。
- **crosshair 高频 setState**：数值条 state 更新频率与鼠标移动一致（可能 >60fps）。缓解：setState 用 ref 缓存上次值，值未变不 set；React 18 自动批处理；数值条是轻量 DOM。可接受。
- **seriesData vs 用 time 查 rows**：用 `param.time` 在 rowsRef 查行最直接（所有指标同源）；不用 seriesData（它只含 hover 到的 series 数据，且 time 才是时间索引）。
- **tick 更新最后 K 线后**，数值条「最新值」会滞后于 tick（指标不随 tick 重算，60s 轮询兜底）——与现有图表行为一致（评审 H3 已接受），计划中说明。
- **i18n**：新增开关标签/数值条标签补 zh/en（charts.json）；项目无 th。

## 实施中发现（2026-09-24 Phase 1 补充）

### v5 原代码隐患（commit 777226c 遗留，本次修复方案规避）
- 逆向 v5 development build 确认：`chart.addSeries(def, opts, paneIndex = 0)` **默认 paneIndex=0**（12750 行源码硬编码）。
- 上一任务 TradingChart 的 RSI/MACD 创建用的是 `line("rsi")` → `chart.addSeries(LineSeries, {...})` **无 paneIndex → 实际加到 pane0**（与蜡烛同 pane 叠加）。
- 但 `chart.priceScale("rsi", 1).applyOptions(...)` 指向 pane1 的 rsi scale——`_internal_applyPriceScaleOptions` 在 pane1 找不到 rsi scale 会 **抛 Error**（`Trying to apply price scale options with incorrect ID`）。
- 结论：原实现的 RSI/MACD 独立子图**依据源码推断可能未真实渲染**（或抛错被 effect 吞），用户验收可能看的是主图叠加。**不深究**（不影响本次），本次用 `pane.addSeries(...)`（IPaneApi.addSeries 内部 `chart.addSeries(def, opts, this.paneIndex())`）**显式指定 pane**，彻底规避。
- v5 series 删除用 **`chart.removeSeries(seriesApi)`**，**无 `ISeriesApi.remove()`**；`IPaneApi` 需要泛型参数 `IPaneApi<Time>`。
- `chart.addPane()` 仅 append 到 `_panes`，不改变后续 addSeries 的默认 index；空 pane 的 `preserveEmptyPane=false` 时在 series 移除后自动清理。

## 验证方式
- `cd frontend && npx tsc --noEmit` + `npm run build` + eslint。
- 手动（dev server）：开关切换 → 图上线/子图即时显隐、数值条同步增减；鼠标滑动 → 数值条随十字线联动、离开回到最新值；切品种/周期 → 数值条刷新到最新；tick 更新最后 K 时数值条最终值随 60s 轮询刷新。
