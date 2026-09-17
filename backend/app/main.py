"""
AI Trading Agent — FastAPI Main Application (multi-symbol)
"""

import asyncio
import os
from contextlib import asynccontextmanager, suppress

import redis.asyncio as redis_lib
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.ai.client import AIClient
from app.ai.news_sentiment import NewsSentimentAnalyzer
from app.ai.strategy_optimizer import StrategyOptimizer
from app.api.routes import (
    activity,
    admin,
    agent_chat,
    agent_prompts,
    ai_insights,
    ai_usage,
    analytics,
    backtest,
    bot,
    data,
    history,
    integration,
    jobs,
    macro,
    market_data,
    ml,
    positions,
    quant,
    rollout,
    runners,
    secrets,
    strategy,
    webhooks,
)
from app.api.routes import (
    memory as memory_routes,
)
from app.api.routes import metrics as metrics_routes
from app.api.routes import (
    symbols as symbols_routes,
)
from app.api.websocket import router as ws_router
from app.api.ws_runners import router as ws_runners_router
from app.auth import require_auth
from app.auth import router as auth_router
from app.auth_webauthn import router as webauthn_router
from app.bot.manager import BotManager
from app.bot.scheduler import BotScheduler
from app.config import settings
from app.data.collector import HistoricalDataCollector
from app.data.macro import MacroDataService
from app.data.macro_events import MacroEventCalendar
from app.db.observability import (
    PoolPressureMonitor,
    SessionLifetimeMiddleware,
    get_pool_stats,
    install_slow_query_logger,
    long_hold_tracker,
    slow_query_tracker,
)
from app.db.session import async_session
from app.db.session import engine as db_engine
from app.health import check_health
from app.mt5.connector import MT5BridgeConnector
from app.notifications.telegram import TelegramNotifier
from app.ai.circuit_breaker import llm_circuit_breaker


def _init_sentry() -> None:
    """Wire Sentry when ``SENTRY_DSN`` is set. Skipped silently otherwise.

    Initialized at import time (before FastAPI app construction) so the SDK can
    capture errors that happen during module import / lifespan setup, not only
    request handlers.
    """
    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.sentry_environment,
            traces_sample_rate=settings.sentry_traces_sample_rate,
            send_default_pii=False,
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
        )
        logger.info(f"Sentry initialized (env={settings.sentry_environment!r})")
    except Exception as e:
        logger.warning(f"Sentry init failed: {e!r}")


_init_sentry()


