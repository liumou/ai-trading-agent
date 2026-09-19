"""ManualOrderGate 风控防火墙测试矩阵(Phase 3)。

锁定防火墙不变量:
- 硬闸门拒 → REJECTED(不烧 LLM token)
- switching 门禁手动通道 fail-closed
- 马丁/复仇 block 级规则 → 直接拒
- LLM APPROVED → 执行(MANUAL magic、comment 清洗、record_order_opened)
- CAUTION → PENDING_CONFIRM;confirm 绑定 review_id 参数,重跑硬闸门,TTL 过期 → EXPIRED
- LLM 失败/畸形 verdict/超时 → fail-closed REJECTED + AI_AGENT_ERROR 事件
- 执行前重验硬状态,状态漂移 → REJECTED
- 撤单仅 ticket 归属 + switching;改 SL/TP 以 entry 为锚点的漂移预算
"""

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

import app.bot.manager as manager_mod
import app.db.session as db_session_module
from app.config import SYMBOL_PROFILES, settings
from app.services.manual_order_gate import CONFIRM_TTL_S, LLM_REVIEW_TIMEOUT_S, ManualOrderGate


@pytest.fixture(autouse=True)
def _profiles():
    snapshot = {k: dict(v) for k, v in SYMBOL_PROFILES.items()}
    SYMBOL_PROFILES.clear()
    SYMBOL_PROFILES["GOLD"] = {"pip_value": 1.0, "volume_min": 0.01, "volume_step": 0.01}
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


@pytest_asyncio.fixture
async def session_patched(db_engine, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_session_module, "async_session", maker)
    return maker


@pytest.fixture
def connector():
    c = AsyncMock()
    c.get_account.return_value = {"success": True, "data": {"balance": 10000.0, "equity": 10000.0, "profit": 0.0}}
    c.get_positions.return_value = {"success": True, "data": []}
    c.get_tick.return_value = {"success": True, "data": {"bid": 2000.0, "ask": 2000.5}}
    c.get_history.return_value = {"success": True, "data": []}
    c.get_orders.return_value = {"success": True, "data": [{"ticket": 555, "symbol": "GOLD_"}]}
    c.place_order.return_value = {"success": True, "data": {"ticket": 777, "price": 2000.5}}
    # AsyncMock 未配置的子属性 await 后仍返回 AsyncMock(其 .get 返回 coroutine),
    # 必须显式配置每个会被调用的方法。
    c.place_pending_order.return_value = {"success": True, "data": {"ticket": 888, "price": 1980.0}}
    c.modify_order.return_value = {"success": True, "data": {"ticket": 555, "price": 1999.0}}
    c.cancel_order.return_value = {"success": True, "data": {"cancelled": True}}
    c.modify_position.return_value = {"success": True, "data": {}}
    return c


@pytest.fixture
def ai_client():
    c = AsyncMock()
    c.complete_json_async.return_value = {
        "verdict": "APPROVED", "confidence": 0.9,
        "risk_flags": [], "emotional_indicators": [], "reasoning": "sane order",
    }
    return c


@pytest_asyncio.fixture
async def gate(connector, redis_client, ai_client):
    return ManualOrderGate(connector, redis_client, ai_client)


async def _drain_tasks(gate):
    if gate._tasks:
        await asyncio.gather(*list(gate._tasks))


def _loss_deal(minutes_ago=5, lot=0.1, symbol="GOLD", profit=-50.0):
    t = (datetime.utcnow() - timedelta(minutes=minutes_ago)).isoformat()
    return {"ticket": 1, "symbol": symbol, "type": "SELL", "lot": lot,
            "price": 2000.0, "profit": profit, "time": t}


# ─── 硬闸门 / switching ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hard_gate_rejection_inline(gate, session_patched):
    """guardrails 拒 → REJECTED,不烧 LLM token(审查任务不创建)。"""
    gate.connector.get_tick.return_value = {"success": False, "error": "no tick"}
    result = await gate.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                                     lot=0.1, sl=1900.0, tp=2100.0)
    assert result["status"] == "REJECTED" and result["kind"] == "data_fetch"
    gate.ai_client.complete_json_async.assert_not_called()
    review = await gate.get_review(result["review_id"])
    assert review["status"] == "REJECTED"


