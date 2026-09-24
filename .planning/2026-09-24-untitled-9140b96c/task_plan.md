# 手动交易页增加品种图表展示 —— 实施计划（v2，经四路评审迭代）

## Goal

在手动交易页（`frontend/app/trading/page.tsx`）嵌入交互式蜡烛图，随品种联动展示 **SMA55/EMA20/EMA50 均线、RSI、MACD、Ichimoku（ich）指标**，并展示该品种**当日最高价与最低价**。

> 前置条件：本计划需经用户明确同意后方可执行。四路评审（架构/前端/后端/指标）已于 2026-09-24 完成，本次为迭代版。

## 方案概述

- **数据流**：交易页 → `GET /api/market-data/ohlcv`（附加 `indicators` 字段，`?indicators=` 开关）+ 新增 `GET /api/market-data/day-range` → 新 `TradingChart` 组件多 pane 渲染。
- **后端**：`indicators.py` 新增 `sma`/`macd`/`ichimoku` + `rsi_wilder`（不改现有 `rsi`，避免策略回归）；`/ohlcv` 增量附加 `indicators`（`candles` 保持兼容）；新增 `/day-range`（含当日对齐校验）。
- **前端**：新建 `TradingChart.tsx`（v5 原生多 pane），**不改** `PriceChart.tsx`；交易页嵌入并联动品种/周期/实时 tick；i18n 补 **zh/en**（项目无 th locale）。

## Decisions（评审后定稿）

1. **指标在后端计算、随 OHLCV 一并返回**（`/ohlcv` 附加 `indicators`）：复用现有 pandas 指标库（AI/ML 链路已消费 `app.strategy.indicators`）、可单测、前端零计算、前后端一致。指标基于**原始未 round OHLC** 计算，最后一步才 round。
2. **RSI 用新增 `rsi_wilder`（`ewm(alpha=1/14, adjust=False)`）**，不改现有 `rsi`（策略/风控已依赖 ewm(span=14)，改有回归风险）。图表展示与 TradingView 对齐。
3. **当日高低走独立端点 `/api/market-data/day-range`**：读 D1 最新 K，**复用 `app/market/sessions.py` 重置时区规则做当日对齐校验**（返回 `is_current`），非当日返回 `day_range: null` + `date`，前端标注"上一交易日"而非裸标"今日"；空数据返回 `{day_range: null}` 不报错。
4. **新建 `TradingChart` 组件而非改造 `PriceChart`**（dashboard 零回归）；指标准确性优先于共享基建，图表基础模式复制，`useChartInit`/`useWatermarkCleanup` hook 抽取记为技术债可选。
5. **Ichimoku 用 v5 原生多 pane + Line/Area 组合**：`chart.addPane()` + `addSeries(..., paneIndex)` + `setStretchFactor`；senkou 双 Line + 淡色单 Area 背景折中（**异色双色云需 IPanePrimitive，记为后续增强，计划标注妥协**）；`layout.attributionLogo: false` 去水印。
6. **Ichimoku 位移**：senkou_a/senkou_b 用 `.shift(+26)`（投影未来），chikou 用 `.shift(-26)`（回退）；图表 getOHLCV 请求 `count = 200 + 预热(~80) ≥ 280`，后端全量算、前端截取后 200 根展示，避免左段 NaN 起线。
7. **周期默认承接 `/symbols` 的 `timeframe`**（`symbolInfo.default_timeframe`），提供 M1/M5/M15/H1/H4/D1 切换；用 shadcn ToggleGroup/Tabs，带 aria-selected。
8. **指标周期参数**：本期做**集中常量**（`constants.py`）固化 SMA55/EMA20/50/RSI14/MACD(12,26,9)/Ichimoku(9,26,52,26) 与 `/ohlcv` 可选 `indicator_params` 覆盖（默认=计划参数，向后兼容）；前端周期可配记为二期。
9. **"移动均线（55日）"解读为 SMA(55)** 简单移动平均；保留 EMA20/50 衔接 dashboard 观感。
10. **tick 只更新主图最后 K 线**，子图不动（tick 无指标值），60s 轮询全量重设 indicators；**前端不重算指标**（与后端算法一致）。

## Phases

### Phase 1 — 后端指标计算（纯函数 + 单测）

**Status:** complete

**涉及文件**：`backend/app/strategy/indicators.py`、`backend/tests/unit/test_indicators.py`

