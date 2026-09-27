# Schema DDL 双写 + broker-catalog 异常收窄 (Phase 3, Problem 5/6)

> 从 `2026-09-27-code-review-b8f1bbd-fixes`（commit b8f1bbd 审查修复）拆分的独立任务。原相位 Phase 3 问题 5 + 问题 6。

## Goal

解决两处架构权衡：
1. **问题 5**：Alembic 迁移 `c1d2e3f4a5b6` 与 main.py lifespan `schema_stmts`（行 392-402）的 DDL 完全重复，双套真相源易漂移。
2. **问题 6**：broker-catalog `except Exception` 过宽（symbols.py:144-148），DB 挂时所有账号退化共用 `'0'` key 串目录。

## Background（来自评审 findings.md）

### 问题 5：DDL 双写
`main.py` lifespan `schema_stmts` 与迁移文件重复 5 条 DDL：
- `ALTER TABLE symbol_configs ADD COLUMN IF NOT EXISTS account_login ...`
- `UPDATE symbol_configs SET account_login = COALESCE(...)`
- `DROP CONSTRAINT/INDEX uq_symbol_configs_symbol`
- `CREATE UNIQUE INDEX uq_symbol_configs_account_symbol`
- `CREATE INDEX ix_symbol_configs_account_login`

CLAUDE.md 明确"Never reuse revision IDs"，main.py schema_stmts 是"幂等兜底"（create_all 路径/测试 SQLite）。但双套真相源需后续每次 schema 变更同步两处。

候选方案：
- A. 提取 `app/db/schema_sync.py` 单一函数返回 DDL 列表，迁移与 lifespan 共用
- B. 接受现状（main.py 兜底有存在价值，CLAUDE.md 已知此模式）
- 验收（若选 A）：迁移与 lifespan 输出一致（对比两处语句集）

### 问题 6：broker-catalog 异常过宽
`symbols.py:144-148`：
```python
try:
    account_login = await svc.get_current_account_login(db)
except Exception as e:  # noqa: BLE001
    logger.warning(f"broker-catalog: resolve active account failed ({e}); using '0'")
    account_login = "0"
```
DB 真正挂时所有账号共用 `xm:catalog:v2:0` 缓存 → 跨账号串目录。

候选方案：
- 收窄异常类型（`SQLAlchemyError`）+ DB 挂时不写缓存（直接 `_fetch` 不缓存）
- **边界注意（评审强调）**：只收窄 `get_current_account_login` 调用，保留 `_fetch`→`connector.list_symbols()` 的既有异常路径
- 验证：模拟 DB down 的集成测试（monkeypatch `get_current_account_login` 抛 SQLAlchemyError）

## Phases

### Phase 1: 现状核对 — `Status: complete`
- main.py lifespan `schema_stmts`（行 245-256 原）6 条 symbol_configs DDL 与迁移 `c1d2e3f4a5b6` upgrade 完全对应（ADD COLUMN / 回填 / DROP 双形式 / 双 INDEX）
- 无既有 schema_sync.py（app/db 现有 models/observability/session）
- **关键可行性确认**：alembic env.py:7 已 `from app.db.models import Base` → 迁移文件能 import `app` 包，共享常量可行

### Phase 2: 问题 5 实施 — `Status: complete`
- **选方案 A 的安全变体**：提取 DDL **文本**为共享常量，非执行逻辑
  - 新建 `app/db/schema_ddl.py`：`SYMBOL_CONFIG_ACCOUNT_LOGIN_DDL`（6 条幂等 DDL，含"只覆盖 upgrade 正向"注释）
  - 迁移 `c1d2e3f4a5b6` upgrade() 改循环执行共享常量（downgrade 保留自包含）
  - `main.py` schema_stmts 用 `*SYMBOL_CONFIG_ACCOUNT_LOGIN_DDL` 展开 + 顶部 import
- **等价性验证**：对比共享常量 vs 原 main.py 文本——SQL 语义一致（UPDATE 缩进差异非语义）；迁移原始文本同样等价。编译 OK、main+ddl import OK、迁移文件 import OK（revision 链保持）

### Phase 3: 问题 6 实施 — `Status: complete`
- `symbols.py` broker_catalog：`except Exception` → `except SQLAlchemyError`（收窄，只捕 DB 层）+ `db_degraded=True` 标记 + 退化时直接 `_fetch` 不写缓存（`if redis_client is not None and not db_degraded`）
- 新增 `SQLAlchemyError` import
- 补集成测试 `test_db_degraded_bypasses_cache`：mock `get_current_account_login` 抛 SQLAlchemyError → 断言 200 目录 + 无 `xm:catalog:v2:*` 缓存 key

### Phase 4: 回归 — `Status: complete`
- 完整 test_symbol_account_isolation.py 17 passed（含新退化用例）
- 更宽回归集 54 passed（isolate + account_switch + market_data_alias + symbol_validation + symbol_resolver）

## Errors Encountered

（无）

## Next Step

Task 完成（问题 5/6 均修复 + 验证）。待 commit 到主分支。