# Findings — Code Review of commit b8f1bbd

## 审查方法

OCR（open-code-review v1.12.8）运行 `ocr review --audience agent --commit HEAD` 失败：LLM 后端返回 HTTP 404（17/17 文件全部 rejected by provider）。判定为 OCR 自身配置问题（API key/base URL），非代码问题。回退为人工专业审查：完整读取 diff（944 行）+ grep 验证调用方 + Read 确认关键路径。

## 被审查提交

- **commit**: `b8f1bbd`
- **message**: `docs(findings): 添加黄金价格提醒调查分析文档`
- **实际内容**: MT5 账号隔离功能（944 行，26 文件）
- **commit message 与实际内容严重不符** —— 这是问题 1 的核心

## 实际变更范围

| 模块 | 变更 |
|------|------|
| Alembic 迁移 | `c1d2e3f4a5b6_add_symbol_account_login.py`（新）—— symbol_configs 加 account_login，唯一约束 (symbol) → (account_login, symbol) |
| model | `SymbolConfig` 加 `account_login` 字段 + `UniqueConstraint` |
| service | `symbol_config_service.py` —— list/get/create/load 全部按当前活跃账号隔离；新增 `get_current_account_login` |
| routes | `symbols.py` —— bootstrap、alias 碰撞、broker-catalog、create 均限账号；broker-catalog 缓存 key 加 `:account_login` |
| scheduler | `_set_symbol_ml_status` 按 manager.current_account_login 限定更新 |
| manager | `__init__` 与 `reload_engines` —— DB 已同步但无启用品种时不再回退静态 settings.symbol_list |
| config | 新增模块级全局 `SYMBOL_PROFILES_DB_SYNCED` 标记 |
| main.py | lifespan `schema_stmts` 重复迁移 DDL；`first_engine` 可空兜底；修复 `asyncio` 函数内 import 遮蔽 |
| connector | `get_symbol_spec` 不再做 `to_broker_alias` 映射（语义变更） |
| account_switch | 切号成功后 publish `account_update` 到 Redis |
| websocket.py | CHANNELS 加 `account_update` |
| 前端 | AppShell 订阅 `account_update` 重置 store；symbols 页 openCreate 强制重取目录；dashboard 等待行情占位；botStore 加 `resetAccountScopedData` |
| 测试 | 新增 `test_symbol_account_isolation.py`（322 行）；修改 `test_account_switch.py`、`test_market_data_alias.py` |

## 问题详情

### 问题 1 — commit message 误导（HIGH，已提交无法回溯）

commit `b8f1bbd` 消息为 `docs(findings): 添加黄金价格提醒调查分析文档`，但实际是 944 行核心代码变更（账号隔离 + 迁移 + connector 语义变更）。违反 conventional commits 与项目 git-workflow 规范。影响后续 git log 追溯、revert 决策、release notes 准确性。

**不可修复**（已提交，`main` 分支 ahead 1 未 push，理论上可 `git commit --amend` 但会改写历史，需用户决定）。

### 问题 2 — manager.py 行内 import 违规（HIGH，可立即修）

`app/bot/manager.py` 在两处方法体内执行行内 import：
- `__init__`（约行 67）：`from app.config import SYMBOL_PROFILES_DB_SYNCED`
- `reload_engines`（约行 317）：`from app.config import SYMBOL_PROFILES_DB_SYNCED`

模块顶部已有 `from app.config import settings, ...`。应合并到顶部。行内 import 违反 coding-style，且 `reload_engines` 每次调用都执行 import 语句（虽 Python 有缓存，但风格不佳）。

**修复**：顶部 `from app.config import settings, SYMBOL_PROFILES_DB_SYNCED`，删除两处行内 import。

### 问题 3 — SYMBOL_PROFILES_DB_SYNCED 跨进程不同步（HIGH，架构权衡）

`app/config.py` 用模块级 `global SYMBOL_PROFILES_DB_SYNCED = False`，`apply_db_symbol_profiles` 中 `global` 修改。这是进程级状态。

问题：MCP server stdio 子进程、agent runner（Docker sandbox）各自有独立 Python 进程，该标记副本独立。主进程切号后调用 `load_profiles_into_memory` 更新主进程标记，但子进程标记不变 —— 子进程的 `reload_engines`（若有）仍可能启用静态兜底。

注释（config.py:128-132、load_profiles_into_memory docstring）提及子进程加载但未真正解决跨进程一致性。

**权衡方案**（待讨论）：
- A. Redis 存 `symbol_profiles:synced:{account_login}` 标记，子进程启动时读取
- B. 子进程启动时强制调用 `load_profiles_into_memory` 自检（已是 agent_entrypoint 行为，但 MCP server stdio 未确认）
- C. 接受现状（子进程不跑 reload_engines，只读 SYMBOL_PROFILES 内存映射）

