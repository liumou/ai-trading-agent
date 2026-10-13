# Findings — Discipline gate 方向切换拦截误判

## Requirements

用户反馈手动单被拦截：`Discipline gate: Second direction switch today on GOLD is rejected — one direction per day (already flipped 2 time(s) today incl. this attempt)`，无法明确拦截含义，请求说明并修复。

## 拦截含义（给用户）

「一日一个方向」纪律规则：同一品种同一外汇日（22:00 UTC 日界）内禁止频繁多空反手。
- `one direction per day` = 当日只允许一个方向
- `already flipped 2 time(s)` = 本次是当天第 2 次方向切换（被拒的就是这次）
- 目的：防止亏损后 FOMO 追涨杀跌反手，强制冷静

## 根因（双重缺陷）

### 缺陷 1 — flip key 跨通道共享（主根因）
`discipline:flip:{account}:{symbol}` 无 channel 维度。三通道（manual/engine/ai）共用同一 key：
- 写入：engine.py:1212 / manual_order_gate.py:553 / broker.py:166 都调 `record_order_opened_discipline` 覆写同一 key
- 读取：discipline_gate.py:232
- 后果：引擎/AI 反手 → flip_today=1 → 手动 BUY 被判「第 2 次反手」FLIP_REJECT

**复现**（fakeredis）：引擎 BUY→SELL 后手动 BUY → FLIP_REJECT ✅

### 缺陷 2 — 挂单同向被误判反手
`record_order_opened_discipline` 用原始字符串比较 `prev_dir == direction`（`"BUY" != "BUY_LIMIT"` 判定为方向不同），
但检查端用 `_direction_of()` 归一化。同通道市价 BUY → 挂单 BUY_LIMIT → flip_today 虚增 1。

**复现**（fakeredis）：manual BUY→BUY_LIMIT 后 SELL → FLIP_REJECT（应放行，是第 1 次反手）✅

## 修复方案（已实施）

1. **flip key 按 channel 隔离**：`discipline:flip:{account}:{symbol}:{channel}`
   - 检查端优先读 channel key，无则回退读旧 key（迁移期兼容）
   - 写入端只写 channel key
2. **方向归一化**：写入/比较都用 `_direction_of()`（BUY_LIMIT/BUY_STOP → BUY）
   - flip_count 连续同向计数，flip_today 当日反手次数（仅真反手 +1）

## 验证

| 场景 | 修复前 | 修复后 |
|------|--------|--------|
| 引擎反手后手动 BUY | FLIP_REJECT（误判） | 放行 ✅ |
| 手动 BUY→BUY_LIMIT 后 SELL | FLIP_REJECT（误判） | 放行（第1次反手）✅ |
| 手动 SELL 后 BUY（真反手） | FLIP_REJECT | FLIP_REJECT（保留）✅ |
| 旧格式 key 回退 | - | FLIP_REJECT（兼容）✅ |

## Key Files

- `backend/app/services/discipline_gate.py` — 门禁主逻辑（检查 + 记账）
- `backend/tests/unit/test_discipline_gate.py` — flip key 更新为新格式
- `backend/tests/unit/test_review_fixes.py` — 新增 3 个隔离/兼容/同向测试
