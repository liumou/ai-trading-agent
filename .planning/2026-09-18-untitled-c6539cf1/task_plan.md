# Task Plan: 分析机器人为何没有自主交易信号

## Goal
定位机器人运行一天无交易信号/无自主交易的根本原因，并给出可执行的解决方案路径。

## Next Step
向用户提交根因分析报告，说明为何没有交易，以及如何达成自主交易。

## Current Phase
Phase 1（分析完成，待交付）

## Phases

### Phase 1: 根因分析 — ✅ complete
- [x] 追踪信号生成链路（process_candle → _generate_signal → _check_trade_permission → _size_and_place_order）
- [x] 确认实际运行模式（trading_mode / agent_mode / rollout_mode）
- [x] 分析 AI 自主交易路径（_run_ai_agent → run_multi_agent → broker.place_order）
- [x] 用日志证据验证（Signal detected=0、HOLD×58、place_order 8 次但全部未成交）

### Phase 2: 方案设计 — ✅ complete
- [x] 为每个根因提出可选修复路径（见最终报告）
- [x] 评估每条路径的影响与风险

### Phase 3: 交付 — ✅ complete
- [x] 向用户提交分析报告

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 分析范围限定于"为什么没有交易"，不做代码修改 | 用户要求的是分析 + 如何达成目的，属于诊断任务 |

## 核心根因（摘要）

**机器人以 `ai_autonomous` 模式运行，该模式下：**
1. 策略引擎 `process_candle()` 被直接跳过（engine.py:384）→ 策略信号为 0
2. `_run_ai_agent()` 只跑 AI 分析、记录决策文本，**从不真正下单**（scheduler.py:432-557 无任何下单调用）
3. AI 唯一的执行通道是 MCP 的 `place_order` 工具，但受多重限制：
   - 多智能体流水线超时（openai_loop 60/120s 硬预算，deepseek-v4-flash 太慢）→ 决策多为 HOLD 或失败转述
   - `system_prompt.md` 禁止单 agent 下单
   - `LLM_MAX_ORDERS_PER_LOOP=1` 硬上限
   - rollout_mode 实际为 `micro`（lot ≤ 0.01）——虽有 1 次 `mode=live` 尝试但被 MT5 拒绝（Invalid comment argument）
4. MT5 Bridge 时有断连（"All connection attempts failed"、"Server disconnected"）

## Errors Encountered
| Error | Resolution |
|-------|------------|
| 无 | 纯分析任务，无代码修改 |

## 附录：日志证据
- `Bot started: strategy=ai_autonomous, symbol=GOLD`（9-17 10:15 起多次）
- `Restored trading_mode from Redis: ai_autonomous`（每次重启）
- `Signal detected: 0 次`、`Trade blocked: 0 次`、`Order placed: 0 次`
- `AI agent [GOLD]: ## 交易决策：GOLD — HOLD`（58 次）
- `place_order` 工具调用 8 次，实际成功 0 次
- 9-17 16:21：`place_order [GOLD_] BUY lot=0.1 mode=live` → `Order send failed: Invalid "comment" argument`
- 9-18 03:57：`LIVE TRADE MODE — rollout='micro'; orders will hit broker`
