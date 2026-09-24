# Progress — 手动交易页品种蜡烛图与指标展示

## 会话日志

### 2026-09-24 调研阶段
- 创建规划目录 `2026-09-24-untitled-9140b96c`。
- 核实现状：交易页无图表；PriceChart 已有蜡烛图+EMA20/50；lightweight-charts v5.1.0 支持多 pane/Histogram/Area；后端有 `GET /ohlcv` 与 `GET /tick`，指标库缺 SMA/MACD/Ichimoku；无当日高低端点。
- 关键决策：指标后端计算（/ohlcv 附加 indicators 字段）、当日高低走新 `/day-range` 端点、新建前端 TradingChart 组件避免 dashboard 回归。
- 调研结论已写入 `findings.md`。
- 规划代理完成计划起草，已定稿写入 `task_plan.md`（5 个 Phase + 6 项关键决策）。
- 计划已呈交用户审批；用户要求**多路评审并迭代修改计划**后再批准。
- **四路评审完成**（2026-09-24）：
  - 指标评审（python-reviewer）：阻塞 B1（Ichimoku 位移方向）+ 重要 I1/I3（RSI/ATR/ADX Wilder 平滑）+ M2（先算再 round）。
  - 前端评审（react-reviewer）：阻塞 B1（Ichimoku 双色云无法用 Area 实现，需折中）+ B2（位移/预热 NaN 必须 WhitespaceData）+ H1（v5 原生多 pane addPane/paneIndex）+ H2（attributionLogo:false）+ H3（tick 不更新子图）+ H4（布局重构/组件拆分）+ H5（dynamic ssr:false）。
  - 后端评审（fastapi-reviewer）：无阻塞；High1（shift 符号）+ High2（RSI 不改现有，新增 rsi_wilder）+ High3（day-range 空数据返回 null 不 500）+ Medium4-6（先算再 round/indicators 开关/性能可接受）。
  - 架构评审（architect）：无阻塞；A1（day-range 当日对齐校验复用 sessions.py）+ A2（Ichimoku 公式/位移参考值断言）+ A3（warmup count）+ A4（指标参数策略集中常量）+ A5（布局/多 pane scaleMargins）+ i18n 事实修正（仅 zh/en，无 th）。
- **已迭代为 v2 计划**写入 `task_plan.md`（Decisions 10 项 + Phases 1-5 + 评审汇总/采纳/驳回）。
- 用户批准 v2 计划，开始实施。

## 实施记录（2026-09-24）

### Phase 1 完成 — 后端指标计算
- `indicators.py` 新增 `sma`/`rsi_wilder`/`macd`/`ichimoku`（评审锁定公式：Ichimoku senkou shift(+26)/chikou shift(-26)；RSI 用 Wilder alpha=1/14）。
- `test_indicators.py` 新增 4 个测试类，**37 passed**。

### Phase 2 完成 — 后端 API 扩展
- `constants.py` 新增 `INDICATOR_*` 集中常量（决策 8）。
- `market_data.py`：`/ohlcv` 附加 `indicators`（原始 df 先算后 round、NaN→None、`indicators`/`indicator_params` 参数）+ 新增 `/day-range`（D1 最新 K + `is_current` 自然日判定，空数据返回 null）。
- `is_current_day`：评审初稿用 sessions.reset_hour，测试暴露语义混淆，实测定稿为**自然日 UTC 判定**（更贴近"当日"直觉，且兼容两种经纪商日界模型）。
- 新增 `test_market_data_indicators.py`（13 passed：indicators 对齐、NaN→None、indicators=false、day-range 三态、is_current_day）。

### Phase 3 完成 — 前端 TradingChart 组件
- 新建 `TradingChart.tsx`：v5 原生多 pane（主图 + RSI + MACD），`attributionLogo:false` 去水印，WhitespaceData 归一化（NaN→{time}），Ichimoku 双 Line + 淡 Area 云（B1 妥协），count=280 预热截取。
- `api.ts` 扩展 `getOHLCV`（indicators 参数）+ `getDayRange` + `ChartCandle/ChartIndicatorRow/DayRange` 类型。
- **tsc 零错误**。

### Phase 4 完成 — 交易页接入
- `trading/page.tsx`：动态加载 TradingChart（ssr:false）、`timeframe` state（默认承接品种 timeframe）、周期切换（TimeframeSelector）、当日高低徽标（`is_current=false` 标注"上一交易日"）、图表卡全宽独立行。
- **tsc 零错误 + npm run build 成功**（25 页面，/trading 动态路由）。

### Phase 5 完成 — 收尾验证
- i18n：charts.json + trading.json（zh/en）补齐指标与当日高低文案。
- `git diff` 确认 **PriceChart.tsx / dashboard 零改动**；指标+市场数据测试 64 passed。
- **eslint 0 错误 0 警告 + tsc 零错误 + npm run build 成功**。
- **后端全量回归**：999 passed / 9 failed——9 个失败与实施前完全一致（test_multi_agent 7 个模型 ID 环境问题 + test_backtest_consistency/test_ml_barrier_validation 2 个模块加载顺序 flaky），**零新增失败**，与本次改动无交集。
- 顺手清理 `trading/page.tsx` 中既有的未使用 `api` import。
- 移除本地无法运行的 ruff（CI 会跑）。
- **遗留**：MT5 bridge 在线抽查 D1 休市行为（residual risk，需真实 VPS 环境）。

## 完成总结

全部 5 个 Phase 完成并验证。改动 11 个文件（后端 4 + 前端 7），新建 2 个组件/测试文件。交付路径见最终报告。