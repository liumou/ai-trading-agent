# Findings — 分析自主交易信号缺失问题

## 目标问题
用户报告：机器人运行一天，没有任何交易信号，没有自主交易。

## 证据链（全部来自代码 + 日志，2026-09-18）

### 核心结论：机器人以 `ai_autonomous` 模式运行，该模式下策略引擎被跳过，而 AI 路径从不真正下单

### 证据 1：引擎实际以 ai_autonomous 模式启动
- 日志 `app.bot.engine:start:308 - Bot started: strategy=ai_autonomous, symbol=GOLD`
- 多次重启均恢复自 Redis：`app.main:lifespan - Restored trading_mode from Redis: ai_autonomous`
- Redis 有持久化的 `trading_mode`，优先于 `.env`/config 默认 `"strategy"`

### 证据 2：ai_autonomous 模式下策略引擎 process_candle 被跳过
- `backend/app/bot/engine.py:381-388`：`if settings.trading_mode == "ai_autonomous": return` 直接跳过
- `backend/app/bot/scheduler.py:_candle_job`：非 strategy 模式不调用 `process_candle()`，只跑 `_detect_regime()` + `_run_ai_agent()`
- **日志统计：Signal detected 次数 = 0，trade_blocked = 0，Order placed = 0，PAPER trade = 0**

### 证据 3：AI agent 路径从不下单
- `_run_ai_agent`（scheduler.py:432）只调用 `run_agent`/`run_multi_agent` 获取决策文本，然后记录到日志/事件/WebSocket
- **AI 决策从不经过 `_size_and_place_order`**，也没有把 decision 转成实际交易
- 唯一的"下单"通道是 `mcp_server/tools/broker.py::place_order`（供 orchestrator 调用 MCP 工具）
- 但 `system_prompt.md:16` 明确指示单 agent："You do NOT place orders or execute trades"

### 证据 4：AI 决策实际内容
- HOLD 决策 58 次（日志 `_run_for_symbol:474 - AI agent [GOLD]: ## 交易决策：GOLD — HOLD`）
- 有少量 SELL/BUY 决策，如 9-17 10:53「决策：SELL（影子模式已记录）」、9-17 19:21「BUY」，9-18 04:06「BUY（Mean Reversion）」
- 但 rollout_mode = **shadow**（.env `ROLLOUT_MODE=micro` 但运行时是 shadow？需确认），订单被拦截
- 日志显示 AI 决策多为"Shadow mode（已记录待审核，未发送至经纪商）"

### 证据 5：多智能体流水线超时问题（既有已知问题）
- `backend/.env`: `LLM_PROVIDER=openai_compat`, ARK + deepseek-v4-flash
- 旧 `run_multi_agent`（orchestrator.py）specialist 60s / orchestrator 120s 硬预算
- findings 已记录：分析报告全部子 Agent 超时（"Agent loop terminated / timeout"），失败被转述成"市场无信号"
- 新 V2 聊天 runtime（600s 预算）代码完成但**未部署**

### 证据 6：MT5 Bridge 连接问题
- 9-17 23:58-9-18 00:00：MT5 Bridge "All connection attempts failed"（多次）
- `_fetch_tick` 报 "Server disconnected without sending a response"
- 9-18 03:57 重启后似乎恢复（有引擎创建、candle job scheduled 日志）
- 但 tick 错误在 03:15-03:56 仍零星出现

### 证据 7：place_order 曾尝试但失败/被拦截
- AI 确实调用过 `Tool: place_order` 8 次
- 9-17 16:21：`place_order [GOLD_] BUY lot=0.1 mode=live` → `Order send failed: Invalid "comment" argument`（MT5 拒绝）
- 之后 `trade limit hit: 1/1 — place_order blocked`（每 loop 上限 1 次）
- 多数情况是 shadow mode 拦截

### 关键区分：rollout_mode 的实际值
- .env 写 `ROLLOUT_MODE=micro`
- 但 AI 决策文本明确说"Shadow mode（已记录待审核，未发送至经纪商）"
- 需进一步确认 guardrails.get_rollout_mode() 的运行时值（可能 UI/Redis 覆盖为 shadow）
- micro 模式会把 lot 限制到 0.01

## 根因链（为什么没有交易）

```
用户期望"AI 自主交易"
  → trading_mode = ai_autonomous（Redis 持久化）
  → process_candle() 被跳过（策略引擎不产信号）
  → _run_ai_agent() 只分析不下单（决策只是文本记录）
  → 即使 AI 想下单：rollout_mode=shadow/micro 拦截 + MT5 comment 校验失败 + 多agent超时
  → 最终：0 信号、0 订单
```

## 三路评审结论（2026-09-18，已整合进正式计划）

### 关键事实修正（评审交叉验证）
1. **GOLD_ 是正确券商别名，不是 bug**：DB `symbol_configs.broker_alias`，`connector.py:98` 注释证实，`/tick/GOLD_` 实测正常。删除"修复品种名"
2. **comment 是真正失败根因**：MT5 comment 上限 **27 字符**（非 32），前缀 `[Agent:micro]` 占 13 字符，`[`/`]` 是 MT5 不接受的字符
3. **micro 保护实际未生效**：实际以 `mode=live, lot=0.1` 下单。`broker.py` 用同步 `get_rollout_mode()`（读 env）绕过 Redis 持久化模式
4. **`.env` 与运行时值不一致**：`LLM_MAX_ORDERS_PER_LOOP`（.env=2 vs 运行时=1）、`ROLLOUT_MODE`（.env=micro vs 运行时=live），存在未记录的配置覆盖源
5. **预算已配置化**：`config.py:200-205` 已有 `multi_agent_*` 配置且各分析师已引用，只需调 .env
6. **实测 specialist 完成 339-761s**（远超 60s 预算），超时值运行时未生效/统计口径待核

### 安全评审确认的系统性护栏失效（CRITICAL）
- **日亏熔断失效**：`record_trade_result()` 无任何生产调用点，`daily_pnl` 恒 0
- **连亏熔断失效**：`broker.py:228` 开仓即 `record_trade(is_win=True)`，无平仓回调
- **spread 熔断失效**：`avg_spread = spread` → 恒等式
- **rollout 跨进程 fail-open**：broker 同步读 env，openai_loop 异步读 Redis
- **SL/TP 完全无校验**：AI 可下无止损单/方向错误单

### 用户决策
- 执行范围：**修复 + 补护栏**（修 comment + 预算 + 系统性护栏修复）
- 最终形态：**先 AI 后混合**（先打通纯 AI 自主，跑稳后叠加策略混合）

## 待确认项
1. ~~guardrails.get_rollout_mode() 运行时真实值~~ → 已确认：实际 mode=live（micro 未生效）
2. ~~前端是否有切换 trading_mode 的入口~~ → 有（settings 页策略下拉选 ai_autonomous）
3. ~~MT5 Bridge 当前是否健康~~ → 健康（status ok, login 336773771, XMGlobal-MT5 9）
