# Findings & Decisions

## Requirements
- 品种管理数据与当前 MT5 账号绑定:切换后展示当前账号的品种;新增品种可查询可用。
- 修复仪表盘右上角品种实时行情不展示的问题。
- 计划需用户批准后才执行。

## Research Findings

### 运行时证据(2026-09-26 只读查询远端 DB)
- `mt5_accounts`: 2 个账号,**同券商 XM 不同服务器**
  - id=1, login=110351809, server=XMGlobal-MT5 2, is_active=False
  - id=2, login=336773771, server=XMGlobal-MT5 9, is_active=True
- `symbol_configs`: 7 行,**全部 is_enabled=False**,别名是旧账号的(XM 不同服务器符号命名不同,如 GOLD→`GOLD_`)

### 问题 1:品种配置全局共享,不随账号切换
- `backend/app/db/models.py:444` — `SymbolConfig.symbol` 全局 `unique=True`,**无 account_id 字段**。
- `backend/app/services/symbol_config_service.py` — `list_configs/get_config/load_profiles_from_db` 无账号过滤;`SYMBOL_PROFILES` 进程内存全局单例。
- `backend/app/api/routes/symbols.py:633` — `/broker-catalog` Redis 缓存 key 固定 `xm:catalog:v2`,TTL **1 小时**,切号不清除 → Add Symbol 下拉仍是旧服务器目录,新账号品种搜不到("新增加品种后无法查询")。
- `frontend/app/symbols/page.tsx:103` — 前端 catalog 每次进页只拉一次(`if (catalog || catalogLoading) return`),无刷新途径。
- `backend/app/bot/manager.py:315` — `reload_engines` 从全局 `SYMBOL_PROFILES` 取 is_enabled;**全 disabled 时退回 `settings.symbol_list` 静态默认**(品种名与当前券商不匹配)。

### 问题 2:右上角行情链路在切号后断链
推送链:`scheduler._tick_job` → `engine.market_data.get_current_tick` → `to_broker_alias`(查全局 SYMBOL_PROFILES,旧别名)→ bridge `/tick/{alias}` → 成功才 `_push_event("price_update")` → WS → 前端 `botStore.ticks` → `activeTick = ticks[activeSymbol] || tick` → `{activeTick && ...}` 条件渲染(`frontend/app/dashboard/page.tsx:287-303`)。
断链点(切号后任一命中即无声消失):
1. 旧 `broker_alias`(如 `GOLD_`)在新服务器不存在 → bridge "No tick data"(`market_data.py:29` 返回 None,无推送)。
2. `MAX_TICK_AGE_SECONDS=30`(`market_data.py:17`):新服务器休市/tick 过期 → None。
3. 引擎集合退回静态 `settings.symbols`,品种名在新券商不存在。
前端展示端无异常,纯粹是收不到 `price_update`。

### 切号流程现状(`backend/app/bot/account_switch.py:67 switch()`)
- stop engines → bridge `/account/switch`(仅 `mt5.login`,无品种重选)→ 更新 is_active → `load_profiles_into_memory()`(全局)→ `_validate_symbols()` → `reload_engines()` → start()。
- **catalog 缓存未失效**;**无账号变更通知前端**;profiles 加载不区分账号。

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| `symbol_configs` 加 `account_id` + 唯一约束 (account_id, symbol) | 用户需求即"按账号绑定";canonical symbol 字符串保持不变,positions/trades/ml 等历史表无需迁移 |
| 存量 7 行回填到当前活跃账号(account 2) | 用户切完号在当前账号上操作;切回旧账号后可自行重建配置 |
| catalog 缓存 key 改 `xm:catalog:v2:{login}` | 天然按账号隔离,免失效逻辑;旧 key 1h TTL 自然过期 |
| `load_profiles_into_memory` 只加载活跃账号 | 切号流程已调用该函数,无需新钩子;MCP/runner 共用同函数 |
| `reload_engines` 兜底修正:DB 有配置(哪怕全 disabled)不再退回静态默认 | 静态默认品种名与当前券商不匹配是行情断链诱因之一 |
| 前端:打开 Add Symbol 对话框即 refetch catalog;切号成功后全局刷新 | 现状每页只拉一次,切号后看到 stale 目录 |
| 可选:切号后 WS 广播 `account_switched` | 前端各页可即时刷新,不做则依赖页面级 refetch |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| 本机 logs/backend.log 停在 09-14,后端当前未运行 | 诊断基于代码 + 远端 DB;联调阶段需启动服务验证 |
| mt5_accounts 无 broker_alias 列(首查误写) | 已修正查询字段 |

## Resources
- 切号测试: backend/tests/unit/test_account_switch.py
- 品种 API 测试: backend/tests/integration/test_api_symbols.py
- 迁移注意: alembic 需 DATABASE_URL_SYNC + PYTHONPATH(见 memory backend-deployment-topology)
