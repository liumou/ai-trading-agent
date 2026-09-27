"""共享 schema DDL —— 消除 Alembic 迁移与 main.py lifespan 兜底的双写漂移。

背景：symbol_configs 按 MT5 账号隔离的 schema 变更同时出现在两处：
- Alembic 迁移 `c1d2e3f4a5b6`（一次性 upgrade）
- `main.py` lifespan `schema_stmts`（启动幂等兜底，应对 create_all / 测试 SQLite）

两处 DDL 文本完全一致，此前各自维护易漂移。提取为共享常量：迁移与
lifespan 都引用同一份文本，未来 schema 变更只需改一处。

注意：本常量只覆盖 **upgrade 正向** DDL（幂等写法，IF NOT EXISTS / DROP IF
EXISTS）。迁移的 `downgrade()` 逆向 DDL 语义不同，仍留在迁移文件内自包含。
"""

SYMBOL_CONFIG_ACCOUNT_LOGIN_DDL: list[str] = [
    # 加列（幂等）。account_login 与 trades/bot_events 的约定一致：'0' = 未知。
    "ALTER TABLE symbol_configs ADD COLUMN IF NOT EXISTS account_login VARCHAR(32) NOT NULL DEFAULT '0'",
    # 存量回填到当前活跃账号（login 为 BigInteger，显式转 varchar 以防隐式
    # 转换差异）。COALESCE 兜底：无活跃账号时保持 '0'，避免 NOT NULL 违例。
    """
    UPDATE symbol_configs
    SET account_login = COALESCE((
        SELECT ma.login::varchar FROM mt5_accounts ma
        WHERE ma.is_active = true AND ma.is_deleted = false
        ORDER BY ma.id LIMIT 1), '0')
    WHERE account_login = '0'
    """,
    # 全局唯一 → 账号内唯一。旧唯一既可能是 CONSTRAINT（create_all 路径）也
    # 可能是裸唯一 INDEX（手写迁移路径），两种情况都覆盖。
    "ALTER TABLE symbol_configs DROP CONSTRAINT IF EXISTS uq_symbol_configs_symbol",
    "DROP INDEX IF EXISTS uq_symbol_configs_symbol",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_symbol_configs_account_symbol "
    "ON symbol_configs (account_login, symbol)",
    "CREATE INDEX IF NOT EXISTS ix_symbol_configs_account_login ON symbol_configs (account_login)",
]
