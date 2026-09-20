# Task Plan — 手动交易页面：品种下拉 + 实时价格 + 持仓刷新

## Goal

`/trading` 手动交易页的品种选择改为下拉（来源=已配置品种），实时展示当前品种 bid/ask/点差，并在切换品种与每次变更操作后刷新持仓数据（显示全部持仓、不过滤不排序）。

## Next Step

无 —— 全部交付与自动验证完成。仅剩人工验收（需 MT5 bridge 运行时）：下拉选 OILCash 下单不被拒、价格 pill 实时刷新且断 WS 30s 后显示「延迟」、每次操作后持仓区刷新。

## Current Phase

Phase 6（Testing & Delivery）

## 已确认决策（用户确认）

| 决策点 | 结论 |
|--------|------|
| 持仓显示范围 | 显示**全部**持仓，不过滤、不排序；仅保证 10s + 操作后刷新 |
| 实时价格兜底 | A 方案：WS `price_update` 为主 + 新增 `GET /api/market-data/tick?symbol=` 兜底（WS 断线时 2s 轮询） |
| 现价填充 SL/TP 按钮 | 不做（用户未要求） |
| 品种输入约束 | 只允许下拉里的已配置规范品种名；**去掉 `toUpperCase()`**（否则 OILCash 被后端 fail-closed 拒绝） |

## Phases

### Phase 0: 规划文件初始化 (complete)
- [x] `init-session.sh "Trading Symbol Dropdown Live Price"` → `.planning/2026-09-20-trading-symbol-dropdown-live-price/`
- [x] `.planning/.active_plan` pin 到本计划；现状核查写入 findings.md
- **Status:** complete

### Phase 1: 代码现状核查 (complete)
- [x] 品种输入是自由文本 `page.tsx:306-312`；默认值逻辑 `page.tsx:59-65`
- [x] 缺陷确认：`page.tsx:120` `symbol.toUpperCase()` vs 规范名 `OILCash`（`config.py:24`）+ `_resolve_symbol` 精确匹配（`order_preflight.py:57-86`）
- [x] tick 链路：`scheduler.py:343-356` 每秒全引擎推 `price_update`；仅 dashboard 订阅（`dashboard/page.tsx:191`）
- [x] 持仓链路：`/api/positions`（`routes/positions.py:16-31`）实时拉取；`position_update` 仅 RUNNING 引擎 30s 推（`scheduler.py:559-565`）
- **Status:** complete

### Phase 2: 品种下拉 (complete)
- [x] `frontend/app/trading/page.tsx` 用 `components/ui/select.tsx`（对齐 backtest 页写法）
- [x] 选项=store.symbols（`display_name` 展示、`value` 规范名）；默认 `activeSymbol` 优先
- [x] 切换品种：清空 price/sl/tp、lot 取 `default_lot`、`step` 依 `price_decimals`、`max` 依 `max_lot`
- [x] 删除 `toUpperCase()`
- **Status:** complete

### Phase 3: 实时价格 (complete)
- [x] 后端 `GET /api/market-data/tick?symbol=`（`routes/market_data.py`，`get_current_tick(validate=False)`）
- [x] 前端 `api.ts` `getTick(symbol)` + `TickQuote` 类型
- [x] 页面 `useWebSocket()` 订阅 `price_update` → `setTick`；PageHeader pill 显示 bid/ask/点差
- [x] 兜底：挂载/切品种拉一次；WS 断开时 2s 轮询；tick 陈旧(>30s) 显示延迟提示
- [x] 单测 `backend/tests/unit/test_market_data_tick.py`
- **Status:** complete

### Phase 4: 持仓刷新 (complete)
- [x] `refreshPositions()` 走 `getPositions()`（无 symbol，全账户快照）
- [x] 空数组守卫：连续两次空才清空（对齐 `engine.py:1221-1227` 超时守卫）
- [x] 触发点：挂载、切品种、10s 定时（仅页面可见）、每个变更操作终态后
- [x] 订阅 `position_update` 按 ticket 合并
- **Status:** complete

### Phase 5: PositionsTable 现价列 + i18n (complete)
- [x] `components/trading/PositionsTable.tsx` 新增 `colCurrent`（渲染已有 `current_price`）
- [x] `frontend/messages/{zh,en}/trading.json` 同步新增键
- **Status:** complete

### Phase 6: 测试与验证 (complete)
- [x] 后端新增单测 + 相关既有单测
- [x] `ruff check app`
- [x] 前端 `npx tsc --noEmit` / `npm run lint` / `npm run build`
- **Status:** complete

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| 持仓用 REST 全量快照整体替换，而非 ticket 合并 | 页面展示全部持仓，快照即完整账户视图；合并会残留已平仓行 |
| 空快照需连续两次才清空 | MT5 bridge 超时也会返回空数组，单次空直接清空会造成持仓“闪没” |
| WS `price_update` 为主、REST 兜底 | WS 每秒全品种推送（无 RUNNING 过滤），但断线时页面需有价可显示 |
| `get_current_tick(validate=False)` 供展示 | validate=True 在点差异常/陈旧时返回 None，会把页面价格抹成空 |
| 删 `toUpperCase()` | 规范名含混合大小写（OILCash），后端严格精确匹配 |

## Errors Encountered

| Error | Resolution |
|-------|------------|
| 见 progress.md 实际记录 | |
