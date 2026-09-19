"""Phase 0 收口回归 — DELETE /api/positions/{ticket} 不得再绕过闸门。

历史漏洞：路由直连 first_engine.executor.close_position(ticket)，零检查——
无 switching 门禁、无 rollout 拦截、无 circuit_breaker/guardrails 记账。
本测试锁定 close_position_gated 的门禁序列与记账语义（含引擎双记账规避）。
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

import app.db.session as db_session_module
import app.services.position_close as position_close_module
from app.services.position_close import close_position_gated


@pytest.fixture
def connector(mock_connector):
    mock_connector.get_positions.return_value = {
        "success": True,
        "data": [{"ticket": 111, "symbol": "GOLD_", "type": "BUY", "volume": 0.1, "profit": -12.5}],
    }
    return mock_connector


@pytest.fixture
def no_engine(monkeypatch):
    """默认无活跃 BotManager（引擎归账分支的对照组）。"""
    monkeypatch.setattr(position_close_module, "_lookup_engine", lambda symbol: None)


@pytest.fixture
def db_session_patched(db_engine, monkeypatch):
    """把 position_close 内部使用的 async_session 指到测试引擎。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_session_module, "async_session", maker)
    return maker


@pytest_asyncio.fixture
async def live_mode(redis_client):
    """rollout 默认 shadow 会拦截手动平仓；成功路径显式置于 live。"""
    from mcp_server.guardrails import _KEY_PREFIX

    await redis_client.set(f"{_KEY_PREFIX}:rollout_mode", "live")


# ─── 门禁序列 ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_switching_in_progress_rejects(connector, redis_client, no_engine, db_session_patched):
    await redis_client.set("switching:in_progress", "1")
    result = await close_position_gated(connector, redis_client, 111)
    assert result == {"closed": False, "rejected": True, "reason": "Account switch in progress"}
    connector.close_position.assert_not_awaited()


@pytest.mark.asyncio
async def test_redis_failure_fails_closed(connector, no_engine, db_session_patched):
    """手动通道 Redis 异常必须拒绝（与 MCP broker 的 fail-open 相反）。"""
    broken = AsyncMock()
    broken.get.side_effect = RuntimeError("redis down")
    result = await close_position_gated(connector, broken, 111)
    assert result["closed"] is False
    assert result["rejected"] is True
    connector.close_position.assert_not_awaited()


@pytest.mark.asyncio
async def test_ticket_not_on_current_account_rejected(connector, redis_client, no_engine, db_session_patched):
    """跨账号 ticket 可重复：只允许平当前账号的持仓。"""
    result = await close_position_gated(connector, redis_client, 999)
    assert result["closed"] is False
    assert "not found" in result["error"].lower()
    connector.close_position.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["shadow", "paper"])
async def test_rollout_mode_intercepts(connector, redis_client, no_engine, db_session_patched, mode):
    await redis_client.set("guardrails:rollout_mode", mode)
    result = await close_position_gated(connector, redis_client, 111)
    assert result["closed"] is False
    assert result["rollout"] == mode
    connector.close_position.assert_not_awaited()


@pytest.mark.asyncio
async def test_positions_fetch_failure_rejects(connector, redis_client, no_engine, db_session_patched):
    connector.get_positions.return_value = {"success": False, "data": None, "error": "bridge down"}
    result = await close_position_gated(connector, redis_client, 111)
    assert result["closed"] is False
    connector.close_position.assert_not_awaited()


