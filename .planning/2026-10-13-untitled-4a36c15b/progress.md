# Progress — 汇率显示精度修复

## Session Log

### 2026-10-13 T1 — 调查 + 方案确认
- 用户反馈:欧元、英镑等汇率只显示 2 位小数,至少应显示 4 位
- 定位根因:系统已有 `price_decimals` 动态精度机制(dashboard/trading 顶部报价已用),但 **持仓/挂单/历史表格硬编码 `toFixed(2)`**
- 受影响 4 处:PositionsTable、dashboard 持仓区、trading 挂单表、history 历史表
- 用户确认方案:前端按 symbol 从 symbols 列表查 price_decimals(fallback 2),不动后端 API
- 额外发现:price-alerts 表格 trigger_price 也硬编码 2,一并修复

### 2026-10-13 T2 — 实施(5 个文件全改完)
- `PositionsTable.tsx`:新增 `priceDecimalsBySymbol` prop + `priceDecimalsFor()` 帮助函数,价格列动态精度
- `dashboard/page.tsx`:持仓区按 `p.symbol` 查 symbols 的 price_decimals
- `trading/page.tsx`:挂单表按 `o.symbol` 查精度;挂单修改输入 step 从硬编码 0.01 改 `1/10^opd`
- `history/page.tsx`:symbols state 扩展含 price_decimals,价格列按 symbol 映射
- `price-alerts/page.tsx`:trigger_price 按 symbol 映射

### 2026-10-13 T3 — 验证
- `tsc --noEmit`:多次运行全过
- `npm run build`:生产构建成功,22 个页面全部编译
- eslint:0 errors(19 警告全为既有)
- 后端确认为纯显示层问题:DB Float 存储完整精度,MT5 返回完整 float,无需后端改动

### 2026-10-13 T4 — 图表价格精度修复(用户追加反馈)
- 用户反馈图表里价格仍显示 2 位小数 → 定位 lightweight-charts 默认 priceFormat precision=2
- `PriceChart.tsx`:给 candle/EMA line series 加 `priceFormat:{precision:priceDecimals,minMove}`;移除 calcEMA 硬编码 `Math.round(ema*100)/100`(汇率品种 EMA 精度丢失)
- `TradingChart.tsx`:给 candle/line/cloud series 加 priceFormat;数值条 `fmt` 默认 `priceDecimals`(SMA/EMA 用价格精度,RSI/MACD 保持 2 位)
- 调用方传 priceDecimals:dashboard 2 处 PriceChart + trading 1 处 TradingChart
- tsc + build 全过;eslint 0 errors

## 待办
- [x] 用户确认修复方案
- [x] 5 个前端文件实施完成
- [x] 图表精度修复(T4)
- [x] tsc + build 验证通过
- [ ] 用户 review 后提交(commit + PR)