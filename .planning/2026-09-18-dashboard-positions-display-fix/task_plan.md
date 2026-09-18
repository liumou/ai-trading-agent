# Task Plan — 仪表盘持仓显示叠加修复

## Goal

修复仪表盘"未平仓持仓"卡片持仓数量叠加（旧数据不清空）的问题。

## Status

✅ **全部 Phase 完成 — 2026-09-18**。改动：`order_executor.py`（后端归一化）+ `page.tsx`（前端 ticket 去重）+ 新增 6 个单测。未提交 git（等待用户确认）。

## 根因（已确认）

**实盘模式**下，MT5 Bridge 返回的持仓 `symbol` 是**券商原始名**（如 `GOLD_`/`XAUUSD`/`GOLDmicro`），而前端 `position_update` 合并逻辑用**引擎符号**（`GOLD`）做 `p.symbol !== sym` 过滤：

- `d.symbol` = `GOLD`（引擎符号，来自后端推送）
- 持仓 `p.symbol` = `GOLD_`（券商名，来自 MT5）
- `GOLD_ !== GOLD` → 旧持仓永不被过滤 → 每 30 秒推送一次就叠加一份

**佐证**：仓库在 history/analytics/news/scheduler/correlation 都使用 `get_canonical_symbol()` 做券商名→规范名归一化，唯独持仓推送链路（后端 `position_update` + 前端合并）遗漏。

**Paper 模式不触发**：paper 持仓的 `symbol` 直接用引擎符号 `self.symbol`，与推送的 `d.symbol` 一致，过滤正确。

## 修复方案（推荐：双保险，后端归一化 + 前端 ticket 去重）

### 方案 A（根治，后端归一化持仓 symbol）

在 `backend/app/mt5/order_executor.py:get_open_positions()` 返回前，把每个持仓的 `symbol` 字段用 `get_canonical_symbol()` 归一化为规范名：

```python
async def get_open_positions(self, symbol: str | None = None) -> list[dict]:
    result = await self.connector.get_positions()
    if not result.get("success"):
        return []
    positions = result.get("data", [])
    if symbol:
        broker = to_broker_alias(symbol)
        positions = [p for p in positions if p.get("symbol") in (symbol, broker)]
    # 归一化 symbol 为规范名（券商名 → 引擎名），与 history/analytics 口径一致
    for p in positions:
        p["symbol"] = get_canonical_symbol(p.get("symbol") or "")
    return positions
```

**影响**：
- `position_update` 推送的持仓 symbol 变成规范名，前端过滤 `p.symbol !== sym` 立即正确
- REST `GET /positions` 同样返回规范名 → 前端表格显示统一为规范名（更友好）
- 后端 `close_position`（按 ticket）不受影响
- 与现有 `_deal_matches_symbol` 归一化口径一致

### 方案 B（前端加固，ticket 去重，防御任意别名不一致）

在 `frontend/app/dashboard/page.tsx:192-204` 的 `position_update` 处理中，改用 **ticket 唯一键去重**：

```typescript
subscribe("position_update", (data) => {
  const d = data as { symbol?: string; positions: typeof positions };
  if (d.positions) {
    // 按 ticket 去重合并：同一持仓（ticket）只保留最新一份
    setPositions([
      ...useBotStore.getState().positions.filter(
        (p) => !d.positions.some((np) => np.ticket === p.ticket)
      ),
      ...d.positions,
    ]);
  }
});
```

**影响**：
- 即使 symbol 别名仍不一致（方案 A 未部署或遗漏），只要 ticket 相同就不重复
- 若未来有同 ticket 跨 symbol 的极端情况也不会叠加
- 不依赖 symbol 字段的规范一致性，更健壮

## 建议

**A + B 一起实施**（双保险）。A 根治数据源，B 防御前端。若只想最小改动，方案 B 单用已能消除叠加。

## 批准状态

✅ **2026-09-18 用户批准 A+B 双保险方案**

### Phase 1 — 后端归一化（方案 A）
**Status:** complete

- `order_executor.py` 导入 `get_canonical_symbol`，`get_open_positions` 返回前归一化每个持仓 symbol
- 新增 `tests/unit/test_order_executor_normalization.py`（6 个用例，全部通过）

### Phase 2 — 前端 ticket 去重（方案 B）
**Status:** complete

- `page.tsx:192-205` `position_update` 改为按 ticket 去重合并
- 前端 `tsc --noEmit` 退出码 0
- Node 模拟验证：连续 3 次推送数量稳定不叠加

### Phase 3 — 测试验证
**Status:** complete

- ✅ 新增 6 个归一化单测通过
- ✅ 符号相关回归 47 个通过
- ✅ 前端 tsc 通过
- ✅ 完整后端测试套件：**841 通过 / 8 失败**
- ⚠️ 8 个失败为**既有失败**（`test_multi_agent.py` 模型配置环境差异 + `test_ml_barrier_validation.py` 模块加载），用 `git stash` 验证改动前同样失败，与本次持仓修复**无关**

## 修改文件

| 文件 | 修改 |
|------|------|
| `backend/app/mt5/order_executor.py` | `get_open_positions` 归一化 symbol（方案 A） |
| `frontend/app/dashboard/page.tsx` | `position_update` 改为 ticket 去重（方案 B） |

## 测试计划

1. 后端单测：`get_open_positions` 归一化行为（新增测试）
2. 前端 tsc 类型检查
3. 现有 496 测试回归（`--no-cov`）

## 验证

- 实盘模式：连续 2+ 次 30s `position_update` 后持仓行数不增长
- paper 模式：回归不破坏
- 表格 symbol 列显示规范名（GOLD/OILCash/BTCUSD/USDJPY）
