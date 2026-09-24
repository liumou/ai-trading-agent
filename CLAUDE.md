# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Language Rule (强制)

**中文是唯一工作语言** — 所有以下内容必须使用中文：
- **代码注释**：任何新增/修改代码的注释必须用中文撰写
- **对话**：与用户的交流必须使用中文
- **思考过程**：内部推理、分析、计划必须使用中文

代码标识符、变量名、函数名、类型名、字符串字面量、日志消息中的英文专有名词（如 API 端点名、品牌名、错误码）除外。提交信息（git commit）保持英文（见 Git Workflow）。

---

# AI Trading Agent — Claude Code Guide

## Project Overview

Multi-symbol automated trading bot: FastAPI backend (Railway) + Next.js frontend (Vercel) + MT5 Bridge (Windows VPS). Trades GOLD, OILCash, BTCUSD, USDJPY.

**Current state**: Phase 6-10 complete (testing, resilience, trading features, ML, polish). AI Agent architecture (Phases 0-F) complete per ROADMAP-AI-AGENT.md. Now in production hardening and feature polish.

## Architecture

```
Frontend (Next.js 16) → Backend (FastAPI) → MT5 Bridge (Windows VPS)
  /trading 手动下单 → ManualOrderGate 风控防火墙（硬闸门+AI 审查） ↗
                                          → PostgreSQL + Redis (Docker)
                                          → Claude AI (sentiment + optimization)
                                          → LightGBM ML models (per-symbol)
                                          → MCP Agent (Claude Code SDK)
                                          → Telegram notifications
```

## Key Directories

- `backend/app/` — FastAPI backend
  - `bot/engine.py` — main trading engine (refactored: process_candle → sub-methods)
  - `bot/scheduler.py` — APScheduler jobs (candle, sentiment, sync, health, retrain)
  - `bot/health_monitor.py` — MT5 Bridge heartbeat + auto-pause/resume
  - `bot/account_switch.py` — **MT5 账号切换服务**：安全暂停 → Bridge `/account/switch` → 刷新规格 → 显式恢复引擎；Redis `switching:in_progress` 门禁 + asyncio 锁（防止切换瞬间错账号下单）；失败不恢复引擎；审计日志
  - `services/order_preflight.py` — **共享下单硬闸门（唯一真相源）**：resolve_symbol → 并发行情 → per-symbol rolling spread → `guardrails.validate_order` → volume 归一 → rollout → llm_allow_live。AI/MCP 通道与手动通道共同调用；strict_symbol=True（手动）时 symbol 不可解析/无 volume 配置 fail-closed 拒单
  - `services/manual_order_gate.py` — **手动交易风控防火墙**：per-account 锁 → 硬闸门 → 情绪规则（马丁=block 直接拒 / 复仇·连亏·频率=warn 交 LLM）→ OrderAudit(PENDING_REVIEW) → 异步 LLM 审查（`asyncio.wait_for` 25s 只约束 LLM 调用；verdict 白名单）→ APPROVED 执行 / CAUTION 二次确认（review_id 绑定参数 + 120s TTL）/ REJECTED 硬拦截不可强制；LLM 失败 fail-closed。改挂单=全流水线重审；改持仓 SL/TP 用 entry 锚点漂移预算（Redis `manual:sl_anchor:{ticket}`）
  - `services/position_close.py` — 手动平仓统一闸门（switching fail-closed + rollout 拦截 + ticket 归属 + 记账防双计；dashboard DELETE /api/positions 与 /api/trading 平仓共用）
  - `services/price_alert_service.py` — **行情提醒巡检引擎**：价格阈值 → 飞书卡片。独立 interval job（每 2s）从 Redis price cache 读 tick（`price:cache:{symbol}`，scheduler `_fetch_tick` 写），严格 `>`/`<` 比较，Redis `price_alert:first_trigger:{id}` 维护连续满足时长，原子计数防并发双发，达 `max_notifications` 自动停用（toggle 重开重置计数）。规则表 `price_alerts`，API `/api/price-alerts`，卡片模块 `notifications/feishu.py`（env `FEISHU_WEBHOOK_URL`）
  - `strategy/` — 11 strategies (EMA, RSI, Breakout, Mean Reversion, ML, DCA, Grid, MomentumRank, PairSpread, RiskParity, Ensemble) + MTF filter + regime detection
  - `risk/` — risk manager, circuit breaker (H3: circuit key 按账号隔离), correlation filter
  - `ml/` — LightGBM trainer, features (40+), predictor, drift detection, sentiment features
  - `backtest/` — engine, optimizer, walk_forward, monte_carlo, overfitting (composite score)
  - `data/` — collector, macro data, macro events
  - `news/` — news fetcher + sources
  - `notifications/` — Telegram alerts + Feishu card alerts（`feishu.py`，价格阈值提醒）
  - `memory/` — session memory service + consolidator
  - `ai/` — Claude AI client (SDK first, Anthropic API fallback), context builder, prompts, strategy optimizer
  - `api/routes/` — 120+ REST endpoints across 28 route files（含 manual_trading.py 手动交易）
  - `auth.py` — legacy JWT password auth (active)
  - `auth_webauthn.py` — Passkey (WebAuthn) auth (code exists, disabled)
  - `middleware/auth.py` — global JWT cookie auth middleware (backward compat)
  - `vault.py` — VaultService (AES-256-GCM encryption, HKDF key derivation)
  - `vault_health.py` — OAuth token health checker (scheduler job)
  - `runner/` — Docker Sandbox Runner system
    - `backend.py` — RunnerBackend ABC + ProcessRunnerBackend
    - `manager.py` — RunnerManager (lifecycle, secrets injection, observability)
    - `job_queue.py` — Redis-backed job queue with DB persistence
    - `heartbeat.py` — RunnerHeartbeatMonitor (APScheduler integration)
    - `agent_entrypoint.py` — asyncio job loop, Redis BRPOP, health check, heartbeat
  - `api/routes/runners.py` — Runner CRUD + lifecycle + observability (12 endpoints)
  - `api/routes/jobs.py` — Job CRUD + cancel + retry (5 endpoints)
  - `api/routes/activity.py` — AI activity log
  - `api/routes/agent_prompts.py` — agent prompt CRUD
  - `api/routes/rollout.py` — rollout mode + deploy readiness
  - `api/routes/accounts.py` — MT5 账号 CRUD + 切换（`/api/accounts`，Vault 加密凭据，独立于 secrets 表）
  - `api/routes/integration.py` — service connectivity diagnostics
  - `api/routes/memory.py` — session memory management
  - `api/ws_runners.py` — WebSocket live log streaming per runner
  - `audit.py` — shared audit logging utility
  - `constants.py` — all magic numbers centralized
  - `config.py` — Settings + SYMBOL_PROFILES + SESSION_PROFILES
  - `metrics.py` — Redis-backed timing/counters
  - `cache.py` — Redis response cache helper
  - `logging_config.py` — structured JSON logging
