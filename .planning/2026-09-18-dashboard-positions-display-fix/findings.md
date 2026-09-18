# Findings — 仪表盘持仓显示叠加

## 问题现象

仪表盘"未平仓持仓"卡片中，持仓数量每次刷新都会叠加，旧数据没有被清空。

## 数据流追踪

### 后端推送链路
1. **MT5 Bridge** (`mt5_bridge/main.py:240-263`) — `GET /positions` 返回持仓，`symbol` 字段 = **MT5 平台内的原始券商符号**（如 `XAUUSD` / `GOLDmicro` / `OILCash` 等），非引擎符号。
2. **`engine.sync_positions()`** (`backend/app/bot/engine.py:1196-1239`):
   - 实盘: `positions = await self.executor.get_open_positions(self.symbol)`
   - paper: `positions = await self._sync_paper_positions()`
   - 推送: `await self._push_event("position_update", {"symbol": self.symbol, "positions": positions})`
3. **`executor.get_open_positions(symbol)`** (`backend/app/mt5/order_executor.py:80-88`):
   - 调用 `connector.get_positions()`（MT5 全量持仓）
   - 用 `to_broker_alias(symbol)` 过滤，保留 `p["symbol"] in (symbol, broker_alias)` 的持仓
   - **返回的持仓 `symbol` 字段仍是券商符号**（如 `XAUUSD`），不会改写成引擎符号
4. **调度频率**: `scheduler.py:98-105` 每 30 秒运行 `_sync_job`，每个 RUNNING 引擎各推送一次 `position_update`。

### 前端合并逻辑
`frontend/app/dashboard/page.tsx:192-204`:
```typescript
subscribe("position_update", (data) => {
  const d = data as { symbol?: string; positions: typeof positions };
  if (d.positions) {
    // Each engine pushes only its own symbol's positions — merge, don't replace
    const sym = d.symbol || (d.positions.length > 0 ? d.positions[0].symbol : null);
    if (sym) {
      setPositions([
        ...useBotStore.getState().positions.filter((p) => p.symbol !== sym),
        ...d.positions,
      ]);
    }
  }
});
```

## 根因分析

### 根因 A（主因，实盘模式）：`symbol` 别名不匹配导致旧持仓永不清理
- 后端推送 `d.symbol` = 引擎符号（`GOLD`）
- 实盘持仓对象的 `p.symbol` = **券商符号**（`XAUUSD` 或 `GOLDmicro` 等 DB 别名）
- 前端过滤 `p.symbol !== sym` → 当 `p.symbol`（券商名）≠ `d.symbol`（引擎名）时，旧持仓**不会被移除**
- 每 30 秒一次推送 → 同名（同 ticket）持仓被反复追加，虽然 React key 相同，但数组 `positions` 长度持续增长 → 表格行数叠加

**触发条件**：实盘模式（非 paper）+ 配置了 `broker_alias` 别名（或引擎符号 ≠ 券商符号）。

### 根因 B（次要）：`_sync_paper_positions` 用引擎符号，paper 模式不叠加
- paper 持仓 `_create_paper_order` (`engine.py:1140-1155`) 里 `symbol: self.symbol`（引擎符号）
- 推送 `d.symbol` 也 = `self.symbol` → 一致 → paper 模式过滤正确
- 因此用户只在**实盘模式**看到叠加

### 根因 C（潜在竞态）：REST 全量替换 vs WS 增量合并竞态
- `fetchData()` (page.tsx:129) 用 `setPositions(posRes.data.positions)` **全量替换** —— 正确
- WS `position_update` 用**合并** —— 在别名不匹配时叠加
- 二者交替执行：WS 叠加把错误状态带进来，下一次 REST 会清掉，但 WS 每 30s 又叠加回去 → 表现为"每次数量叠加"持续存在

## 关键代码位置

| 位置 | 作用 |
|------|------|
| `mt5_bridge/main.py:251` | 持仓 `symbol` = `p.symbol`（券商原始名） |
| `backend/app/mt5/order_executor.py:80-88` | `get_open_positions` 按别名过滤，但不改写 symbol 字段 |
| `backend/app/bot/engine.py:1237` | 推送 `position_update`，`symbol` = 引擎符号 |
| `backend/app/bot/scheduler.py:98-105` | 每 30s 同步一次持仓 |
| `frontend/app/dashboard/page.tsx:192-204` | WS 合并逻辑（`p.symbol !== sym` 过滤失效） |
| `frontend/app/dashboard/page.tsx:129` | REST 全量替换（正确路径） |
| `frontend/store/botStore.ts:137` | `setPositions` 直接替换（正确） |
| `frontend/app/dashboard/page.tsx:608` | 表格 `key={p.ticket}` |

## 修复方向（候选）

1. **前端按 ticket 去重合并**（最小改动、最稳）：`position_update` 里用 ticket 作为唯一键过滤，而不是 symbol：
   ```typescript
   const incoming = d.positions;
   setPositions([
     ...useBotStore.getState().positions.filter((p) => !incoming.some((np) => np.ticket === p.ticket)),
     ...incoming,
   ]);
   ```
   这样即使 symbol 别名不一致，只要 ticket 相同就不会重复叠加。

2. **后端把持仓 symbol 归一化为引擎符号**（根治别名问题）：`get_open_positions` 返回前把每个持仓的 `symbol` 改写为引擎符号（或在推送 `position_update` 前规范化）。这会影响表格显示（显示引擎符号还是券商符号需斟酌），改动面较大。

3. **前端直接全量替换**（最简单）：`position_update` 里直接 `setPositions(d.positions)`。但注意后端是**每个引擎单独推送自己的持仓**（多 symbol 场景下各推各的），全量替换会互相覆盖 → **不可行**，除非后端改为推送全量聚合。

推荐：**方案 1（按 ticket 去重）+ 可选加固：方案 2（后端归一化 symbol）**。
