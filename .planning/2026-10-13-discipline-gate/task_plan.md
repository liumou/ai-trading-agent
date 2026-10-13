# Discipline gate 方向切换拦截误判修复

- **计划 ID**: 2026-10-13-discipline-gate
- **日期**: 2026-10-13
- **状态**: complete

## Goal

修复手动单被 `Discipline gate: Second direction switch today on GOLD is rejected — one direction per day` 误拦截的问题。向用户说明该拦截的真实含义，并修复导致误判的双重缺陷。

## 拦截含义说明（给用户）

这条拦截 = 交易纪律门禁的「**一日一个方向**」规则：系统要求同一品种在同一个外汇日（22:00 UTC 日界）内不要频繁多空反手。
- `one direction per day`：当日只允许一个方向（BUY 或 SELL），反手切换被限制
- `already flipped 2 time(s) today incl. this attempt`：本次是当天第 2 次方向切换（被拒绝的就是这次）
- 目的：防止用户在亏损后立刻反手（FOMO 追涨杀跌），强制冷静

## 根因（双重缺陷）

### 缺陷 1 — flip key 跨通道共享（主根因，已复现）
`discipline:flip:{account}:{symbol}` key **不含 channel 维度**。三通道（manual/engine/ai）共用：
- `record_order_opened_discipline`（engine.py:1212 / manual_order_gate.py:553 / broker.py:166）每次成交都覆写**同一个** flip key
- `check_discipline_gate`（discipline_gate.py:232）也读同一个 key
- **后果**：引擎/AI 今天 GOLD BUY→SELL 反手 1 次 → flip_today=1 → 用户手动再开 BUY 被判为「第 2 次反手」→ FLIP_REJECT
- 用户的手动交易纪律被引擎/AI 自动交易行为惩罚

**复现证据**：
```
引擎 BUY → 引擎 SELL → flip key = SELL|...|1|1|...
手动 BUY 检查 → FLIP_REJECT "Second direction switch today..."
```

### 缺陷 2 — flip 记账原始字符串比较（已复现）
`record_order_opened_discipline` 写入 `audit.order_type`（挂单可能是 `BUY_LIMIT`），比较 `prev_dir == direction` 用**原始字符串**（discipline_gate.py:479）。
而 `check_discipline_gate` 判定方向用 `_direction_of()` 归一化（`BUY_LIMIT.split('_')[0]='BUY'`）。
- **后果**：同通道内市价 `BUY` → 挂单 `BUY_LIMIT`（同方向），`"BUY" != "BUY_LIMIT"` → 被误判为方向不同 → flip_today 虚增 → 后续反手被提前误判为「第 2 次」

**复现证据**：
```
manual BUY → manual BUY_LIMIT → flip key = BUY_LIMIT|...|1|1  (flip_today 虚增为 1)
manual SELL 检查 → FLIP_REJECT "Second direction switch today..."
```

## Phase 1 — 修复缺陷 1：flip key 按 channel 隔离

- [x] 1.1 `check_discipline_gate`：flip key 改为 `discipline:flip:{account}:{symbol}:{channel}`；向后兼容读旧无 channel key（迁移期）
- [x] 1.2 `record_order_opened_discipline`：写入同 channel 维度的 flip key
- [x] 1.3 确认 flip key 的 expire/TTL 逻辑不变（7d TTL 保留）
- **Status:** complete

## Phase 2 — 修复缺陷 2：flip 记账方向归一化

- [x] 2.1 `record_order_opened_discipline`：比较用 `_direction_of()` 归一化（挂单 BUY_LIMIT == 市价 BUY）
- [x] 2.2 写 key 时存归一化方向（保持一致性与比较一致）
- **Status:** complete

## Phase 3 — 回归测试

- [x] 3.1 新增跨通道隔离测试（引擎反手不影响手动通道 → test_channel_isolation）
- [x] 3.2 新增同通道挂单/市价同向测试（BUY → BUY_LIMIT 不虚增 flip_today → test_pending_same_direction_not_penalized）
- [x] 3.3 新增旧格式 key 回退兼容测试（test_old_format_flip_key_fallback）
- [x] 3.4 运行现有 discipline 全部测试，确保不破坏既有行为（23 passed）
- **Status:** complete

## Phase 4 — 验证与收尾

- [x] 4.1 全部相关测试通过（相关模块 73 passed；全量 1302 passed，10 failed 全为 test_multi_agent 既有环境性失败，与本次改动无关）
- [x] 4.2 ruff lint 通过（无新引入错误，剩余 8 为既有 E731）
- [x] 4.3 向用户提交修复说明
- **Status:** complete

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| flip key 按 channel 隔离（manual/engine/ai 独立） | 方向翻转纪律应针对同一通道行为，跨通道共享是误判根源 |
| 保留旧无 channel key 的向后兼容读取 | 生产已有数据，避免迁移期误判 |
| 比较方向用 `_direction_of()` 归一化 | 挂单 BUY_LIMIT 与市价 BUY 同方向，字符串比较是缺陷 |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| fakeredis 同步版不能用 | 1 | 用 fakeredis.aioredis.FakeRedis()（测试同款） |

## Next Step

Phase 1：改 flip key 按 channel 隔离。