async def _validate_symbols_at_startup(connector, manager, notifier) -> None:
    """校验每个已启用品种在券商侧仍然存在且可交易。

    在 lifespan 启动时执行一次，并发校验（每次检查以 3s 封顶，因此 bridge 挂掉
    只损失一次超时，而不是每品种一次）。行为：
      - 校验通过 → 把券商 volume 限制回填进内存 profile
                    （供订单侧手数防线使用）并重新应用到引擎
      - warn 模式 → 记日志 + Telegram 告警，引擎照常启动
      - strict    → 券商明确报告品种缺失/不可交易时引擎置 PAUSED；
                    bridge 不可达时两种模式都退化为 warn
    """
    from app.bot.engine import BotState
    from app.config import SYMBOL_PROFILES
    from app.services.symbol_validation import verify_enabled_symbols

    symbols = list(manager.engines.keys())
    if not symbols:
        return
    broker_names = {
        s: (SYMBOL_PROFILES.get(s, {}).get("broker_alias") or s) for s in symbols
    }
    checks = await verify_enabled_symbols(connector, [broker_names[s] for s in symbols])
    strict = settings.symbol_startup_validation == "strict"

    for symbol in symbols:
        check = checks.get(broker_names[symbol])
        if check is None:
            logger.warning(f"Startup validation [{symbol}] skipped (batch failure)")
            continue
        if check.ok and check.spec:
            profile = SYMBOL_PROFILES.get(symbol)
            if profile is not None:
                profile.update(
                    volume_min=check.spec.get("volume_min"),
                    volume_max=check.spec.get("volume_max"),
                    volume_step=check.spec.get("volume_step"),
                )
                engine = manager.get_engine(symbol)
                if engine is not None:
                    engine.apply_profile(profile)
            logger.info(f"Startup validation [{symbol}]: OK ({broker_names[symbol]})")
            continue

        reason = check.error or "unknown"
        logger.warning(f"Startup validation [{symbol}] FAILED ({broker_names[symbol]}): {reason}")
        if notifier is not None and notifier.enabled:
            try:
                await notifier.send_error_alert(
                    f"⚠️ Startup symbol validation failed: {symbol} ({broker_names[symbol]}) — {reason}"
                )
            except Exception as e:
                logger.debug(f"Startup validation alert failed: {e}")
        if strict and check.broker_answered:
            engine = manager.get_engine(symbol)
            if engine is not None:
                engine.state = BotState.PAUSED
                logger.warning(
                    f"Startup validation strict [{symbol}]: engine paused — "
                    f"re-validate via /api/symbols/{symbol}/validate before trading"
                )


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.logging_config import configure_logging

    configure_logging()

    # mcp 依赖版本门禁（fail-fast）：venv 若漂移到 mcp 2.x，`mcp.server.fastmcp`
    # 导入路径失效，AI 分析会以 "Agent error: No module named 'mcp.server.fastmcp'"
    # 的形式每根 K 线刷屏（2026-09-14 事件之一）——启动即拒绝，避免带病运行。
    # mcp 完全未安装时不阻断（AI 不可用但交易可跑，沿用既有 warning 语义）。
    try:
        import importlib.metadata as _md

        _mcp_ver = _md.version("mcp")
        if int(_mcp_ver.split(".")[0]) != 1:
            raise RuntimeError(
                f"mcp {_mcp_ver} installed but backend requires mcp 1.x "
                "(mcp.server.fastmcp). Pin mcp>=1.0,<2 and reinstall the venv."
            )
        logger.info(f"mcp version gate OK: mcp {_mcp_ver}")
    except _md.PackageNotFoundError:
        logger.warning("mcp not installed — AI agent will be unavailable")

    logger.info("Starting Trading Bot (multi-symbol)...")

    from app.auth import _assert_auth_consistent

    _assert_auth_consistent()

    # LLM provider 配置 fail-fast（AC-10）：未知名/openai_compat 缺 base_url 时
    # 拒绝启动，避免交易任务在运行中才 KeyError/超时。
    try:
        from app.ai.provider import get_provider

        _provider = get_provider()
        logger.info(f"LLM provider: {_provider.name} (model={settings.llm_model or 'per-agent defaults'})")
    except RuntimeError as e:
        logger.error(f"LLM provider config invalid: {e}")
        raise

    # Phase 1 observability: slow query logger — attach once, survives entire app lifetime
    install_slow_query_logger(db_engine, threshold_ms=settings.db_slow_query_threshold_ms)

    # Auto-add missing columns/tables (safe for production — IF NOT EXISTS guards)
    # Each statement runs in its own session to isolate transaction abort on error.
    from sqlalchemy import text

    schema_stmts = [
        "ALTER TABLE trades ADD COLUMN IF NOT EXISTS trade_reason VARCHAR(255)",
        "ALTER TABLE trades ADD COLUMN IF NOT EXISTS pre_trade_snapshot JSON",
        "ALTER TABLE trades ADD COLUMN IF NOT EXISTS post_trade_analysis JSON",
        "ALTER TABLE trades ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE",
        "CREATE INDEX IF NOT EXISTS ix_trades_is_archived ON trades (is_archived)",
        """CREATE TABLE IF NOT EXISTS ai_usage_logs (
            id BIGSERIAL PRIMARY KEY,
            timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
            agent_id VARCHAR(100) NOT NULL,
            model VARCHAR(100) NOT NULL,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd_sdk DOUBLE PRECISION,
            cost_usd_calc DOUBLE PRECISION,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            turns INTEGER NOT NULL DEFAULT 0,
            tool_calls_count INTEGER NOT NULL DEFAULT 0,
            success BOOLEAN NOT NULL DEFAULT TRUE,
            raw_usage JSON
        )""",
        "CREATE INDEX IF NOT EXISTS ix_ai_usage_logs_timestamp ON ai_usage_logs (timestamp)",
        "CREATE INDEX IF NOT EXISTS ix_ai_usage_logs_agent_id ON ai_usage_logs (agent_id)",
    ]
    for stmt in schema_stmts:
        try:
            async with async_session() as _tmp_session:
                await _tmp_session.execute(text("SET lock_timeout = '5s'"))
                await _tmp_session.execute(text(stmt))
                await _tmp_session.commit()
        except Exception as e:
            logger.warning(f"Schema stmt skipped: {str(e)[:120]}")
    logger.info("DB schema check complete")

    # Mint long-lived JWT so MCP tools can call the backend API (localhost) with auth.
    import os as _os

    from app.auth import mint_internal_token

    _internal_token = mint_internal_token()
    if _internal_token:
        _os.environ["INTERNAL_API_TOKEN"] = _internal_token
        logger.info("Minted INTERNAL_API_TOKEN for MCP tool → backend calls")

    # Initialize shared components
    connector = MT5BridgeConnector()
    redis_client = redis_lib.from_url(settings.redis_url)
    ai_client = AIClient()

    # Create a persistent DB session for bot engines
    db_session = async_session()

    # 复用与其它进程（MCP server stdio / agent runner）同一份加载逻辑，
    # 避免"主进程一套、子进程另一套"漂移出不一致的别名映射。
    try:
        from app.services.symbol_config_service import load_profiles_into_memory

        await load_profiles_into_memory()
    except Exception as e:
        logger.warning(f"Symbol profile DB load failed (using static defaults): {e}")

    # Initialize BotManager (creates one engine per symbol)
    manager = BotManager(connector, db_session, redis_client)

    # Initialize sentiment analyzer (shared)
    sentiment_analyzer = NewsSentimentAnalyzer(ai_client, db_session, redis_client)
    manager.set_sentiment_analyzer(sentiment_analyzer)

    # Initialize historical data collector (uses first engine's market_data)
    first_engine = next(iter(manager.engines.values()))
    hist_collector = HistoricalDataCollector(first_engine.market_data, db_session)

    # Initialize macro data service — register with BotManager so newly-added
    # engines (via /api/symbols hot-reload) receive the same wiring.
    macro_service = MacroDataService(db_session)
    event_calendar = MacroEventCalendar()
    manager.set_macro_service(macro_service)
    manager.set_event_calendar(event_calendar)

    # Initialize optimizer (uses Claude Agent SDK via AIClient)
    optimizer = StrategyOptimizer(ai_client, db_session)
    optimizer.set_collector(hist_collector)
    manager.set_optimizer(optimizer)

    # Initialize Telegram notifier
    notifier = TelegramNotifier()
    manager.set_notifier(notifier)
    if notifier.enabled:
        logger.info("Telegram notifications enabled")
        # Alert the operator when the LLM circuit breaker trips (endpoint down).
        import asyncio

        llm_circuit_breaker.set_alert_callback(
            lambda msg: asyncio.create_task(notifier._send(msg))
        )
    else:
        logger.info("Telegram notifications disabled (no token/chat_id)")

    # 启动券商校验 —— 券商是"什么能交易"的事实来源。"warn"（默认）仅告警，
    # 使 VPS/bridge 故障绝不会停摆交易；"strict" 把券商明确报告缺失或不可
    # 交易的引擎置 PAUSED（它们保留在 manager.engines 中，持仓对账与手动平仓
    # 仍可用）。同时把券商 volume 限制回填进内存 profile，供订单侧手数防线使用。
    await _validate_symbols_at_startup(connector, manager, notifier)

    # Set up routes with manager reference
    bot.set_manager(manager)
    webhooks.init_webhooks(manager)
    backtest.set_market_data(first_engine.market_data)
    backtest.set_collector(hist_collector)
    data.set_collector(hist_collector)
    ml.set_ml_deps(hist_collector)
    macro.set_macro_deps(macro_service, event_calendar)

    # Store references for health checks
    app.state.manager = manager
    app.state.connector = connector
    app.state.redis = redis_client
    app.state.ai_client = ai_client
    app.state.hist_collector = hist_collector

    # Initialize metrics
    from app.metrics import Metrics, set_metrics

    metrics = Metrics(redis_client)
    set_metrics(metrics)
    app.state.metrics = metrics

    # Initialize health monitor
    from app.bot.health_monitor import HealthMonitor

    health_monitor = HealthMonitor(connector, manager, notifier)
    app.state.health_monitor = health_monitor

    # Initialize MCP tools (needed for AI agent trading via scheduler)
    try:
        from mcp_server.tools import init_mcp_tools
    except ImportError:
        logger.warning("MCP tools not available (mcp_server not importable)")
    else:
        try:
            init_mcp_tools(redis_client)
            logger.info("MCP tools initialized for AI agent")
        except Exception as e:
            logger.error(f"MCP tools init failed: {e} — AI agent trading may not work")
        try:
            from mcp_server.agents.prompt_registry import init_prompt_registry

            init_prompt_registry(redis_client)
            logger.info("Prompt registry initialized")
        except Exception as e:
            logger.warning(f"Prompt registry init failed: {e}")

    # Restore trading_mode from Redis (survives redeploy)
    try:
        cached_mode = await redis_client.get("trading_mode")
        if cached_mode:
            mode = cached_mode if isinstance(cached_mode, str) else cached_mode.decode()
            if mode in ("strategy", "ai_autonomous"):
                settings.trading_mode = mode
                logger.info(f"Restored trading_mode from Redis: {mode}")
    except Exception as e:
        logger.debug(f"Redis trading_mode restore failed: {e}")

    # Phase 1 observability: pool pressure monitor — Telegram alert on sustained high utilization
    pool_monitor = PoolPressureMonitor(
        db_engine,
        notifier=notifier,
        high_threshold=settings.db_pool_alert_threshold,
        sustained_seconds=settings.db_pool_alert_sustained_seconds,
    )
    app.state.pool_monitor = pool_monitor

    # Start scheduler — BotScheduler.__init__ calls manager.set_scheduler(self),
    # which propagates the back-reference to every engine including future ones.
    scheduler = BotScheduler(manager)
    scheduler.set_health_monitor(health_monitor)
    scheduler.start()
    scheduler.scheduler.add_job(
        pool_monitor.tick,
        "interval",
        seconds=10,
        id="db_pool_pressure",
        replace_existing=True,
    )
    app.state.scheduler = scheduler

    if macro_service.is_configured:
        logger.info("FRED macro data service configured")
    else:
        logger.info("FRED macro data disabled (no FRED_API_KEY)")

    # Initialize Runner Manager (non-fatal: app works without it if tables don't exist yet)
    try:
        from app.runner.backend import ProcessRunnerBackend
        from app.runner.heartbeat import RunnerHeartbeatMonitor
        from app.runner.job_queue import JobQueue
        from app.runner.manager import RunnerManager
        from app.vault import vault

        runner_backend = ProcessRunnerBackend()
        runner_db_session = async_session()
        runner_manager = RunnerManager(runner_db_session, redis_client, runner_backend, vault)
        job_queue = JobQueue(runner_db_session, redis_client)
        heartbeat_monitor = RunnerHeartbeatMonitor(
            runner_manager,
            interval_seconds=settings.runner_heartbeat_interval,
            max_misses=settings.runner_heartbeat_max_misses,
        )

        # Rebuild job queue from DB on startup
        await job_queue.rebuild_from_db()

        # Add heartbeat check to scheduler
        scheduler.scheduler.add_job(
            heartbeat_monitor.check_all,
            "interval",
            seconds=settings.runner_heartbeat_interval,
            id="runner_heartbeat",
            replace_existing=True,
        )

        app.state.runner_manager = runner_manager
        app.state.job_queue = job_queue
        app.state.heartbeat_monitor = heartbeat_monitor

        logger.info("Runner manager initialized")
    except Exception as e:
        logger.warning(f"Runner manager init failed (non-fatal): {e}")
        logger.warning("Runner features disabled — run 'alembic upgrade head' to create runner tables")

    # Chat V2 worker (non-fatal). Chat runs stay queued until the tables exist,
    # so a pending `alembic upgrade head` degrades safely instead of dropping work.
    chat_stop = asyncio.Event()
    try:
        from app.services.chat_runs import chat_worker

        app.state.chat_worker_task = asyncio.create_task(chat_worker(chat_stop))
        logger.info("Chat run worker started")
    except Exception as e:
        logger.warning(f"Chat worker init failed (non-fatal): {e}")

    # Start symbol-config hot-reload subscriber
    await manager.start_reload_subscriber()

    symbols = manager.get_symbols()
    logger.info(f"Trading Bot initialized — symbols: {symbols}")

    # Loud startup banner for safety-critical config so an operator skimming
    # logs after a deploy can spot dangerous combinations at a glance.
    if settings.paper_trade:
        logger.warning("PAPER TRADE MODE — orders are simulated; no broker calls will hit MT5")
    else:
        logger.warning(f"LIVE TRADE MODE — rollout={settings.rollout_mode!r}; orders will hit broker")
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        logger.warning("Telegram alerts DISABLED — set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID to enable")
    if not settings.fred_api_key:
        logger.info("FRED macro data DISABLED — set FRED_API_KEY for macro features")
    if not settings.trusted_host_list:
        logger.warning("TRUSTED_HOSTS not set — Host header validation disabled (set in production)")
    if not settings.auth_password_hash:
        logger.warning("AUTH_PASSWORD_HASH empty — API auth DISABLED (do not run in production)")

    yield

    # Shutdown
    logger.info("Shutting down...")
    chat_stop.set()
    task = getattr(app.state, "chat_worker_task", None)
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    if hasattr(app.state, "runner_manager"):
        await app.state.runner_manager.shutdown()
    if "runner_db_session" in dir():
        await runner_db_session.close()
    scheduler.stop()
    await manager.stop_reload_subscriber()
    await manager.stop()
    await connector.close()
    await db_session.close()
    await redis_client.close()


