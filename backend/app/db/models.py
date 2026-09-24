import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ─── Auth: Passkey (WebAuthn) ─────────────────────────────────────────────────


class Owner(Base):
    """Single-owner model — only 1 user ever exists."""

    __tablename__ = "owner"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(String(100))
    is_setup_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class WebAuthnCredential(Base):
    __tablename__ = "webauthn_credentials"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(Integer, default=0)
    device_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    jwt_jti: Mapped[str] = mapped_column(String(64), unique=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(50))
    actor: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resource: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BotEventType(enum.StrEnum):
    STARTED = "STARTED"
    STOPPED = "STOPPED"
    TRADE_OPENED = "TRADE_OPENED"
    TRADE_CLOSED = "TRADE_CLOSED"
    SIGNAL_DETECTED = "SIGNAL_DETECTED"
    TRADE_BLOCKED = "TRADE_BLOCKED"
    ORDER_FAILED = "ORDER_FAILED"
    ERROR = "ERROR"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    SENTIMENT_CHANGE = "SENTIMENT_CHANGE"
    OPTIMIZATION_RUN = "OPTIMIZATION_RUN"
    SETTINGS_CHANGED = "SETTINGS_CHANGED"
    STRATEGY_CHANGED = "STRATEGY_CHANGED"
    AI_ANALYSIS = "AI_ANALYSIS"
    # AI agent 运行失败（LLM 连接错误 / SDK 异常等）——与正常 AI_ANALYSIS 决策区分，
    # 避免基础设施故障伪装成一次"分析结论"出现在通知中心。
    AI_AGENT_ERROR = "AI_AGENT_ERROR"


