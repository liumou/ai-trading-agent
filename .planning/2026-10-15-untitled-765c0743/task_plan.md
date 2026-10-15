# Task Plan: 首页增加当日回撤/当周回撤/当月回撤数据及重置

## Goal
首页增加「当日回撤、当周回撤、当月回撤」三张数据卡（基于账户余额 vs 各周期峰值），并支持分别重置各周期峰值。

## Next Step
全部完成。已实现并验证（后端测试 + 前端 build 通过）。

## Current Phase
Phase 5 (Delivery)

## Phases

### Phase 1: Requirements & Discovery
- [x] 理解需求：当日回撤 / 当周回撤 / 当月回撤 + 可重置（已澄清「回测」=「回撤」笔误）
- [x] 确认口径：账户余额（当前余额 vs 周期峰值，与现有 drawdown_pct 同口径）
- [x] 盘点现状：首页 6 张卡、get_account / reset-peak / CircuitBreaker 峰值逻辑、discipline 周期键
- [x] 确认周期边界：复用 discipline_day_key / discipline_week_key / discipline_month_key（22:00 UTC 外汇日界）
- **Status:** complete

### Phase 2: Planning & Structure
- [x] 设计后端：CircuitBreaker 周期峰值方法 + get_account 扩展 + reset-peak 端点扩展
- [x] 设计前端：3 张回撤卡 + 重置交互 + i18n 文案
- [x] 确认布局：替换原 Drawdown 卡为 3 张周期卡（用户确认）
- **Status:** complete

### Phase 3: Implementation
- [x] 后端：CircuitBreaker 周期峰值跟踪（daily/weekly/monthly，含周期号 key + TTL）
- [x] 后端：get_account 返回 period_drawdowns
- [x] 后端：reset-peak 端点支持 period 参数（daily/weekly/monthly/all）
- [x] 后端：单元测试（TestPeriodPeakDrawdown，6 用例）
- [x] 前端：api.ts 支持 period 参数 + dashboard 3 张周期回撤卡 + i18n
- **Status:** complete

### Phase 4: Testing & Verification
- [x] 后端 pytest 相关模块通过（test_circuit_breaker 42 pass；test_api_bot 13 pass）
- [x] 前端 tsc + build 通过
- [x] ruff 检查通过
- [x] 全量后端测试无回归（1314 passed / 10 failed，与基线一致的既有 test_multi_agent 环境性失败）
- [x] bot/account/peak 相关测试无回归（120 passed）
- **Status:** complete

### Phase 5: Delivery
- [x] 向用户提交完成说明
- **Status:** complete

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 「回测」是「回撤」笔误 | 用户已确认 |
| 口径用账户余额 | 用户已确认（与现有 drawdown_pct 同口径，含浮亏） |
| 周期边界复用 discipline 周期键（22:00 UTC 外汇日） | 与纪律门禁/周月 PnL 口径一致，避免多套周期定义 |

## Errors Encountered
| Error | Resolution |
|-------|------------|