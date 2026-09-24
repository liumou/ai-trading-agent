# Findings — 手动交易页品种蜡烛图与指标展示

> 调研时间：2026-09-24。所有结论均已通过实际读取代码核实。

## 需求
手动交易页面增加品种图表：蜡烛图 + 均线/移动均线(55) + RSI + MACD + Ichimoku(ich) + 当日最高/最低价。

## 现状（已核实）

### 前端
- `frontend/app/trading/page.tsx`（679 行）：手动下单页，**目前无任何图表**。
  - 已有：品种下拉 `Select`（`symbols`/`setSymbol`，L480-497）、实时报价 `liveTick`（WS `price_update` + REST `/api/market-data/tick` 兜底）、下单/挂单/持仓表单、多语言 `useTranslations("trading")`。
- `frontend/components/chart/PriceChart.tsx`：现有图表组件。
  - 已实现：蜡烛图、EMA20/50 均线（同主图 scale）、交易开平仓标记、持仓 TP/SL/Entry price line、实时 tick 更新最后 K 线、TradingView 水印移除。
  - **局限**：只有主图 candle series + 两条 EMA line（同一 price scale），**无独立子图 pane**，无 RSI/MACD/Ichimoku/当日高低。
  - Props：`{ symbol, timeframe, tick?, emaFast=20, emaSlow=50 }`。
- `frontend/lib/api.ts`：已封装 `getOHLCV(symbol, timeframe, count)` → `GET /api/market-data/ohlcv`。
- dashboard 通过 `dynamic(() => import("@/components/chart/PriceChart"), { ssr:false })` 使用 PriceChart（传入 `symbol/timeframe/tick` + `TimeframeSelector`），有两处：多品种网格 + 单品种大图。**改 PriceChart 有回归风险**，建议新建组件。

### 图表库
- `lightweight-charts` `^5.1.0`（已装，typings 确认含 `AreaSeries`/`HistogramSeries`/多 `priceScale`）。
- v5 **无内置 Ichimoku 系列**：需用 Line/Area/Baseline 组合绘制（云 = senkouA/senkouB 两条 Area 线 + 填充区间）。
- 多 pane 方案：`chart.addSeries(..., { priceScaleId: "rsi" })` + `chart.priceScale("rsi").applyOptions({ scaleMargins })`。

### 后端
- `backend/app/api/routes/market_data.py`：
  - `GET /api/market-data/ohlcv?symbol&timeframe&count` → `{candles:[{time,open,high,low,close}]}`（L18-44）。
  - `GET /api/market-data/tick` → `{tick:{bid,ask,spread,time}}`（L47-71）。
- `backend/app/strategy/indicators.py`：纯 pandas 指标库，已有 `ema`、`rsi`、`atr`、`adx`、`bollinger_bands`、`stochastic`。**无 SMA、MACD、Ichimoku**。
- **无当日高低端点**。

## 关键设计决策（推荐）
1. **指标在后端计算**：在 `indicators.py` 补齐 `sma`、`macd`、`ichimoku`，并在 `GET /ohlcv` 响应附加 `indicators` 字段（前端拿到一套数据画多个 pane），复用现有 pandas 库、可单测、前端轻量。
2. **当日高低走后端新端点**：`GET /api/market-data/day-range?symbol` → 读日线 `D1` 最新 K 的 high/low/open（`engine.market_data.get_ohlcv(symbol, "D1", 2)` 取最后一根），避免 tick 流聚合不可靠问题。
3. **新建前端组件** `TradingChart.tsx`（或扩展 PriceChart 但加开关）避免破坏 dashboard 现有用法；交易页嵌入该组件并随 `symbol` 联动；右上角加周期切换（M1/M5/M15/H1/H4/D1）。
4. "均线、移动均线（55日）"解读：**SMA(55)** 简单移动平均（55 周期）；保留现有 EMA20/50 以衔接 dashboard 观感。

