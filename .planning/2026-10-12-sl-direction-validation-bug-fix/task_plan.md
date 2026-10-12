# Task Plan: 修改止损方向校验 Bug 修复

## Goal
修复 `modify_position_sltp` 用「开仓价 entry」而非「现价 current_price」做 SL/TP 方向合法性基准的 bug。用户 SELL 单（entry 4178.08）想设 SL=4175 锁盈被误拒。

## Next Step
Phase 2: 写失败测试（SELL 盈利仓收紧 SL 被误拒的复现用例）

## Current Phase
Phase 1 (根因调查 — 已完成)

## Phases

### Phase 1: 根因调查
- [x] 定位错误来源：`manual_order_gate.py:631-634` 方向校验（非 broker 返回）
- [x] 确认基准错误：用 entry（开仓价）而非 current_price（现价）
- [x] 确认影响：所有盈利仓（价格越过开仓价）收紧/移动 SL 被误拒
- [x] 确认 get_positions 返回含 current_price 字段（mt5_bridge/main.py:489）
- **Status:** complete

### Phase 2: 失败测试（RED）
- [x] 写 SELL 盈利仓收紧 SL 用例：entry=4178.08, current_price=4168.0, sl=4175.0 → 放行
- [x] 写 SELL SL 低于现价（无保护）→ 拒绝
- [x] 写 BUY 盈利仓收紧 SL 用例：sl 高于 entry 但低于现价 → 放行
- [x] 写 current_price 缺失回退 entry 用例
- [x] 跑测试确认失败（3 个新增用例复现误拒）
- **Status:** complete

### Phase 3: 实现修复（GREEN）
- [x] 修改 `modify_position_sltp`：方向校验基准从 entry → current_price
- [x] 基准：SELL SL > 现价、BUY SL < 现价（TP 相反），缺失回退 entry
- [x] 漂移预算锚点逻辑（仍以 entry 为锚）保持不变
- [x] 跑测试确认通过（SLTP 9 用例全绿）
- **Status:** complete

### Phase 4: 验证
- [x] test_manual_order_gate.py 全绿（28）+ test_position_close + test_guardrails 共 78 通过
- [x] 改动本身 ruff clean（既有 11 个错误非本次引入）
- [x] 回归检查：既有 BUY 方向用例语义仍成立（无 current_price 回退 entry）
- **Status:** complete

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 方向校验基准改为现价 current_price | SL/TP 方向合法性的本质是与市场现价的关系，而非开仓价；开仓价只用于距离/锚点 |
| SELL SL 用 bid、BUY SL 用 ask | MT5 成交价语义：SELL 平仓价是 bid、BUY 是 ask |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| SL 4175.0 must be above entry 4178.08 for SELL | 1 | 定位到 manual_order_gate.py:632 方向校验用 entry 基准误伤盈利仓 |