# Gate Swagger / ReDoc / OpenAPI behind ENABLE_API_DOCS — default off so prod
# doesn't expose the full route catalog to unauthenticated scanners.
_docs_enabled = os.getenv("ENABLE_API_DOCS", "0") == "1"
app = FastAPI(
    title="Trading Bot",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)


# i18n: translate error details per Accept-Language
from fastapi import HTTPException, Request  # noqa: E402, F811
from fastapi.responses import JSONResponse  # noqa: E402

from app.i18n import pick_language, translate_detail  # noqa: E402


@app.exception_handler(HTTPException)
async def http_exception_i18n_handler(request: Request, exc: HTTPException):
    lang = pick_language(request.headers.get("accept-language"))
    detail = translate_detail(str(exc.detail), lang) if isinstance(exc.detail, str) else exc.detail
    return JSONResponse(status_code=exc.status_code, content={"detail": detail}, headers=exc.headers)


# Security headers middleware
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        # CSP: API serves JSON in prod (docs gated by ENABLE_API_DOCS). Drop
        # both script + style 'unsafe-inline'. If a future endpoint serves an
        # HTML error page that needs inline style, switch that endpoint to
        # JSON or use a per-response nonce instead of broadening CSP again.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self'; "
            "img-src 'self' data: blob:; "
            "connect-src 'self' https: wss:; "
            "frame-ancestors 'none'"
        )
        return response


