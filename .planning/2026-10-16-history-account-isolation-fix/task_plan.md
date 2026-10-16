# Task Plan: 交易数据跨账号泄漏修复（Trading data account isolation fix）

## Goal
历史记录与手动交易风控的所有查询/执行端点，在切换 MT5 账号后必须严格按账号隔离——只允许「当前活跃账号 + '0' 遗留桶」的数据，禁止读出/执行其它账号的数据。

## Next Step
Phase 6：补 activity.py AI 活动日志账号隔离（Trade + BotEvent + summary），随后补测试。

## Current Phase
Phase 3 (Implementation)

## Phases

### Phase 1: Requirements & Discovery
- [x] 确认泄漏路径：/api/history/trades|daily-pnl|performance、/api/analytics/performance|slippage 均不过滤 account_login
- [x] 手动交易风控探索：**GET /reviews/{id} IDOR 跨账号读审查** + **POST /orders/{id}/confirm 跨账号执行**
- [x] 纪律状态/统计/挂单/平仓：已按账号隔离（Redis key + DB 查询 + bridge 天然隔离）
- [x] 纪律配置 discipline:cfg:*：全局共享（有意设计）
- **Status:** complete

### Phase 2: Planning & Structure
- [x] 用户拍板：**当前活跃账号 + '0' 遗留桶**、**覆盖所有 trades 聚合端点**
- [x] 设计统一 owned 集合辅助（history._resolve_history_owned / analytics._resolve_analytics_owned）
- [x] 确认 review_only 复盘跨账号回看是有意特性（不动）
- [x] 确定 manual_trading 修复方向：get_review 加账号校验 + confirm 校验归属
- **Status:** complete

### Phase 3: Implementation
- [x] history.py：_resolve_history_owned + 三端点 DB 过滤 + raw SQL fallback + daily-pnl 缓存键加账号 + review_only 保持
- [x] analytics.py：_resolve_analytics_owned + performance/slippage 过滤 + 缓存键加账号 + profit_factor inf guard
- [x] 补测试：history 隔离 3 新用例 + analytics 隔离新文件 3 用例
- [x] manual_trading.py + manual_order_gate.py：get_review 加 account_login 校验（IDOR）
- [x] manual_order_gate.py：confirm_and_execute 校验 audit.account_login == 当前账号（跨账号执行）
- [x] 补测试：get_review 跨账号 404/403 + confirm 跨账号拒绝
- **Status:** complete

### Phase 4: Testing & Verification
- [x] 全部新增/相关测试通过（活动隔离 3 用例 + 相关套件 68 passed，含历史隔离 3 + analytics 隔离 3 + manual IDOR/confirm 防护用例）
- [x] 全量回归（no new failures）：排除 test_multi_agent 环境性失败后 1317 passed；剩余 3 失败均为环境性/既有（飞书 webhook 本地已配置 + ML barrier 顺序问题），与本次改动零交集
- [x] ruff 检查通过
- [x] 反向验证：activity 禁用隔离 → 3 用例全失败，证明测试真实覆盖
- **Status:** complete

### Phase 5: Delivery
- [x] 向用户提交完成说明
- **Status:** complete

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 隔离口径 = 当前活跃账号 + '0' 遗留桶 | 用户拍板：严格隔离但不丢存量老数据 |
| 修复范围 = 所有 trades 聚合端点 | 用户拍板：口径一致，避免首页卡/analytics 混算 |
| review_only 复盘保持跨账号回看 | 有意特性，收敛会让切换后复盘徽章消失 |
| 纪律配置全局共享不动 | 设计使然（discipline:cfg:* 无账号维度） |
| profit_factor 全盈窗口用 999.99 | 既有 500 级 bug，最小有界化修复 |

## Phase 6 (补充发现): AI 活动日志账号隔离

> 回归验证时发现第三个泄漏点：`/api/ai/activity`（activity.py）的 Trade 查询
> 与 BotEvent 查询均不过滤 account_login —— 切换账号后 AI 活动日志仍会列出
> 其它账号的 AI 交易与事件。与历史记录属同类泄漏，纳入统一隔离口径。

- [x] activity.py：主日志 Trade 过滤 + BotEvent 过滤（account_login 可空 → `IS NULL OR IN owned`，不误杀无归属早期事件）
- [x] activity.py：summary 端点 trade_count/events_count 按 owned 过滤
- [x] 补测试：test_api_activity_isolation.py 3 用例（trade 隔离 / BotEvent 隔离 / summary 计数隔离）
- [x] 反向验证：临时置 owned=None → 3 用例全失败，证明测试真实覆盖隔离逻辑
- **Status:** complete

