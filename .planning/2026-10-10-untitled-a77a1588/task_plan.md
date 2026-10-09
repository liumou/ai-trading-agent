# Task Plan: 交易纪律门禁实施（M1-M5，计划 v3.1 批准后）

## Goal
按 docs/optimization/trading-discipline-enhancement.md v3.1（用户 2026-10-10 批准 + 冻结三项口径）实施交易纪律风控增强：M1 时区+基础设施 → M2 P0 硬闸门 → M3 P1 → M4 P2 → M5 P3。

## Next Step
向用户汇报 M1-M5 全部完成（1133 passed / 8 test_multi_agent 既有非回归）；用户部署 bridge + 存量回填 + 浏览器验收。

## Current Phase
全部代码完成 ✅（M1 时区 + M2 P0 + M3 P1 + M4 P2 + M5 P3）

## Phases

### M1: 时区 + 基础设施（已完成）
- [x] F1 bridge 输出 UTC：main.py 8 处 fromtimestamp 改 _iso_utc + /account 补 leverage + 输入侧 4 处 _parse_utc_input
- [x] F2 后端 9 处收敛：engine.py 4 处 + history.py 3 处 + analytics.py 1 处 + manual_order_gate 1 处，全部走 parse_bridge_time_to_naive_utc
- [x] discipline.py：discipline_now/日周月 key（22:00 UTC 外汇日）/until/休息日/parse_bridge_time
- [x] config.py：discipline_* + guardrails 去硬编码字段（max_trades_per_hour/min_interval/max_daily_loss）
- [x] guardrails.py：_daily_key/_hourly_key 走纪律日界 + 3 常量 _env_limit 化
- [x] F3 前端：format.ts toDate + Asia/Shanghai，15+ 页面/组件统一（history/PriceChart/insights/activity/manual-reviews/ReviewHistoryDialog/NewsCard/notification-center/connection-status/ai-usage/macro/EventFeed/chat-utils）
- [x] F6 存量回填脚本 backend/scripts/backfill_trade_timezone.py（--dry-run 支持）
- [x] F5 CLAUDE.md 时区约定文档化
- [x] 测试：test_discipline_timezone.py 15 用例（外汇日/iso_year 陷阱/until/休息日/新旧 bridge 兼容）
- **Status:** complete（相关 159 用例全绿）

### M2: P0 硬闸门（已完成）
- [x] 引擎 fail-closed：engine.py preflight 异常不再吞掉继续下单（评审 3 A1），记 TRADE_BLOCKED；engine_discipline_enabled 豁免开关
- [x] discipline_gate.py：check_discipline_gate（休息日/周月熔断/冲动冷却/反手冷静期/日周次数/保证金上限）+ record_order_opened_discipline（日周计数 + flip 方向记账）
- [x] preflight 6c 步骤：channel 参数（manual/engine/ai）+ 纪律门禁接入 + 调用方三处传 channel
- [x] CircuitBreaker：record_trade_result 顺带写周/月 PnL（周期号 key）+ get_period_pnl 账户级聚合 + set/check_period_halt
- [x] guardrails.record_trade_closed：跨日序列 streak（loss_streak key，盈利归零亏损+1；去 Lua 兼容 fakeredis）+ _get_consecutive_losses 读新 key
- [x] BotEvent.account_login：engine/_manual_order_gate _log_event 补账号维度
- [x] 测试：test_discipline_gate.py 8 用例（休息日/次数/反手/Redis-down/周月 PnL/halt/streak）
- **Status:** complete（M1+M2 相关 167 用例全绿；全量 1130 passed / 8 failed 均 test_multi_agent 既有非回归）

