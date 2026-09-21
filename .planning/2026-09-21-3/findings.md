# Findings — 风控失效排查：日亏 3% 未拦截（2026-09-21）

## 用户报告

当天回撤约 8%（实际 9.6%），机器人仍在交易、3% 日亏风控未拦截。要求：审计全部风控措施，输出优化计划，经批准后执行。

## 事实链（2026-09-21，全部实证）

### 账户与回撤
- 峰值余额 `circuit:peak_balance` = **12073.18**
- 当前 balance = **10916.58**（回撤 **-9.6%**）；equity = 11009.32（浮盈 +92.74，回撤 **-8.8%** ≈ 用户所述 8%）
- MT5 桥 `/account` 实测：login=336773771, server=XMGlobal-MT5 9, mode=**live**（`guardrails:rollout_mode=live`，真实资金）

### 今日真实平仓（MT5 /history，7 笔，合计 **-504.77**）
| ticket | symbol | lot | profit |
|---|---|---|---|
| 966080652 | GOLD | 0.05 | -122.05 |
| 966106977 | GOLD | 0.05 | -103.20 |
| 966119757 | GOLD | 0.05 | -74.15 |
| 966139915 | GOLD | 0.05 | -62.70 |
| 966616447 | GOLD | 0.01 | -7.22 |
| 966668039 | BTCUSD | 0.1 | -44.55 |
| 966680474 | GOLD | 0.1 | -90.90 |

### 记账结果（Redis 实测）
- `circuit:daily_pnl:BTCUSD` = -44.55（**唯一被记录的一笔**，引擎 09:51:38 记录）
- `circuit:daily_pnl:GOLD` = **不存在** → GOLD 今日 -460 已实现亏损完全没进日亏计数器
- `guardrails:trade_results:2026-09-21` = 1 条（连亏计数同样漏记）
- `guardrails:trades:2026-09-21T01` = 2（每小时计数，只计 AI 通道）
- `guardrails:last_trade_time` = 09:57:15

### 下单通道（今日全部为 AI agent 实盘下单）
- 08:55 GOLD SELL 0.01（ticket 966616447）→ 亏 -7.22
- 09:41 BTCUSD BUY 0.1（966668039）→ 亏 -44.55
- 09:42 GOLD BUY **被 120s 间隔风控拦截**（实证：`交易频率保护机制拦截`，09:42:18 日志）→ **频率闸门有效**
- 09:57 GOLD BUY 0.1（966680474）→ 亏 -90.9
- 走 mcp_server.tools.broker.place_order → order_preflight.preflight_order → validate_order（**该通道完整走硬闸门**）

### 引擎状态
- trading_mode = **ai_autonomous**（Redis 持久化，09:04/09:16 恢复日志实证）
- GOLD 引擎 09:17:32 启动（ema_crossover）、BTCUSD 09:18:37 启动（breakout）
- **ai_autonomous 下 process_candle 在 engine.py:396 直接 return** → `_check_circuit_breakers`（分品种+全局+15% 回撤）与 `_check_trade_permission` 全部不执行
- sync_positions 持续报错：09:02-09:04 旧引擎 `paper_trade AttributeError`（每 30s 崩溃）；10:16+ `Position fetch returned empty but 1 known — skipping sync (possible timeout)`；reconcile_positions 每 5 分钟 `This session is provisioning a new connection; concurrent operations are not permitted`（共享 AsyncSession 并发——engine.py:1927+ 仍用 self.db，已知 bug 类）

## 根因（为什么 3% 日亏没拦下 8%+ 回撤）

### R1 记账链路断裂（直接原因）
日亏 3% 的唯一数据源 = 引擎 `sync_positions` 检测平仓 → `_handle_closed_trades` → `circuit_breaker.record_trade_result`。今日多环节断裂：
- 07:45-09:00 的 4 笔 GOLD 平仓发生在**旧破损引擎**运行期（paper_trade AttributeError）→ 未记录；
- 10:15 的 GOLD 平仓（-90.9）后 sync 持续 "fetch empty → skip"（bridge 超时）→ 未记录；
- `sync_positions` **仅引擎 RUNNING 时运行**（engine.py:1211）——AI 交易通道与引擎状态脱钩：引擎停止时 AI 仍可下单，但平仓无人记账 → 日亏盲区。
- 附带数据完整性 bug：DB trades id 11-14（GOLD 966080652 等）close_time=09-19 06:39 / profit=0.0，与 MT5 实际（09-21 才平、真实亏损）不符——DB 曾把它们误标为已平且 0 盈亏。

