# Findings & Decisions

## Requirements

- 手动交易页（`/trading`）选择品种要做成**下拉选项**（不再是自由文本输入）。
- 需要知道**当前品种的实时价格**。
- 如果当前品种有持仓，需要**刷新持仓数据**。
- 用户确认：持仓区**显示全部持仓、不过滤不排序**，只保证 10s + 操作后刷新。

## Research Findings

（以下均为本仓库代码核实结果，非外部资料；行号为核查时版本）

### 1) 品种选择
- `frontend/app/trading/page.tsx:306-312`：`<input type="text" placeholder="GOLD">`，`onChange` 内 `toUpperCase()`。
- 默认值 effect：`page.tsx:59-65` → `if (!symbol) setSymbol(symbols.length > 0 ? symbols[0].symbol : activeSymbol || "")`（`symbols[0]` 优先于 `activeSymbol`）。
- 兼容下拉组件已存在：`frontend/components/ui/select.tsx`（`@base-ui/react/select`）；既有用法参考 `frontend/app/backtest/page.tsx:293-300`。
- 品种列表来源：`GET /api/market-data/symbols`（`backend/app/api/routes/market_data.py:47-70`），全局预取在 `frontend/components/layout/AppShell.tsx:50-59` → `store.symbols`（`SymbolInfo{symbol, display_name, timeframe, state, price_decimals, max_lot, default_lot}`）。

### 2) 缺陷：`toUpperCase()` 破坏 OILCash
- 规范品种名含混合大小写：`backend/app/config.py:24` `"OILCash"`；迁移 `q7r8s9t0u1v2_add_symbol_configs.py`、`notifications/telegram.py:25`、`news/sources.py:123` 均用 `OILCash`。
- 后端解析是**精确匹配**：`backend/app/services/order_preflight.py:57-86` `_resolve_symbol(strict=True)` → `BotManager.resolve_symbol` → `SYMBOL_PROFILES[symbol].canonical` 或 `self.engines.get(symbol)`（`backend/app/bot/manager.py:89-105`），无大小写归一。
- 结论：现有页面提交 `OILCash` 会变成 `OILCASH` → 拒单 `Symbol 'OILCASH' not found in active engines`。下拉改为送规范名原样后该问题消失。

### 3) 实时价格链路
- WS 单例：`frontend/lib/websocket.ts`（`startWebSocket()` 由 `AppShell.tsx:63-66` 调用，仅建连、无订阅）。
- 推送源：`backend/app/bot/scheduler.py:343-356` `_tick_job` 每 **1s** 遍历**全部引擎**（无 `RUNNING` 过滤）→ `engine._push_event("price_update", tick)`，tick 带 `symbol`。
- 前端唯一订阅方是 dashboard：`frontend/app/dashboard/page.tsx:191` `subscribe("price_update", d => setTick(d))` → store `ticks[symbol]`（`frontend/store/botStore.ts:135-141`）。
- tick 字段：`bid/ask/spread/time`（`mt5_bridge/main.py:278-291`）。
- **不存在单品种 REST 报价端点**：`routes/market_data.py` 只有 `/ohlcv`、`/symbols`。
- 可用后端方法：`MT5Connector.get_tick`（`backend/app/mt5/connector.py:106-107`）；`MarketDataService.get_current_tick(symbol, validate)`（`backend/app/mt5/market_data.py:25-63`，`validate=True` 在 tick 超 30s 或点差 >3×均值时返回 `None`）。
- 展示格式参考：`dashboard/page.tsx:283-297`。

### 4) 持仓链路
- 页面只读 store：`page.tsx:35` `useBotStore(s => s.positions)`；**自身既不请求 `/api/positions` 也不订阅 `position_update`**。
- 数据源：`GET /api/positions`（`backend/app/api/routes/positions.py:16-31`）。底层 `OrderExecutor.get_open_positions`（`backend/app/mt5/order_executor.py:91-104`）返回该账号**全部真实持仓**（含手动 magic），symbol 归一为规范名。
- WS 持仓推送：`engine._push_event("position_update", ...)`（`backend/app/bot/engine.py:1250`），但 `_sync_job` **只对 RUNNING 引擎**每 30s 执行（`scheduler.py:559-565`）→ 品种停跑后必须靠 REST。
- dashboard 合并写法（按 ticket）：`dashboard/page.tsx:192-206`。
- 超时守卫先例：`engine.py:1221-1227`（空但已知持仓非空 → 跳过）。
- 已知限制：账号上存在**没有对应引擎**的品种持仓时，`/api/positions` 两条分支都取不到（本次不处理）。

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| 下拉 value 用规范名原样、展示用 `display_name` | 后端严格精确匹配；展示名对用户更友好 |
| 切品种时重置 price/sl/tp、lot 取该品种 `default_lot` | 避免把上一品种的价格带过去；手数带 `max=max_lot` 约束 |
| REST 快照整体替换 + 空数组连续两次才清空 | 页面展示全部持仓，快照即全量；单次空可能是 bridge 超时 |
| WS `price_update` 为主 + `GET /api/market-data/tick` 兜底 | WS 断线/首屏时页面仍需有价可显示 |
| 展示用 `get_current_tick(validate=False)` | validate=True 在点差异常/陈旧时返回 None，会把价格抹空 |
| 10s 轮询仅页面可见时执行 | 避免后台标签页无谓打 MT5 bridge |

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| （执行期问题与修复见 progress.md） | |

## Resources

- 后端路由：`backend/app/api/routes/{market_data,positions,manual_trading}.py`
- 前端页面/组件：`frontend/app/trading/page.tsx`、`frontend/components/trading/PositionsTable.tsx`、`frontend/components/ui/select.tsx`
- WS/状态：`frontend/lib/websocket.ts`、`frontend/store/botStore.ts`
- 既有计划（本任务前身）：`.planning/2026-09-19-mt5-agent/`
