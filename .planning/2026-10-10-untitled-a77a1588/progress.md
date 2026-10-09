# Progress Log

## Session: 2026-10-10（M1+M2 实施）
### Current Status
- **Phase:** M2 完成（M1 时区基础设施 + M2 P0 硬闸门）

### Actions Taken
**M1（时区+基础设施）**
- [x] bridge F1：8 处时间输出 _iso_utc（UTC 带偏移）+ leverage 字段 + 输入侧 4 处 _parse_utc_input
- [x] 后端 F2：engine 4 处 + history 3 处 + analytics 1 处 + manual_order_gate 1 处 → parse_bridge_time_to_naive_utc
- [x] discipline.py（新）：外汇日时钟 + 周期号 + until + 休息日 + 时间收敛
- [x] config.py：discipline_* 系列 + guardrails 3 常量去硬编码
- [x] guardrails.py：日界走 22:00 UTC + _env_limit
- [x] 前端 F3：toDate + Asia/Shanghai，覆盖 15+ 文件
- [x] F6 回填脚本 + F5 CLAUDE.md

**M2（P0 硬闸门）**
- [x] engine fail-closed + engine_discipline_enabled 开关
- [x] discipline_gate.py（新）：休息日/周月熔断/冷却/反手/次数/保证金
- [x] preflight 6c 步骤 + channel 参数（三处调用方）
- [x] CircuitBreaker 周/月 PnL + period halt
- [x] guardrails 跨日 streak（loss_streak）
- [x] BotEvent.account_login

### Test Results
| 套件 | 结果 |
|------|------|
| test_discipline_timezone.py | 15 passed |
| test_discipline_gate.py | 8 passed |
| M1+M2 相关（guardrails/circuit/preflight/manual_gate/engine/history/bot） | 167 passed |
| M3 后全量后端 | 1130 passed / 8 failed（全部 test_multi_agent，既有非回归，模型配置差异） |
| 前端 build | Compiled successfully |

### Errors
| Error | Resolution |
|-------|------------|
| 测试 _loss_deal naive UTC 被当 EET | mock 改带 +00:00（新 bridge 语义） |
| fakeredis 不支持 eval | streak 改 GET+SET |
| _balance 未 await / time NameError / bytes 比较 | 修复 discipline_gate |
| discipline_local_now 模块级绑定不生效 | 延迟 import（测试可 monkeypatch） |
| 全量 24 失败（WEEKEND_CLOSE 误拦） | test_mcp_broker_guard mock 区分 discipline:cfg: key；总开关顺序 settings 优先 |
| get_runtime_setting 默认值绕过 conftest | 回退 settings 字段（env/conftest 可覆盖） |

### Next
- M4（P2）：3c 逆势 CAUTION + 限次豁免（systemone direction_flip）+ 4b 冲动冷却阶梯
- M5（P3）：4c 复盘统计页 + 防拆单
