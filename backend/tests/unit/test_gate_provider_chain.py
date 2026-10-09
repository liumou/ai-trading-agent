"""ManualOrderGate provider 链测试（local_jev → LLM 兜底）。

锁定行为：
- local_jev 正常 → 绝不调 LLM（token/延迟双省）
- 规则 REJECTED：kind=systemone_rejected + TRADE_BLOCKED，绝不发 AI_AGENT_ERROR
- 引擎故障 → 降级 LLM 兜底（review 无 systemone 块）+ 降级计数告警
- 全链失败 → fail-closed REJECTED + AI_AGENT_ERROR
- provider=llm 回滚纯度：行为与旧路径一致，不写 review.systemone
- CAUTION 确认链路与 SystemOne 判决的连续性
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import numpy as np
import pandas as pd
import pytest
import pytest_asyncio

import app.bot.manager as manager_mod
import app.db.session as db_session_module
from app.config import SYMBOL_PROFILES, settings
from app.services.manual_order_gate import ManualOrderGate


@pytest.fixture(autouse=True)
def _profiles():
    snapshot = {k: dict(v) for k, v in SYMBOL_PROFILES.items()}
    SYMBOL_PROFILES.clear()
    SYMBOL_PROFILES["GOLD"] = {"pip_value": 1.0, "volume_min": 0.01, "volume_step": 0.01, "max_lot": 1.0}
    yield
    SYMBOL_PROFILES.clear()
    SYMBOL_PROFILES.update(snapshot)


@pytest.fixture(autouse=True)
def _manager(monkeypatch):
    mgr = MagicMock()
    mgr.resolve_symbol.return_value = "GOLD"
    mgr.engines = {"GOLD": MagicMock()}
    monkeypatch.setattr(manager_mod, "get_global_manager", lambda: mgr)
    return mgr


@pytest.fixture(autouse=True)
def _allow_live(monkeypatch):
    monkeypatch.setattr(settings, "llm_allow_live", True)


@pytest.fixture(autouse=True)
def _provider_local(monkeypatch):
    """与旧回归文件（provider=llm）互补：本文件锁定 SystemOne 链为默认路径。
    同时清空 JEV key —— .env 里有真实 key，local 失败降级路径会真打外部 API
    （慢/耗配额/测试不稳定）；需要 JEV 的用例各自显式配置。"""
    monkeypatch.setattr(settings, "manual_review_provider", "local_jev")
    monkeypatch.setattr(settings, "manual_review_typesafe_api_key", "")


@pytest_asyncio.fixture
async def session_patched(db_engine, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_session_module, "async_session", maker)
    return maker


def _ohlcv_rows(per_bar=0.0, vol=1.0, seed=7, rows=120, base=2000.0):
    """通过 MarketDataService 真实路径喂 OHLCV（rows → bridge 响应形状）。
    默认平盘：参考价 2000 与数据一致（趋势数据会让 entry 偏离 EMA20 触发
    spike_chase —— 那是规则正确工作，不该在健康单用例里发生）。"""
    rng = np.random.default_rng(seed)
    close = base + np.arange(rows) * per_bar + rng.normal(0, vol, rows)
    high = close + np.abs(rng.normal(0, vol * 0.5, rows)) + vol * 0.5
    low = close - np.abs(rng.normal(0, vol * 0.5, rows)) - vol * 0.5
    open_ = close + rng.normal(0, vol * 0.3, rows)
    idx = pd.date_range("2026-01-05 09:00", periods=rows, freq="15min")
    return [{"time": str(t), "open": o, "high": h, "low": l, "close": c}
            for t, o, h, l, c in zip(idx, open_, high, low, close)]


SPEC = {"trade_tick_value": 1.0, "trade_tick_size": 0.01, "trade_contract_size": 100}


@pytest.fixture
def connector():
    c = AsyncMock()
    c.get_account.return_value = {"success": True, "data": {"balance": 10000.0, "equity": 10000.0, "profit": 0.0}}
    c.get_positions.return_value = {"success": True, "data": []}
    c.get_tick.return_value = {"success": True, "data": {"bid": 2000.0, "ask": 2000.5}}
    # 全量历史（unfamiliar/中位手数）：空 = 跳过不打标
    c.get_history.return_value = {"success": True, "data": []}
    c.get_orders.return_value = {"success": True, "data": [{"ticket": 555, "symbol": "GOLD_"}]}
    c.place_order.return_value = {"success": True, "data": {"ticket": 777, "price": 2000.5}}
    c.place_pending_order.return_value = {"success": True, "data": {"ticket": 888, "price": 1980.0}}
    c.modify_order.return_value = {"success": True, "data": {"ticket": 555, "price": 1999.0}}
    c.cancel_order.return_value = {"success": True, "data": {"cancelled": True}}
    c.modify_position.return_value = {"success": True, "data": {}}
    # SystemOne 数据面：M15/H1 + 品种规格
    c.get_ohlcv.return_value = {"success": True, "data": _ohlcv_rows()}
    c.get_symbol_spec.return_value = {"success": True, "data": SPEC}
    return c


@pytest.fixture
def ai_client():
    c = AsyncMock()
    c.complete_json_async.return_value = {
        "verdict": "APPROVED", "confidence": 0.9,
        "risk_flags": [], "emotional_indicators": [], "reasoning": "sane order",
    }
    return c


class _BrokenEngine:
    """模拟引擎基础设施故障（evaluate 抛异常 → 网关降级）。"""

    provider = "local_jev"

    async def evaluate(self, snapshot, ctx):
        raise ProviderUnavailableSim("ohlcv fetch failed: bridge down")


class ProviderUnavailableSim(Exception):
    pass


@pytest_asyncio.fixture
async def gate(connector, redis_client, ai_client):
    # 不注入 market_data —— 刻意走生产 main.py 的 3 参构造 + 惰性构建路径
    g = ManualOrderGate(connector, redis_client, ai_client)
    await redis_client.set("guardrails:rollout_mode", "live")
    return g


async def _drain_tasks(g):
    if g._tasks:
        await asyncio.gather(*list(g._tasks))


async def _ai_error_events():
    from sqlalchemy import select

    from app.db.models import BotEvent, BotEventType
    from app.db.session import async_session

    async with async_session() as s:
        return (await s.execute(
            select(BotEvent).where(BotEvent.event_type == BotEventType.AI_AGENT_ERROR)
        )).scalars().all()


async def _circuit_events():
    from sqlalchemy import select

    from app.db.models import BotEvent, BotEventType
    from app.db.session import async_session

    async with async_session() as s:
        return (await s.execute(
            select(BotEvent).where(BotEvent.event_type == BotEventType.CIRCUIT_BREAKER)
        )).scalars().all()


# ─── 默认 provider ───────────────────────────────────────────────────────────


def test_default_provider_is_local_jev():
    """配置默认值 = local_jev（本迁移的验收锚点，env 可覆盖）。"""
    from app.config import Settings

    assert Settings.model_fields["manual_review_provider"].default == "local_jev"


# ─── local_jev 主路径 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_systemone_approves_without_llm_call(gate, session_patched):
    """健康单 → SystemOne APPROVED → 执行；LLM 零调用（延迟与 token 双省）。"""
    result = await gate.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                                     lot=0.1, sl=1985.0, tp=2050.0)
    assert result["status"] == "PENDING_REVIEW"
    await _drain_tasks(gate)
    review = await gate.get_review(result["review_id"])
    assert review["status"] == "EXECUTED"
    assert review["ticket"] == 777
    gate.ai_client.complete_json_async.assert_not_called()
    # 审计：systemone 块 + 兼容 llm 形状同存
    assert review["review"]["systemone"]["provider"] == "local_jev"
    assert review["review"]["llm"]["verdict"] == "APPROVED"
    assert len(review["review"]["systemone"]["checks"]) == 5
    assert (await _ai_error_events()) == []


@pytest.mark.asyncio
async def test_rule_reject_uses_systemone_kind_and_no_ai_error(gate, session_patched):
    """规则 block（risk 10% > 5%）→ REJECTED kind=systemone_rejected，
    retryable=False，绝不发 AI_AGENT_ERROR（正常拒绝 ≠ 基础设施故障）。"""
    result = await gate.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                                     lot=0.1, sl=1900.0, tp=2050.0)  # loss $1000 = 10%
    assert result["status"] == "PENDING_REVIEW"
    await _drain_tasks(gate)
    review = await gate.get_review(result["review_id"])
    assert review["status"] == "REJECTED"
    assert review["review"]["reject_kind"] == "systemone_rejected"
    assert review["review"]["retryable"] is False
    assert review["review"]["systemone"]["converge"]["verdict"] == "REJECTED"
    gate.connector.place_order.assert_not_awaited()
    gate.ai_client.complete_json_async.assert_not_called()
    assert (await _ai_error_events()) == []


@pytest.mark.asyncio
async def test_caution_confirm_continuity_with_systemone(gate, session_patched):
    """SystemOne CAUTION（手数近上限）→ PENDING_CONFIRM → 确认后执行。
    锁定 confirm 链路与 provider 判决的连续性（confirm_expires_at 完整 120s）。"""
    result = await gate.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                                     lot=0.9, sl=1995.0, tp=2050.0)  # 0.9≥0.8×cap warn
    await _drain_tasks(gate)
    review = await gate.get_review(result["review_id"])
    assert review["status"] == "PENDING_CONFIRM"
    assert review["review"]["confirm_expires_at"]
    assert review["review"]["llm"]["verdict"] == "CAUTION"
    assert "手数接近上限" in review["review"]["llm"]["reasoning"]
    gate.connector.place_order.assert_not_called()

    confirm = await gate.confirm_and_execute(result["review_id"])
    assert confirm["status"] == "EXECUTED"
    gate.connector.place_order.assert_awaited_once()


# ─── 降级链 ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_engine_crash_degrades_to_llm(connector, redis_client, ai_client, session_patched):
    """引擎故障 → 静默降级 LLM 兜底：订单照常执行，review 无 systemone 块，
    计数 < 阈值不发 CIRCUIT_BREAKER。"""
    from app.mt5.market_data import MarketDataService

    g = ManualOrderGate(connector, redis_client, ai_client,
                        systemone=_BrokenEngine(), market_data=MarketDataService(connector))
    await redis_client.set("guardrails:rollout_mode", "live")
    result = await g.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                                  lot=0.1, sl=1985.0, tp=2050.0)
    await _drain_tasks(g)
    review = await g.get_review(result["review_id"])
    assert review["status"] == "EXECUTED"
    assert "systemone" not in review["review"]
    assert review["review"]["llm"]["verdict"] == "APPROVED"
    assert g._degraded_streak == 1
    assert (await _circuit_events()) == []


@pytest.mark.asyncio
async def test_degraded_alert_after_threshold(connector, redis_client, ai_client,
                                              session_patched, monkeypatch):
    """连续失败达阈值 → CIRCUIT_BREAKER 事件（聚合告警，只发一次）。"""
    monkeypatch.setattr(settings, "manual_review_degraded_alert_threshold", 2)
    from app.mt5.market_data import MarketDataService

    g = ManualOrderGate(connector, redis_client, ai_client,
                        systemone=_BrokenEngine(), market_data=MarketDataService(connector))
    await redis_client.set("guardrails:rollout_mode", "live")
    for _ in range(2):
        r = await g.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                                 lot=0.1, sl=1985.0, tp=2050.0)
        await _drain_tasks(g)
        assert r["status"] == "PENDING_REVIEW", r
        # 清频率间隔键（否则第二单被 guardrails 120s 最小间隔硬拒）
        await redis_client.delete("guardrails:last_trade_time")
    events = await _circuit_events()
    assert len(events) == 1
    assert "systemone" in events[0].message
    # 第 3 单不再重复发（一条 streak 一次聚合告警）
    r = await g.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                             lot=0.1, sl=1985.0, tp=2050.0)
    await _drain_tasks(g)
    assert len(await _circuit_events()) == 1


@pytest.mark.asyncio
async def test_all_data_missing_degrades_to_llm(gate, connector, redis_client, ai_client, session_patched):
    """OHLCV 全空（bridge 响应 success 但无数据）→ ProviderUnavailable → LLM 兜底。"""
    connector.get_ohlcv.return_value = {"success": True, "data": []}
    result = await gate.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                                     lot=0.1, sl=1985.0, tp=2050.0)
    await _drain_tasks(gate)
    review = await gate.get_review(result["review_id"])
    assert review["status"] == "EXECUTED"
    assert "systemone" not in review["review"]


@pytest.mark.asyncio
async def test_total_failure_fails_closed(connector, redis_client, ai_client, session_patched):
    """引擎故障 + LLM 畸形 verdict → 全链失败 → REJECTED + AI_AGENT_ERROR。"""
    ai_client.complete_json_async.return_value = {"verdict": "GARBAGE"}
    from app.mt5.market_data import MarketDataService

    g = ManualOrderGate(connector, redis_client, ai_client,
                        systemone=_BrokenEngine(), market_data=MarketDataService(connector))
    await redis_client.set("guardrails:rollout_mode", "live")
    result = await g.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                                  lot=0.1, sl=1985.0, tp=2050.0)
    await _drain_tasks(g)
    review = await g.get_review(result["review_id"])
    assert review["status"] == "REJECTED"
    assert review["review"]["reject_kind"] == "llm_unavailable"
    assert review["review"]["retryable"] is True
    assert any("review" in e.message for e in await _ai_error_events())
    connector.place_order.assert_not_awaited()


# ─── 回滚纯度 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_provider_llm_rollback_purity(gate, session_patched, monkeypatch):
    """env 切 provider=llm：行为与旧路径逐字段一致 —— 不写 systemone 块，
    LLM APPROVED 照常执行（回滚 = 改 env + 重启）。"""
    monkeypatch.setattr(settings, "manual_review_provider", "llm")
    result = await gate.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                                     lot=0.1, sl=1985.0, tp=2050.0)
    await _drain_tasks(gate)
    review = await gate.get_review(result["review_id"])
    assert review["status"] == "EXECUTED"
    assert "systemone" not in review["review"]
    assert review["review"]["llm"]["verdict"] == "APPROVED"
    gate.ai_client.complete_json_async.assert_awaited_once()


# ─── TypeSafe JEV 链集成（Phase 3）───────────────────────────────────────────


def _mock_jev_approve(monkeypatch, captured: list | None = None):
    """配置 JEV 凭据 + MockTransport 返回 APPROVED 应答。"""
    from app.services.typesafe_jev import JEV_CHECK_OPTIONS

    def _q(question: str, choice: str, confidence: float = 0.8) -> dict:
        options = sorted(JEV_CHECK_OPTIONS[question])
        probs = {o: 0.01 for o in options}
        probs[choice] = 1.0 - 0.01 * (len(options) - 1)
        return {"choice": choice, "probabilities": probs, "confidence": confidence}

    answers = {
        "data_quality": _q("data_quality", "sufficient"),
        "signal_alignment": _q("signal_alignment", "aligned"),
        "market_regime": _q("market_regime", "neutral"),
        "risk_check": _q("risk_check", "clear", 0.8),
        "execution_quality": _q("execution_quality", "clear", 0.8),
    }
    monkeypatch.setattr(settings, "manual_review_typesafe_api_key", "sk-test-jev")
    monkeypatch.setattr(settings, "manual_review_typesafe_base_url", "https://api.test/v1/systemone")

    async def handler(request):
        if captured is not None:
            captured.append(json.loads(request.content or b"{}"))
        return httpx.Response(200, json={"answers": answers})

    def _make(proxy, timeout_s):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout_s)

    monkeypatch.setattr("app.services.typesafe_jev.make_client", _make)
    return captured


@pytest.mark.asyncio
async def test_typesafe_second_link_when_local_fails(gate, connector, session_patched, monkeypatch):
    """provider=local_jev：local 数据盲（OHLCV 全空）→ JEV AI 兜底判 APPROVED
    → 执行；audit.provider=typesafe_jev，LLM 零调用。"""
    _mock_jev_approve(monkeypatch)
    connector.get_ohlcv.return_value = {"success": True, "data": []}  # local → ProviderUnavailable
    result = await gate.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                                     lot=0.1, sl=1985.0, tp=2050.0)
    await _drain_tasks(gate)
    review = await gate.get_review(result["review_id"])
    assert review["status"] == "EXECUTED"
    assert review["review"]["systemone"]["provider"] == "typesafe_jev"
    assert review["review"]["systemone"]["converge"]["verdict"] == "APPROVED"
    assert len(review["review"]["systemone"]["checks"]) == 5
    gate.ai_client.complete_json_async.assert_not_called()


@pytest.mark.asyncio
async def test_typesafe_fails_then_llm(gate, connector, session_patched, monkeypatch):
    """JEV 网络故障 + local 数据盲 → LLM 兜底执行；无 systemone 块。"""
    monkeypatch.setattr(settings, "manual_review_typesafe_api_key", "sk-test-jev")
    connector.get_ohlcv.return_value = {"success": True, "data": []}

    async def boom(request):
        raise httpx.ConnectError("connection refused")

    def _make(proxy, timeout_s):
        return httpx.AsyncClient(transport=httpx.MockTransport(boom), timeout=timeout_s)

    monkeypatch.setattr("app.services.typesafe_jev.make_client", _make)
    result = await gate.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                                     lot=0.1, sl=1985.0, tp=2050.0)
    await _drain_tasks(gate)
    review = await gate.get_review(result["review_id"])
    assert review["status"] == "EXECUTED"
    assert "systemone" not in review["review"]
    assert review["review"]["llm"]["verdict"] == "APPROVED"
    gate.ai_client.complete_json_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_provider_typesafe_primary(gate, connector, session_patched, monkeypatch):
    """provider=typesafe_jev：JEV 主审成功 → local 引擎不参与。
    （JEV 自己会拉行情证据 → get_ohlcv 有调用；local 引擎特有的
    get_symbol_spec 不被调用才是「local 未运行」的指纹。）"""
    _mock_jev_approve(monkeypatch)
    monkeypatch.setattr(settings, "manual_review_provider", "typesafe_jev")
    result = await gate.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                                     lot=0.1, sl=1985.0, tp=2050.0)
    await _drain_tasks(gate)
    review = await gate.get_review(result["review_id"])
    assert review["status"] == "EXECUTED"
    assert review["review"]["systemone"]["provider"] == "typesafe_jev"
    connector.get_symbol_spec.assert_not_awaited()
    gate.ai_client.complete_json_async.assert_not_called()