## 评审意见（2026-09-24，多路评审进行中）

### 指标正确性评审（python-reviewer，已返回）
**阻塞 B1**：Ichimoku 位移方向——索引时间升序时，senkou_a/senkou_b 用 `.shift(26)`（正，云画在未来），chikou 用 `.shift(-26)`（负，把未来 close 拉到现在）。写反云图错位 52 根。记忆法：正 shift=往未来推（senkou），负 shift=往过去拉（chikou）。

**重要 I1**：RSI(14) 现用 `ewm(span=14)`（alpha≈0.133）与 Wilder/TradingView RMA（alpha=1/14≈0.071）不等价，必须改 `ewm(alpha=1/length, adjust=False)`。
**重要 I3**：ATR/ADX 同样用 `ewm(span=length)` 而非 Wilder RMA，建议一并改 `ewm(alpha=1/length)`（既有策略在用，需回归测试）。

**确认正确**：MACD(12,26,9) EMA-based 与 TradingView/talib 一致（勿改 RMA）；SMA(55)=rolling(55).mean()；Ichimoku tenkan/kijun 公式正确。

**次要 M1-M5**：
- M1 预热 NaN 保持 min_periods=length，前端 NaN→null 跳过；senkou_b 预热 77 根，count≥300。
- M2 指标必须基于 df 原始 float 计算，**不能复用已 round 的 candles**（round 后算 MACD/Ichimoku 放大舍入噪声）。正确顺序：先在 df 算指标 → 再 round OHLCV → 拼装响应。
- M3 RSI/ADX(0-100) 与价格不同量纲，前端需独立 pane/priceScaleId；histogram 与 macd/signal 同量纲无需缩放。
- M4 不建议引 talib（Docker 体积/C 扩展），用固定 fixture 对照测试（1e-6 内一致）。
- M5 新函数带完整类型注解 + 中文 docstring，Ichimoku 键名统一。

**推荐实现表（代码级）**：
- SMA: `series.rolling(55).mean()`
- EMA: `series.ewm(span=N, adjust=False).mean()`
- MACD: `macd=ema(c,12)-ema(c,26)`; `signal=macd.ewm(span=9,adjust=False).mean()`; `hist=macd-signal`
- RSI(14): `gain.ewm(alpha=1/14, adjust=False).mean()`（loss 同）
- ATR(14): `tr.ewm(alpha=1/14, adjust=False).mean()`
- Ichimoku: `tenkan=(h.rolling(9).max()+l.rolling(9).min())/2`; `kijun` 同理 26; `senkou_a=((tenkan+kijun)/2).shift(26)`; `senkou_b=((h.rolling(52).max()+l.rolling(52).min())/2).shift(26)`; `chikou=c.shift(-26)`

## 风险
- lightweight-charts v5 多 pane 布局/scale 定位需要小心（`priceScaleId` 需在 addSeries 时指定，位置由 `scaleMargins` 控制）。
- Ichimoku 无内置系列，云图需 Area 组合实现，chikou 延迟线可选用（展示可简化）。
- OHLCV 时间戳为秒级（`int(ts.timestamp())`），lightweight-charts 需要 `UTCTimestamp`，现有代码用 `Time` 类型已兼容。
- i18n：新增文案需同步 `frontend/messages/*.json`（zh/en/th）。
- 避免破坏 dashboard 现有 PriceChart（勿直接改其行为）。
- 指标须先算再 round；RSI/ADX 需独立 pane（M2/M3）。

### 前端评审（react-reviewer，已返回，实际查证 v5.1.0 typings.d.ts）
**阻塞 B1**：Ichimoku 云"双色异色填充"无法用两条 AreaSeries 实现。v5 AreaStyleOptions 只有 topColor/bottomColor/lineColor，填充是"线到底/顶"单向渐变，两线交叉区间颜色会叠加成泥色。Baseline baseValue 是固定价格，不适用。→ 建议：Phase 3 用"双 LineSeries（senkou_a 绿 / senkou_b 红）+ 单条极淡 AreaSeries 背景"折中落地，异色云染色留作后续（需 IPanePrimitive，成本高）。计划须标注此妥协。

