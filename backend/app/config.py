import json
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings

# Per-symbol trading profiles
SYMBOL_PROFILES: dict[str, dict] = {
    "GOLD": {
        "display_name": "Gold (XAUUSD)",
        "default_timeframe": "M15",
        "pip_value": 1.0,
        "default_lot": 0.1,
        "max_lot": 1.0,
        "price_decimals": 2,
        "sl_atr_mult": 1.5,
        "tp_atr_mult": 2.0,
        "contract_size": 100,
        "ml_tp_pips": 10.0,  # ~$10 move on XAUUSD ~$3,000
        "ml_sl_pips": 10.0,
        "ml_forward_bars": 10,
        "ml_timeframe": "M15",
        "asset_class": "metal",
    },
    "OILCash": {
        "display_name": "WTI Oil",
        "default_timeframe": "M15",
        "pip_value": 10.0,
        "default_lot": 0.1,
        "max_lot": 5.0,
        "price_decimals": 2,
        "sl_atr_mult": 1.5,
        "tp_atr_mult": 2.0,
        "contract_size": 100,
        "ml_tp_pips": 0.5,  # ~$0.50 move on WTI ~$70
        "ml_sl_pips": 0.5,
        "ml_forward_bars": 10,
        "ml_timeframe": "M15",
        "asset_class": "energy",
    },
    "BTCUSD": {
        "display_name": "Bitcoin",
        "default_timeframe": "M15",
        "pip_value": 1.0,
        "default_lot": 0.01,
        "max_lot": 0.5,
        "price_decimals": 2,
        "sl_atr_mult": 2.0,
        "tp_atr_mult": 3.0,
        "contract_size": 1,
        "ml_tp_pips": 500.0,  # ~$500 move on BTC ~$100,000
        "ml_sl_pips": 500.0,
        "ml_forward_bars": 5,  # BTC moves fast — shorter horizon
        "ml_timeframe": "H1",  # H1 better for BTC volatility
        "asset_class": "crypto",
    },
    "USDJPY": {
        "display_name": "USD/JPY",
        "default_timeframe": "M15",
        "pip_value": 100.0,
        "default_lot": 0.1,
        "max_lot": 5.0,
        "price_decimals": 3,
        "sl_atr_mult": 1.5,
        "tp_atr_mult": 2.0,
        "contract_size": 100000,
        "ml_tp_pips": 0.3,  # ~30 pips on USDJPY ~145
        "ml_sl_pips": 0.3,
        "ml_forward_bars": 10,
        "ml_timeframe": "M15",
        "asset_class": "forex",
    },
}

# ─── 品种别名解析（DB `broker_alias` 是唯一来源）────────────────────────────
# 券商特定名称（如 XM 微账户的 "GOLDmicro"）通过 symbol_configs 表的
# `broker_alias` 列映射到规范名。load_profiles_from_db() 会为每行注册一条
# 别名条目 —— SYMBOL_PROFILES[alias]["canonical"] 携带规范名反查标记。
# 刻意不设静态别名表：别名只在 DB 加载完成后才存在。

def get_canonical_symbol(symbol: str) -> str:
    """把券商别名解析为规范名（如 GOLDmicro → GOLD）。

    对已经是规范名或未知的名称恒等返回。
    """
    profile = SYMBOL_PROFILES.get(symbol)
    canonical = profile.get("canonical") if profile else None
    return canonical or symbol


def resolve_canonical_symbol(symbol: str) -> str:
    """把任意已配置名称（规范名或券商别名）通过在线 BotManager
    归一化为规范引擎键（如 GOLDmicro → GOLD）。

    manager 未运行时回退到基于 profile 的别名解析；两者都不匹配时
    原样返回输入。
    """
    try:
        from app.bot.manager import get_global_manager

        mgr = get_global_manager()
        if mgr is not None:
            key = mgr.resolve_symbol(symbol)
            if key:
                return key
    except Exception:
        pass
    return get_canonical_symbol(symbol)