class OHLCVData(Base):
    __tablename__ = "ohlcv_data"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    timeframe: Mapped[str] = mapped_column(String(10))
    time: Mapped[datetime] = mapped_column(DateTime, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (UniqueConstraint("account_login", "ticket", name="uq_trades_account_ticket"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket: Mapped[int] = mapped_column(BigInteger, index=True)  # 全局唯一改复合唯一 (H4)
    account_login: Mapped[str] = mapped_column(
        String(32), default="0", server_default="0", index=True
    )  # 所属 MT5 账号（H4：跨账号 ticket 可重复）
    symbol: Mapped[str] = mapped_column(String(20))
    type: Mapped[str] = mapped_column(String(10))  # BUY / SELL
    lot: Mapped[float] = mapped_column(Float)
    open_price: Mapped[float] = mapped_column(Float)
    expected_price: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )  # tick price before order, for slippage calc
    close_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    sl: Mapped[float] = mapped_column(Float)
    tp: Mapped[float] = mapped_column(Float)
    open_time: Mapped[datetime] = mapped_column(DateTime)
    close_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    comment: Mapped[str | None] = mapped_column(String(255), nullable=True)
    strategy_name: Mapped[str] = mapped_column(String(50))
    ai_sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_sentiment_label: Mapped[str | None] = mapped_column(String(20), nullable=True)
    trade_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pre_trade_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    post_trade_analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class MT5Account(Base):
    """MT5 交易账号（Phase 4，账号实时切换）。

    凭据独立于 `secrets` 表存储（H5：secrets 表会被 runner 注入到沙箱进程），
    密码用 VaultService AES-256-GCM 加密存 password_encrypted/password_nonce。
    """

    __tablename__ = "mt5_accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    login: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    password_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    password_nonce: Mapped[bytes] = mapped_column(LargeBinary)  # 12 bytes AES-GCM
    server: Mapped[str] = mapped_column(String(100), default="", server_default="")
    broker_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", index=True)
    last_switched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class NewsSentiment(Base):
    __tablename__ = "news_sentiments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    headline: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(100))
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sentiment_label: Mapped[str] = mapped_column(String(20))  # bullish/bearish/neutral
    sentiment_score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AIOptimizationLog(Base):
    __tablename__ = "ai_optimization_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    period_start: Mapped[datetime] = mapped_column(DateTime)
    period_end: Mapped[datetime] = mapped_column(DateTime)
    current_params: Mapped[str] = mapped_column(Text)  # JSON string
    suggested_params: Mapped[str] = mapped_column(Text)  # JSON string
    rationale: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    backtest_result: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON backtest comparison
    # 回测口径版本号：回测公式（contract_size 换算、品种配置读取等）一旦变化，
    # 用旧口径算出的 suggested_params 不得再被应用到实盘（防"旧数据新用"）。
    backtest_formula_version: Mapped[str] = mapped_column(String(16), default="v1", server_default="v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class MacroData(Base):
    __tablename__ = "macro_data"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    series_id: Mapped[str] = mapped_column(String(50), index=True)
    series_name: Mapped[str] = mapped_column(String(200))
    date: Mapped[datetime] = mapped_column(DateTime, index=True)
    value: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class MLModelLog(Base):
    __tablename__ = "ml_model_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    model_name: Mapped[str] = mapped_column(String(100))
    timeframe: Mapped[str] = mapped_column(String(10))
    train_start: Mapped[datetime] = mapped_column(DateTime)
    train_end: Mapped[datetime] = mapped_column(DateTime)
    test_start: Mapped[datetime] = mapped_column(DateTime)
    test_end: Mapped[datetime] = mapped_column(DateTime)
    metrics: Mapped[str] = mapped_column(Text)  # JSON
    feature_importance: Mapped[str] = mapped_column(Text)  # JSON
    model_path: Mapped[str] = mapped_column(String(255))
    model_binary: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # HMAC-SHA256 of ``model_binary`` signed with SECRET_KEY at save time.
    # Verified before joblib.load() so a tampered DB row cannot trigger pickle
    # RCE when the strategy / scheduler reloads the model.
    model_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BotEvent(Base):
    __tablename__ = "bot_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_type: Mapped[BotEventType] = mapped_column(Enum(BotEventType))
    message: Mapped[str] = mapped_column(Text)
    account_login: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class MLPredictionLog(Base):
    __tablename__ = "ml_prediction_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    model_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    symbol: Mapped[str] = mapped_column(String(20))
    predicted_signal: Mapped[int] = mapped_column(Integer)  # -1, 0, 1
    confidence: Mapped[float] = mapped_column(Float)
    actual_outcome: Mapped[int | None] = mapped_column(Integer, nullable=True)  # filled later
    was_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class OrderAudit(Base):
    """订单审计（统一三条通道：strategy / ai_agent / manual）。

    手动交易（manual）扩展列：source 区分来源、account_login 支撑跨账号
    ticket 复用（H4 同口径）、order_kind/order_price 承载挂单、review 存
    LLM 审查结论 + 情绪规则明细。status 扩展生命周期值：
    PENDING_REVIEW / PENDING_CONFIRM / REJECTED / EXPIRED / CANCELLED /
    EXECUTED（历史值 FILLED / REJECTED / TIMEOUT / ERROR 保留）。
    """

    __tablename__ = "order_audits"
    __table_args__ = (Index("ix_order_audits_account_created", "account_login", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20))
    order_type: Mapped[str] = mapped_column(String(10))  # BUY / SELL
    requested_lot: Mapped[float] = mapped_column(Float)
    requested_sl: Mapped[float] = mapped_column(Float)
    requested_tp: Mapped[float] = mapped_column(Float)
    expected_price: Mapped[float] = mapped_column(Float)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    ticket: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(20), index=True)  # FILLED / REJECTED / TIMEOUT / ERROR / PENDING_REVIEW / PENDING_CONFIRM / EXPIRED / CANCELLED / EXECUTED
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    signal_source: Mapped[str] = mapped_column(String(50))  # strategy name
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    # ─── 手动交易扩展（manual trading firewall）─────────────────────────
    source: Mapped[str] = mapped_column(String(20), default="strategy", server_default="strategy", index=True)
    account_login: Mapped[str] = mapped_column(String(32), default="0", server_default="0")
    order_kind: Mapped[str] = mapped_column(String(10), default="market", server_default="market")  # market/pending
    order_price: Mapped[float | None] = mapped_column(Float, nullable=True)  # 挂单价
    review: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # LLM verdict + 情绪规则明细
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ─── Secrets Vault ────────────────────────────────────────────────────────────


class Secret(Base):
    """Encrypted secrets store — managed via UI, injected into runners."""

    __tablename__ = "secrets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    encrypted_value: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary)  # 12 bytes for AES-GCM
    category: Mapped[str] = mapped_column(String(50), default="general")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_required: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    last_rotated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ─── Docker Sandbox Runner ───────────────────────────────────────────────────


class RunnerStatus(enum.StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    ONLINE = "online"
    DEGRADED = "degraded"
    ERROR = "error"


class JobStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Runner(Base):
    """Docker sandbox runner — executes Claude AI Agent tasks."""

    __tablename__ = "runners"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    container_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image: Mapped[str] = mapped_column(String(200))
    status: Mapped[RunnerStatus] = mapped_column(
        Enum(RunnerStatus, values_callable=lambda e: [x.value for x in e]),
        default=RunnerStatus.STOPPED,
    )
    max_concurrent_jobs: Mapped[int] = mapped_column(Integer, default=3)
    tags: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    resource_limits: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RunnerJob(Base):
    """Job executed by a runner — tracks input, output, and agent reasoning."""

    __tablename__ = "runner_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    runner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    job_type: Mapped[str] = mapped_column(String(50))
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, values_callable=lambda e: [x.value for x in e]),
        default=JobStatus.PENDING,
    )
    input: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RunnerLog(Base):
    """Log entry from a runner — persisted for history, streamed via WebSocket."""

    __tablename__ = "runner_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    runner_id: Mapped[int] = mapped_column(BigInteger)
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    level: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(Text)
    log_metadata: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)