**阻塞 B2（高）**：位移 + NaN/缺口必须用 WhitespaceData。setData 每条要么 {time,value} 要么 {time}（WhitespaceData），**不接受 value:null/NaN**；time 升序唯一。senkou 前移 26 产生未来 time（v5 支持，右留白），chikou 后移 26 产生历史 time。→ 后端 indicators 契约：每个 time 槽必须存在，未定义值 value:null，前端归一化 `value==null||NaN ? {time} : {time,value}`。多 series 共享 timeScale，time 集合须一致或子集。

**重要 H1**：多 pane 用 v5 原生 addPane + paneIndex + setStretchFactor（已确认存在于 typings）：
- createChart 默认 addDefaultPane:true → pane 0 主图；`chart.addPane()` 加 RSI=pane1、MACD=pane2。
- `chart.addSeries(def, opts, paneIndex)` 第三参指定 pane，或 `pane.addSeries(def, opts)`。
- `pane.setStretchFactor(n)` 相对高度（主图 0.6/RSI 0.2/MACD 0.2）。
- RSI/MACD series 设独立 priceScaleId（"rsi"/"macd"），`chart.priceScale(id, paneIndex).applyOptions({scaleMargins:{top:0.1,bottom:0.1}})`。
- 不要用单 pane + 多 priceScaleId overlay 模拟子图。

**重要 H2**：水印清理用 v5 原生 `layout.attributionLogo:false`，弃用 MutationObserver（PriceChart 的 MutationObserver 是 v4 workaround）。TradingChart 用原生；PriceChart 保持现状（Phase 5 零改动约束）。

**重要 H3**：tick 只含 bid/ask，不含指标值。tick 只更新主图 candle，子图不动，60s 轮询全量重设 indicators；**不要前端重算指标**（与后端不一致）。视觉不一致仅限最后 1 根 K 线指标末端，可接受，tick effect 加注释。

**重要 H4**：trading/page.tsx 679 行，插入图表需重构布局。建议：图表放 PageHeader 与下单表单之间（grid 左 2 列图表 + 右 1 列报价/今日高低）；timeframe 按钮组放图表标题栏，默认值从 `symbolInfo.default_timeframe` 承接（不硬编码 M15）；组件超 200 行本就违规，抽 `TradingChartPanel` 子组件，page.tsx 只管布局组合。

**重要 H5**：SSR 兼容必须 `dynamic(() => import(...), { ssr:false })`（lightweight-charts 访问 window），复用 dashboard L28 模式。

**次要 M1-M4**：
- M1 getDayRange：优先复用 /ohlcv 取 D1 最新一根，避免新增端点（架构评审可能交叉确认）。
- M2 timeframe 切换用 shadcn ToggleGroup/Tabs（项目已有 ui 库），保证键盘可达 + aria-selected，不要裸 button。
- M3 console.error 沿用 PriceChart 模式；用户可见错误走 lib/toast.ts。
- M4 RSI 30/70 参考线用 `rsiSeries.createPriceLine`（画在所属 priceScale），0/100 会贴边只画 30/70。

**v5 确定用法**：多 pane（addPane/paneIndex/setStretchFactor）；独立 priceScale（priceScaleId + chart.priceScale(id,paneIndex)）；Histogram 每条带 color 字段 + base:0；Area 云 topColor/bottomColor；WhitespaceData {time} 占位；layout.attributionLogo:false；chart.remove() 清理全部。

**i18n 位置**：图表通用 key（周期名/RSI/MACD/Ichimoku 标签）放 charts.json；交易页专属（今日高低/图表区块标题）放 trading.json；en/zh 都要补、结构一致。

