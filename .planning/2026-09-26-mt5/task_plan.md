# Task Plan: MT5 账号切换品种绑定 + 仪表盘行情修复

## Goal
品种管理数据按当前 MT5 账号隔离(切换后看到当前账号的品种、可新增可查询),并修复切号后仪表盘右上角实时行情不展示。

## Next Step
全部阶段完成。待办:周一开盘后确认非加密品种行情自动恢复;如需切回 MT5 2 账号,在品种页为该账号重建品种配置。

## Current Phase
完成(Phase 1-5 全部 complete)

## Phases

### Phase 1: 数据库迁移与模型 — account_id 绑定
- [ ] alembic 迁移:`symbol_configs` 加 `account_id BIGINT NULL REFERENCES mt5_accounts(id)`
- [ ] 唯一约束 (symbol) → 唯一索引 (account_id, symbol)(表达式索引兜底 NULL)
- [ ] 回填:存量行 account_id = 当前活跃账号 id
- [ ] `models.py` SymbolConfig 模型同步 + 建表兜底逻辑更新
- **Status:** complete

### Phase 2: 服务层与路由按账号隔离
- [ ] `symbol_config_service`: list_configs / get_config / load_profiles_from_db 按活跃账号过滤
- [ ] `load_profiles_into_memory`: 只加载活跃账号配置
- [ ] symbols 路由: create 写入 account_id;别名冲突检查按账号;list/get/toggle/delete 天然隔离
- [ ] 新增 helper: 获取当前活跃账号 id(带缓存,失败 fail-closed 报错明确)
- **Status:** complete

### Phase 3: 切号联动与行情链路修复
- [ ] `/broker-catalog` 缓存 key → `xm:catalog:v2:{login}`
- [ ] `reload_engines` 兜底修正:DB 有配置(哪怕全 disabled)不再退回 settings.symbol_list;仅无任何配置时退回并告警
- [ ] 切号成功后:发布 WS `account_switched` 事件;校验 `_validate_symbols` 对缺失品种的日志可见性
- [ ] 行情胶囊断链护栏:tick 连续失败超阈值时 push 一次诊断事件(可选,视改动风险)
- **Status:** complete

### Phase 4: 前端
- [ ] symbols 页: 打开 Add Symbol 对话框强制 refetch catalog(去掉只拉一次 guard 或按 activeAccount 依赖)
- [ ] accounts 页: 切号成功后触发全局刷新(symbols/status/botStore ticks 清空)
- [ ] dashboard: 行情胶囊无数据时显示"等待行情…"占位(替代无声空白)
- [ ] WS 订阅 account_switched → 全局 refetch + 清 stale ticks
- **Status:** complete

### Phase 5: 测试与验证
- [ ] 单测: 账号隔离(list/get/create/alias 冲突/切号后 profiles 重载)、catalog key、reload_engines 兜底
- [ ] 既有测试回归: test_account_switch / test_api_symbols / test_sl_tp_modes
- [ ] alembic 迁移在远端 DB 执行(DATABASE_URL_SYNC + PYTHONPATH)
- [ ] 启动服务联调: 切号 → 品种列表/新增目录 → 仪表盘行情恢复
- **Status:** complete

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 绑定键用 account_login(字符串) 而非 account_id | 与 trades/bot_events 既有约定一致,免 join |
| 存量品种全部恢复 is_enabled=True | 用户此前经 UI 逐个关闭疑为排障;行情推送不依赖引擎 start(交易仍 STOPPED) |
| connector.get_symbol_spec 取消二次别名映射 | 全部调用方已传券商名;二次映射会让"改别名"被旧值顶回 |
| 周末休市导致的行情缺失不属缺陷 | MAX_TICK_AGE_SECONDS=30 新鲜度护栏正确行为 |

| 存量品种回填到当前活跃账号(account 2) | 用户切完号在当前账号操作;旧账号切回后可重建配置 |
| canonical symbol 字符串不变,仅配置行按账号隔离 | positions/trades/ml_prediction_logs 按 symbol 字符串关联,免历史数据迁移 |
| catalog 缓存按 login 分 key 而非切号失效 | 实现最简,天然隔离,旧 key 1h 自然过期 |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| 首查 mt5_accounts.broker_alias 列不存在 | 该表无此列,已修正查询 |
