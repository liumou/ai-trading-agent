# 交易图表指标开关 + 指标数值条 + 鼠标滑动联动 —— 实施计划

## Goal

在手动交易页现有行情图表（`TradingChart.tsx`）上：
1. 为各指标增加**显示开关**（默认全开）：开启→展示在图表上，关闭→不展示。
2. 图表**上方增加指标数值条**：默认展示最新一根 K 线的指标数据。
3. **鼠标滑动**（crosshair 移动）时，数值条联动展示**该时间点**的指标数据。

> 前置条件：本计划需经用户明确同意后方可执行。

## 方案概述

纯前端改动（后端指标数据链路上一任务已就绪，`/ohlcv` 已返回完整 `indicators` 数组）。

- **开关**：series 级 `applyOptions({ visible })` 动态显隐（v5 文档明示 hiding ≠ deleting，不影响时间线、不重建 series）；子图（RSI/MACD）关闭时收起空 pane，开启时还原。
- **数值条**：图表上方新增一行指标数值（SMA55/EMA20/EMA50/RSI14/MACD 值），默认取 `indicators` 最后一行；随开关联动显示。
- **滑动联动**：`subscribeCrosshairMove` 监听，取 `param.time` 查已加载指标行，更新数值条；鼠标离开回落到最新值。

## Decisions

1. **开关粒度 = 6 个指标开关**：SMA55 / EMA20 / EMA50 / RSI / MACD / Ichimoku（Ichimoku 复合控制 tenkan/kijun/senkouA/senkouB/云 Area 五条线）。与数值条条目一一对应，直观。（用户可改为更细粒度，默认推荐此方案）
2. **开关默认全开**（用户明确要求）。
3. **数值条展示范围**：SMA55 / EMA20 / EMA50 / RSI(14) / MACD（DIF/DEA/柱）数值；**Ichimoku 的 5 条线不放入数值条**（线多值密，图上已展示）。随开关联动：某指标关闭 → 数值条对应条目隐藏。
4. **关闭主图均线系列不收起 pane0**（pane0 还有蜡烛）；**关闭 RSI/MACD 时收起对应子图 pane**，重新开启时还原。
   - pane 引用保存到 ref；removePane/moveToPane 按固定顺序管理（RSI 恒在 MACD 上方，先删 RSI 时 MACD 上移，动态取 paneIndex()）。
   - 退路（若 v5 moveToPane 有坑）：setStretchFactor 压成极小 + visible:false，或重建 series 到默认 pane。
5. **crosshair 联动用 `param.time` 查 rowsRef**（所有指标同源），不用 seriesData；`param.point === undefined`（鼠标离开）→ 回落最新值。
6. **性能**：rows 存 ref；数值条 setState 前与 ref 缓存比较，值未变不触发渲染；React 自动批处理。
7. **tick 不重算指标**：数值条「最新值」随 60s 轮询刷新（与现有图表 H3 行为一致，计划中说明）。
8. **i18n**：新增开关/数值条标签补 **zh/en**（charts.json）；项目无 th locale。
9. **布局**：开关按钮组放图表卡标题栏（周期切换右侧），小号 ToggleGroup/开关组；数值条放 TradingChart 组件上方（图表卡内）。
10. **Ichimoku chikou 线现状未绘制，本次不新增**（保持现状范围）。

## Phases

### Phase 1 — TradingChart 组件改造（series 显隐 + pane 动态收起 + 数值条 + crosshair 联动）

**Status:** complete

**涉及文件**：`frontend/components/chart/TradingChart.tsx`、`frontend/lib/api.ts`（如需辅助类型）

**改动要点**：
- 新增 props：`indicatorVisibility`（受控，父级持有开关 state）+ `onIndicatorVisibilityChange`（回调）。默认全开。
- series 引用扩展：保存 `rsiPane`/`macdPane` 到 ref。
- `setIndicatorVisible(key, on)`：
  - 主图均线（sma55/ema20/ema50）：`series.applyOptions({ visible: on })`。
  - Ichimoku（tenkan/kijun/senkouA/senkouB/cloud）：联动 `visible`。
  - RSI：rsi series `visible`；若从开→关且 MACD pane 存在，`removePane(rsiPaneIndex)`；若从关→开，`addPane()` + rsi series `moveToPane()` + 重建 30/70 priceLine + setStretchFactor。
  - MACD：macd/macdSignal/hist series `visible`；pane 同理增删。