# ─── 成功路径与记账 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_success_without_engine_records_accounting(
    connector, redis_client, no_engine, db_session_patched, live_mode
):
    """无引擎品种：gate 负责记账（日亏 + 连亏），否则手动平仓绕过熔断计数。"""
    from mcp_server.guardrails import _daily_key

    result = await close_position_gated(connector, redis_client, 111)
    assert result["closed"] is True
    assert result["profit"] == -12.5

    from app.config import get_canonical_symbol

    pnl = float(await redis_client.get(f"circuit:daily_pnl:{get_canonical_symbol('GOLD_')}"))
    assert pnl == -12.5
    losses = await redis_client.llen(_daily_key("trade_results"))
    assert losses == 1

    # 事件落库 + WS 推送
    from sqlalchemy import select

    from app.db.models import BotEvent, BotEventType

    async with db_session_patched() as session:
        events = (await session.execute(select(BotEvent).where(BotEvent.event_type == BotEventType.TRADE_CLOSED))).scalars().all()
    assert any("[Manual]" in e.message for e in events)
    # fakeredis 的 publish 无法直接读回，这里只验证 connector 被正确调用
    connector.close_position.assert_awaited_once_with(111)


@pytest.mark.asyncio
async def test_close_success_with_engine_skips_accounting(
    connector, redis_client, db_session_patched, monkeypatch, live_mode
):
    """引擎在管品种：引擎 sync 会记账，gate 重复记录 = 日亏双计。"""
    from app.bot.engine import BotState

    engine = MagicMock()
    engine.state = BotState.RUNNING  # 引擎活跃，sync 会记账
    engine._log_event = AsyncMock()
    fake_mgr = MagicMock()
    fake_mgr.resolve_symbol.return_value = "GOLD"
    fake_mgr.engines = {"GOLD": engine}
    monkeypatch.setattr(position_close_module, "_lookup_engine", lambda symbol: engine)

    result = await close_position_gated(connector, redis_client, 111)
    assert result["closed"] is True
    assert await redis_client.get("circuit:daily_pnl:GOLD") is None
    from mcp_server.guardrails import _daily_key

    assert await redis_client.llen(_daily_key("trade_results")) == 0


@pytest.mark.asyncio
async def test_close_with_paused_engine_records_accounting(
    connector, redis_client, db_session_patched, monkeypatch, live_mode
):
    """I1 回归：引擎存在但 PAUSED（日亏熔断/风险事件暂停）时不跑 sync，
    手动平仓必须自行记账 —— 否则日亏/连亏计数在引擎暂停期间失效。"""
    from app.bot.engine import BotState

    engine = MagicMock()
    engine.state = BotState.PAUSED  # 引擎暂停，sync 不跑
    engine.account_login = None  # 无账号维度，落到无前缀 key
    fake_mgr = MagicMock()
    fake_mgr.resolve_symbol.return_value = "GOLD"
    fake_mgr.engines = {"GOLD": engine}
    monkeypatch.setattr(position_close_module, "_lookup_engine", lambda symbol: engine)

    result = await close_position_gated(connector, redis_client, 111)
    assert result["closed"] is True
    # 应记账(而非跳过)—— circuit breaker 记录了盈亏
    from app.config import get_canonical_symbol

    pnl = float(await redis_client.get(f"circuit:daily_pnl:{get_canonical_symbol('GOLD_')}"))
    assert pnl == -12.5
    from mcp_server.guardrails import _daily_key

    assert await redis_client.llen(_daily_key("trade_results")) == 1


@pytest.mark.asyncio
async def test_close_bridge_failure_returns_error(connector, redis_client, no_engine, db_session_patched, live_mode):
    connector.close_position.return_value = {"success": False, "error": "invalid request"}
    result = await close_position_gated(connector, redis_client, 111)
    assert result["closed"] is False
    assert result["error"] == "invalid request"
    assert await redis_client.get("circuit:daily_pnl:GOLD") is None


@pytest.mark.asyncio
async def test_ws_push_payload_shape(connector, redis_client, no_engine, db_session_patched, monkeypatch, live_mode):
    published = []

    async def fake_publish(channel, payload):
        published.append((channel, json.loads(payload)))

    monkeypatch.setattr(redis_client, "publish", fake_publish)
    await close_position_gated(connector, redis_client, 111)
    channel, payload = published[0]
    assert channel == "bot_event"
    assert payload == {"type": "trade_closed", "ticket": 111, "profit": -12.5, "manual": True}
