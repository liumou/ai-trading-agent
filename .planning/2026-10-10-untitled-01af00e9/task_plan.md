# Task Plan: 历史记录字段补全（平仓时间/止损/止盈/开仓价）

## Goal

修复历史记录页：MT5 通道合并行「开仓价=平仓价」的问题，并增加**平仓时间、止损(SL)、止盈(TP)** 三列显示，与平仓时间数据一并补齐。

## Next Step

Phase 7 完成（审查通过 + 3 个 MEDIUM 修复 + 文档更新）。所有测试通过，无待办。

## Current Phase

全部完成

## Phases

### Phase 1: Requirements & Discovery ✅
- [x] 理解需求：历史记录开仓价=平仓价 bug + 增加平仓时间/止损/止盈字段
- [x] 定位根因链：Bridge `/history` 只返回平仓 deal → 后端合并行开仓价取不到 → 前端只显示开仓时间
- [x] 确认数据来源与字段可用性（见 findings.md）
- [x] 确认测试现状（Bridge 无 history 单测；后端仅 4 个冒烟集成测试）
- **Status:** complete

### Phase 2: 方案设计 ✅
- [x] 设计 Bridge `/history` 关联配对：`history_orders_get` 提供开仓价/时间/SL/TP，`history_deals_get` 提供平仓价/时间/净盈亏
- [x] 确定后端 API 字段契约（open_time 与 close_time 分离、sl/tp 真实值）
- [x] 确定前端展示方案（新增平仓时间/SL/TP 列 + CSV 导出字段）
- **Status:** complete

### Phase 3: Bridge 实现（mt5_bridge/main.py）✅
- [x] 重写 `/history`：orders_get + deals_get 配对，含部分平仓聚合
- [x] 返回 open_price/open_time/sl/tp/close_price/close_time/profit/net_profit 全字段
- [x] Bridge 单元测试（test_history.py 9 用例，全部 46 测试通过）
- [x] **CRITICAL 修复**：`MqlTradeOrder` 无 `position_id` → 改为 **deal 锚定**（entry==0 开仓 deal 提供开仓价/时间/方向，entry==1 平仓 deal 聚合盈亏，SL/TP 经 `deal.order → order.ticket` 从订单取；订单匹配不到回落开仓成交不丢行）
- [x] 符号过滤宽松归一（`_symbol_matches` 去下划线/小写），带下划线别名不误过滤
- **Status:** complete

### Phase 4: 后端适配（backend/app/api/routes/history.py）✅
- [x] MT5 合并行使用新字段，不再 `open_price=price`
- [x] `open_time` 用开仓时间、`close_time` 用平仓时间（含 str/datetime 类型归一）
- [x] sl/tp 真实值（替换硬编码 0）
- [x] 后端集成测试更新（test_api_history.py 5 passed）
- **Status:** complete

### Phase 5: 前端展示（frontend/app/history/page.tsx + 翻译）✅
- [x] 表格增加平仓时间列（Asia/Shanghai）+ 开仓时间列改名
- [x] 表格增加 SL/TP 列（0/缺失显示 —）
- [x] CSV 导出增加开仓时间/平仓时间/SL/TP
- [x] zh/en 翻译新增 thOpenTime/thCloseTime/thSl/thTp
- [x] tsc --noEmit 通过
- **Status:** complete

### Phase 6: 测试与验证 ✅
- [x] ruff check history.py 通过
- [x] Bridge 全量 46 测试通过（含 9 个新 history 测试 + 1 个下划线别名用例）
- [x] 后端 history 集成 6 测试通过（含新增 daily_pnl net_profit 优先用例）
- [x] 后端 manual_order_gate 24 测试通过（net_profit 净额口径）
- [x] discipline/position_close/circuit_breaker 63 测试通过（兼容别名无回归）
- [x] tsc --noEmit 通过
- **Status:** complete

### Phase 7: 交付
- [x] 代码审查（code-reviewer，COMMENT：无 CRITICAL/HIGH）
  - [x] MEDIUM-1 open_price 恢复 `or price` 兜底（混合部署期不显示 0）
  - [x] MEDIUM-2 符号过滤宽松归一（`_symbol_matches`）+ 下划线别名测试
  - [x] MEDIUM-3 analytics/manual_order_gate 毛额改净额优先（net_profit 优先回落 profit）
  - [x] LOW 已知并保留（comment 与最终平仓价段非严格对应，非功能性）
- [x] 更新文档
- **Status:** complete

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Bridge 层做订单-成交配对（orders+deals） | MT5 标准做法：订单含开仓价/SL/TP，成交含平仓价/盈亏，单独任一都无法提供完整字段 |
| 后端不再依赖 Bridge 提供 open_price 兜底 | 彻底修复「开仓价=平仓价」，不靠 `or price` 这种掩盖数据的回退 |
| 前端平仓时间用 Asia/Shanghai | 与现有 open_time 展示时区约定一致（lib/format toDate + Asia/Shanghai） |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| **CRITICAL：Bridge `/history` 生产恒空** | 实现从 `history_orders_get` 的 order 读 `position_id`（`getattr(order, "position_id")`），测试 mock 给 order 伪造了该字段故未暴露 | `MqlTradeOrder` 无 `position_id`（只有 `MqlTradeDeal` 有）。重写为 **deal 锚定**：entry==0 开仓 deal 提供开仓价/时间/方向，entry==1 平仓 deal 聚合盈亏，SL/TP 经 `deal.order → order.ticket` 从订单取。同步修正测试 mock 反映真实字段结构 |
| daily_pnl 测试 daily_pnl=0 | 测试数据用 2026-10-01（历史日期），daily-pnl 只统计当天已平仓成交 | 改用 `datetime.now(timezone.utc)` 动态时间 |

## Notes

- 更新 phase 状态时同步刷新 Next Step。
- 部分平仓（partial close）场景：同一 position_id 多条平仓 deal，需聚合盈亏，价格用最后一条（最终平仓价）。