**改动要点**：
- 新增 `sma(series, length)`：`series.rolling(length).mean()`。
- 新增 `macd(series, fast=12, slow=26, signal=9)`：`macd=ema(c,12)-ema(c,26)`；`signal=macd.ewm(span=9, adjust=False).mean()`；`hist=macd-signal`（EMA-based，**勿用 RMA**，与 TradingView/talib 一致）。
- 新增 `ichimoku(high, low, close, tenkan=9, kijun=26, senkou_b=52, displacement=26)`：`tenkan=(hh9+ll9)/2`；`kijun=(hh26+ll26)/2`；`senkou_a=((tenkan+kijun)/2).shift(+26)`（**正位移=投影未来**）；`senkou_b=((high.rolling(52).max()+low.rolling(52).min())/2).shift(+26)`；`chikou=close.shift(-26)`（**负位移=回退**）。返回 `{tenkan, kijun, senkou_a, senkou_b, chikou}`。
- 新增 `rsi_wilder(series, length=14)`：`gain.ewm(alpha=1/length, adjust=False).mean()`（loss 同），**不改现有 `rsi`**。
- 全部纯 pandas、无外部依赖；完整类型注解 + 中文 docstring；key 命名统一（tenkan/kijun/senkou_a/senkou_b/chikou）。
- **单测（test_indicators.py）**：sma 与 `df.rolling(55).mean()` 一致；macd/hist 容差对照；ichimoku 各线长度=输入、`senkou_a[t] == (tenkan+kijun)[t-26]/2`（位移断言）、`chikou[t] == close[t+26]`；**用已知参考值（TradingView/教科书样例）断言**，不得只与自身实现对比；边界：空 df、单根、<52 根（senkou_b 全 NaN）、全 NaN、常数序列（macd hist=0）。

**验收标准**：`cd backend && .venv/bin/python -m pytest tests/unit/test_indicators.py -v --no-cov` 通过；`ruff check` 通过。

### Phase 2 — 后端 API 扩展（ohlcv 附加 indicators + day-range 端点）

**Status:** complete

**涉及文件**：`backend/app/api/routes/market_data.py`、`backend/app/constants.py`、`backend/tests/`（market_data 相关测试）

**改动要点**：
- 指标参数做**集中常量**（`constants.py`）：SMA55/EMA20/50/RSI14/MACD(12,26,9)/Ichimoku(9,26,52,26)，`/ohlcv` 接受可选 `indicator_params` 覆盖（默认=常量，向后兼容）。
- `/ohlcv` 响应附加 `indicators` 数组（与 candles 对齐）：每个元素 `{sma55, ema20, ema50, rsi14, macd, macd_signal, macd_histogram, ichimoku_tenkan, ichimoku_kijun, ichimoku_senkou_a, ichimoku_senkou_b, ichimoku_chikou}`；**基于原始 df 计算、最后一步 round**；价格按 `price_decimals`、指标按**固定 2 位**（RSI 按 decimals 舍入是语义错误）；NaN→null。**`candles` 字段保持兼容**，现有 dashboard/PriceChart 消费方零改动。加 `indicators: bool = Query(True)` 开关，只要 K 线的调用方可跳过（count=5000 时 payload 较大）。
- 指标计算抽成可复用辅助函数（供 `/ohlcv` 使用）。
- 新增 `GET /api/market-data/day-range?symbol`：
  - `engine.market_data.get_ohlcv(symbol, "D1", 2)` 取最后一根 K；空 df 返回 `{symbol, day_range: null, date: null}`（不 500）。
  - 复用 `app/market/sessions.py` 重置时区规则判断当日对齐 → 返回 `{symbol, date, day_range: {open, high, low} | null, is_current: bool}`；非当日 `day_range: null`、`is_current: false`（前端标注"上一交易日"）。
- **测试**（mock 模式参考 `test_market_data_tick.py`：MagicMock engine + AsyncMock get_ohlcv + set_global_manager + ASGITransport）：indicators 长度==candles 长度；NaN→null；count=5000 兼容；当日高低三态（正常、空数据→null、周末昨日 K→is_current=false）；indicator_params 覆盖生效。

**验收标准**：`cd backend && .venv/bin/python -m ruff check .` 通过；pytest 相关用例通过；curl 验证 `curl "/api/market-data/ohlcv?symbol=GOLD&timeframe=M15&count=300"` 响应含 `indicators` 且 `candles` 兼容；`curl "/api/market-data/day-range?symbol=GOLD"` 返回当日高低（MT5 在线时）。

### Phase 3 — 前端 TradingChart 组件（v5 原生多 pane）

**Status:** complete

