# Progress Log

## Session: 2026-09-21（风控失效排查 + 修复实施）

### Current Status
- **Phase:** 1 诊断 + 2-5 修复实施完成，6 验证进行中；**代码已部署并重启验证**
- **Started:** 2026-09-21

### Actions Taken（诊断阶段）
- [x] 读取风控全链路（guardrails / preflight / circuit_breaker / risk_manager / engine / broker / manual_gate）
- [x] 实测 MT5 桥账户/历史、Redis 熔断 key、DB trades、日志 —— 定位"日亏 3% 未拦截"根因 R1-R5（详见 findings.md）

### Actions Taken（实施阶段，用户 2026-09-21 批准后执行）
- [x] **P2 记账断裂**：reconcile 隔离 session（enginе 共享会话 bug 修复）；sync_positions 空取回用 history 对账；`record_trade_result`/`record_trade_closed` 按 ticket 幂等；启动回填 `backfill_today`；phantom 不再擅自标 0 盈亏平仓
- [x] **P3 日亏升级**：guardrails `account_daily_pnl` 账户级日亏检查；preflight 聚合账户级已实现日亏 + equity 日内回撤闸门（`CircuitBreaker.is_equity_drawdown_halted`）；ai_autonomous 下 scheduler 驱动 `_run_risk_gate`（引擎熔断回路不再停摆）
- [x] **P4 引擎通道**：`_size_and_place_order` 接入统一 preflight（check_live_auth=False）+ record_order_opened 频率计数
- [x] **P5 可观测性**：validate_order 拒绝审计（Redis `guardrails:rejections:{date}` + WARNING 日志）；`max_equity_drawdown`/`max_drawdown_from_peak` 暴露到 status；.env.example 新增 MAX_EQUITY_DRAWDOWN=0.03
- [x] 测试：新增 test_circuit_breaker（幂等/equity 闸门/回填）、test_guardrails（账户级日亏/审计）
- [x] **重启后端（PID 34870→64857）+ 启动 GOLD/BTCUSD 引擎验证**

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| 单元套件（除已知 test_multi_agent/backtest_consistency 顺序污染） | pass | **813 passed** | ✅ |
| 集成套件 tests/integration | pass | **124 passed** | ✅ |
| 重启后 errors.log 新进程（≥10:51） | 0 | **0 条**（旧 reconcile/sync 报错消失） | ✅ |
| GOLD 日亏回填 | -460.22 | `circuit:daily_pnl:GOLD=-460.22`, trade_count=6 | ✅ |
| BTCUSD 回填幂等 | 不重复计数 | -44.55 保持，trade_count=1 | ✅ |
| 连亏列表 | 7 条（6 GOLD + 1 BTCUSD） | `guardrails:trade_results:2026-09-21` len=7 全亏损 | ✅ |
| status 新字段 | max_equity_drawdown=0.03 | API 实测存在 | ✅ |
| 风控回路 | equity_ref 建立 | `circuit:equity_ref=11010.4`（11:00 调度周期） | ✅ |
| 引擎状态 | 恢复 RUNNING | GOLD + BTCUSD 均 RUNNING | ✅ |

### 一次性迁移伪影与修复
- 现象：BTCUSD 日亏 -89.10（应 -44.55）——重启前旧代码记账不带 ticket（无去重集合），重启后回填按 ticket 去重找不到旧记录而重复入账。
- 修复：清掉 BTCUSD pnl/count + 共享 trade_results 与去重集合，重启两引擎重跑回填 → 恢复 GOLD -460.22/6、BTCUSD -44.55/1、trade_results 7 条。
- 结论：**此后所有记账点均传 ticket，重启/回填天然幂等，此类伪影不会再发生**。

### Errors
| Error | Resolution |
|-------|------------|
| 单脚本 preflight 缺 DB broker_alias 配置致 tick 失败 | 不阻塞——线上后端同一路径经 DB 配置正常；单元测试已覆盖闸门逻辑 |
| test_backtest_consistency::test_gold_reads_profile 全量跑挂 | 与本次改动无关：clean tree 同样失败（顺序污染），未纳入范围 |
| BTCUSD 回填重复记账（一次性迁移伪影） | 见上节，已修复并验证 |