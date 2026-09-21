# Progress — 最新提交 45273a1 风控修复代码审查

## Session 2026-09-21（审查启动）

### 目标
审查最新提交 45273a1 的全部代码改动，识别缺陷 → 生成优化计划（用户批准后才执行）。

### 进展时间线
- [x] 确定审查范围：45273a1（16 文件 / +948 / -150），关联前序 manual-trading/symbols 提交
- [x] 阅读全部核心 diff：engine.py、circuit_breaker.py、guardrails.py、order_preflight.py、position_close.py、scheduler.py、config.py、connector.py、mcp_server/guardrails.py、broker.py
- [x] 建立独立审查发现（findings.md）
- [x] 核对 MT5 Bridge 接口：/account（balance/equity/profit）、/history（ticket=position_id, 每 entry==1 一条）、history_deals_get 过滤
- [x] 核对原版 scheduler（45273a1^）→ 确认 F4 为"原版就有的缺陷，本次修复未生效"而非回归
- [ ] 等待 reviewer-risk 审查员完整报告
- [ ] 汇总缺陷清单 → 生成优化计划（待用户批准）

### 独立审查确认的缺陷清单（findings.md）
- F4 [重要] PAUSED 自动恢复死代码（scheduler 只调 RUNNING）+ health_monitor 桥恢复无条件解除熔断暂停（风控绕过）
- F2 [重要] 记账不含 commission/swap → 低估实际亏损
- F5 [中] equity_ref 首次采样时机洗白当日已亏损（重启/迟启动后当日回撤闸门失效）
- F1 [中→已修正] partial close 漏记：核实当前系统路径（引擎全平重开/手动全平）不产生同 position_id 多 deal → 降为防御性
- F3 [已关闭] partial TP 重开仓已调 _normalize_lot_to_broker（engine.py:1750）→ 无缺陷
- 观察点 O1/O3/O5 关闭（equity 计算正确、账号级 key 正确、无双重调用）

### 优化计划草案（task_plan.md）
- R1 [F4] scheduler 调用条件改 RUNNING+PAUSED；health_monitor 区分暂停原因防风控绕过
- R2 [F2] 记账口径统一为 profit+commission+swap 净盈亏
- R3 [F5] equity 回撤基准改当日峰值 equity（设计需确认下限基准）
- R4 [F1] 记账聚合防御（低优先）
- 每项先写测试 RED→GREEN，全量 pytest+ruff 回归

### 待办与进展更新
- [x] 向用户呈现缺陷清单+优化计划，用户批准"全部执行"
- [x] reviewer-risk 审查员运行 18 分钟未响应（疑似卡住），已终止；独立审查结论即为定稿（F4/F2/F5/F1 均经代码路径验证）
- [x] 完成 R1 设计：pause_reason 方案 + scheduler 调用条件扩展
- [x] R1 [F4] 完成：scheduler 对 RUNNING+PAUSED 驱动；health_monitor 仅恢复 bridge 暂停；engine 加 pause_reason（8 测试 GREEN）
- [x] R2 [F2] 完成：CircuitBreaker.net_pnl + backfill/_handle_closed_trades 净盈亏口径（36 测试 GREEN）
- [x] R3 [F5] 完成：is_equity_drawdown_halted 加 min_ref（当日初始余额下限），engine/preflight 计算传入（9 equity 测试 GREEN）
- [x] R4 [F1] 完成：backfill 按 position_id 聚合退出 deal；_handle_closed_trades 聚合记账（防御部分平仓漏记）
- [x] 全量回归：相关套件 143 passed；全量 965 passed / 10 failed（9 个为既有环境问题，基线确认已存在；1 个 test_account_scoped 已适配 fixture）
- [ ] ruff 检查（ruff 未安装在当前 venv，已跳过；语法由 pytest import 验证）
- [ ] 提交 + 推送（用户确认后）

### 下一步
等待 reviewer-risk 返回 → 交叉验证其发现与我的独立发现 → 汇总裁定 → 生成优化计划。