- `backend/mcp_server/` — MCP Agent system (Claude Code SDK)
  - `agents/` — orchestrator, technical/fundamental/risk analysts, reflector, prompt_registry
  - `tools/` — 14 tool modules (broker, market_data, indicators, risk, portfolio, sentiment, history, journal, learning, session, strategy_gen, memory, overfitting, strategy_switch)
  - `guardrails.py` — non-bypassable trading limits at broker tool level
  - `strategy_switch_guard.py` — AI auto-strategy-switch safety (cooldown 1h, max 3/day, feature flag)
  - `sdk_client.py` — Claude Code SDK client
  - `server.py` — MCP server entry
  - `agent_config.py` — agent entry point
- `frontend/` — Next.js App Router (19 pages: dashboard, backtest, history, insights, ai-usage, ml, macro, quant, activity, agent-prompts, accounts, integration, notifications, settings, db-health, symbols, login, setup, root)
  - `app/accounts/` — MT5 账号管理页（列表/新增/切换/删除，实时切换）
  - `app/dashboard/` — main trading dashboard
  - `app/backtest/` — backtest, optimizer, walk-forward analysis
  - `app/history/` — trade history/journal
  - `app/insights/` — sentiment analysis + optimization reports
  - `app/ml/` — ML model performance monitoring
  - `app/macro/` — macro economic data + correlations
  - `app/activity/` — unified AI activity log
  - `app/agent-prompts/` — customize AI agent system prompts
  - `app/integration/` — service connectivity status + config
  - `app/notifications/` — event history
  - `app/login/` — passkey login (WebAuthn via @simplewebauthn/browser)
  - `app/setup/` — first-time passkey registration wizard
  - `app/settings/` — per-symbol risk + AI filter + paper trade switch
  - `app/quant/` — quantitative risk analysis (VaR, correlation, volatility)
  - `app/ai-usage/` — per-agent token + cost monitoring
  - `components/layout/` — AppShell (auth guard + sidebar), Sidebar, PageHeader, PageInstructions
  - `components/ui/` — 33 UI primitives (badge, button, card, dialog, data-table, gold-gauge, etc.)
  - `components/ai/` — NewsCard, OptimizationReport, SentimentBadge
  - `components/chart/` — PriceChart (lightweight-charts)
  - `lib/api.ts` — axios client with auth interceptor
  - `lib/websocket.ts` — WS client with token auth