app.add_middleware(SecurityHeadersMiddleware)

# Trust only configured hostnames in production (blocks spoofed Host headers).
# Empty list disables the middleware entirely so dev / tests are not affected.
if settings.trusted_host_list:
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list)

# Phase 1 observability: warn on long-held DB connections per request
app.add_middleware(
    SessionLifetimeMiddleware,
    async_engine=db_engine,
    warn_threshold_ms=settings.db_request_warn_ms,
    error_threshold_ms=settings.db_request_error_ms,
)

# Phase 4 rate limit — Redis token bucket per (IP, path). Fails open if Redis unavailable.
from app.middleware.rate_limit import RateLimitMiddleware

app.add_middleware(
    RateLimitMiddleware,
    sustained_per_minute=settings.rate_limit_per_minute,
    burst_capacity=settings.rate_limit_burst,
)

# Auth middleware disabled — using Bearer token auth (legacy password mode)
# app.add_middleware(AuthMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
)

# Routes
app.include_router(admin.router)
app.include_router(auth_router)
app.include_router(webauthn_router)
app.include_router(bot.router)
app.include_router(positions.router)
app.include_router(history.router)
app.include_router(strategy.router)
app.include_router(ai_insights.router)
app.include_router(backtest.router)
app.include_router(market_data.router)
app.include_router(data.router)
app.include_router(ml.router)
app.include_router(macro.router)
app.include_router(analytics.router)
app.include_router(metrics_routes.router)
app.include_router(secrets.router)
app.include_router(runners.router)
app.include_router(jobs.router)
app.include_router(rollout.router)
app.include_router(integration.router)
app.include_router(webhooks.router)
app.include_router(activity.router)
app.include_router(agent_prompts.router)
app.include_router(agent_chat.router)
app.include_router(ai_usage.router)
app.include_router(memory_routes.router)
app.include_router(quant.router)
app.include_router(symbols_routes.router)
app.include_router(ws_router)
app.include_router(ws_runners_router)