class RunnerMetric(Base):
    """Resource usage snapshot from a runner."""

    __tablename__ = "runner_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    runner_id: Mapped[int] = mapped_column(BigInteger)
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    cpu_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    memory_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    memory_limit_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    network_rx_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    network_tx_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


# ─── Agent Memory (Layered Memory System) ────────────────────────────────────


class MemoryTier(enum.StrEnum):
    MID = "mid"
    LONG = "long"


class MemoryCategory(enum.StrEnum):
    PATTERN = "pattern"
    STRATEGY = "strategy"
    RISK = "risk"
    REGIME = "regime"
    CORRELATION = "correlation"


class AgentMemory(Base):
    __tablename__ = "agent_memories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tier: Mapped[MemoryTier] = mapped_column(Enum(MemoryTier), index=True)
    category: Mapped[MemoryCategory] = mapped_column(Enum(MemoryCategory), index=True)
    symbol: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    summary: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    hit_count: Mapped[int] = mapped_column(Integer, default=1)
    miss_count: Mapped[int] = mapped_column(Integer, default=0)
    last_validated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="reflector")
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SymbolConfig(Base):
    """User-managed symbol trading config — replaces static SYMBOL_PROFILES when present."""

    __tablename__ = "symbol_configs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(64))
    broker_alias: Mapped[str | None] = mapped_column(String(32), nullable=True)
    asset_class: Mapped[str] = mapped_column(String(16), default="forex", server_default="forex")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    default_timeframe: Mapped[str] = mapped_column(String(8), default="M15", server_default="M15")
    pip_value: Mapped[float] = mapped_column(Float)
    default_lot: Mapped[float] = mapped_column(Float)
    max_lot: Mapped[float] = mapped_column(Float)
    price_decimals: Mapped[int] = mapped_column(Integer, default=2, server_default="2")
    sl_atr_mult: Mapped[float] = mapped_column(Float, default=1.5, server_default="1.5")
    tp_atr_mult: Mapped[float] = mapped_column(Float, default=2.0, server_default="2.0")
    # 止损/止盈标准化模式。默认 "atr" = 旧行为（sl=ATR×倍数，tp=ATR×倍数）；
    # "clamped" 时 sl_distance 被夹在 [sl_floor, sl_cap] 之间（价格单位）；
    # tp_mode="rr" 时 tp_distance = target_r_multiple × 实际止损距离，盈亏比恒等于 R。
    sl_mode: Mapped[str] = mapped_column(String(8), default="atr", server_default="atr")
    sl_floor: Mapped[float | None] = mapped_column(Float, nullable=True)
    sl_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp_mode: Mapped[str] = mapped_column(String(8), default="atr", server_default="atr")
    target_r_multiple: Mapped[float | None] = mapped_column(Float, nullable=True)
    contract_size: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
    # 券商 volume 限制：创建/更新时从 MT5 规格回填。可空：该列存在之前创建的
    # 行（存量品种）保持 NULL，订单侧手数防线会跳过它们，直到重新校验。
    volume_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_step: Mapped[float | None] = mapped_column(Float, nullable=True)
    ml_tp_pips: Mapped[float] = mapped_column(Float)
    ml_sl_pips: Mapped[float] = mapped_column(Float)
    ml_forward_bars: Mapped[int] = mapped_column(Integer, default=10, server_default="10")
    ml_timeframe: Mapped[str] = mapped_column(String(8), default="M15", server_default="M15")

    ml_status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    ml_last_trained_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class AIUsageLog(Base):
    __tablename__ = "ai_usage_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    agent_id: Mapped[str] = mapped_column(String(100), index=True)
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd_sdk: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_usd_calc: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    turns: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls_count: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    raw_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)