- `mt5_bridge/` — FastAPI on Windows VPS (MetaTrader5 SDK); `main.py` + `watchdog.py` (auto-restart) + `requirements.txt`
- `scripts/backup_db.sh` — daily pg_dump
- `backend/tests/` — 496 tests across 27 test files (unit + integration)
- `Dockerfile.trading-agent` — Python 3.11-slim agent image (for sandboxed agents)
- `backend/Dockerfile` — Python 3.12-slim **main app image** (Railway deploy target; CMD runs `alembic upgrade head` then uvicorn)
- `docs/` — `LONG-TERM-DB-SCALING.md` + logo/screenshots

## Tech Stack

| Layer | Tech |
|-------|------|
| Backend | FastAPI 0.115, SQLAlchemy 2.0 (async), asyncpg, Redis, APScheduler, Python 3.12 (backend/Dockerfile) |
| Frontend | Next.js 16, React 19, Tailwind 4, Zustand, lightweight-charts, recharts |
| ML | LightGBM, scikit-learn, pandas |
| AI | Claude Code SDK (Max subscription) + Anthropic SDK fallback |
| Auth | JWT Bearer token (username/password) — WebAuthn code exists but disabled |
| CI/CD | GitHub Actions (ruff, pytest, tsc, build), Railway auto-deploy |
| DB | PostgreSQL 15, Redis 7 (AOF persistence), 22 Alembic migrations |
| Notifications | Telegram bot alerts |

## AI Agent Architecture (Phases 0-F — all code complete)

### Phase 0 — Passkey Auth + Security (code complete, pending deploy)
- Backend: Owner, WebAuthnCredential, AuthSession, AuditLog models
- WebAuthn endpoints: register, login, logout, sessions, me
- Global auth middleware + security headers + CORS tightened
- Frontend: `/setup` (registration) + `/login` (authentication)
- **Disabled**: cross-origin cookie issues on Railway (`.up.railway.app` is public suffix)

### Phase A — Secrets Vault (code complete, pending deploy)
- VaultService: AES-256-GCM + HKDF key derivation from `VAULT_MASTER_KEY`
- Secrets API: CRUD + masked read + test connectivity + history
- OAuth health monitor: scheduler job every 5 min
- Frontend: `/secrets` page (removed from sidebar, accessible via integration page)

### Phase B — Docker Sandbox Runner (backend + frontend done)
- RunnerManager: lifecycle, secrets injection from Vault, observability
- Job Queue: Redis-backed with DB persistence, rebuild on restart
- Heartbeat Monitor: APScheduler job, 3-miss auto-restart
- Runner API (12 endpoints) + Job API (5 endpoints) + WebSocket live logs
- Agent entrypoint: asyncio job loop, Redis BRPOP, health check on :8090

### Phase C — Claude Agent Core (code complete)
- Claude Code SDK: `claude-code-sdk` (Max subscription, no API key needed)
- MCP Tools (15 modules): broker, market_data, indicators, risk, portfolio, quant, sentiment, history, journal, learning, session, strategy_gen, strategy_switch, memory, overfitting
- Guardrails: non-bypassable limits at broker tool level
- `backend/app/ai/client.py`: `complete_async()` tries SDK first, falls back to Anthropic API

### Phase D — Multi-Agent Architecture (code complete)
- Orchestrator (Sonnet) + Technical/Fundamental/Risk Analysts (Haiku) + Reflector (Haiku)
- Only orchestrator has execution tools — specialists are read-only
- Activated via `AGENT_MODE=multi` env var (default: `single`)
- Prompt registry: customizable per-agent system prompts via `/agent-prompts` page

### Phase E — Advanced Capabilities (code complete)
- Self-reflection & learning loop: analyze_recent_trades, detect_regime
- Session memory: Redis-backed daily context (24h TTL) + cross-session learnings (7d TTL)
- Adaptive strategy selection: STRATEGY_PROFILES with regime suitability mapping
- Reflector agent runs as Phase 0 before analysis