@app.get("/health")
async def health():
    mgr = app.state.manager
    first_engine = next(iter(mgr.engines.values())) if mgr.engines else None
    payload = await check_health(
        first_engine,
        app.state.connector,
        app.state.redis,
        app.state.ai_client,
    )
    # Return 503 when degraded so platform liveness probes (Railway, k8s) can
    # actually evict / restart the pod. Plain 200 every time gave probes
    # nothing to act on, hiding outages.
    if payload.get("status") != "ok":
        from fastapi.responses import JSONResponse

        return JSONResponse(payload, status_code=503)
    return payload


@app.get("/health/pool", dependencies=[Depends(require_auth)])
async def health_pool():
    """Live DB pool stats + recent slow queries + long-hold requests.

    Auth-gated: leaks DB topology, partial SQL, and request-path latencies that
    are useful for an attacker fingerprinting the deployment.
    """
    stats = get_pool_stats(db_engine)
    monitor = getattr(app.state, "pool_monitor", None)
    return {
        "pool": stats,
        "samples": monitor.recent(60) if monitor else [],
        "slow_queries": slow_query_tracker.top(10),
        "long_holds": long_hold_tracker.top(10),
        "thresholds": {
            "alert_utilization": settings.db_pool_alert_threshold,
            "alert_sustained_seconds": settings.db_pool_alert_sustained_seconds,
            "slow_query_ms": settings.db_slow_query_threshold_ms,
            "request_warn_ms": settings.db_request_warn_ms,
            "request_error_ms": settings.db_request_error_ms,
        },
    }