- 数值条：
  - 组件顶部渲染一行（或 JSX 区块）展示各开关开启的指标最新值，**颜色与图表一致**（用 THEME）。
  - 默认值 = `indicators` 最后一行（最新 K）；随数据拉取更新。
- crosshair：
  - 挂载时 `chart.subscribeCrosshairMove(handler)`；卸载 `unsubscribeCrosshairMove`。
  - handler：`param.time != null` → 在 rowsRef 查行 → 更新数值条 state；`param.point === undefined` → 回落最新行。
  - rowsRef 在每次数据拉取/截取时更新。
- 代码注释全部中文；保持 200-400 行文件约束（必要时抽子组件 `IndicatorValueBar`）。

**验收标准**：`cd frontend && npx tsc --noEmit` 通过；dev server 渲染无报错，开关切换即时显隐、子图 pane 收起/还原、数值条同步、crosshair 滑动联动。

### Phase 2 — 开关 UI + 布局接入（trading 页 + i18n）

**Status:** complete

**涉及文件**：`frontend/app/trading/page.tsx`、`frontend/messages/zh/charts.json`、`frontend/messages/en/charts.json`

**改动要点**：
- 图表卡标题栏增加**指标开关组**（周期切换右侧）：SMA55/EMA20/EMA50/RSI/MACD/Ichimoku 六个小开关 + 可选「全部」总开关，默认全开。用 shadcn ToggleGroup/Checkbox/开关组件（项目已有 ui 库），键盘可达 + aria。
- 开关 state 放 trading 页（受控）：`const [indicatorVisibility, setIndicatorVisibility] = useState<Record<IndicatorKey, boolean>>(allOn)`；传给 `<TradingChart indicatorVisibility={...} onIndicatorVisibilityChange={...} />`。
- i18n：charts.json 增加 sma55Switch/ema20Switch/.../indicatorShowAll 等开关标签 + 数值条标签（zh/en），结构一致。

**验收标准**：`cd frontend && npx tsc --noEmit` + `npm run build` 通过；trading 页开关组可点、与图表联动、i18n 无缺 key。

### Phase 3 — 全量验证 + 回归

**Status:** complete

**涉及文件**：无新增

**改动要点**：
- `cd frontend && npx tsc --noEmit`、`npm run build`、eslint 0 错误。
- 确认 dashboard `PriceChart.tsx` 零改动（本次只动 TradingChart + trading 页 + i18n）。
- 手动验证（dev server + 登录）：开关切换即时显隐、子图收起/还原、数值条随开关增减、鼠标滑动联动、离开回落最新、切品种/周期数值条刷新、tick 更新最后 K 不破坏数值条、下单/持仓表单无回归。

**验收标准**：tsc/build/eslint 全绿；git diff 确认 PriceChart/dashboard 零改动；zh/en 无缺 key。

## Next Step

全部 3 个 Phase 完成并验证（tsc / eslint / npm run build / code-review）。待用户手动浏览器验证（需登录态）与提交。

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| TS 类型：IPaneApi 需泛型、ISeriesApi 无 remove() | 1 | IPaneApi<Time>；改用 chart.removeSeries(seriesApi) |
| rebuildSubPanes 缺函数右括号（删除 crosshair effect 时误删） | 1 | 补 `}`（括号计数定位） |
| 全部总开关 on→全关逻辑错（总设全开） | 1 | 用 allIndicatorsOn() 作基底，按 on 设全开/全关 |
| code-review 重要-1：数值条高度硬编码 30px | 1 | flex 列布局，图表 flex-1 填满 |
| code-review 重要-2：轮询后 hover 闪烁 | 1 | hoverTimeRef 记录 time，数据刷新后仅当 time 失效才回落 |
| code-review 重要-3：主题切换清空数据缓存致子图空窗 | 1 | cleanup 不清空 rowsRef/timesRef/timeIndexRef |
| code-review 次要-4/5 | 1 | time 用 typeof number 守卫；subPanesRef 收窄为 "rsi"\|"macd" |
