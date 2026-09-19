# Progress Log

## Session: 2026-09-19

### Current Status
- **Phase:** Phase 0(存量漏洞修复)接近完成,待解决 test_gold_reads_profile 全量污染疑云
- **Started:** 2026-09-19

### Actions Taken
- 2026-09-19:三路 Explore 探索(执行链/AI 层/前端)+ 三路 critic 评审(安全/架构/可行性),计划迭代至 v2 并获用户批准
- 2026-09-19:init-session 建计划目录;task_plan.md/findings.md/progress.md 落盘
- 2026-09-19:**Phase 0 实施完成**:
  - 新建 `app/services/position_close.py`(close_position_gated:switching fail-closed→ticket 归属→rollout 拦截→平仓→记账(有引擎品种跳过防双计)→TRADE_CLOSED 事件+WS 推送)
  - `routes/positions.py` DELETE 收口到 gated 路径
  - `connector._request` 加 `retry_ambiguous` 参数(下单 POST 不重试歧义失败,防双开仓);executor `_NO_RETRY_ERRORS` 加 timeout/timed out
  - 新测试 `tests/unit/test_position_close.py` 10 个(门禁矩阵/记账/双计规避/WS payload)

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| test_position_close.py | 10 passed | 10 passed | ✅ |
| executor/guardrails/broker/lot 回归 4 文件 | all pass | 62 passed | ✅ |
| 全量 pytest | 仅既有失败 | 13 failed / 861 passed | ⚠️ |
| 既有失败基线(stash 复跑) | — | multi_agent 7 + test_engine 4 + ml_barrier 1 均改动前已失败;backtest_consistency 1 个待查 | 🔍 |

### Errors
| Error | Resolution |
|-------|------------|
| 测试 rollout 默认 shadow 拦截成功路径 | live_mode fixture 显式 set |
| pytest_asyncio 未导入 | 补 import |
| CircuitBreaker key 测试口径不一致 | 断言按 get_canonical_symbol 同口径计算 |
| test_gold_reads_profile 全量失败/单跑通过 | 待查(疑 SYMBOL_PROFILES 测试污染,需 stash 全量基线) |

- 2026-09-19:**Phase 0 完成**——全量基线(stash 后)同样 13 failed/861 passed,含 test_gold_reads_profile 亦为存量污染,零回归。
- 2026-09-19:**Phase 1 完成**——Bridge 挂单 4 端点 + `_retcode_reject` 结构化 retcode(补齐存量端点)+ conftest 常量修正(IOC=1)/函数挂载 + test_pending_orders.py 28 测试;mt5_bridge 36/36 全绿。
- 2026-09-19:**Phase 2 完成**——order_preflight.py 共享闸门(12 测试);broker.place_order 重构调用(66+37 回归全绿);connector 4 挂单方法;OrderAudit 扩展迁移 a9b8c7d6e5f4(单 head);MANUAL_MAGIC_NUMBER。坑:normalize_lot_to_volume_grid 对缺失配置原样放行 → strict 检查必须前置。
- 2026-09-19:**Phase 3 完成**——manual_order_gate.py(提交/审查/确认/撤单/改SLTP)+ prompts.py ORDER_REVIEW prompt + preflight 挂单方向参数(direction/entry_price)。测试 20/20。坑:①AsyncMock 未配置子属性 await 后仍是 AsyncMock(.get 返回 coroutine)——夹具必须显式配置;②wait_for 不能包住 review+execute 全程(执行中途取消会留孤行)——只约束 LLM 调用;③零 SL 订单被 guardrails validate_order 硬拒(存量行为,no_stop_loss flag 仅防御性)。
- 2026-09-19:**Phase 4 完成**——manual_trading.py 路由 9 端点 + main.py lifespan/路由接线;离线 alembic SQL 验证;路由测试 17/17(坑:FastAPI 默认 200,PENDING_REVIEW 需显式 JSONResponse 202)。
- 2026-09-19:**Phase 5 完成**——/trading 页 + ReviewResultCard/PositionsTable 组件 + api.ts 接口 + i18n×2 + 导航。tsc/build 通过。偏离记录:dashboard 持仓表未迁移到共享 PositionsTable(无前端测试,回归风险>收益),列为后续项。
- 2026-09-19:**Phase 6 完成**——新增 87 测试全绿;全量 909 passed/13 failed(与改动前基线核对为同一批存量失败,零回归);bridge 36/36;tsc+build 通过。
- 2026-09-19:**Phase 7 完成**——CLAUDE.md 更新(防火墙不变量/retcode 语义/key directories)。**全部 Phase 0-7 完成。**
