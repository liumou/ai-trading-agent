# Findings & Decisions — 汇率显示精度修复

## Requirements

用户反馈：欧元、英镑等汇率显示只有 2 位小数点，至少应显示 4 位。

## Research Findings

### 系统已有 `price_decimals` 机制
- 后端：`config.py SYMBOL_PROFILES`（静态 4 品种：GOLD=2, OILCash=2, BTCUSD=2, USDJPY=3）+ DB `symbol_configs` 表（`db/models.py:469` `price_decimals` 默认 2）
- 后端 API：`/api/market-data/symbols` 返回 `price_decimals`（`market_data.py:261`）
- 前端：`SymbolConfig.price_decimals`（`lib/api.ts:536`）、botStore `SymbolInfo.price_decimals`（`store/botStore.ts:53`）
- dashboard/trading 顶部报价已用动态精度：`activeTick.bid.toFixed(priceDecimals)`（dashboard:287, trading:442）

### 硬编码 `toFixed(2)` 的价格显示点（本次要修）
1. `components/trading/PositionsTable.tsx:65-68` — open_price / current_price / sl / tp
2. `app/dashboard/page.tsx:625-630` — open_price / sl / tp
3. `app/trading/page.tsx:756-760` — price_open / sl / tp（挂单表）
4. `app/history/page.tsx:420-423` — open_price / close_price / sl / tp（历史表）

### 数据流
- 持仓：MT5 Bridge `main.py:488-491` → `order_executor.get_open_positions`（`mt5/order_executor.py:91`）透传 → `/api/positions` → 前端 `TradingPosition`
- 挂单：`/api/trading/orders` → 前端 `PendingOrder`（`lib/api.ts:186`）
- 历史：`/api/trading/history` 或类似 → 前端 trade 对象（`history/page.tsx:39`）

### 关键设计点
- 后端持仓 API 当前**不透传 price_decimals**，需在前端按 symbol 查 `SymbolConfig.price_decimals`（前端已有 `symbols` 列表，含 price_decimals）
- profit 是货币值，保持 `toFixed(2)` 正确，不动

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| 前端按 symbol 从 `symbols` 列表查 price_decimals，而非后端透传 | 前端 dashboard/trading 已有 symbols 列表含 price_decimals；改动最小，不触碰后端 API 契约 |
| history 页同样按 symbol 映射 | 历史 trade 来自多个 symbol，前端映射最简洁 |
| 用户确认「前端按 symbol 查 price_decimals」方案 (2026-10-13) | 已选 |

## Implementation Notes

- PositionsTable 增加 `priceDecimalsBySymbol` prop（Record<symbol, number>），默认空对象 fallback 2
- dashboard 持仓区：`symbols.find(s => s.symbol === p.symbol)?.price_decimals ?? 2`
- trading 挂单表：`symbols.find(s => s.symbol === o.symbol)?.price_decimals ?? 2`
- history 页：扩展 symbols state 类型为含 price_decimals（getSymbols 已返回），表格用 `symbolPriceDecimals[t.symbol] ?? 2`

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| dashboard 持仓区与 PositionsTable 重复渲染持仓（dashboard 自建 Table + trading 用组件） | 两处都要修 |

## Resources

- `backend/app/config.py` SYMBOL_PROFILES
- `backend/app/db/models.py:469` symbol_configs.price_decimals
- `frontend/components/trading/PositionsTable.tsx`
- `frontend/app/dashboard/page.tsx:620`
- `frontend/app/trading/page.tsx:756`
- `frontend/app/history/page.tsx:420`