@pytest.mark.asyncio
async def test_switching_fail_closed(gate, redis_client, session_patched):
    await redis_client.set("switching:in_progress", "1")
    result = await gate.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                                     lot=0.1, sl=1900.0, tp=2100.0)
    assert result["status"] == "REJECTED" and result["kind"] == "switching"
    gate.connector.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_switching_redis_error_fails_closed(connector, ai_client, session_patched):
    broken = AsyncMock()
    broken.get.side_effect = RuntimeError("redis down")
    gate = ManualOrderGate(connector, broken, ai_client)
    result = await gate.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                                     lot=0.1, sl=1900.0, tp=2100.0)
    assert result["status"] == "REJECTED"
    connector.place_order.assert_not_called()


# ─── 情绪化规则 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_martingale_block_rule_rejects_without_llm(gate, session_patched):
    """亏损后 5 分钟内手数翻倍 → block 级规则直接拒,不走 LLM。"""
    gate.connector.get_history.return_value = {"success": True, "data": [_loss_deal(minutes_ago=5, lot=0.1)]}
    result = await gate.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                                     lot=0.25, sl=1900.0, tp=2100.0)
    assert result["status"] == "REJECTED" and result["kind"] == "emotion"
    gate.ai_client.complete_json_async.assert_not_called()


@pytest.mark.asyncio
async def test_revenge_window_is_warn_not_block(gate, session_patched):
    """亏损后快速再入场但手数未放大 → warn(交给 LLM),不硬拒。"""
    gate.connector.get_history.return_value = {"success": True, "data": [_loss_deal(minutes_ago=5, lot=0.5)]}
    result = await gate.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                                     lot=0.1, sl=1900.0, tp=2100.0)
    assert result["status"] == "PENDING_REVIEW"
    flags = {f["flag"] for f in result["rule_flags"]}
    assert "revenge_trade_window" in flags
    await _drain_tasks(gate)


# ─── APPROVED 执行路径 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approved_executes_with_manual_magic(gate, session_patched):
    result = await gate.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                                     lot=0.1, sl=1900.0, tp=2100.0)
    assert result["status"] == "PENDING_REVIEW"
    await _drain_tasks(gate)

    review = await gate.get_review(result["review_id"])
    assert review["status"] == "EXECUTED"
    assert review["ticket"] == 777
    kwargs = gate.connector.place_order.call_args.kwargs
    assert kwargs["magic"] == 234100  # MANUAL_MAGIC_NUMBER
    assert kwargs["comment"].startswith("M")  # [Manual] 前缀清洗
    gate.connector.get_account.assert_awaited()  # record_order_opened 前置的执行已发生
    # 频率计数被更新（否则手动通道对频率限制免疫）
    assert int(await gate.redis.get(
        f"guardrails:trades:{datetime.utcnow().date().isoformat()}T{datetime.utcnow().hour:02d}") or 0) == 1


@pytest.mark.asyncio
async def test_pending_order_executes_via_place_pending(gate, session_patched):
    result = await gate.submit_order(symbol="GOLD", order_kind="pending", order_type="BUY_LIMIT",
                                     lot=0.1, sl=1970.0, tp=2020.0, price=1980.0)
    await _drain_tasks(gate)
    review = await gate.get_review(result["review_id"])
    assert review["status"] == "EXECUTED"
    assert gate.connector.place_pending_order.await_count == 1
    kwargs = gate.connector.place_pending_order.await_args.kwargs
    assert kwargs["price"] == 1980.0
    assert kwargs["order_type"] == "BUY_LIMIT"


