# Progress — 仪表盘持仓显示叠加修复

## 会话 1 — 2026-09-18

### 完成的工作
- [x] 追踪后端持仓推送链路：`scheduler._sync_job`（30s）→ `engine.sync_positions()` → `_push_event("position_update")`
- [x] 确认 MT5 Bridge `/positions` 返回 `symbol` = 券商原始名（`p.symbol`）
- [x] 确认 `get_open_positions` 用 `to_broker_alias` 过滤但不改写 symbol 字段
- [x] 确认前端 `position_update` 合并逻辑用 `p.symbol !== sym`（引擎符号）过滤 → 券商名持仓永不被清理
- [x] 用仓库既有测试 `test_deal_symbol_normalization.py` 佐证券商名/规范名差异是已知场景，且后端 history/analytics 已用 `get_canonical_symbol` 归一化，持仓推送链路遗漏
- [x] 确认 paper 模式不触发（paper 持仓 symbol = 引擎符号，一致）
- [x] 确认 REST `fetchData` 是全量替换（正确），WS 是增量合并（bug 所在）
- [x] 根因写入 findings.md
- [x] 修复方案写入 task_plan.md

### 关键文件
- `frontend/app/dashboard/page.tsx:192-204` — WS position_update 合并逻辑（bug 点）
- `backend/app/mt5/order_executor.py:80-88` — get_open_positions（未归一化）
- `backend/app/bot/engine.py:1237` — position_update 推送
- `backend/app/bot/scheduler.py:98-105` — 30s 同步
- `mt5_bridge/main.py:251` — 持仓 symbol 券商名

### 待办（等待用户批准计划后）
- [x] 实施方案 A：`get_open_positions` 归一化 symbol
- [x] 实施方案 B：前端 `position_update` 改为 ticket 去重
- [x] 测试：新增后端单测 + 前端 tsc + 回归

## 会话 2 — 2026-09-18（实施 A+B 双保险）

### 用户批准
✅ 批准 A+B 双保险方案。

### 实施完成
- [x] **方案 A**：`backend/app/mt5/order_executor.py` — 导入 `get_canonical_symbol`，`get_open_positions` 返回前归一化每个持仓 symbol
- [x] **方案 B**：`frontend/app/dashboard/page.tsx:192-205` — `position_update` 改为按 ticket 去重合并
- [x] 新增 `tests/unit/test_order_executor_normalization.py`（6 用例）

### 验证结果
- ✅ 新增 6 个归一化单测：全部通过
- ✅ 符号相关回归（test_symbol_resolver / test_deal_symbol_normalization / test_market_data_alias / test_phase_f）：47 通过
- ✅ 前端 `tsc --noEmit`：退出码 0
- ✅ Node 模拟方案 B：连续 3 次推送数量稳定（旧逻辑会叠加）
- ✅ 完整后端套件：841 通过 / 8 失败
- ✅ **8 失败确认为既有失败**（git stash 后同样失败）：
  - `test_multi_agent.py`（6 个）：模型配置环境差异（期望 claude-*，实际 deepseek-v4-flash）+ SDK mock AttributeError
  - `test_ml_barrier_validation.py`（1 个）：模块加载问题
  - 与本次持仓修复无关

### 未提交
改动尚未 git commit（等待用户确认后提交）。
