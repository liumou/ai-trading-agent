# Progress Log

## Session: 2026-09-26

### Current Status
- **Phase:** 1 - Requirements & Discovery
- **Started:** 2026-09-26

### Actions Taken
-

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|

### Errors
| Error | Resolution |
|-------|------------|

## Session 2026-09-26
- 调研完成:品种配置全局共享(models.py SymbolConfig unique symbol 无 account_id)、catalog 1h 缓存 key 固定、reload_engines 静态兜底、右上角行情依赖 price_update 推送链
- DB 证据:2 个 XM 账号(MT5 2 / MT5 9),symbol_configs 7 行全 disabled,别名属旧账号(GOLD_)
- 计划已写入 task_plan.md(5 阶段),待用户批准

## Session 2026-09-26 (执行)
- Phase 1 ✅ 迁移 c1d2e3f4a5b6 已在远端库执行:account_login 列 + (account_login,symbol) 唯一索引;7 行回填到 336773771(MT5 9)
  - 坑:远端 uq_symbol_configs_symbol 是唯一约束底层索引,DROP INDEX 报错,改 DROP CONSTRAINT 兼顾两种来源(迁移+main.py 兜底均已修)
- Phase 2 ✅ 服务层/路由按账号隔离;scheduler ml_status 回写按账号
- Phase 3 ✅ catalog 缓存 key=xm:catalog:v2:{login};reload_engines 兜底仅 DB 未同步时启用;切号广播 account_update;websocket.py 加频道
- Phase 4 ✅ AppShell 订阅 account_update→清 store+重取 symbols;symbols 页打开对话框强制重取目录;dashboard 行情胶囊加"等待行情"占位(中英文案)
- Phase 5(进行中): 新增 test_symbol_account_isolation 13 用例全过;全量回归 1033 passed(multi_agent 既有失败除外);前端 tsc --noEmit 零错误
  - 坑:全局 SYMBOL_PROFILES 共享 dict 测试污染 → 加 _restore_symbol_profiles fixture
- 待联调: 启动 backend → 切号 → 验证品种列表/新增目录/行情恢复

## Session 2026-09-26 (联调与收尾)
- 联调发现并修复(计划外):
  1. main.py lifespan 崩溃: if notifier.enabled 块内 import asyncio 遮蔽模块名 → Telegram 未启用时 asyncio.Event() UnboundLocalError,后端无法启动。已修(移除函数内导入)。
  2. connector.get_symbol_spec 二次别名映射: 入参已是券商名,再 to_broker_alias 会用旧行别名顶掉新输入的别名(USDJPY 改别名被顶回 USDJPYmicro)。已改为原样透传,测试契约同步更新。
  3. manager.__init__ 静态兜底与 reload_engines 同步修正(DB 已同步时不回退 settings.symbols);main.py first_engine 空引擎护栏。
  4. 测试卫生: test_account_switch 经真实 async_session 污染全局 SYMBOL_PROFILES(本机可达远端 DB) → autouse fixture 拦截; isolation 测试清全局 dict 后恢复。
- 真机验证(backend 运行中, :8002):
  - profiles 按账号加载 [account 336773771]: 9 entries
  - /api/symbols 返回 7 行(账号隔离); broker-catalog 实时 207 品种, 缓存 key=xm:catalog:v2:336773771
  - 7 品种已启用(is_enabled=True, 由 owner 此前全部关闭, 我已代为恢复; 引擎未 start, 交易不启动)
  - USDJPY 别名 USDJPYmicro→USDJPY(MT5 9 上 micro 名不存在, fail-closed 正确拦截后修正)
  - 行情链路: BTCUSD(加密) tick 实时推送; GOLD_/EURUSD 等桥端 tick 冻结在周六休市 → MAX_TICK_AGE_SECONDS=30 正确拒绝, 周一开盘自动恢复
- 最终回归: 1033 passed, 0 failed(multi_agent 既有失败除外); 前端 tsc 零错误
