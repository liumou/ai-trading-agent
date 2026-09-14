# Long-Term DB Scaling Plan

## Context

2026-04-17 prod outage: 996× `QueuePool limit ... timeout 30.00`. Short-term fix (raise pool 15→50, cache /sentiment/history, slow polling, split ML retrain session) shipped — buys time, does not solve root problem.

Root problem: single-pool architecture + long-held sessions + aggressive polling + no observability. At 2-3× current load it breaks again.

Goal: survive 10× traffic, 5× scheduler jobs, multi-replica deploy without pool exhaustion.

---

## Phase 1 — Observability First (1 week, P0)

**Must do before anything else.** Cannot optimize what we can't see.

### 1.1 Pool metrics export

File: [backend/app/metrics.py](backend/app/metrics.py), [backend/app/db/session.py](backend/app/db/session.py)

Export every 10s to Redis + `/health/pool`:
- `db_pool_size`, `db_pool_checked_out`, `db_pool_overflow`, `db_pool_checked_in`
- `db_pool_wait_time_p50`, `db_pool_wait_time_p99`
- Per-endpoint connection-hold histogram (middleware-based)

APScheduler job 10s tick. Expose via `GET /health/pool` for Railway observability panel.

### 1.2 Slow-query log

PG side: set `log_min_duration_statement = 500` via Railway PG config. Ship logs to same place as app logs.

App side: SQLAlchemy `before_cursor_execute` / `after_cursor_execute` events → log any query >500ms with stack trace of origin route/job.

### 1.3 Session-lifetime middleware

File: [backend/app/main.py](backend/app/main.py) — new middleware logs:
- Request path + session checkout duration
- Warn if >2s, error if >60s

Output goes to structured JSON log. Alert any request holding conn >60s.
The error threshold is high because it is wall-clock request time: compute-heavy
endpoints (e.g. overfitting-score) take 30s+ of pure CPU while holding no pool
conns. Config: `DB_REQUEST_WARN_MS` / `DB_REQUEST_ERROR_MS`.

### 1.4 Alert on pool pressure

Threshold: `checked_out / (pool_size + max_overflow) > 0.7` for 60s → Telegram alert via existing notifier ([backend/app/notifications/](backend/app/notifications/)).

**Deliverable**: dashboard page `/integration/db-health` showing live pool stats + top 10 slow queries + top 10 long-hold endpoints.

---

## Phase 2 — Fix Root Leaks (1-2 weeks, P1)

Use Phase 1 data to drive, not guess.

### 2.1 Audit all sessions across `await` boundaries

Target pattern: `async with async_session() as s: ... await external_call() ... await s.commit()`.

Grep targets:
- [backend/app/bot/scheduler.py](backend/app/bot/scheduler.py) — 6 jobs, already fixed `_ml_retrain_symbol`. Audit: `_memory_consolidation_job`, `_ai_usage_cleanup_job`, `_daily_summary_job`, `_pending_trades_recovery_job`, `_reconciliation_job`
- [backend/app/bot/engine.py](backend/app/bot/engine.py) — `process_candle` and subhelpers. Engine holds shared `db_session` — CLAUDE.md already flags as problem
- [backend/app/ai/](backend/app/ai/) — Claude API calls inside session scope? 3-30s hold possible
- [backend/app/mcp_server/tools/](backend/app/mcp_server/tools/) — 14 tool modules. Any DB + external call mixed

Rule: never hold session across network I/O. Pattern:
```python
async with async_session() as s: data = await load(s)
# session released
result = await external_api(data)
async with async_session() as s: await persist(s, result)
```

### 2.2 Kill shared `db_session` in BotManager/engine

[backend/app/bot/manager.py](backend/app/bot/manager.py) + engine share one long-lived session. Cause of `InFailedSQLTransactionError` (CLAUDE.md).

Refactor: remove `self.db_session`. Every method takes session or opens its own via `async_session()`. Short scope = short hold.

Risk: touches hot path. TDD. Run 444 tests plus paper-trade soak 24h.

### 2.3 Cache layer for read-heavy endpoints

Reuse [backend/app/cache.py](backend/app/cache.py). Candidates (from dashboard fetchData 9 calls):
- `/api/ai/sentiment/history` ✅ done
- `/api/ai/optimization/latest` — TTL 60s
- `/api/bot/status` — TTL 5s
- `/api/trades/recent` — TTL 15s
- `/api/ml/performance` — TTL 120s

