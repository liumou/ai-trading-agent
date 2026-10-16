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

## Errors Encountered
| Error | Resolution |
|-------|------------|
| 既有 test_get_trades_account_login 失败 | 新隔离逻辑生效——更新该用例为登记 active 账号 |
| analytics 测试 JSON 序列化 inf | profit_factor 全盈时 float('inf') → 有界化 999.99 |