def get_active_symbols() -> list[str]:
    """返回全部活跃引擎品种，manager 不可用时回退到 SYMBOL_PROFILES 规范名。

    优先使用在线 BotManager（反映 /symbols UI 的运行时增删）。manager 不可用
    （测试、启动阶段）时回退到非别名 profile 条目。
    """
    try:
        from app.bot.manager import get_global_manager

        mgr = get_global_manager()
        if mgr is not None and mgr.engines:
            return list(mgr.engines.keys())
    except Exception:
        pass
    return [sym for sym, p in SYMBOL_PROFILES.items() if "canonical" not in p]


# 静态默认快照：多次重载时先恢复静态值，再叠加 DB 条目。
_STATIC_SYMBOL_PROFILES: dict[str, dict] = {k: v.copy() for k, v in SYMBOL_PROFILES.items()}


# DB profiles 是否已成功同步过（当前账号）。reload_engines 据此决定是否启用
# 静态 settings.symbols 兜底：DB 已同步但品种全禁用/为空 = 操作员的真实意图
# （不交易），不应再拉起与当前券商不匹配的静态品种。
SYMBOL_PROFILES_DB_SYNCED = False


def apply_db_symbol_profiles(db_profiles: dict[str, dict]) -> None:
    """用"静态默认 + DB 覆盖"的结果整体替换 SYMBOL_PROFILES。"""
    global SYMBOL_PROFILES_DB_SYNCED
    SYMBOL_PROFILES.clear()
    SYMBOL_PROFILES.update(_STATIC_SYMBOL_PROFILES)
    SYMBOL_PROFILES.update(db_profiles)
    SYMBOL_PROFILES_DB_SYNCED = True


# Session profiles — SL/TP multiplier overrides by trading session
SESSION_PROFILES = {
    "asian": {"hours": (0, 8), "sl_atr_mult": 1.2, "tp_atr_mult": 1.5, "confidence_boost": 0.05},
    "london": {"hours": (8, 13), "sl_atr_mult": 1.5, "tp_atr_mult": 2.0, "confidence_boost": 0.0},
    "overlap": {"hours": (13, 16), "sl_atr_mult": 1.8, "tp_atr_mult": 2.5, "confidence_boost": -0.05},
    "ny": {"hours": (16, 21), "sl_atr_mult": 1.5, "tp_atr_mult": 2.0, "confidence_boost": 0.0},
    "off": {"hours": (21, 24), "sl_atr_mult": 1.0, "tp_atr_mult": 1.2, "confidence_boost": 0.10},
}


def get_current_session(utc_hour: int) -> dict:
    """Return session profile for the given UTC hour."""
    for name, profile in SESSION_PROFILES.items():
        start, end = profile["hours"]
        if start <= utc_hour < end:
            return {**profile, "name": name}
    return {**SESSION_PROFILES["off"], "name": "off"}