### 问题 4 — 测试未覆盖 list_configs(include_disabled=False)（HIGH，可立即修）

`test_symbol_account_isolation.py::TestScopedQueries` 只调 `list_configs(two_accounts)`（默认 `include_disabled=True`）。`include_disabled=False` 路径的 `where(SymbolConfig.is_enabled.is_(True))` 逻辑未被断言。

低于 CLAUDE.md 要求的 80% 覆盖。`_seed_symbol` 已支持 `is_enabled` 参数，补用例成本低。

**修复用例**：
```python
@pytest.mark.asyncio
async def test_list_filters_disabled_when_requested(self, two_accounts):
    await _seed_symbol(two_accounts, "GOLD", "111", is_enabled=True)
    await _seed_symbol(two_accounts, "SILVER", "111", is_enabled=False)
    enabled = [c.symbol for c in await svc.list_configs(two_accounts, include_disabled=False)]
    assert enabled == ["GOLD"]

@pytest.mark.asyncio
async def test_list_disabled_filter_scoped_to_account(self, two_accounts):
    await _seed_symbol(two_accounts, "GOLD", "111", is_enabled=True)
    await _seed_symbol(two_accounts, "SILVER", "222", is_enabled=True)  # 222 非活跃
    enabled = [c.symbol for c in await svc.list_configs(two_accounts, include_disabled=False)]
    assert enabled == ["GOLD"]  # 222 的启用品种不串入
```

### 问题 5 — Alembic 迁移与 main.py DDL 双写（HIGH，中等改动）

`main.py` lifespan `schema_stmts`（行 392-402）与迁移文件 `c1d2e3f4a5b6` 的 DDL 完全重复：
- `ALTER TABLE symbol_configs ADD COLUMN IF NOT EXISTS account_login ...`
- `UPDATE symbol_configs SET account_login = COALESCE(...)`
- `DROP CONSTRAINT/INDEX uq_symbol_configs_symbol`
- `CREATE UNIQUE INDEX uq_symbol_configs_account_symbol`
- `CREATE INDEX ix_symbol_configs_account_login`

CLAUDE.md "Alembic" 小节明确"Never reuse revision IDs"，且 main.py 的 schema_stmts 是"幂等兜底"（用于 create_all 路径 / 测试 SQLite）。但双套真相源需后续每次 schema 变更同步两处，易漂移。

**修复方案**（待讨论）：提取 `app/db/schema_sync.py` 单一函数，返回 DDL 语句列表，迁移与 lifespan 共用。或接受现状（main.py 兜底有其存在价值，CLAUDE.md 已知此模式）。

### 问题 6 — broker-catalog except Exception 过宽（HIGH，需讨论）

`app/api/routes/symbols.py` 行 144-148：
```python
try:
    account_login = await svc.get_current_account_login(db)
except Exception as e:  # noqa: BLE001
    logger.warning(f"broker-catalog: resolve active account failed ({e}); using '0'")
    account_login = "0"
```

DB 真正挂掉时，所有账号都退化到 `xm:catalog:v2:0` 缓存 —— 跨账号串目录数据（A 账号的目录缓存被 B 账号读到）。`logger.warning` 已有但未 fail-closed。

**权衡**：目录请求是只读辅助功能，fail-closed（503）会让 Add Symbol 对话框不可用；退化 `'0'` 至少能用。但串目录风险真实。建议收窄异常类型（`SQLAlchemyError`）+ DB 挂时不写缓存（直接 `_fetch` 不缓存）。

### 问题 7 — AppShell useEffect 依赖稳定性（HIGH，可立即修）

`frontend/components/layout/AppShell.tsx` 行 68-82：
```tsx
const subscribe = useWebSocket().subscribe;
const resetAccountScopedData = useBotStore((s) => s.resetAccountScopedData);
useEffect(() => {
  if (!authChecked || isAuthPage) return;
  subscribe("account_update", () => { resetAccountScopedData(); ... });
}, [authChecked, isAuthPage, subscribe, resetAccountScopedData, setSymbols]);
```

`useWebSocket().subscribe` —— 若 `useWebSocket` 每次返回新对象，`subscribe` 引用每次 render 变化 → effect 反复重订阅 → 同一次 `account_update` 触发多次 `resetAccountScopedData` + `getSymbols`。

**需确认**：`lib/websocket.ts` 的 `useWebSocket` 实现。若 `subscribe` 是模块级稳定函数则无问题；若是 hook 内部 `useCallback` 依赖状态则不稳定。zustand 的 `resetAccountScopedData` / `setSymbols` 通常是稳定 action 引用（zustand v4+ 保证），低风险。

**修复**：用 `useRef` 缓存 subscribe，或从依赖数组移除（`subscribe` 加 eslint-disable 注释说明稳定）。

### 问题 8 — get_symbol_spec 契约变更未文档化（LOW）