### 后端评审（fastapi-reviewer，已返回）
**阻塞**：无。不触碰手动交易防火墙不变量、不引入硬编码密钥、不绕过 require_auth。

**High 1**：Ichimoku 位移方向须用正确 pandas shift 符号——senkou_a/senkou_b 投影未来 = `.shift(+26)`，chikou 回退 = `.shift(-26)`。写反云图错位 52 根且肉眼难发现。建议实现写死符号 + 断言性单测（senkou_a 第 t 行 == 第 t-26 行 (tenkan+kijun)/2）。

**High 2**：现有 rsi 用 ewm(span=14) alpha≈0.133，TradingView/Wilder 用 RMA alpha=1/14≈0.071，同行情 RSI 可差 1-3。**不要改现有 rsi**（策略/风控已依赖，有回归风险）；方案：新增 `rsi_wilder`（ewm(alpha=1/length)）供图表专用，或在计划显式标注偏差。→ 与指标评审 I1 一致，但策略不同：**图表用 rsi_wilder 新函数，不改现有 rsi**（避免回归）。

**High 3**：/day-range 空 df 必须返回 `{symbol, day_range: None, date: None}` 而非 500（mt5 离线/未订阅 get_ohlcv 返回空，`df.iloc[-1]` 会 IndexError）。语义对齐 /tick 的 `{tick: None}`。同时返回 `date`（UTC 日）供前端判断是否"当日"。

**Medium 4**：指标必须用未舍入原始 OHLC 计算，最后一步才 round（BTCUSD decimals=2 @~100000 在 round 后算会损失有效位）。

**Medium 5**：/ohlcv 附加 indicators 向后兼容 ✓；count=5000 时 payload 几百 KB，建议加 `indicators: bool = Query(True)` 开关让只要 K 线的调用方跳过。

**Medium 6**：性能可接受无需缓存（pandas C 实现 200-5000 根 × 11 指标 <5ms；主要成本是 bridge 网络调用已有）。不加 Redis 缓存（行情每根刷新，缓存引入陈旧）。

**实现确认**：MACD/SMA/Ichimoku 公式正确；D1 用 `mt5.copy_rates_from_pos(symbol, D1, 0, count)` position 0 = 当前未收盘 K 线，**交易日内实时刷新 high/low**，休市最新 D1=上一交易日 → /day-range 取最后一根 high/low 作当日高/低合理；price_decimals GOLD=2/OILCash=2/BTCUSD=2/USDJPY=3；测试 mock 模式参考 test_market_data_tick.py（MagicMock engine + AsyncMock get_ohlcv + set_global_manager + ASGITransport）；指标单测边界：空 df/单根/<52 根（senkou_b 全 NaN）/全 NaN/常数序列（macd hist=0）；day-range 的 date 用 `df.index[-1].strftime("%Y-%m-%d")`。
**Residual risk**：无法在无 MT5 bridge 环境实测 D1 休市行为，上线后用真实 bridge 抽查一次。

### 架构评审（architect，已返回）
**阻塞**：无。总体判断：方向正确、数据链路清晰、Phases 合理、核心取舍（后端算指标/新建 TradingChart/D1 最新 K 做当日高低）与现状吻合。批准前 5 个重要问题需把决策补进计划，另有 1 处计划事实性错误。

**重要 A1（高）**：/day-range 的"当日对齐"校验缺失。周末/当日开盘前，D1 最新 K 是上一交易日（周五/昨日），其 high/low 不是"今日"。BTCUSD 24/7 恒为今日，行为不一致。→ 建议复用 `app/market/sessions.py` 的 per-asset-class 重置时区规则判断 `is_current`；非当日返回 `{day_range: null, last_date: ...}`，前端标注"上一交易日"或隐藏，绝不裸标"今日"。测试覆盖三态：正常/空数据→null/周末昨日 K→is_current=false。

