# Progress — 交易图表指标开关 + 指标数值条 + 鼠标滑动联动

## 会话日志

### 2026-09-24 规划阶段
- 创建规划目录 `2026-09-24-untitled-40246450`（init-session.sh）。
- 恢复上一任务计划上下文（`2026-09-24-untitled-9140b96c`，commit 777226c 已完成图表+指标功能）。
- 核实新需求对应现状：
  - `TradingChart.tsx`（350 行）：主图蜡烛+3均线+Ichimoku 线/云 + RSI/MACD 子图 pane；series 存 ref，pane 引用未保存。
  - `api.ts`：`ChartIndicatorRow` 类型齐备（sma55/ema20/ema50/rsi14/macd 全系/ichimoku 全系）。
  - lightweight-charts v5：`SeriesOptionsCommon.visible`（hiding≠deleting）、`addPane/removePane/moveToPane`、`subscribeCrosshairMove` + `MouseEventParams.time/point` 均已查证。
  - trading/page.tsx 图表卡标题栏（L466-496）可放置开关组；i18n 仅 zh/en。
- 调研结论写入 `findings.md`；实施计划写入 `task_plan.md`（Goal + 方案概述 + 10 项 Decisions + 3 Phases）。
- 计划已呈交用户，**用户批准执行**（AskUserQuestion 确认：6 个指标开关 + 数值条范围「均线+RSI+MACD」）。

### 2026-09-24 实施阶段

#### Phase 1 完成 — TradingChart 组件改造
- **series 显隐**：主图均线/Ichimoku 用 `applyOptions({ visible })` 动态显隐，不重建（v5 文档 hiding≠deleting）。
- **pane 动态收起/还原**：RSI/MACD 独立 pane，关闭时 `chart.removeSeries` + `chart.removePane` 销毁，开启时 `chart.addPane` + `pane.addSeries`（显式指定 paneIndex）重建，数据从 rowsRef 重放。修复了原代码 `chart.addSeries` 默认 pane0 的隐患（发现原 RSI/MACD 可能未真正独立子图）。
- **数值条**：`IndicatorValueBar` 子组件，图表上方，显示 SMA55/EMA20/EMA50/RSI/MACD(DIF/DEA/柱)，颜色与图表一致，默认最新 K。
- **crosshair 联动**：`chart.subscribeCrosshairMove` 移入 createChart effect（随图表生命周期订阅/退订，theme 重建重订阅），`param.time` 查 timeIndexRef 行，`param.point===undefined` 回落最新。
- 关键修复：theme 重建后主图 series 显隐状态丢失 → `applyMainSeriesVisibility()` 在 createChart effect 恢复。
- **tsc 零错误**。

#### Phase 2 完成 — 开关 UI + i18n
- trading 页标题栏加 6 个 Switch（SMA55/EMA20/EMA50/RSI/MACD/Ichimoku）+「全部」总开关（开→全开 关→全关），state 受控（默认全开），用 base-ui Switch。
- TradingChart 加 `labels` prop（i18n 数值条标签）；charts.json（zh/en）加 `indicator`/`showAll`/`macdSignal`/`macdHist`。
- **tsc 零错误 + eslint 0 错误 0 警告**。

#### Phase 3 完成 — 全量验证 + 回归 + code-review 修复
- `npm run build` 两次成功（exit 0，/trading 路由正常）。
- git diff 确认 **PriceChart/dashboard 零改动**；改动 5 文件（trading 页 + TradingChart + zh/en charts.json + .active_plan）。
- i18n zh/en key 完全对齐。
- **code-reviewer 审查**（sonnet）：无阻塞；3 重要 + 6 次要，已修复 3 重要 + 2 次要（见 task_plan Errors 表）：
  - 重要-1 数值条高度硬编码 → flex 列布局
  - 重要-2 轮询后 hover 闪烁 → hoverTimeRef 保留 hover time，仅 data 失效才回落
  - 重要-3 主题切换清空数据缓存致子图空窗 → cleanup 保留 rowsRef/timesRef/timeIndexRef
  - 次要-4 time typeof 守卫 / 次要-5 subPanesRef 收窄
  - 次要-1/2/3/6 记为后续可选项（依赖脆性、增量重建、removePane 范围、渲染遍历）
- 修复后 **tsc + eslint 0 错误 + build 成功**。
- **遗留**：用户手动浏览器验证（需登录态；本地后端启用认证未能登录）——开关切换即时显隐、子图收起/还原、数值条联动、鼠标滑动 crosshair。

## 待办
- [x] 用户批准计划
- [x] Phase 1：TradingChart 组件改造
- [x] Phase 2：trading 页开关 UI + i18n
- [x] Phase 3：全量验证（tsc/eslint/build + code-review修复）
- [ ] 用户手动浏览器验证 + 提交（常规流程）