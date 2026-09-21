# Findings & Decisions

## Requirements

- 用户报告：仪表盘点击"启动机器人"后机器人无法启动，日志报错。
- 要求：先分析原因、生成优化计划，经用户同意后才能执行修复。本次只做调查+计划，不改代码。

## 启动链路（已追踪确认）

```
[前端] frontend/app/dashboard/page.tsx:229 handleStart → lib/api.ts:38 POST /api/bot/start?symbol=<sym>
[后端] app/api/routes/bot.py:92-103 start_bot → app/bot/manager.py:145-160 mgr.start(symbol) → engine.start()
[引擎] app/bot/engine.py:295-322 engine.start(): state=RUNNING + started_at=now + 种known tickets + 读sentiment + _log_event
[后台] app/bot/scheduler.py:559 sync_positions 每30s；scheduler.py:837 status_update 每15s(WS广播)
```

## 根因（实证，100% 确认）

**commit 7f5ed99 `fix(account-switch)`（2026-09-19 17:06）把 `BotEngine.__init__` 拦腰截断。**

- 该 commit 在 `__init__` 中部（原 `self._known_tickets` 之后，engine.py:222）插入了 `def set_account_login()`（engine.py:224），原属于 `__init__` 的约 25 行属性初始化（现 engine.py:231-256）因未缩进处理被整体并入新方法体：
  - `fixed_lot`、`paper_trade`、`_paper_positions`、`_paper_ticket_counter`、`_paper_balance`
  - `_event_calendar`、`_last_regime`、`_multi_tf_regime`
  - `trailing_stop_enabled`、`trailing_start_atr`、`trailing_step_atr`
  - `_position_atr`、`_position_atr_pct`、`_position_entry_time`、`_position_group`、`_position_partial_closed`、`_position_breakeven`
  - `last_signal_time`
- `set_account_login` 只有一处调用：`manager.py:183`（`set_current_account`，账号切换路径）。`_build_engine`（manager.py:274-297）构造引擎后**不调用**它 → 正常启动构造的引擎从未初始化这些属性。
- 证据（git show 7f5ed99 -- backend/app/bot/engine.py）：diff hunk `@@ -221,6 +221,15 @@` 显示方法定义插在 `_known_tickets` 与 `# Lot sizing mode` 之间，后续行未重新缩进。

## 后果链（为什么"点启动没反应"）

1. `engine.start()` 本身**成功**：`started_at` 是赋值（engine.py:299），Python 允许新建属性 → bot.log 08:41:26 `Bot started: strategy=ema_crossover, symbol=GOLD`，HTTP 返回 200，按钮点击"有效"。
2. **读取**缺失属性的路径全部崩：
   - `sync_positions` engine.py:1214 读 `self.paper_trade` → 每 30 秒 AttributeError（bot.log/errors.log 持续出现，最近一条 08:54:45 `Position sync error: 'BotEngine' object has no attribute 'paper_trade'`）
   - `get_status` engine.py:349/354/355 读 `paper_trade`/`fixed_lot`/`_last_regime` → `/api/bot/status` 与 WS `status_update` 广播失败（scheduler.py:837-845 的 except 吞成 debug）→ 前端 isRunning 永远 false，状态永远非 RUNNING → **用户感知"无法启动"**
   - 真实交易路径：`get_lot_size` engine.py:932 读 `fixed_lot`、`send_order` engine.py:959/962 读 `paper_trade` → 即使状态置 RUNNING 也无法下单
3. 结论：机器人处于"假启动"——状态被置为 RUNNING，但没有一个后台链路能正常工作。

## 测试为何没拦住回归

- `backend/tests/integration/test_engine.py:24` fixture 手工 `engine.paper_trade = True`，掩盖缺失属性；`test_get_status` 依赖该掩盖。`test_start` 借助 start() 内赋值恰好通过。
- commit 声明"所有相关测试均已通过"，实为测试结构掩盖，非真实构造路径验证。

## 运行时证据（2026-09-21 实测）

- 后端进程 29253 运行于 8002，跑的是含 bug 代码（4:18PM 启动，未重启）。
- MT5 bridge / Redis / DB / LLM 当前均健康，**不在阻塞路径**。
- bot.log 尾部还有与本次无关的次要问题（不阻塞启动主链路）：
  - `mcp_server.agents.openai_loop:_execute_tool:105 check_correlation execution error: 'str' object ...`
  - `backtest:_run_permutation:399 / _run_monte_carlo:413 Unknown strategy: Trend Following`
  - `strategy_optimizer:optimize:171 bool not JSON serializable`

## 潜伏缺陷（顺手可修）

- `_build_engine`（manager.py:274-297）不调用 `set_account_login` → 账号切换后 `reload_engines` 新建的引擎 `account_login` 翻回 "0"（H4 账号隔离不完整）。**已随本次修复处理**（_build_engine 返回前补调）。
- 附加发现：被并进 `set_account_login` 的 `MacroEventCalendar(redis_client)` 引用了 `__init__` 的局部参数 `redis_client`——方法体内该名字未定义，若真实触发账号切换会 NameError。**已随属性移回 `__init__` 自动消除**，无需单独处理。

## 修复实施记录（2026-09-21 获批后执行）

- `backend/app/bot/engine.py`：属性初始化（fixed_lot/paper_trade/_paper_*/trailing_stop_*/_position_*/_event_calendar/_last_regime/_multi_tf_regime/last_signal_time，现 224-260 行）移回 `__init__`；`set_account_login` 精简为 account_login 赋值 + circuit_breaker 重建（H3 key 带账号维度语义不变）。
- `backend/app/bot/manager.py`：`_build_engine` 返回前 `engine.set_account_login(self.current_account_login)`（manager.py:52 已初始化默认 "0"）。
- `backend/tests/integration/test_engine.py`：
  - fixture 去掉掩盖性 `engine.paper_trade = True`；
  - 新增 `test_default_attributes_initialized`（构造后不调用 set_account_login，断言默认属性存在 + get_status 返回 paper_trade/fixed_lot）；
  - `test_paper_trade_mode` 内显式 `engine.paper_trade = True`（保持原语义）。

## 验证结果（2026-09-21）

| 验证项 | 结果 |
|--------|------|
| pytest test_engine.py + test_lot_volume_guard.py + test_account_switch.py | **34 passed**（含新增回归测试） |
| 重启后 /api/bot/status | HTTP 200，paper_trade/fixed_lot/regime/multi_tf_regime 字段完整 |
| POST /api/bot/start?symbol=GOLD | 200 → state RUNNING，started_at 已设置 |
| 启动后 sync_positions（每 30s） | 09:05:00 后 paper_trade 错误 **0 条** |
| 重启瞬间旧进程残留 | 09:04:45 一条（旧进程 29253 收尾最后一轮，其后归零） |
| 引擎最终状态 | STOPPED（验证后已停止，控制权交回用户） |