**涉及文件**：新建 `frontend/components/chart/TradingChart.tsx`、`frontend/lib/api.ts`（类型）、`frontend/messages/zh|en/charts.json`

**改动要点**：
- `lib/api.ts`：`getOHLCV` 返回类型扩展为 `{candles, indicators?}`（**可空字段**，dashboard 消费方零改动通过 tsc）；新增 `getDayRange(symbol)` → `{date, day_range, is_current}`。
- `TradingChart` props：`{ symbol, timeframe, tick?, count? }`。内部：
  - **v5 原生多 pane**（已查证 typings.d.ts 确认）：`createChart` 默认 pane 0；`chart.addPane()` 加 RSI=pane1、MACD=pane2；`chart.addSeries(def, opts, paneIndex)` 或 `pane.addSeries()`；`pane.setStretchFactor`（主图 0.6/RSI 0.2/MACD 0.2）。
  - 主图 pane：Candlestick + SMA55/EMA20/EMA50 三条 Line（主价轴）。
  - RSI pane：LineSeries + `priceScaleId:"rsi"` + `chart.priceScale("rsi", paneIndex).applyOptions({scaleMargins:{top:0.1,bottom:0.1}})`；30/70 参考线用 `rsiSeries.createPriceLine`（0/100 贴边不画）。
  - MACD pane：快/慢线 Line + HistogramSeries（base:0，每条 `{time,value,color}` 正绿负红）+ `priceScaleId:"macd"`。
  - Ichimoku：主图叠加 tenkan/kijun Line + senkou_a/senkou_b 双 Line + **单条极淡 AreaSeries 背景**（折中；异色双色云需 IPanePrimitive 记为后续增强）+ chikou Line（可选用）。
  - **数据归一化（关键，防 setData 抛异常）**：每个 time 槽必须存在；`value==null||NaN` 的槽转 **WhitespaceData `{time}`**（无 value 字段），禁 value:null/NaN 传入 setData；多 series 共享 timeScale，time 集合一致或子集、各自升序。
  - count 默认 `200 + 预热(~80) ≥ 280`，后端全量算、前端截取后 200 根展示（避免左段 NaN 起线）。
  - 主题/水印：**`layout.attributionLogo: false`**（v5 原生，弃用 MutationObserver）；复用 PriceChart 的 ResizeObserver/chart.remove() 清理模式。
  - tick 只更新主图最后 K 线（子图不动，60s 轮询兜底）；**不前端重算指标**；tick effect 加注释。
  - loading/error 态；console.error 沿用 PriceChart 模式，用户可见错误走 lib/toast.ts。
- **复制而非改造**，不 import 不改动 `PriceChart.tsx`。

**验收标准**：`cd frontend && npx tsc --noEmit` 通过；dev server 下单组件渲染无报错、主图蜡烛+均线+RSI/MACD 子图+Ichimoku 云正常显示、切品种/周期刷新、无 value:null 报错。

### Phase 4 — trading 页接入（品种/周期联动 + 当日高低 + 实时 tick）

**Status:** complete

**涉及文件**：`frontend/app/trading/page.tsx`、新建子组件（如 `TradingChartPanel.tsx`）、`frontend/messages/zh|en/trading.json`

**改动要点**：
- **布局**（评审确定方案）：图表放 PageHeader 与下单表单之间**全宽独立行**（高度 `h-[420px]`~`h-[520px]`）；组件超 200 行（现 679 行）本就违规，抽 `TradingChartPanel` 子组件，page.tsx 只管布局组合。
- 新增 `timeframe` state，默认 `symbolInfo.default_timeframe`（**不硬编码 M15**）；周期切换用 shadcn ToggleGroup/Tabs（键盘可达 + aria-selected，不用裸 button）。
- 嵌入 `<TradingChart symbol={symbol} timeframe={timeframe} tick={liveTick} />`（复用 `liveTick`）；**`TradingChart` 用 `dynamic(() => import(...), { ssr:false })`**（lightweight-charts 访问 window）。
- 当日高低徽标：来自 `getDayRange`，随 symbol/timeframe 变化重取（独立 effect，**绝不随 tick 触发**，tick 1Hz）；`is_current: false` 时标注"上一交易日"，`day_range: null` 隐藏不阻塞。
- 布局不破坏下单/持仓表单；切品种/切周期联动图表刷新。

**验收标准**：`cd frontend && npx tsc --noEmit` 与 `npm run build` 通过；手动验证（dev server + 登录）：切品种→图表与当日高低联动、切周期→刷新、WS tick 实时更新最后 K、下单表单无回归、响应式布局正常。