### Phase F — Production Hardening (code complete)
- Rollout modes: `shadow` → `paper` → `micro` → `live`
- Broker enforcement: shadow/paper intercepted, micro caps at 0.01 lot
- Deploy readiness checks: DB, Redis, Vault, WebAuthn, OAuth, rollout mode
- Frontend: rollout mode banner + readiness panel on `/runners` page

## Development Commands

```bash
# Backend (Python 3.12 venv)
cd backend
.venv/bin/python -m pytest tests/ -v --no-cov      # 全部测试（496 tests）；pyproject 默认 addopts 含 --cov，用 --no-cov 跳过覆盖统计加快速度
.venv/bin/python -m pytest tests/unit/test_risk_manager.py -v --no-cov  # 单个测试文件
.venv/bin/python -m pytest tests/unit/test_risk_manager.py -k "lot_size" -v --no-cov  # 单个用例
.venv/bin/python -m ruff check .                   # lint（pyproject.toml: line-length 120, E/F/I/UP/B）
.venv/bin/python -m ruff format .                  # format

# Frontend
cd frontend
npx tsc --noEmit          # type check
npm run build             # production build
npm run dev               # dev server

# Railway
railway vars list -s backend --kv    # list env vars
railway logs                          # view logs
railway vars set -s backend "KEY=value"  # set env var
```

## Important Patterns

- **Auth**: Using Bearer token auth (username/password). Login uses constant-time bcrypt regardless of username match (timing-oracle fix). `AuthMiddleware` (WebAuthn) disabled in `main.py`. `require_auth` dependency checks `Authorization: Bearer <token>` header. Frontend stores token in localStorage. Tests bypass auth via `AUTH_PASSWORD_HASH=""` in conftest.py.
- **Router auth**: Use `app.api.router_factory.make_authed_router(prefix, tags)` for new routers — applies `Depends(require_auth)` at router level so endpoints can not silently ship unauthenticated.
- **DB session**: Shared `BotEngine.db` is the legacy long-lived session. New code MUST use `app.db.session.transaction()` (commits + rolls back automatically) or `async_session()` directly. `_log_event` already opens its own session per call to avoid asyncpg "concurrent operations" errors when multiple scheduler jobs touch the engine simultaneously.
- **Lot sizing**: `RiskManager.calculate_lot_size(balance, sl_distance, ...)` and `calculate_kelly_size(...)` take a **price-unit distance** (NOT pips). Formula uses `contract_size` directly — passing the wrong unit silently mis-sizes trades. Originally used `pip_value × 100`, only correct for GOLD; broken for OIL / BTC / USDJPY (10×–100× off).
- **SYMBOL_PROFILES**: Per-symbol config in config.py (timeframe, pip_value, SL/TP mults, ML defaults). Use `AssetClass` enum (in `app.market.sessions`) instead of raw strings when comparing or constructing.
- **Constants**: All magic numbers in `constants.py` — never hardcode
- **Tests**: 496 tests across 27 files. SQLite in-memory for DB, fakeredis, mock MT5 connector. Auth disabled via `os.environ["AUTH_PASSWORD_HASH"] = ""` in conftest.py. SDK mocks use `type` attribute instead of `isinstance`. mcp-dependent tests need `claude-agent-sdk` package.
- **Runner**: `RunnerManager` init in `main.py` lifespan. Uses `ProcessRunnerBackend` by default (Railway-compatible). Heartbeat monitor runs as APScheduler job. Job queue uses dual Redis+DB storage. Runner logs streamed via Redis pub/sub to WebSocket.
- **DB pool**: Default `db_pool_size=8`, `max_overflow=12` (Railway Hobby plan caps connections at 25). Bump in env on Pro plans.
- **Daily reset**: Per-asset-class hour from `app.market.sessions._RULES` — forex/metal/energy reset at 22 UTC, index 22, stock 21, crypto 0. Scheduler registers one cron job per unique reset hour across active engines.
- **Coverage**: CI threshold 30% (overall ~29%, critical paths ~89%)
- **Telegram**: Notifications for trade signals, AI analysis, system alerts. Thai language alerts.
- **Manual trading firewall invariants**（手动交易防火墙不变量，勿破坏）: ① 硬闸门先行，LLM 审查只能收紧不能放宽；② REJECTED 不可强制（无 override 端点）；③ LLM 失败/超时/畸形 verdict → fail-closed 拦截 + `AI_AGENT_ERROR` 事件（基础设施故障不伪装成分析结论）；④ 手动通道 switching 门禁 fail-closed（Redis 异常拒单），AI 通道 best-effort；⑤ 执行类请求（place_order/place_pending_order/cancel_order）禁歧义重试（超时重试=双开仓）；⑥ 下单前必须经 `preflight_order`，禁止直连 connector 下单；⑦ 手动单 `MANUAL_MAGIC_NUMBER=234100`；⑧ CAUTION 确认绑定 OrderAudit 行（review_id），参数不可重传，TTL 120s。
- **Retcode 语义**: 挂单成功 retcode 是 `TRADE_RETCODE_PLACED(10008)`，市价是 `DONE(10009)` —— 挂单端点成功判定必须接受两者；挂单 type_filling 按 `symbol_info().filling_mode` 位掩码推导（回落 RETURN），硬编码 IOC 会被 10030 拒。