**重要 A2（高）**：Ichimoku 公式与位移未定义清楚。`senkou_b` 必须是 `(high.rolling(52).max() + low.rolling(52).min())/2`（标准定义）；位移：senkou 时移 +26、chikou 时移 −26。若按 candle 对齐全量返回，图尾会缺 26 根云的视觉位移。→ Phase 1 明确公式 + 位移策略（后端锚到显示时间 t+26 vs 前端平移时间戳，接受图尾云截断）；测试必须对**已知参考数值**（TradingView/教科书样例）断言，不能只与自身实现对比。

**重要 A3（中高）**：指标 warmup 使 count=200 左半段大量 NaN 起线。SMA55 需 55 根、senkou_b 需 52+26=78 根预热。→ TradingChart 请求 `count = 显示数(200) + 最大预热(≈80)`，后端全量算前端截取后 200 根展示；或明确接受"线从图表中部开始"。

**重要 A4（中）**："指标周期参数不硬编码"与"后端计算随 /ohlcv 返回"自相矛盾——周期一旦在后端算就成为端点契约。→ 二选一：(a) 参数做集中常量（constants.py）+ /ohlcv 接受可选 `indicator_params` 覆盖（推荐，改动小）；或 (b) 本期固定参数，记为二期能力。

**重要 A5（中）**：图表在 trading 页布局位置/高度未定义；多 pane"子图"机制需明确（plan 写 priceScaleId 只定义刻度，子图要与主图同时间轴并压下半区须用 scaleMargins 定位）。→ 固定布局方案（建议 symbol 区下方全宽独立行，高度 h-[420px]~h-[520px]）；明确"同一 chart + 独立 priceScaleId + scaleMargins 下压"；Phase 4 前与用户确认 UI 布局。

**次要**：
- **i18n 事实错误**：项目 locale 只有 `["zh","en"]`（i18n/config.ts:1），**无 th locale**。Phase 5 应为"补 zh/en"，th 仅是 Telegram 语言。
- 指标舍入精度：RSI(0-100) 按 price_decimals（GOLD=2）舍入语义错误；指标统一固定精度（如 2 位），价格才用 price_decimals。
- 图表共享基础设施：PriceChart 的 init/水印/resize 约 1/3 行数，复制有漂移风险，建议抽 `useChartInit`/`useWatermarkCleanup` hook（行为等价纯移动）或记技术债。
- day-range 请求频率：只随 [symbol,timeframe] 变化 + 与 getOHLCV 同节奏（60s），**绝不随 tick 触发**（tick 1Hz）；trading 页独立 effect 隔离，避免每秒 WS price_update 引发图表无关状态更新。
- payload 上限：TradingChart 校验 count ≤300（配合 warmup），避免 5000×11 序列。
- getOHLCV 扩展用可空字段 `{candles, indicators?}`，dashboard 消费方零改动通过 tsc。

**架构评审核心权衡确认**：后端算指标牺牲参数可配性/payload 便宜性，但换来与 AI/ML 链路一致（context_builder/ml features 已消费 indicators）+ pandas 单测 + 前端零计算——对本代码库是更优解，支持维持。真正风险在 warmup、位移、当日对齐三个边界。

**批准条件（架构评审）**：① /day-range 当日对齐判定+三测试态；② Phase 1 写死 Ichimoku 标准公式+位移+参考值断言；③ Phase 3 明确 warmup 取数 + RSI/MACD scaleMargins 定位；④ Phase 2 明确指标参数策略（集中常量+可选覆盖）；⑤ Phase 4 前与用户确认图表布局；⑥ 计划 i18n 改为 zh/en。

## 验证方式
- 后端：`cd backend && .venv/bin/python -m pytest tests/ -q --no-cov`（496 个不回归）+ 新增 `test_indicators.py`。
- 前端：`cd frontend && npx tsc --noEmit`。
- 手动：交易页切品种看图表联动、指标 pane 渲染、当日高低显示。