# ─── CAUTION / confirm ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_caution_requires_confirm(gate, session_patched):
    """LLM 判 CAUTION → PENDING_CONFIRM;确认后绑定原参数执行。
    (零 SL 会被 guardrails 硬拒——本就不允许无止损单,no_stop_loss 只作
    防御性 flag。)"""
    gate.ai_client.complete_json_async.return_value = {
        "verdict": "CAUTION", "confidence": 0.6, "risk_flags": ["near_frequency_limit"],
        "emotional_indicators": [], "reasoning": "size near limit",
    }
    result = await gate.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                                     lot=0.1, sl=1900.0, tp=2100.0)
    await _drain_tasks(gate)
    review = await gate.get_review(result["review_id"])
    assert review["status"] == "PENDING_CONFIRM"
    assert review["review"]["confirm_expires_at"]
    gate.connector.place_order.assert_not_called()

    confirm = await gate.confirm_and_execute(result["review_id"])
    assert confirm["status"] == "EXECUTED"
    gate.connector.place_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_expired(gate, session_patched, monkeypatch):
    gate.ai_client.complete_json_async.return_value = {
        "verdict": "CAUTION", "confidence": 0.6, "risk_flags": [],
        "emotional_indicators": [], "reasoning": "",
    }
    result = await gate.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                                     lot=0.1, sl=1900.0, tp=2100.0)
    await _drain_tasks(gate)
    # 把 TTL 拨回过去
    from app.db.session import async_session as _mk
    from sqlalchemy import select as _sel
    from app.db.models import OrderAudit as OA
    async with _mk() as s:
        row = (await s.execute(_sel(OA).where(OA.id == result["review_id"]))).scalar_one()
        row.review = {**row.review, "confirm_expires_at": (datetime.utcnow() - timedelta(seconds=CONFIRM_TTL_S * 2)).isoformat()}
        await s.commit()

    confirm = await gate.confirm_and_execute(result["review_id"])
    assert confirm["status"] == "EXPIRED"
    gate.connector.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_confirm_wrong_status_rejected(gate, session_patched):
    result = await gate.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                                     lot=0.1, sl=1900.0, tp=2100.0)
    await _drain_tasks(gate)  # APPROVED → EXECUTED
    confirm = await gate.confirm_and_execute(result["review_id"])
    assert confirm["status"] == "EXECUTED"  # 非 PENDING_CONFIRM 直接返回当前态
    gate.connector.place_order.assert_awaited_once()


# ─── LLM fail-closed ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_malformed_verdict_blocks(gate, redis_client, session_patched):
    gate.ai_client.complete_json_async.return_value = {"verdict": "MAYBE", "reasoning": "hm"}
    result = await gate.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                                     lot=0.1, sl=1900.0, tp=2100.0)
    await _drain_tasks(gate)
    review = await gate.get_review(result["review_id"])
    assert review["status"] == "REJECTED"
    assert review["review"]["reject_kind"] == "llm_unavailable"
    assert review["review"]["retryable"] is True
    # 基础设施故障 → AI_AGENT_ERROR 事件（不伪装成分析结论）
    from sqlalchemy import select as _sel
    from app.db.models import BotEvent as BE, BotEventType
    from app.db.session import async_session as _mk
    async with _mk() as s:
        evts = (await s.execute(_sel(BE).where(BE.event_type == BotEventType.AI_AGENT_ERROR))).scalars().all()
    assert any("review" in e.message for e in evts)


@pytest.mark.asyncio
async def test_llm_timeout_blocks(gate, session_patched, monkeypatch):
    monkeypatch.setattr("app.services.manual_order_gate.LLM_REVIEW_TIMEOUT_S", 0.05)

    async def slow(*a, **k):
        await asyncio.sleep(5)

    gate.ai_client.complete_json_async.side_effect = slow
    result = await gate.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                                     lot=0.1, sl=1900.0, tp=2100.0)
    await asyncio.wait_for(_drain_tasks(gate), timeout=5)
    review = await gate.get_review(result["review_id"])
    assert review["status"] == "REJECTED"


@pytest.mark.asyncio
async def test_state_drift_between_review_and_execute_blocks(gate, session_patched):
    """审查通过后、执行前状态漂移(如行情不可得)→ REJECTED,不下单。"""
    result = await gate.submit_order(symbol="GOLD", order_kind="market", order_type="BUY",
                                     lot=0.1, sl=1900.0, tp=2100.0)
    # 审查任务还没跑(测试里我们手动控制):先让行情挂掉
    gate.connector.get_account.return_value = {"success": False, "error": "bridge down"}
    await _drain_tasks(gate)
    review = await gate.get_review(result["review_id"])
    assert review["status"] == "REJECTED"
    gate.connector.place_order.assert_not_called()