### M3: P1（已完成）
- [x] 2b 参数 Redis 运行时通道：discipline_gate.get_runtime_setting/set_runtime_setting/list_runtime_settings（Redis 为真相、settings 回退）+ GET/PUT /api/trading/discipline/config 端点
- [x] 纪律状态端点：GET /api/trading/discipline/status（今日/周次数、冷却/熔断解禁时间、休息日、时区）
- [x] 1d 周末不留仓：WEEKEND_CLOSE 检查（Asia/Shanghai 本地周六/日，crypto 24/7 除外，sessions._rules_for）
- [x] broker.py AI 通道 DB 落库：开仓成功后写 OrderAudit（source=ai_agent，评审 8 前移项）
- [x] 修复：discipline_local_now 模块级绑定改为延迟 import（测试可 monkeypatch）；总开关读取顺序（settings 优先，Redis 覆盖）；get_runtime_setting 默认回退 settings；test_mcp_broker_guard 的 switching mock 区分 discipline:cfg: key
- **Status:** complete（全量 1130 passed / 8 failed 均 test_multi_agent 既有非回归）

### M4: P2（已完成）
- [x] 3c 逆势 CAUTION：systemone `_rule_direction_flip`（同品种反向持仓 → warn，CAUTION 确认流携带方向切换上下文）；硬拒（第 2 次反手）已由 discipline_gate 完成
- [x] 4b 冲动冷却阶梯：discipline_gate.trigger_impulse_cooldown（第 2 次 24h → 第 3 次 72h → 第 4 次本周禁 + 72h）；preflight 拦截后自动触发（非冷却/熔断类拦截即手痒信号）
- [x] 冷却与日界正交（绝对时间戳跨日不解除）
- **Status:** complete

### M5: P3（已完成）
- [x] 4c 复盘统计：discipline_stats.py（读 OrderAudit/Trade/BotEvent → 开仓次数/违规/胜率/最大回撤/纪律评分）+ GET /api/trading/discipline/stats 端点
- [x] 防拆单（M5/P3）：discipline_max_lots_per_day（默认 1.0）日累计手数上限，check_discipline_gate MAX_LOTS_DAY + record_order_opened_discipline incrbyfloat（3 通道传 lot）
- **Status:** complete

### 最终验收（已完成）
- [x] 全量后端 1133 passed / 8 failed（全部 test_multi_agent 既有非回归，模型配置差异）
- [x] 前端 build 通过（M3 后无前端改动）
- [ ] 桥部署：mt5_bridge 时间输出 UTC（F1）+ leverage 需 Windows 侧重启（用户部署）
- [ ] 存量回填：backend/scripts/backfill_trade_timezone.py --dry-run 后执行（用户部署）
- [ ] 浏览器端到端验收 + /trading 页纪律状态展示（需用户登录，后端 8002 运行中）
- **Status:** 代码完成，部署/回填/浏览器验收留给用户

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 纪律门禁走 preflight 6c 步骤 + channel 参数 | 三通道共享，最小侵入 |
| streak 用跨日 loss_streak key（非日粒度 trade_results） | 评审 7：跨日跨周末连续、盈利即断 |
| flip key 用 epoch 秒绝对时间戳 | 与日界正交（评审 7），跨日不解除 |
| 周/月 PnL 用周期号 key + get_period_pnl 账户级聚合 | 堵分散亏损旁路 |
| 测试 conftest 默认禁用 discipline_gate | 单元测试不受休息日/计数干扰；专项用例显式启用 |
| streak 更新用 GET+SET 非 Lua | fakeredis 不支持 eval；单 worker 下单路径串行足够 |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| 测试 _loss_deal naive UTC 被当 EET 解析偏移 3h | 测试 mock 改带 +00:00（模拟新 bridge）；旧 bridge naive 语义保留在单测 |
| _hourly_key 格式变化致 manual gate 测试失败 | 断言改用 discipline_day_key + 22h 偏移 |
| fakeredis 不支持 eval | streak 更新改 GET+SET |
| discipline_gate `time` NameError / bytes 比较 | 统一 _time.time() + _parse_flip/_parse_dt 处理 bytes |
| check_discipline_gate 中 _balance 未 await | 补 await + balance>0 守卫 |

## Reference
- 计划：docs/optimization/trading-discipline-enhancement.md（v3.1）
- 探索/评审结论：.planning/2026-10-09-untitled-332db516/findings.md
- 记忆：manual-trading-firewall、backend-deployment-topology、user-prefers-autonomous-chinese
