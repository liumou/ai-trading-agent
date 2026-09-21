# Task Plan: 风控失效排查与优化（日亏 3% 未拦截）

## Goal

修复"当天回撤 8-9.6% 但 3% 日亏风控未拦截、机器人持续实盘交易"的问题，并审计/补齐全部风控措施。

## Next Step

实施完成 + 已重启验证；待用户确认效果 / 决定是否提交代码。

## Current Phase

Phase 1-5 全部完成，Phase 6 验证完成（重启 + 双引擎回填实证）。

## 诊断结论（详见 findings.md）

- 今日余额从峰值 12073.18 → 10916.58（-9.6%），MT5 真实平仓 7 笔合计 -504.77（GOLD -460 + BTCUSD -44.55），全部为 AI agent live 实盘。
- 但 Redis 日亏计数器只有 BTCUSD -44.55，GOLD 完全漏记 → validate_order 读到 daily_pnl≈0 → 3% 日亏闸门盲视。
- 根因：R1 记账链路断裂（旧引擎 bug 期平仓未记 + sync 超时跳过 + 仅引擎 RUNNING 才记账）；R2 日亏按单品种无账户级汇总；R3 只看已实现不看 equity 浮动；R4 阈值语义错位（3% 日亏 vs 15% 绝对回撤）；R5 引擎策略通道绕过 validate_order。
- 其余风控：AI/manual 通道的间隔/频率/点差/并发/SL-TP 均生效（09:42 被 120s 间隔实证拦下）；缺口集中在日亏数据源、账户级汇总、equity 回撤、引擎通道覆盖。

## Phases

### Phase 1: 需求与诊断（已完成）
- [x] 用户报告澄清（当天回撤 8%，期望 3% 停止）
- [x] 账户/持仓/历史实证（MT5 桥 + Redis + DB + 日志）
- [x] 根因 R1-R5 确认，全风控逐条审计
- **Status:** complete

### Phase 2: 修记账断裂（日亏数据可信）— 待批准
- [ ] 2.1 reconcile_positions 共享 AsyncSession 并发 bug 修复（engine.py:1927+ 改隔离 session，参照 _handle_closed_trades 模式）
- [ ] 2.2 sync_positions 平仓检测加固：fetch 空 → 重试一次再判"全部平仓"；用 get_history 对账兜底检测漏掉的平仓
- [ ] 2.3 启动时用 MT5 history + DB 对账一次性回填当日已实现 P&L（修复"引擎不在场/崩溃期平仓漏记"）
- [ ] 2.4 AI 通道平仓记账与引擎 RUNNING 状态解耦（引擎停止时 AI 仍交易 → 平仓也须记账）
- **Status:** pending

### Phase 3: 日亏升级为账户级 + 含浮动 equity — 待批准
- [ ] 3.1 preflight/validate_order 增加账户级已实现日亏汇总（get_global_daily_pnl 聚合全部在线品种，3% 上限按账户总余额）
- [ ] 3.2 新增 equity 日内回撤闸门：balance + 浮动盈亏相对日内峰值 ≥ 3% 即拒单/停交易（用户真正期望的"3% 就停"；阈值可配）
- [ ] 3.3 ai_autonomous 模式下引擎 circuit breaker 照常运行：process_candle 早退前先跑 _check_circuit_breakers，或把全局检查并入 broker.py 下单前
- **Status:** pending

### Phase 4: 引擎策略通道接入统一硬闸门 — 待批准
- [ ] 4.1 _size_and_place_order 下单前调用 preflight_order（与 AI/manual 同一真相源，补齐点差/每小时/间隔/总持仓/SL-TP）
- [ ] 4.2 引擎开仓 record_order_opened（频率/间隔计数覆盖引擎通道）
- **Status:** pending

### Phase 5: 阈值、配置与可观测性 — 待批准
- [ ] 5.1 明确三层语义并 UI 展示：日亏 3%（账户级已实现）、equity 回撤 X%（新）、绝对回撤 15%；评估绝对回撤是否下调
- [ ] 5.2 风控拒绝事件全部落库 + Telegram/UI 通知（现 validate_order 拒绝仅返 reason，AI 自总结，用户不可见）
- **Status:** pending

### Phase 6: 回归测试与验证 — 待批准
- [ ] 6.1 单测：分品种 vs 账户级日亏、equity 回撤、引擎停止时 AI 平仓记账
- [ ] 6.2 集成：GOLD 连亏 5 笔触发连亏熔断、日亏超 3% 拒单、回填逻辑幂等
- [ ] 6.3 后端重启 + 模拟验证，回归 test_engine/test_lot_volume_guard/test_account_switch
- **Status:** pending

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| 先诊断后实施，计划待批准 | 用户明确要求"经过我同意才能执行" |
| 修复优先级：记账断裂 > 账户级日亏 > equity 回撤 > 引擎通道 > 可观测性 | 日亏闸门的数据源不可信，一切上层升级都白搭 |
| 默认阈值：账户级日亏 3%、equity 日内回撤 3%（可配）、绝对回撤保持 15%（评估） | 对齐用户"3% 就停"预期；15% 为熔断级兜底 |

## Errors Encountered

| Error | Resolution |
|-------|------------|
| （诊断阶段无执行错误；记录了运行时的 sync/reconcile 报错作为证据，非本任务错误） | - |