# ─── 撤单 / 改挂单 / 改 SL/TP ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_ownership_checked(gate, session_patched):
    ok = await gate.cancel_pending_order(555)
    assert ok["cancelled"] is True
    missing = await gate.cancel_pending_order(999)
    assert missing["cancelled"] is False
    gate.connector.cancel_order.assert_awaited_once_with(555)


@pytest.mark.asyncio
async def test_modify_pending_full_pipeline(gate, session_patched):
    """改挂单 = 全流水线重审(C-2:改价逼近市价 = 事实市价单,必须重审)。"""
    result = await gate.submit_order(symbol="GOLD", order_kind="pending", order_type="BUY_LIMIT",
                                     lot=0.1, sl=1970.0, tp=2020.0, price=1999.0,
                                     modify_ticket=555)
    assert result["status"] == "PENDING_REVIEW"
    await _drain_tasks(gate)
    review = await gate.get_review(result["review_id"])
    assert review["status"] == "EXECUTED"
    gate.connector.modify_order.assert_awaited_once_with(555, price=1999.0, sl=1970.0, tp=2020.0)


@pytest.mark.asyncio
async def test_modify_pending_requires_ownership(gate, session_patched):
    result = await gate.submit_order(symbol="GOLD", order_kind="pending", order_type="BUY_LIMIT",
                                     lot=0.1, sl=1970.0, tp=2020.0, price=1999.0,
                                     modify_ticket=888)
    assert result["status"] == "REJECTED"
    gate.ai_client.complete_json_async.assert_not_called()


@pytest.mark.asyncio
async def test_sltp_set_first_stop_allowed_and_anchored(gate, redis_client, session_patched):
    """SL=0 仓位:设任何合法 SL 放行(风险收紧),距离记为漂移锚点。"""
    gate.connector.get_positions.return_value = {
        "success": True,
        "data": [{"ticket": 42, "symbol": "GOLD_", "type": "BUY", "volume": 0.1,
                  "open_price": 2000.0, "sl": 0, "tp": 0, "profit": 0}],
    }
    res = await gate.modify_position_sltp(42, sl=1980.0, tp=None)
    assert res["modified"] is True
    anchor = float(await redis_client.get("manual:sl_anchor:42"))
    assert anchor == pytest.approx(20.0)  # |2000-1980|


@pytest.mark.asyncio
async def test_sltp_widen_beyond_anchor_rejected(gate, redis_client, session_patched):
    """评审 C-4:拉宽不得超过锚点距离 ×5(固定基数,杜绝几何漂移)。"""
    gate.connector.get_positions.return_value = {
        "success": True,
        "data": [{"ticket": 42, "symbol": "GOLD_", "type": "BUY", "volume": 0.1,
                  "open_price": 2000.0, "sl": 1980.0, "tp": 0, "profit": 0}],
    }
    await redis_client.set("manual:sl_anchor:42", "20.0")
    res = await gate.modify_position_sltp(42, sl=1890.0, tp=None)  # dist 110 > 20×5
    assert res["rejected"] is True
    gate.connector.modify_position.assert_not_called()


@pytest.mark.asyncio
async def test_sltp_tighten_always_allowed(gate, redis_client, session_patched):
    gate.connector.get_positions.return_value = {
        "success": True,
        "data": [{"ticket": 42, "symbol": "GOLD_", "type": "BUY", "volume": 0.1,
                  "open_price": 2000.0, "sl": 1980.0, "tp": 2100.0, "profit": 0}],
    }
    await redis_client.set("manual:sl_anchor:42", "20.0")
    gate.connector.modify_position.return_value = {"success": True, "data": {}}
    res = await gate.modify_position_sltp(42, sl=1995.0, tp=None)  # 收紧
    assert res["modified"] is True
    assert res["sl"] == 1995.0


@pytest.mark.asyncio
async def test_sltp_direction_validated(gate, session_patched):
    gate.connector.get_positions.return_value = {
        "success": True,
        "data": [{"ticket": 42, "symbol": "GOLD_", "type": "BUY", "volume": 0.1,
                  "open_price": 2000.0, "sl": 0, "tp": 0, "profit": 0}],
    }
    res = await gate.modify_position_sltp(42, sl=2010.0, tp=None)  # BUY SL ≥ entry
    assert res["modified"] is False
    assert "below entry" in res["error"]