### Phase 5 — i18n + 收尾验证（含回归确认）

**Status:** complete

**涉及文件**：`frontend/messages/zh/*.json`、`frontend/messages/en/*.json`（**仅 zh/en，项目无 th locale**）

**改动要点**：
- 新文案（图表 loading/周期标签/RSI/MACD/Ichimoku/今日最高/今日最低/上一交易日等）补齐 zh + en；图表通用 key 放 `charts.json`，交易页专属放 `trading.json`，结构一致。
- 全量回归：确认 dashboard `PriceChart` 未受影响（未改动该文件）。
- 代码注释全部中文。

**验收标准**：`cd frontend && npx tsc --noEmit`、`npm run build`、`cd backend && .venv/bin/python -m pytest tests/ -q --no-cov`（496+ 新用例）全绿；`git diff` 确认 `PriceChart.tsx`/dashboard 零改动；zh/en 无缺 key 警告。MT5 在线时用真实 bridge 抽查 D1 休市行为一次（后端评审 residual risk）。

## 评审意见汇总（2026-09-24 四路：架构/前端/后端/指标）

### 已采纳（全部纳入计划）
- **Ichimoku 位移**：senkou `.shift(+26)` / chikou `.shift(-26)`（指标/后端/架构三方一致，写反错位 52 根）。
- **RSI**：新增 `rsi_wilder`（`ewm(alpha=1/length)`）供图表，不改现有 `rsi`（避免策略回归）。
- **/day-range 空数据**：返回 `{day_range: null, date: null}` 不 500（对齐 /tick 语义）。
- **/day-range 当日对齐**：复用 `app/market/sessions.py` 重置时区判断 `is_current`，非当日标注"上一交易日"。
- **指标先算再 round**：基于原始 df，最后一步 round；价格用 price_decimals、指标固定 2 位。
- **指标 warmup**：count=200+预热(~80)，避免左段 NaN 起线。
- **多 pane**：v5 原生 addPane + paneIndex + setStretchFactor；RSI/MACD 独立 priceScaleId + scaleMargins。
- **水印**：`layout.attributionLogo: false`（原生，弃 MutationObserver）。
- **WhitespaceData**：位移/预热 NaN 槽转 `{time}`，禁 value:null/NaN 进 setData。
- **Ichimoku 云妥协**：双 Line + 淡色单 Area，异色双色云记后续增强（需 IPanePrimitive）。
- **指标参数策略**：集中常量 + /ohlcv 可选 `indicator_params` 覆盖（向后兼容）。
- **tick 不更新子图**：60s 轮询兜底，前端不重算指标。
- **布局**：图表全宽独立行，抽 `TradingChartPanel` 子组件。
- **payload**：`indicators: bool` 开关 + count 上限约束。

### 已驳回/调整
- 指标评审"改现有 rsi 为 Wilder"→ **驳回**（后端评审：策略/风控已依赖，回归风险），改为新增 `rsi_wilder`。
- 前端 M1"复用 /ohlcv 取 D1 避免新端点"→ **驳回**（架构 A1 + 后端 High3：需要当日对齐校验与独立语义），新增 `/day-range`。

### i18n 事实修正
- 项目 locale 仅 `["zh","en"]`（`frontend/i18n/config.ts:1`），**无 th**——Phase 5 由"补 zh/en/th 等"改为"补 zh/en"（th 仅为 Telegram 告警语言）。

## Next Step

全部 5 个 Phase 已完成并通过验证（tsc / build / pytest / eslint）。待用户手动验证（MT5 在线时在交易页切品种看图表联动），以及 MT5 bridge 上线抽查 D1 休市行为（residual risk）。

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| is_current_day 用 sessions.reset_hour 判定，测试暴露语义混淆（20:00 vs 22:00 重置日界不清） | 1 | 实测定稿为**自然日 UTC 判定**（`bar_open.date() == now.date()`），更贴近"当日"直觉且兼容两种经纪商日界模型 |
| ruff 不在本地 venv（无 pip 模块） | 1 | 跳过本地 ruff（CI GitHub Actions 会跑）；pytest/eslint/tsc 均通过 |
| test_multi_agent 等 9 个全量失败 | - | 确认均为**既有环境失败**（模型 ID 配置差异/模块加载顺序 flaky），与本次改动零交集 |
| eslint 报 `api` 未使用 warning | 1 | 移除 `trading/page.tsx` 中既有的未使用 default import `api` |