### R2 日亏检查是分品种的，无账户级汇总
- `validate_order` 拿到的 daily_pnl 来自 `CircuitBreaker(redis, symbol, ...).get_daily_pnl()`（order_preflight.py:200）→ **单品种已实现**。
- GOLD -460 若记上，占 balance 10916 的 4.2% > 3%，**本会触发**；但它没被记上。
- 全局 10% 汇总检查（is_global_triggered）只在引擎 `_check_circuit_breakers`，ai_autonomous 下不跑；AI/手动通道根本没有账户级日亏闸门。

### R3 只看已实现、不看浮动（equity）
所有检查（validate_order / risk_manager.can_open_trade / CircuitBreaker.is_triggered）都用 realized daily_pnl 与 `balance`，从不读 `equity`。持仓浮亏 8% 时任何闸门都不会拦。grep 实证：engine/risk 无一处使用 equity。

### R4 阈值语义错位
系统只有：3% 日亏（已实现、分品种、按市场日重置）和 15% 绝对回撤（balance-from-peak，`settings.max_drawdown_from_peak=0.15`）。用户期望"3% 就停"是账户级回撤/日亏语义，两者都不覆盖。9.6% < 15%，即便引擎回路跑着也不拦。

### R5 引擎策略通道绕过 validate_order（潜在）
`process_candle → _size_and_place_order → executor.place_order`（engine.py:965）不经过 preflight/validate_order。ai_autonomous 下引擎不自营，但切回 strategy 模式后：点差/每小时/间隔/总持仓/SL-TP 校验全部不生效；且引擎开仓不调 `record_order_opened`（频率计数漏计引擎单）。

## 其余风控审计（逐条）

| 风控 | AI/manual 通道 | 引擎策略通道 | 今日实证 |
|---|---|---|---|
| 单笔手数 ≤1.0 lot | ✅ validate_order | ✅ risk_manager.max_lot | 全部 ≤0.1 |
| 单品种并发 ≤3 | ✅ validate_order | ✅ can_open_trade(current_positions≥3) | — |
| **总持仓 ≤5** | ✅ validate_order | ❌ 无（check_portfolio_limit 是 3x 杠杆非持仓数） | — |
| **日亏 ≤3%** | ⚠️ 单品种已实现 | ⚠️ 单品种已实现 + ai_autonomous 不跑 | **漏拦（R1/R2）** |
| **连亏 <5 熔断** | ⚠️ trade_results 数据源同 R1 断裂 | 同左 | 今日 len=1，漏记 |
| 每小时 ≤5 | ✅ validate_order（仅开仓时计） | ❌ 不计数 | 今日 2 笔 |
| 间隔 ≥120s | ✅ validate_order（record_order_opened 更新） | ❌ 不计数 | **实证拦下 09:42** |
| 点差 ≤3×均值 | ✅ validate_order | ❌ 无 | — |
| SL/TP 方向 | ✅ validate_order（挂单用挂单价） | SL/TP 自算正确 | — |
| 绝对回撤 15% | ❌ 无 | ⚠️ 仅 process_candle，ai_autonomous 不跑；9.6%<15% 即使跑也不拦 | — |
| 真实资金授权 | ✅ llm_allow_live | — | live 已授权 |

**结论：今天唯一"该拦未拦"的是日亏 3%（及衍生连亏计数），根因 R1+R2+R3；其余闸门要么数据源同样断裂，要么只覆盖 AI/manual 通道，要么阈值本就高于 8%。**

## 关键文件
- backend/mcp_server/guardrails.py（validate_order/record_*）
- backend/app/services/order_preflight.py:200（daily_pnl 读取）
- backend/app/bot/engine.py:396（ai_autonomous 早退）、1211（sync 需 RUNNING）、1254-1294（平仓记账）、1927+（reconcile 共享会话）
- backend/app/risk/circuit_breaker.py、app/risk/manager.py
- backend/mcp_server/tools/broker.py:80（AI 通道 preflight）