## Known Issues

- Manual trading: shadow/paper rollout 模式禁止手动真实单（与 AI 通道同门禁），前端 /trading 有横幅提示；CAUTION 确认窗口 120s 内有效，过期需重新提交（EXPIRED）

- MT5 Bridge frequently shows "stale tick" warnings (market closed or VPS connectivity)
- Health monitor stays in degraded state when MT5 Bridge is offline (by design)
- Shared db_session can cause `InFailedSQLTransactionError` — mitigated with rollback() calls
- DB datetime columns: must use `datetime.utcnow()` (naive), NOT `datetime.now(timezone.utc)` (offset-aware) — asyncpg rejects offset-aware for `TIMESTAMP WITHOUT TIME ZONE`
- Claude Code SDK: `rate_limit_event` parse error on heavy usage — handled gracefully in `base.py`
- WebAuthn passkey auth: disabled due to cross-origin cookie issues on Railway (`.up.railway.app` is public suffix)
- Deploy: Railway uses Dockerfile CMD, NOT Procfile — always edit `backend/Dockerfile` (Python 3.12, Node.js 22 installed for claude-agent-sdk CLI) CMD for startup changes
- Deploy: Alembic migration can hang on table lock during zero-downtime deploy (old instance holds locks) — mitigated with `timeout 120` in backend/Dockerfile CMD + `lock_timeout = 5s` in alembic/env.py and lifespan
- Alembic: Never reuse revision IDs — each migration file must have a unique revision and correct down_revision chain
- Alembic: `s9t0u1v2w3x4` adds performance indexes (idempotent, fast). `t0u1v2w3x4y5` converts `JSON` columns to `JSONB` — runs `ALTER TYPE jsonb USING col::jsonb` per column, takes ACCESS EXCLUSIVE lock, **maintenance window required** for large tables.
- Backups: APScheduler runs `scripts/backup_db.sh` daily at 02:30 UTC when `ENABLE_DB_BACKUPS=1` is set. No-op otherwise so dev environments stay clean.
- Production env vars: `TRUSTED_HOSTS` (comma-separated, blocks Host header injection), `VAULT_SALT` (random per deployment), `ENABLE_DB_BACKUPS=1`, `INTERNAL_API_TOKEN` is minted on startup with 24h expiry.
- Sentry: set `SENTRY_DSN`, optional `SENTRY_ENVIRONMENT` / `SENTRY_TRACES_SAMPLE_RATE`. SDK initialized at module import in `main.py:_init_sentry()` so import-time errors are captured. PII suppressed via `send_default_pii=False`.
- Rate limit: token bucket per (identity, path). Identity = JWT subject when bearer token decodes, else trusted client IP. Auth endpoints always bucket by IP (user not yet identified). Configure via `rate_limit_per_minute` / `rate_limit_burst` env vars.
- PgBouncer: set `db_pgbouncer_mode=true` when routing through transaction-mode pooling. Disables asyncpg statement cache (cache is per-connection, breaks under transaction-mode pool reuse). Recommended pool sizing in PgBouncer mode: `db_pool_size=2-3`, `db_max_overflow=5` — PgBouncer handles the real concurrency, the SQLAlchemy pool only owns server connections.

## User Preferences (from memory)

- **ตรวจละเอียด**: After every edit, grep to verify ALL occurrences were updated. Don't trust replace_all blindly.
- **Check both frontend AND backend** when a feature spans both sides.
- **ภาษา**: User communicates in Thai, code/commits in English.
- **中文规则**: 代码注释、对话、思考过程必须使用中文（见本文件顶部 Language Rule）。与用户交流时，优先使用中文，若用户用泰语则跟随泰语。