class Settings(BaseSettings):
    # MT5 Bridge
    mt5_bridge_url: str = "http://localhost:8001"
    mt5_bridge_api_key: str = ""

    # Database
    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/goldbot"
    database_url_sync: str = "postgresql://user:password@localhost:5432/goldbot"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # AI (Claude Agent SDK uses CLAUDE_CODE_OAUTH_TOKEN env var directly).
    # Declared but unused: kept so older deployments with ANTHROPIC_API_KEY in
    # their .env do not fail Settings validation under strict ``extra=forbid``.
    # Actual API client is built around the SDK's OAuth flow.
    anthropic_api_key: str = ""

    # ─── LLM Provider (multi-model support) ────────────────────────────────
    # Default "claude" keeps the existing Claude Agent SDK path unchanged.
    # Set "openai_compat" to route both simple completions and the agent tool
    # loop through any OpenAI-compatible endpoint (OpenAI / DeepSeek /
    # OpenRouter / Ollama / vLLM). See app/ai/provider.py.
    llm_provider: str = "claude"  # claude | openai_compat
    llm_base_url: str = ""  # e.g. https://api.deepseek.com/v1 ; Ollama: http://localhost:11434/v1
    llm_api_key: str = ""  # may be empty for local servers (Ollama/vLLM); never sent to the frontend
    llm_model: str = ""  # global fallback model name; empty = per-agent defaults below
    llm_temperature: float = 0.2  # low by default — trading decisions favour determinism
    llm_timeout: int = 120  # per-request timeout (seconds)
    # 连接类失败（APIConnectionError/超时/拒绝）的请求内重试次数
    llm_max_retries: int = 2
    # 手动交易 AI 审查的单次 LLM 调用超时（秒）。deepseek-v4-flash 等模型
    # 在复杂评审 system prompt 下响应时间方差大（实测 7s~60s+），25s 会频触
    # fail-closed；默认 90s 对齐 LLM_TIMEOUT 量级，env LLM_REVIEW_TIMEOUT_S 可调。
    llm_review_timeout_s: int = Field(90, ge=5, le=600)
    # 指数退避重试：基础间隔与封顶（秒）。间隔按次数递增 base*2^attempt 封顶 max_s。
    # 放宽默认值以规避平台限流（ARK 等对高频重试返回 429）。
    llm_retry_base_s: float = Field(15, ge=1, le=300)
    llm_retry_max_s: float = Field(120, ge=5, le=600)

    # ─── 手动单审查 provider 链（System One / JEV 式，见 services/systemone.py）───
    # local_jev：本地确定性规则引擎（毫秒~秒级，默认）；typesafe_jev：外部 JEV
    # API（需 JEV_API_KEY，Phase 3）；llm：回滚开关 —— 完全恢复旧 LLM 审查行为
    # （不写 review.systemone 审计块）。非法值启动即报错，不静默回退。
    manual_review_provider: Literal["local_jev", "typesafe_jev", "llm"] = "local_jev"
    # 引擎内数据获取（M15/H1/spec/history 四路并发）总封顶（秒）
    manual_review_fetch_timeout_s: float = Field(5, ge=1, le=30)
    # OHLCV 最低可用根数（请求 120 根留 buffer）；双 TF 均不足 → 降级 LLM 兜底
    manual_review_min_bars: int = Field(60, ge=10, le=200)
    # K 线陈旧度上限（×bar 周期数，同源时钟=bridge tick 时间戳）
    manual_review_freshness_mult: float = Field(3.0, ge=1.0, le=50.0)
    # spike_chase：|entry−EMA20|/ATR14 的近 200 根滚动分位阈值
    manual_review_spike_pct_warn: float = Field(0.90, ge=0.5, le=1.0)
    manual_review_spike_pct_block: float = Field(0.99, ge=0.5, le=1.0)
    # exposure_cap：潜在亏损占 equity 比例（tick_value 账户货币计价）
    manual_review_risk_pct_warn: float = Field(0.02, gt=0, le=1)
    manual_review_risk_pct_block: float = Field(0.05, gt=0, le=1)
    # exposure_cap 辅指标：保证金占用占 equity 比例（bridge /account 有 leverage 才启用）
    manual_review_margin_pct_warn: float = Field(0.25, gt=0, le=1)
    manual_review_margin_pct_block: float = Field(0.50, gt=0, le=1)
    # size_near_limit：单笔手数 ≥ 0.8×min(全局上限, 品种上限) 或 ≥ 3×同品种近10笔中位
    manual_review_size_cap_frac: float = Field(0.8, gt=0, le=1)
    manual_review_size_med_mult: float = Field(3.0, gt=1, le=20)
    # loss_chase_combo：账户级日亏 warn 门槛（3% 由硬闸门拒，此处覆盖 1~3% 梯度）
    manual_review_loss_warn_pct: float = Field(0.01, gt=0, le=0.1)
    # rr_sanity：RR 下限 / SL 距离 ATR 上限
    manual_review_rr_min: float = Field(0.25, gt=0, le=10)
    manual_review_sl_max_atr: float = Field(10.0, gt=0, le=100)
    # unfamiliar_symbol：全量成交历史窗口（天）
    manual_review_familiar_days: int = Field(14, ge=1, le=90)
    # sentiment_conflict：逆向情绪分数阈值
    manual_review_sent_conflict: float = Field(0.5, gt=0, le=1)
    # mtf_conflict 升级 REJECTED 需要的各 TF ADX 下限（conflict 单独只 CAUTION）
    manual_review_mtf_adx_reject: float = Field(25.0, ge=20, le=50)
    # 降级告警：provider 连续失败 N 次 → CIRCUIT_BREAKER 事件 + Telegram 聚合告警
    manual_review_degraded_alert_threshold: int = Field(3, ge=1, le=50)
    # 仅 typesafe_jev 生效：检查置信度下限（local 的 confidence 是构造分非概率）
    manual_review_min_confidence: float = Field(0.55, ge=0, le=1)
    # 低置信分诊地板：risk/exec 置信 < 该值 = 应答整体不可靠 → 降级（LLM 兜底）；
    # 在 地板~min_confidence 之间 → CAUTION 人工确认（free 模型常低置信，
    # 直接降级会让 JEV 永不生效；直接放行又太松 —— 人在环是正确档位）
    manual_review_typesafe_conf_floor: float = Field(0.30, ge=0, le=1)

    # ─── TypeSafe JEV 外部决策 API（provider=typesafe_jev 主审；local 失败时兜底）───
    # API key 属机密：只存 .env/vault，禁入代码/审计/日志/前端（对齐 llm_api_key 约定）。
    # base_url 若以 /systemone 结尾则原样使用，否则自动拼接（兼容两种配置习惯）。
    manual_review_typesafe_api_key: str = ""
    manual_review_typesafe_base_url: str = "https://opencode.ai/zen/v1/systemone"
    manual_review_typesafe_model: str = "jev-1.13-free"
    manual_review_typesafe_timeout_s: float = Field(8, ge=1, le=30)
    manual_review_typesafe_proxy_url: str = ""  # 如 "http://127.0.0.1:7897"；空 = 直连
    # 简单熔断：连续失败 N 次 → 冷却期内跳过 JEV（避免每单白等 8s），只影响
    # 本 provider 的可用性判定，不影响决策链 fail-closed 语义
    manual_review_typesafe_circuit_threshold: int = Field(3, ge=1, le=50)
    manual_review_typesafe_circuit_cooldown_s: int = Field(300, ge=10, le=3600)


    # Chat-only budgets; never change autonomous trading loop deadlines.
    chat_total_timeout_s: int = Field(600, ge=30, le=1800)
    chat_request_timeout_s: int = Field(180, ge=5, le=600)
    chat_tool_timeout_s: int = Field(60, ge=1, le=300)
    chat_heavy_tool_timeout_s: int = Field(300, ge=1, le=600)
    chat_max_turns: int = Field(15, ge=2, le=30)
    chat_worker_poll_s: float = Field(2, ge=0.1, le=30)
    chat_lease_s: int = Field(30, ge=10, le=300)
    # Trade-review（历史订单 AI 复盘）配额与超时。复盘是重 LLM 操作，每日每用户
    # 配额防成本滥用（Redis 计数）；超时只约束单次 LLM 调用（wait_for 包裹）。
    trade_review_daily_quota: int = Field(30, ge=1, le=200)
    trade_review_max_batch: int = Field(50, ge=1, le=100)
    trade_review_timeout_s: int = Field(120, ge=10, le=600)
    trade_review_lease_s: int = Field(120, ge=10, le=600)
    trade_review_poll_s: float = Field(2, ge=0.1, le=30)  # worker 空转轮询间隔
    trade_review_timeframe: str = Field("M15", min_length=2, max_length=10)  # 复盘行情窗口 K 线周期
    trade_review_max_tokens: int = Field(1200, ge=200, le=4000)  # 复盘 LLM 输出 token 上限
    # Multi-agent（旧 run_multi_agent）循环预算：reflector / 分析师 / orchestrator。
    # 默认值对齐此前硬编码值；慢 LLM 端点可在 .env 里调高（不要悄悄放宽自动交易时限）。
    # 预算耗尽会让 openai_loop 返回兜底失败文案——下游必须把它当「分析失败」而非「无信号」。
    multi_agent_specialist_timeout_s: int = Field(60, ge=10, le=600)
    multi_agent_specialist_max_turns: int = Field(8, ge=1, le=30)
    multi_agent_reflector_timeout_s: int = Field(90, ge=10, le=600)
    multi_agent_reflector_max_turns: int = Field(10, ge=1, le=30)
    multi_agent_orchestrator_timeout_s: int = Field(120, ge=10, le=900)
    multi_agent_orchestrator_max_turns: int = Field(10, ge=1, le=30)

    # LLM 熔断：连续连接失败达到阈值 → 冷却期内跳过 LLM 调用（避免每 15 分钟刷屏）
    llm_circuit_threshold: int = 3
    llm_circuit_cooldown_s: int = 300
    llm_fallback_to_claude: bool = False  # explicit opt-in; default fail-closed (no silent switch)
    llm_allow_live: bool = False  # opt-in to let non-Claude agents execute beyond shadow/paper
    llm_max_orders_per_loop: int = 1  # hard cap on executed trade tools per agent loop (max 3)

    # LLM 生成内容（AI 决策分析、sentiment key_factors、优化 assessment/reasoning 等）
    # 的自然语言输出语言。请求触发的生成优先读请求头 Accept-Language（前端 locale），
    # 后台定时任务（scheduler/runner，无请求上下文）使用本默认值。与前端默认 locale
    # 保持一致（frontend/i18n/config.ts defaultLocale = "zh"）。
    llm_response_lang: str = "zh"  # zh | en

    # Per-agent model defaults. Resolution order (most specific wins):
    #   per-agent setting > llm_model > built-in Claude default.
    model_orchestrator: str = "claude-sonnet-4-20250514"
    model_specialist: str = "claude-haiku-4-5-20251001"

    # Optional cost table for non-Claude models (USD per 1M tokens).
    # Env: CUSTOM_PRICE_PER_MILLION={"gpt-4o":{"input":2.5,"output":10}}
    # Accepts a JSON string (env) or a parsed dict (code/tests). Unknown models
    # without an entry produce a null cost rather than crashing.
    custom_price_per_million: dict = {}

    # Bot Config
    # NOTE: `symbol` is a legacy single-symbol default only used by a few AI
    # helpers and tests. Runtime trading uses DB-managed symbol configs via
    # BotManager.engines (`symbol_list` / SYMBOL_PROFILES). Do not assume GOLD.
    symbol: str = "GOLD"
    symbols: str = "GOLD"  # comma-separated list, e.g. "GOLD,OILCash,BTCUSD,USDJPY"
    timeframe: str = "M15"
    # 启动时对已启用品种做券商存在性校验（backend/app/main.py）：
    #   "warn"   —— 仅告警，引擎照常启动（默认；VPS/bridge 故障不得停摆交易）
    #   "strict" —— 券商明确报告品种缺失/不可交易的引擎创建但置 PAUSED
    #               （持仓对账与手动平仓仍可用）；bridge 不可达时两种模式
    #               都退化为 warn。
    symbol_startup_validation: str = "warn"
    max_risk_per_trade: float = 0.01
    max_daily_loss: float = 0.03
    max_concurrent_trades: int = 3
    max_lot: float = 1.0
    # ─── 硬闸门（mcp_server/guardrails.py 消费；默认 = 旧硬编码常量，env 可覆盖）───
    # 三通道共用（AI/MCP/手动）：改动同时收紧自动与手动交易，重启生效。
    # 注意与 manual_review_*（审查层软阈值）区分：这里是「超过即拒」的硬限。
    guardrails_max_lot_per_trade: float = Field(1.0, gt=0, le=100)
    guardrails_max_concurrent_per_symbol: int = Field(3, ge=1, le=50)
    guardrails_max_concurrent_total: int = Field(5, ge=1, le=100)
    guardrails_consecutive_loss_halt: int = Field(5, ge=1, le=50)
    # 去硬编码（评审 1/2：每小时/间隔/日亏原为模块常量，不可配置）
    guardrails_max_trades_per_hour: int = Field(5, ge=1, le=100)
    guardrails_min_interval_seconds: int = Field(120, ge=1, le=3600)
    guardrails_max_daily_loss: float = Field(0.03, gt=0, le=1)
    max_drawdown_from_peak: float = 0.15  # 15% absolute drawdown → halt
    max_equity_drawdown: float = 0.03  # 日内 equity（含浮动盈亏）回撤 ≥3% → 停新开仓；0=禁用
    use_ai_filter: bool = True
    ai_confidence_threshold: float = 0.7
    paper_trade: bool = False

    # ─── 交易纪律门禁（discipline gate）—— docs/optimization/trading-discipline-enhancement.md ───
    # 用户已冻结口径（2026-10-10）：纪律时区 Asia/Shanghai（休息日/展示）、日界 22:00 UTC 外汇日、
    # 保证金分母 equity。参数经 Redis 运行时配置可改（env 为默认值，见 guardrails._runtime_setting）。
    discipline_timezone: str = "Asia/Shanghai"  # 休息日判断/纪律状态端点渲染时区
    mt5_server_tz: str = "Europe/Athens"  # 旧 bridge naive 时间转换用（评审 5 F2）
    discipline_gate_enabled: bool = True  # 总开关：false 时门禁放行并记 kind='discipline_disabled'
    engine_discipline_enabled: bool = True  # 引擎通道纪律开关（false=引擎豁免全部纪律检查）
    # 日/周/月边界（22:00 UTC 外汇日 → 周期号 key 用 (utcnow-22h) 生成）
    discipline_weekly_loss_limit: float = 0.07  # 周亏 ≥7% → 本周剩余禁开
    discipline_monthly_loss_limit: float = 0.12  # 月亏 ≥12% → 当月剩余禁开
    discipline_max_trades_per_day_manual: int = 3  # 手动通道日开仓上限
    discipline_max_trades_per_day_engine: int = 5  # 引擎通道日开仓上限
    discipline_max_trades_per_week_manual: int = 10  # 手动通道周开仓上限
    discipline_max_trades_per_week_engine: int = 15  # 引擎通道周开仓上限
    discipline_flip_cooldown_minutes: int = 30  # 同品种反手冷静期
    discipline_cooldown_minutes: int = 60  # 瞬态闸冷却（非熔断档）
    discipline_impulse_cooldown_hours: int = 24  # 冲动冷却（阶梯升级：24→72→本周禁）
    discipline_mandatory_rest_days: list[int] = [4]  # 强制休息日（Asia/Shanghai 周几，4=周五）
    discipline_consecutive_loss_week_halt: int = 5  # 连亏 ≥5 笔（序列语义）→ 周停
    discipline_max_single_margin_pct: float = 0.20  # 单笔保证金 ≤ equity×20%（兜底闸）
    discipline_max_total_margin_pct: float = 0.40  # 总持仓保证金 ≤ equity×40%
    discipline_max_lots_per_day: float = 1.0  # 防拆单（M5/P3）：日累计手数上限（Σ手数）

    # Position management
    max_position_duration_hours: float = 0  # 0=disabled, e.g. 8.0 = auto-close after 8h
    partial_tp_atr_mult: float = 1.0  # partial TP trigger at ATR * this multiplier
    breakeven_atr_mult: float = 0.5  # move SL to breakeven after profit > this * ATR
    enable_scale_in: bool = False  # enable momentum add-on positions
    max_scale_in_count: int = 1  # max add-on entries per position
    enable_partial_tp: bool = False  # enable close-and-reopen partial TP
    enable_auto_strategy_switch: bool = False  # AI agent can switch strategies based on regime

    # Portfolio risk
    max_portfolio_leverage: float = 3.0  # block trades when total leverage exceeds this

    # Session-aware trading
    use_session_profiles: bool = False  # adjust SL/TP per trading session

    # Notifications
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_proxy_url: str = ""  # e.g. "http://127.0.0.1:7897"; empty = direct connection

    # 飞书群自定义机器人 webhook（完整 URL，含 token，属机密：只存 env，禁止进代码/DB/前端）
    feishu_webhook_url: str = ""  # 空 = 禁用飞书通知

    # 飞书开放平台应用凭据（用于上传 K 线图获取 image_key，属机密：只存 env / Vault）
    feishu_app_id: str = ""  # 应用 App ID
    feishu_app_secret: str = ""  # 应用 App Secret

    # FRED API (macro data)
    fred_api_key: str = ""

    # ML Model
    ml_model_path: str = "models/xauusd_signal.pkl"
    ml_confidence_threshold: float = 0.5
    ml_confidence_dynamic: bool = True  # Phase E: ATR-based dynamic threshold
    ml_adx_regime_filter: bool = True  # Phase D: suppress trades in low-ADX market
    use_mtf_filter: bool = True  # Phase G: multi-timeframe trend confirmation
    mtf_timeframes: str = "H4,D1"  # timeframes for MTF consensus (comma-separated)

    # Strategy ensemble
    ensemble_strategies: str = ""  # e.g. "ema_crossover:0.3,breakout:0.3,mean_reversion:0.2,rsi_filter:0.2"

    # ML auto-rollback
    ml_auto_rollback: bool = True
    ml_rollback_accuracy_floor: float = 0.30  # rollback if accuracy drops below this
    ml_rollback_min_predictions: int = 50  # minimum predictions before rollback check

    # Runner
    runner_backend: str = "process"  # "process" or "docker"
    docker_host: str = ""  # e.g. "tcp://vps:2376" for remote Docker
    docker_tls_ca: str = ""
    docker_tls_cert: str = ""
    docker_tls_key: str = ""
    runner_default_image: str = "trading-agent:latest"
    runner_heartbeat_interval: int = 30  # seconds
    runner_heartbeat_max_misses: int = 3
    runner_max_concurrent_jobs: int = 3

    # Agent
    agent_mode: str = "single"  # "single" (Phase C) or "multi" (Phase D: orchestrator + specialists)
    trading_mode: str = "strategy"  # "strategy" (strategy-first, AI filter) | "ai_autonomous" (AI decides)
    rollout_mode: str = "shadow"  # "shadow" | "paper" | "micro" | "live" (Phase F gradual rollout)

    # Logging
    log_format: str = "text"  # "json" for production, "text" for development
    log_dir: str = "logs"

    # Authentication — both fields required to enable password auth; default
    # empty so the app fails closed when the operator hasn't configured them.
    auth_username: str = ""
    auth_password_hash: str = ""  # bcrypt hash; empty = auth disabled
    jwt_expire_hours: int = 24

    # WebAuthn (Passkey) — new auth system
    webauthn_rp_id: str = "localhost"  # e.g. "gold-trader-01.up.railway.app" for prod
    webauthn_origin: str = "http://localhost:3000"  # frontend origin for WebAuthn verification

    # Secrets Vault
    vault_master_key: str = ""  # AES-256 master key for encrypting secrets; empty = vault disabled

    # API
    secret_key: str = ""
    cors_origins: str = "http://localhost:3000"

    # Error reporting (Sentry). Empty DSN disables the integration entirely so
    # dev / CI environments do not ship breadcrumbs.
    sentry_dsn: str = ""
    sentry_environment: str = "production"
    sentry_traces_sample_rate: float = 0.05

    @property
    def symbol_list(self) -> list[str]:
        return [s.strip() for s in self.symbols.split(",") if s.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]

    # Trusted Host header allowlist. Empty = allow all (dev). Set in prod to
    # block Host header injection / cache poisoning via spoofed forwarded
    # hosts. Comma-separated, e.g. "myapp.up.railway.app,localhost".
    trusted_hosts: str = ""

    @property
    def trusted_host_list(self) -> list[str]:
        return [h.strip() for h in self.trusted_hosts.split(",") if h.strip()]

    # DB connection pool — sized for Railway Postgres Hobby (25-conn cap).
    # Total max (pool_size + max_overflow) must stay under the plan's
    # connection limit minus headroom for migrations / background tools.
    # Override via env on Pro plans where the cap is higher.
    db_pool_size: int = 8
    db_max_overflow: int = 12
    db_pool_timeout: int = 10
    db_pool_recycle: int = 1800

    # DB observability thresholds (Phase 1 long-term scaling plan)
    db_slow_query_threshold_ms: float = 500.0
    db_request_warn_ms: float = 2000.0
    # Wall-clock request budget: heavy compute endpoints (e.g. overfitting-score,
    # 32s+ of pure CPU) must not trip the error alert despite holding no pool conns.
    db_request_error_ms: float = 60000.0
    db_pool_alert_threshold: float = 0.7
    db_pool_alert_sustained_seconds: float = 60.0

    # Rate limit (Phase 4) — per (IP, path)
    rate_limit_per_minute: int = 60
    rate_limit_burst: int = 120

    # PgBouncer compatibility (Phase 3) — set true when routing through PgBouncer in
    # transaction mode. Disables asyncpg prepared statement cache, which PgBouncer
    # transaction mode cannot share across connections.
    db_pgbouncer_mode: bool = False

    @field_validator("custom_price_per_million", mode="before")
    @classmethod
    def _parse_custom_price(cls, v):
        """Env 注入的是 JSON 字符串（如 '{"gpt-4o":{"input":2.5,"output":10}}'），
        pydantic 不会自动反序列化 dict 字段 —— 这里显式解析。解析失败回退空表
        （未知模型成本返回 None 而非崩溃，见 app/ai/pricing.py）。"""
        if isinstance(v, dict):
            return v
        if isinstance(v, str | bytes):
            raw = v.decode() if isinstance(v, bytes) else v
            raw = raw.strip()
            if not raw:
                return {}
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @field_validator("llm_provider")
    @classmethod
    def _validate_llm_provider(cls, v: str) -> str:
        """允许空串（runner 子进程可能尚未注入配置，等价默认 claude）。"""
        if v in ("", "claude", "openai_compat"):
            return v or "claude"
        raise ValueError(f"llm_provider must be 'claude' or 'openai_compat', got {v!r}")

    @field_validator("symbol_startup_validation")
    @classmethod
    def _validate_symbol_startup_validation(cls, v: str) -> str:
        v = (v or "warn").strip().lower()
        if v not in ("warn", "strict"):
            raise ValueError(f"symbol_startup_validation must be 'warn' or 'strict', got {v!r}")
        return v

    @model_validator(mode="after")
    def _validate_manual_review_thresholds(self):
        """跨字段校验：block 阈值必须严格大于对应 warn 阈值（否则 warn 档
        永远不可达或 block 档失效）——env 手误在启动时报错而非静默生效。"""
        pairs = [
            ("manual_review_spike_pct_warn", "manual_review_spike_pct_block"),
            ("manual_review_risk_pct_warn", "manual_review_risk_pct_block"),
            ("manual_review_margin_pct_warn", "manual_review_margin_pct_block"),
        ]
        for warn_name, block_name in pairs:
            if getattr(self, warn_name) >= getattr(self, block_name):
                raise ValueError(f"{block_name} must be > {warn_name}")
        return self

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
