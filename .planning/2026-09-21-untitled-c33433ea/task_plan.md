# Task Plan: 仪表盘启动机器人失败排查与修复

## Goal

修复 `BotEngine` 属性初始化被 commit 7f5ed99 错误并入 `set_account_login()` 导致的"启动机器人假启动"问题，使仪表盘点启动后机器人真实进入 RUNNING 并可正常交易，且回归测试从真实构造路径防住该 bug。

## Next Step

全部完成。用户可在仪表盘点启动验证 UI 表现；如需提交代码可告知。

## Current Phase

Phase 5（交付，已完成）

## Phases

### Phase 1: 需求与根因调查
- [x] 定位启动链路（按钮 → API → manager.start → engine.start）
- [x] 确认根因：`__init__` 被 `set_account_login` 拦腰截断（git show 7f5ed99 实证）
- [x] 确认后果链：get_status / sync_positions / 下单路径读取缺失属性崩溃
- [x] 确认测试掩盖点：test_engine.py:24 fixture 手工赋值 paper_trade
- **Status:** complete

### Phase 2: 修复方案设计
- [x] 属性初始化归位 `__init__`，`set_account_login` 只保留账号/熔断器逻辑
- [x] 防御：`_build_engine` 同步 `engine.set_account_login(self.current_account_login)`
- [x] 回归测试：真实构造路径断言默认属性 + get_status/sync_positions 不抛错
- **Status:** complete

### Phase 3: 修复实施
- [x] `backend/app/bot/engine.py`：属性初始化移回 `__init__`（现 224-260），set_account_login 精简为账号+熔断器
- [x] `backend/app/bot/manager.py`：`_build_engine` 返回前 `engine.set_account_login(self.current_account_login)`
- [x] `backend/tests/integration/test_engine.py`：fixture 去手工赋值；新增 test_default_attributes_initialized；test_paper_trade_mode 内显式 paper_trade=True
- **Status:** complete

### Phase 4: 测试与验证
- [x] pytest：test_engine.py + test_lot_volume_guard.py + test_account_switch.py → **34 passed**
- [x] 重启后端（新 PID 34870，旧 29253 已停）
- [x] `/api/bot/status` 200 + paper_trade/fixed_lot/regime 字段正常
- [x] 模拟启动 GOLD → RUNNING + started_at 设置；09:05:00 后 paper_trade 错误 **0 条**
- [x] 引擎已停止（STOPPED），控制权交回用户
- **Status:** complete

### Phase 5: 交付
- [x] 汇总变更、测试结果、验证证据
- [x] 更新 progress.md / findings.md 收尾
- **Status:** complete

## Key Questions

1. 修复后是否需要用户手动重启后端，还是由我（获批后）直接重启验证？→ 默认：由我重启验证（需用户同意重启，因会中断当前进程）
2. 附带的次要问题（check_correlation 'str' object、backtest Unknown strategy、strategy_optimizer bool 序列化）是否纳入本次范围？→ 默认：不纳入，仅记录，另行处理

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| 属性初始化移回 `__init__`（而非在 _build_engine 强制调 set_account_login） | 恢复 7f5ed99 之前的正确构造语义，所有构造路径自动获得默认属性；set_account_login 语义保持切换专用 |
| `_build_engine` 补调 set_account_login(current_account_login) | 堵住 H4 潜伏缺陷：reload 新建引擎 account_login 翻回 "0" |
| 回归测试走真实构造路径，去掉 fixture 手工赋值 | 测试曾掩盖该 bug，必须有"不手工赋值也能构造成功"的断言 |
| 次要问题不在本次修复范围 | 与"启动机器人"无关，避免范围蔓延；在 findings.md 记录备查 |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| （调查阶段无执行错误） | - | - |

## Notes

- 当前引擎处于"假 RUNNING"：start() 把状态置为 RUNNING 但所有后台链路崩溃。修复+重启后引擎回到 STOPPED，需用户在仪表盘重新点启动验证。
- 日志文件：`backend/logs/bot.log`、`backend/logs/errors.log`（loguru JSON Lines 格式）。
- 关键文件：backend/app/bot/engine.py（224-256 + 299/349/354/932/959/1214）、backend/app/bot/manager.py（145-160/183/274-297）、backend/tests/integration/test_engine.py:24。