`app/mt5/connector.py` `get_symbol_spec` 从 `to_broker_alias(symbol)` 改为直接用 `symbol`。docstring 已说明"必须是券商侧名称"。调用方（symbols.py:1001 `alias`、symbol_validation.py:63 `broker_symbol`）均传券商名，**正确且安全**。

但这是 API 契约破坏性变更 —— 任何外部调用方若仍传 canonical 名（如 "GOLD"）现在会打到 `/symbol-spec/GOLD` 而非 `/symbol-spec/GOLD_`，可能 404。未在 CHANGELOG 记录。

**修复**：在 `docs/` 或 CHANGELOG 记录此契约变更。低优先级。

## 已验证非问题

1. **迁移链**：`c1d2e3f4a5b6.down_revision = "f0e1d2c3b4a5"`，`f0e1d2c3b4a5_add_price_alerts.py` 存在，链完整。
2. **lifespan 顺序**：`load_profiles_into_memory`（main.py:291）在 `BotManager(connector, ...)`（main.py:296）前执行，`SYMBOL_PROFILES_DB_SYNCED` 先置位再构造。
3. **_require_config 限账号**：`symbols.py:219` 的 `svc.get_config(db, symbol)` 自动解析当前活跃账号，PUT/DELETE/toggle/validate 端点经此 helper 均正确隔离。
4. **get_symbol_spec 调用方**：仅 2 处（symbols.py:1001、symbol_validation.py:63），均传券商名（alias/broker_symbol），语义变更安全。
5. **agent_entrypoint _loaded**：旧代码 "DB 可达但无启用品种" 返回 0 并 error 日志；新代码空账号也返回 `len(db_profiles)`（0），触发 "aliases disabled" warning。行为更安全（无别名 → 回退原样），非回归。
6. **account_switch publish 失败兜底**：`account_update` publish 在 `try/except` 内，`noqa: BLE001`，不影响切换结果（门禁 flag 在 finally 清理）。正确。

## 多路评审追加结论（2026-09-27 Session 2）

三位评审代理（critic 完整性 / architect 技术正确性 / verifier 验证策略）并行评审，关键结论：

### CRITICAL — 修复 1 原方案被三方否决（值绑定陷阱）
- `SYMBOL_PROFILES_DB_SYNCED` 是 `global` 重新绑定的不可变 bool（config.py:134→143），`from X import Y` 是值绑定（快照）。
- 顶部 `from app.config import SYMBOL_PROFILES_DB_SYNCED` 只绑定加载时 `False`，之后 `apply_db_symbol_profiles` 置 True 对 manager 不可见 → 静态兜底永远启用 → **回归 b8f1bbd 要修复的切号断链 bug**，且 `test_no_fallback_when_db_synced` 必红。
- critic 已在 venv 实测值绑定语义；architect 指出循环 import 无风险（config 对 manager 仅函数内延迟 import）。
- **正确修法**：`import app.config as config` + `config.SYMBOL_PROFILES_DB_SYNCED`（模块属性访问取最新值），或 config.py 加只读函数。当前行内 import 是刻意为之的延迟绑定，语义正确。

### MAJOR — 问题 7 前提证伪，从 Phase 2 移除
- `subscribe` 为 `useCallback([],)` 稳定引用（websocket.ts:189-201），zustand action 稳定，subscribe 自带同通道替换语义（websocket.ts:191-193）→ **不存在反复重订阅**。原"可立即修"误判。

### MAJOR — 问题 8 落点不存在
- 仓库无 CHANGELOG（`ls docs/` 无，根目录与 backend/ 均无）。"写入 CHANGELOG"是空指令。落点改为新建或写入 `docs/SYMBOL-PARAMETERS-TECH.md`。

### 环境阻塞
- `ruff` 未安装（venv 与系统均无）；前端零自动化测试框架。
- `list_configs` 当前覆盖 48%，line 81 未覆盖；目标应为函数级覆盖。

### 其他评审共识
- **风险分级被纠正**：原计划把问题 2 标"最低风险"，实为三项中最高（静默生产回归 + 测试变红）。利益相关者视角：Phase 2 原三项净成果仅"补一个测试用例"，经纠正后为"正确修 import 绑定 + 补测试"。
- 问题 6 异常收窄注意边界：只收窄 `get_current_account_login` 调用，保留 `_fetch`→`connector.list_symbols()` 既有异常路径。
- 问题 1 值得考虑 `git commit --amend` 修正 message（main ahead 1 未 push），比"记录备案"更有价值，需用户决策。

## 审查工具元数据

- OCR: open-code-review v1.12.8 (5c7b3838) darwin/arm64
- OCR 失败原因: `rejected by provider (HTTP 404)` × 17 文件 + grouping
- 回退方式: 人工读取 `git diff HEAD~1 HEAD`（944 行）+ grep 验证调用方 + Read 关键路径
- 多路评审: 3 代理并行（critic/architect/verifier），全部返回，结论已并入
