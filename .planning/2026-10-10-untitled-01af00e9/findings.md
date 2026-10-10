# Findings — 历史记录字段补全

## 需求原文
> 历史记录的功能，开仓价格和平仓价是一个价格，增加平仓时间、止损、止盈等字段。

## 根因链（完整定位）

### 1. Bridge `/history`（mt5_bridge/main.py:845-877）
- 只调用 `mt5.history_deals_get()`，且**只保留 `entry==1`（平仓 deal）**
- 返回字段：`ticket(position_id)`、`price`（**平仓价**）、`time`（**平仓时间**）、`profit`、`lot`、`comment`
- **缺失**：`open_price`、`open_time`、`sl`、`tp` —— 这些字段在 MT5 `history_orders_get()` 的订单对象里

### 2. 后端合并行（backend/app/api/routes/history.py:158-177）
```python
"open_price": deal.get("open_price") or deal.get("price", 0),  # Bridge 无 open_price → 平仓价
"close_price": deal.get("price", 0),
"sl": 0,          # 硬编码
"tp": 0,          # 硬编码
"open_time": deal_time.isoformat(),    # = 平仓时间
"close_time": deal_time.isoformat(),  # = 平仓时间
```
→ 表现为「开仓价=平仓价」「开仓时间=平仓时间」「SL/TP 恒为 0」

### 3. 前端（frontend/app/history/page.tsx）
- 表格列：时间（用 `open_time`）/品种/类型/手数/开仓价/平仓价/盈亏/策略/原因/AI
- **无平仓时间列、无 SL 列、无 TP 列**
- CSV 导出也缺平仓时间/SL/TP

## 引擎 DB 路径（对照：引擎记账是正确的）
- `engine.py sync_positions → _handle_closed_trades`（1450-1524）：从 MT5 history 取平仓价/时间/盈亏，更新 Trade 表的 `close_price`/`close_time`/`profit`
- **引擎记账的 Trade 行有真实开仓价/开仓时间/SL/TP**（下单时写入）
- 所以 DB 通道（`source: bot`）数据是完整的；**只有 MT5 合并通道（`source: mt5`，手动单/未入库单）字段缺失**

## 数据来源（MT5 Python API 事实）
| 数据 | 来源对象 | 字段 |
|------|---------|------|
| 开仓价 | `history_orders_get()` order | `price_open` |
| 开仓时间 | order | `time_setup`（挂单）/`time_done`（成交） |
| SL/TP | order | `sl` / `tp` |
| 手数 | order/deal | `volume_initial` / `volume` |
| 平仓价 | `history_deals_get()` deal | `price` |
| 平仓时间 | deal | `time` |
| 盈亏 | deal | `profit`（+ commission/swap 需并入净额） |
| 配对键 | **仅 deal 有** | `position_id` |

> **重要修正**：`MqlTradeOrder` **没有** `position_id` 字段（只有 `MqlTradeDeal` 有）。因此配对必须**以成交为锚**：开仓成交（entry==0）提供开仓价/时间/方向 + `order` 字段关联创建它的订单（`deal.order → order.ticket` 匹配 SL/TP）。最初在 order 侧读 `position_id` 的实现生产恒空（`getattr` 恒 None 全跳过），已被 deal 锚定重写修复（见 progress.md）。

## 部分平仓场景
- 同一 `position_id` 有**多条**平仓 deal（`entry==1`）
- 盈亏需按 position_id 聚合（净额 = sum(profit+commission+swap)）
- 平仓价/时间取**最后一条** deal（最终平仓）
- 开仓价/时间/SL/TP 从订单取（`deal.order → order.ticket` 匹配；匹配不到回落开仓成交，不丢行）

## 现有测试现状
- `mt5_bridge/tests/`：conftest.py / test_pending_orders.py / test_switch_account.py —— **无 history 测试**
- `backend/tests/integration/test_api_history.py`：4 个冒烟测试（get_trades/get_trades_empty/get_daily_pnl/get_performance），mock manager 为 None 时走 DB 通道，**未覆盖 MT5 合并通道字段**
- 后端合并通道走 `engine.connector.get_history()`（真实 HTTP 调桥），单测难 mock，倾向在 Bridge 层加单测 + 后端做字段契约校验

## 后端关联代码
- `parse_bridge_time_to_naive_utc()`（app/services/discipline.py）—— bridge 时间统一转 naive UTC
- `CircuitBreaker.net_pnl(d)`（app/risk/circuit_breaker.py）—— 净盈亏聚合已有实现，可复用语义
- 前端 `toDate()`（lib/format.ts）+ Asia/Shanghai 显示约定