Invalidate on mutation via `cache.invalidate("cache:trades:*")`.

---

## Phase 3 — Infra Pool (1 week, P1)

### 3.1 PgBouncer transaction mode

Deploy PgBouncer in front of Railway PG. Config:
```
pool_mode = transaction
max_client_conn = 1000
default_pool_size = 25
reserve_pool_size = 5
```

App pool_size drops 20 → 5 (short connections to PgBouncer which multiplexes). 1000 client slots share 25 backend conns.

Caveats:
- Transaction mode breaks session-level features: `SET LOCAL`, prepared statements (asyncpg uses them). Must set `statement_cache_size=0` in asyncpg + `prepared_statement_cache_size=0` in SQLAlchemy URL.
- LISTEN/NOTIFY not supported — grep to verify we don't use.
- Temp tables scoped to tx.

Test: spin up PgBouncer locally via docker-compose, run full test suite against it, smoke-test paper mode 24h.

### 3.2 Railway PG tuning

Confirm `max_connections`. Raise work_mem/shared_buffers if Railway tier allows. Add PG replica if budget permits (Phase 5).

---

## Phase 4 — Frontend Push (1-2 weeks, P2)

### 4.1 WebSocket-first dashboard

[frontend/app/dashboard/page.tsx](frontend/app/dashboard/page.tsx) polls 9 endpoints/60s. Replace with WS events from existing [backend/app/api/websocket.py](backend/app/api/websocket.py).

Server-side: publish events on state change (trade open/close, sentiment update, ML retrain done). Client subscribes via existing [frontend/lib/websocket.ts](frontend/lib/websocket.ts).

Keep REST only for:
- Initial load (1 fetch on mount)
- Historical queries (backtest, history page)

Target: dashboard steady-state = 0 REST calls after mount.

### 4.2 Rate limit API gateway

FastAPI middleware — `slowapi` or custom Redis token bucket. Limits:
- Authenticated user: 60 req/min per endpoint
- Global per-IP: 300 req/min
- Burst: 2× sustained for 10s

Returns 429 with `Retry-After`. Prevents runaway polling/client bugs from DoS'ing pool.

---

## Phase 5 — Read/Write Split (2-3 weeks, P3, optional)

Only if Phase 1-4 metrics show read contention still dominates.

- Add Railway PG read-replica
- Two engines: `engine_rw` (primary), `engine_ro` (replica)
- `get_db_ro()` dep for read-only routes
- Router based on route tag: dashboard/history/insights → ro, trades/bot/runners → rw
- Accept replica lag 1-2s (fine for dashboards, not for place_order)

Files: [backend/app/db/session.py](backend/app/db/session.py), add `ReadOnlyAsyncSession` alias + `get_db_ro` dep.

---

## Priority + Timeline

| Phase | Effort | Priority | Blocks |
|-------|--------|----------|--------|
| 1 Observability | 1w | P0 | Phase 2 |
| 2 Fix leaks | 1-2w | P1 | — |
| 3 PgBouncer | 1w | P1 | — |
| 4 WS + rate limit | 1-2w | P2 | — |
| 5 Read replica | 2-3w | P3 | optional |

Total: 6-9 weeks, can parallelize Phase 2 + Phase 3 after Phase 1.

## Verification

End-to-end test after each phase:
1. `k6 run load/dashboard.js` — 100 concurrent users, 10 min. Target: 0 pool timeouts, p99 latency <500ms.
2. Chaos: kill scheduler mid-retrain → pool stays <50% utilization.
3. Paper mode 7d soak — zero `QueuePool` errors in logs.
4. Pool dashboard graphs show `checked_out` never >70% of max.

## Files Touched (forecast)

- Backend: `metrics.py`, `db/session.py`, `main.py`, `bot/manager.py`, `bot/engine.py`, `bot/scheduler.py`, `mcp_server/tools/*`, `cache.py`, new `middleware/rate_limit.py`
- Frontend: `app/dashboard/page.tsx`, `lib/websocket.ts`, `app/integration/page.tsx` (pool health panel)
- Infra: Railway PgBouncer service, PG config, new env vars
- Tests: new `tests/load/`, update conftest for PgBouncer-compat

## Risks

- PgBouncer transaction mode bugs subtle (prepared stmts, session vars) — stage on paper mode first
- Removing shared `db_session` touches hot path — regressions possible, need TDD + soak
- WS migration doubles state-sync complexity — keep REST fallback for 1 release
- Read replica lag breaks write-read consistency — route carefully
