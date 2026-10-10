## Session: 2026-10-10（历史记录字段补全实现）

### Actions Taken
1. 需求理解：历史记录「开仓价=平仓价」bug + 增加平仓时间/止损/止盈字段
2. **根因定位**：Bridge `/history` 只返回平仓 deal（entry==1），无开仓价/时间/SL/TP；后端合并行 `open_price=price` 兜底导致开仓价=平仓价；前端无平仓时间/SL/TP 列
3. **Bridge 重写**（mt5_bridge/main.py）：`history_orders_get` + `history_deals_get` 按 position_id 配对，订单提供开仓价/时间/SL/TP，成交提供平仓价/时间/盈亏；部分平仓聚合净额 + 取最后一条；`net_profit` 净额字段；兼容别名 `price`/`time` 保留（引擎/分析等消费点不破坏）
4. **后端适配**（history.py）：合并行用真实 open_price/open_time/sl/tp/close_time；`open_time` str/datetime 类型归一（发现并修复 `str.isoformat()` 崩溃 bug）；daily_pnl/performance 净额优先
5. **前端**（history/page.tsx + 翻译）：新增平仓时间/SL/TP 列，开仓时间列改名；CSV 导出加字段；zh/en 翻译
6. **测试**：Bridge 新增 test_history.py（8 用例，部分平仓/方向/过滤/兼容别名），后端 test_api_history.py 新增合并通道字段契约测试

### Test Results
| 套件 | 结果 |
|------|------|
| mt5_bridge tests（44，含 8 新 history） | ✅ 全过 |
| 后端 history/alias/circuit_breaker（50） | ✅ 全过 |
| 后端引擎集成（17） | ✅ 全过 |
| discipline/position_close（22） | ✅ 全过 |
| ruff check history.py | ✅ 0 error |
| tsc --noEmit | ✅ 通过 |
| npm run build | ✅ 成功 |

### Errors Encountered（调试过程）
| Error | Resolution |
|-------|------------|
| 后端 MT5 合并行 `AttributeError: 'str' object has no attribute 'isoformat'` | Bridge open_time 已是 ISO 字符串，后端 `open_time.isoformat()` 崩溃 → 类型判断转 str（真实 bug，测试暴露） |
| 集成测试 MT5 行为空 | 测试 deal 时间在 365 天外被 cutoff 过滤 → 改为近期时间 |
| 集成测试 patch 未生效 | `get_global_manager` 在函数内 import，需 patch 源头模块 `app.bot.manager` |

## Session: 2026-10-10（CRITICAL 修复 + 代码审查项处理）

### Actions Taken
1. **CRITICAL bug 修复**：Bridge `/history` 生产环境恒空 —— `MqlTradeOrder` 无 `position_id`，原实现在 order 侧读它恒为 None，全部持仓被跳过。重写为 **deal 锚定**：
   - entry==0 开仓 deal（`position_id`/`price`/`time`/`type`）提供 开仓价/开仓时间/方向
   - entry==1 平仓 deal 按 `position_id` 聚合盈亏（profit+commission+swap），价格/时间取最后一条
   - SL/TP 经 `deal.order → order.ticket` 从 `history_orders_get` 的订单取；匹配不到回落开仓成交（不丢行）
   - 方向由开仓 deal.type 奇偶判定（挂单触发的开仓成交 type 恒为 0/1，天然覆盖 SELL_LIMIT 等）
   - 品种过滤用开仓成交 symbol（与后端 `_deal_matches_symbol` 同源）
2. **测试 mock 修正**：`_order_ns` 移除 position_id（反映真实 MqlTradeOrder 结构），新增 `_entry_deal_ns`/`_exit_deal_ns` 反映真实 MqlTradeDeal 结构；所有测试正文改用 entry+exit deal 配对
3. **新增测试用例**：`test_history_open_price_fallback_without_order`（订单匹配不到回落不丢行）、`test_history_direction_from_deal_type`（挂单 SELL 方向）
4. **代码审查 MEDIUM 项处理**：
   - MEDIUM-1：后端 `open_price` 恢复 `or price` 兜底（混合部署期不显示 0，与 open_time 兜底一致）
   - MEDIUM-2：Bridge 符号过滤宽松归一（`_symbol_matches` 去下划线/小写）+ 下划线别名测试
   - MEDIUM-3：analytics.py / manual_order_gate.py 毛额 profit 改净额优先（`net_profit` 回落 `profit`），与 history.py 口径统一
5. **新增测试**：`test_get_daily_pnl_net_profit_priority`（daily-pnl 净额优先断言，首次失败因测试数据是历史日期被"仅统计今天"过滤，改用动态时间）

### Test Results
| 套件 | 结果 |
|------|------|
| mt5_bridge tests（46，含 10 个 history 用例） | ✅ 全过 |
| 后端 history 集成（6，含新 daily_pnl net_profit） | ✅ 全过 |
| manual_order_gate（24） | ✅ 全过 |
| discipline/position_close/circuit_breaker（63） | ✅ 全过 |
| tsc --noEmit | ✅ 通过 |

### Errors Encountered（本次会话）
| Error | Resolution |
|-------|------------|
| Bridge `/history` 生产恒空（CRITICAL） | `MqlTradeOrder` 无 `position_id` → deal 锚定重写 + 测试 mock 反映真实字段 |
| daily_pnl 测试 =0 | daily-pnl 只统计当天平仓，测试数据用历史日期被过滤 → 动态 today 时间 |
