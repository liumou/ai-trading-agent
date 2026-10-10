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

TRADE_REVIEWS_DDL: list[str] = [
    # trade_reviews 表（幂等）。与模型 TradeReview 声明保持一致；migration 与
    # lifespan 共用本常量，消除双写漂移。review 用 JSON（不追 JSONB，新表不锁表）。
    """CREATE TABLE IF NOT EXISTS trade_reviews (
        id BIGSERIAL PRIMARY KEY,
        trade_id BIGINT,
        ticket BIGINT NOT NULL,
        account_login VARCHAR(32) NOT NULL DEFAULT '0',
        symbol VARCHAR(20) NOT NULL,
        classification VARCHAR(32),
        status VARCHAR(16) NOT NULL DEFAULT 'pending',
        review JSON,
        review_history JSON,
        error TEXT,
        provider_name VARCHAR(32),
        confidence DOUBLE PRECISION,
        flagged BOOLEAN NOT NULL DEFAULT FALSE,
        worker_token VARCHAR(36),
        lease_until TIMESTAMP,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        open_time TIMESTAMP NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS ix_trade_reviews_natural_key ON trade_reviews (ticket, account_login)",
    "CREATE INDEX IF NOT EXISTS ix_trade_reviews_status ON trade_reviews (status)",
    "CREATE INDEX IF NOT EXISTS ix_trade_reviews_open_time ON trade_reviews (open_time)",
    # PG partial unique index（生产去重最后防线；SQLite 无 partial index，测试
    # 依赖应用层幂等逻辑 —— 详见迁移注释）。bot 单按 trade_id 去重（trade_id 非空），
    # 手动单按 (ticket, account_login) 去重（trade_id 为 NULL）。
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_trade_reviews_trade_id_excl_null "
    "ON trade_reviews (trade_id) WHERE trade_id IS NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_trade_reviews_ticket_account_excl_null "
    "ON trade_reviews (ticket, account_login) WHERE trade_id IS NULL",
]