## Phase 8 (任务4): 全风控组件账号隔离审计

> 用户请求：「请检查其他的风控查控是否隔离了账号?请给我一个完整检查报告.」
> 审计范围：全部风控/查控组件的 Redis key + DB 查询 + Bridge/引擎绑定维度。

### 审计结论
- **已隔离 ✅**：CircuitBreaker（circuit:acc:{login}:*）、DisciplineGate（全部 key 含账号）、DisciplineStats、guardrails 连亏（Phase 7 修复）、history/analytics/activity、positions（engine.executor 天然绑定）、portfolio（bridge 天然绑定）、trade_reviews、manual_order_gate 连亏、preflight spread_history（按 symbol 非账号数据）
- **泄漏 ❌（3 处，均 BotEvent 相关）**：
  1. `bot.py GET /api/bot/events`（line 413）— BotEvent 查询未过滤 account_login + 缓存 key `cache:bot_events:{days}:{type}:{limit}` 无账号维度（dashboard 首页调用，与 activity.py 同类泄漏）
  2. `bot.py POST /api/bot/events`（line 476）— 创建事件未设 account_login（journal.py MCP 工具调用）→ 无归属事件切换账号后仍显示
  3. `position_close.py:134` — 手动平仓 BotEvent 未传 account_login（上方已解析出账号，一行遗漏）
- **边界 ⚠️（轻微，建议关注）**：guardrails 频率/节流 key（`guardrails:trades:{hour}`、`guardrails:last_trade_time`）跨账号共享——账号切换后旧账号交易频率污染新账号节流判断（"本小时已交易 N 次"），影响轻微（仅节流计数，不泄露数据、不阻断风控）
- **有意设计 ℹ️**：discipline:cfg:* 全局配置、session:agent:{symbol}:{date}（AI 记忆按 symbol）、strategy_switch（引擎级按 symbol）、PriceAlert 用户级配置、新闻/宏观/行情全局共享、ai_usage（全局 AI 资源预算）、metrics（观测指标）、quant（placeholder 假数据非真实持仓）、LLM circuit breaker（基础设施）
- [x] bot.py `GET /api/bot/events`：DB 按 owned 过滤（`IS NULL OR IN owned`）+ 缓存 key 加账号维度 `cache:bot_events:{login}:...`
- [x] bot.py `POST /api/bot/events`：事件打上 `manager.current_account_login`（无 manager 保持 NULL 不臆造）
- [x] position_close.py：手动平仓 BotEvent 传 `account_login`（复用上方已解析的账号）
- [x] 补测试：test_api_bot_events_isolation.py 6 用例 + test_position_close.py 1 用例（事件账号归属）
- [x] 反向验证：revert bot.py 过滤/缓存键/POST 账号 → 4 用例失败；revert position_close → 1 用例失败
- [x] 相关套件 36 passed
- [x] 全量回归：1342 passed；10 失败全部既有环境性/顺序性（多智能体模型常量、本地 FEISHU_WEBHOOK、ML band 顺序），与改动零交集
- **Status:** complete

## Phase 7 (用户复报): 手动交易连亏熔断跨账号

> 用户复报「拦截原因: 连续亏损 10 笔（熔断阈值 3）」—— 手动交易风控仍跨账号。
> 根因：`validate_order` 连亏熔断读**全局 key** `guardrails:loss_streak`
> （无账号维度），且 `_get_consecutive_losses` 在账号无自身 key 时**回退到
> 全账号共写的日粒度 trade_results 列表**——账号 A 连亏泄漏给账号 B。

- [x] PreflightContext 新增 account_login 字段（order_preflight.py）
- [x] preflight_order 构造 ctx 时存入 account_login，并透传给 validate_order
- [x] validate_order / _validate_order_core 新增 account_login 参数，连亏熔断按账号读 key
- [x] _get_consecutive_losses：明确账号无自身 streak key → 返回 0（不再回退全局 trade_results）
- [x] manual_gate._emotion_flags warn 级连亏改用账号隔离 key（ctx.account_login）
- [x] 补测试：guardrails 账号隔离 1 用例 + manual_gate 连亏隔离 1 用例 + preflight 透传 1 用例
- [x] 反向验证：revert guardrails 改动 → 2 个隔离测试失败，证明真实覆盖
- **Status:** complete
| Error | Resolution |
|-------|------------|
| 既有 test_get_trades_account_login 失败 | 新隔离逻辑生效——更新该用例为登记 active 账号 |
| analytics 测试 JSON 序列化 inf | profit_factor 全盈时 float('inf') → 有界化 999.99 |