# ─── Agent Chat（对话式交易计划/报告，只读分析，不可交易）────────────────────


class AgentChatSession(Base):
    __tablename__ = "agent_chat_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), default="新会话", server_default="新会话")
    symbol: Mapped[str] = mapped_column(String(30))
    timeframe: Mapped[str] = mapped_column(String(8), default="M15", server_default="M15")
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    active_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    mode: Mapped[str] = mapped_column(String(20), default="free", server_default="free")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AgentChatMessage(Base):
    __tablename__ = "agent_chat_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(BigInteger, index=True)
    role: Mapped[str] = mapped_column(String(16))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    tool_calls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AgentChatRun(Base):
    """Durable queue; single-owner scope, not a multi-tenant ownership model."""
    __tablename__ = "agent_chat_runs"
    __table_args__ = (UniqueConstraint("session_id", "request_id", name="uq_chat_request"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[int] = mapped_column(BigInteger, index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input: Mapped[dict] = mapped_column(JSON)
    budget: Mapped[dict] = mapped_column(JSON)
    response: Mapped[str] = mapped_column(Text, default="")
    partial_response: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    turns: Mapped[int] = mapped_column(Integer, default=0)
    duration_s: Mapped[float] = mapped_column(Float, default=0)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    worker_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AgentChatEvent(Base):
    """Append-only bounded audit. Agent reports are derived from these events."""
    __tablename__ = "agent_chat_events"
    __table_args__ = (UniqueConstraint("run_id", "sequence", name="uq_chat_event_sequence"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64))
    agent_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    execution_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ─── 行情提醒（价格阈值 → 飞书卡片）─────────────────────────────────────────


class PriceAlert(Base):
    """价格阈值提醒规则：品种价格突破阈值并持续超过设定秒数后发送飞书通知。

    用户配置"品种 + 方向（above/below）+ 触发价 + 持续时长 + 最大发送次数"；
    巡检引擎（price_alert_service）周期性判定，达到条件后通过 FeishuNotifier
    发送卡片。达到 max_notifications 上限后自动停用（is_active=False），需
    用户手动重新开启（开启时重置 sent_count）。
    """

    __tablename__ = "price_alerts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)  # 规范品种名
    condition: Mapped[str] = mapped_column(String(8), default="above")  # above=价格高于阈值, below=价格低于阈值
    trigger_price: Mapped[float] = mapped_column(Float)  # 触发阈值（价格单位）
    duration_seconds: Mapped[int] = mapped_column(Integer, default=60)  # 需连续满足的秒数
    max_notifications: Mapped[int] = mapped_column(Integer, default=1)  # 达到该次数后停用
    sent_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # 已发送次数
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

