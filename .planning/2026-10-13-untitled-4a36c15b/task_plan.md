# 汇率显示精度修复

- **计划 ID**: 2026-10-13-untitled-4a36c15b
- **日期**: 2026-10-13
- **状态**: complete

## Goal

修复前端持仓/挂单/历史表格中硬编码 `toFixed(2)` 的价格显示，改用系统已有的 `price_decimals` 动态精度（EUR/GBP 等外汇品种 ≥4 位小数）。

## 调查结论(摘要)

系统已有 `price_decimals` 机制（config.py `SYMBOL_PROFILES` + DB `symbol_configs` + 前端 `SymbolInfo.price_decimals`），
dashboard/trading 顶部报价已用它。但以下表格**硬编码 `toFixed(2)`**，EUR/GBP 报价显示为 2 位小数（精度丢失）：

| 文件 | 位置 | 硬编码字段 |
|------|------|-----------|
| `components/trading/PositionsTable.tsx` | 持仓表 | open_price / current_price / sl / tp → `.toFixed(2)` |
| `app/dashboard/page.tsx` | 持仓区 (≈620) | open_price / sl / tp → `.toFixed(2)` |
| `app/trading/page.tsx` | 挂单表 (≈756) | price_open / sl / tp → `.toFixed(2)` |
| `app/history/page.tsx` | 历史表 (≈420) | open_price / close_price / sl / tp → `.toFixed(2)` |

profit 保留 2 位小数是正确的（货币值），不改。

## Phase 1 — 修复 PositionsTable（trading 页持仓表）

- [x] 1.1 `PositionsTable` 增加 `priceDecimalsBySymbol` prop（前端映射，fallback 2）
- [x] 1.2 价格单元格改用动态精度 `priceDecimalsFor(p.symbol, bySymbol)`
- [x] 1.3 trading 页调用处传 `Object.fromEntries(symbols.map(...))`
- **Status:** complete

## Phase 2 — 修复 dashboard / trading 挂单表

- [x] 2.1 dashboard 持仓区用 `symbols.find(...)?.price_decimals`（按 p.symbol 查）
- [x] 2.2 trading 挂单表用 `symbols.find(...)?.price_decimals`（按 o.symbol 查）
- **Status:** complete

## Phase 3 — 修复 history 历史表

- [x] 3.1 symbols state 扩展为含 `price_decimals`（getSymbols 已返回）
- [x] 3.2 表格价格列改用 `symbolPriceDecimals[t.symbol] ?? 2`
- **Status:** complete

## Phase 4 — 验证与收尾

- [x] 4.1 `tsc --noEmit`(多次运行全过)
- [x] 4.2 `npm run build` 生产构建成功(全部页面编译通过)
- [x] 4.3 eslint 0 errors(19 个警告全为既有,非本次引入)
- **Status:** complete

## Phase 5 — 图表价格精度修复(用户追加反馈)

- [x] 5.1 `PriceChart`:`priceFormat`(candle/EMA series)+ 移除 EMA 硬编码 2 位舍入
- [x] 5.2 `TradingChart`:`priceFormat`(candle/line/cloud series)+ 数值条 `fmt` 默认精度
- [x] 5.3 调用方传 `priceDecimals`:dashboard 两处 PriceChart + trading 一处 TradingChart
- [x] 5.4 tsc + build 验证通过
- **Status:** complete

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| 复用已有 `price_decimals` 体系，不引入新格式函数 | 系统已建立该机制，只需补齐遗漏位置 |
| profit 保持 2 位小数 | 货币值固定 2 位是行业惯例 |

## Errors Encountered

| Error | Resolution |
|-------|------------|
| (暂无) | - |

## Next Step

确认修复策略后，先做 Phase 1（后端位置 API 附 price_decimals + PositionsTable 动态精度）。
