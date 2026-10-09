"""ManualOrderGate.list_reviews 历史审查查询测试（过滤/分页/统计/verdict 推导）。

覆盖:
- days 时间窗口截断（created_at ≥ now-days）
- status / symbol SQL 过滤 + account_login 隔离
- verdict 内存推导与过滤（llm.verdict → systemone.converge → status 兜底映射）
- offset/limit 分页 + total/stats 基于过滤后未分页集合
- 便捷字段（verdict/provider/confidence/rule_flags）向后兼容
"""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

import app.db.session as db_session_module
from app.db.models import OrderAudit
from app.services.manual_order_gate import ManualOrderGate


def _row(**kw) -> dict:
    base = {
        "symbol": "GOLD", "order_type": "BUY", "requested_lot": 0.1,
        "requested_sl": 1900.0, "requested_tp": 2100.0, "expected_price": 2000.0,
        "status": "EXECUTED", "signal_source": "manual", "source": "manual",
        "account_login": "10086", "order_kind": "market", "review": None,
    }
    base.update(kw)
    return base


@pytest_asyncio.fixture
async def patched_session(db_engine, monkeypatch):
    """把 app.db.session.async_session 换成内存 SQLite（与 gate 测试同款）。"""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_session_module, "async_session", maker)
    return maker


async def _seed(maker, rows: list[dict]):
    async with maker() as s:
        for r in rows:
            s.add(OrderAudit(**r))
        await s.commit()


def _gate():
    # list_reviews 不触碰 connector/redis/ai_client，仅用模块级 helper 与 session。
    return ManualOrderGate(MagicMock(), MagicMock(), MagicMock())


# ─── 基本载荷 / verdict 推导 / 便捷字段 ──────────────────────────────────


@pytest.mark.asyncio
async def test_payload_verdict_derivation_and_convenience_fields(patched_session):
    await _seed(patched_session, [
        _row(symbol="GOLD", order_type="BUY", status="EXECUTED", review={
            "llm": {"verdict": "APPROVED", "confidence": 0.91, "reasoning": "ok",
                    "risk_flags": [], "emotional_indicators": []},
            "systemone": {"provider": "local_jev", "converge": "APPROVED",
                          "latency_ms": 1100, "checks": [], "degraded": False, "ts": "t"},
        }),
        _row(symbol="GOLD", order_type="SELL", status="REJECTED", review={
            "llm": {"verdict": "REJECTED", "confidence": 0.8, "reasoning": "no",
                    "risk_flags": ["exposure"], "emotional_indicators": []},
            "reject_kind": "systemone_rejected", "retryable": False,
        }),
        _row(symbol="EURUSD", order_type="BUY", status="PENDING_CONFIRM", review={
            "llm": {"verdict": "CAUTION", "confidence": 0.62, "reasoning": "mtf",
                    "risk_flags": [], "emotional_indicators": []},
            "confirm_expires_at": "2026-10-09T12:00:00",
        }),
        # 旧 JEV 行：无 llm.verdict，走 systemone.converge（dict，含 verdict 键）兜底
        _row(symbol="USDJPY", order_type="BUY", status="EXECUTED", review={
            "systemone": {"provider": "typesafe_jev",
                          "converge": {"data_quality": "clear", "signal_alignment": "aligned",
                                       "market_regime": "neutral", "risk_check": "clear",
                                       "execution_quality": "clear", "verdict": "APPROVED", "upgrade": ""},
                          "latency_ms": 800, "checks": [], "degraded": False, "ts": "t"},
        }),
        # 更早形状：converge 为字符串
        _row(symbol="USDJPY", order_type="SELL", status="EXECUTED", review={
            "systemone": {"provider": "local_jev", "converge": "APPROVED",
                          "latency_ms": 500, "checks": [], "degraded": False, "ts": "t"},
        }),
        # 极老行：review 为 NULL，走 status 兜底映射
        _row(symbol="GOLD", order_type="BUY", status="REJECTED", review=None),
        # 在途未决：无结论
        _row(symbol="GOLD", order_type="BUY", status="PENDING_REVIEW", review={"rule_flags": []}),
    ])

    res = await _gate().list_reviews(account_login="10086", days=365, limit=100)
    assert res["total"] == 7
    assert res["stats"] == {"approved": 3, "caution": 1, "rejected": 2}
    assert len(res["reviews"]) == 7
    # id 倒序：最后一行是 PENDING_REVIEW
    assert res["reviews"][0]["status"] == "PENDING_REVIEW"
    assert res["reviews"][0]["verdict"] is None

    by_id = {r["id"]: r for r in res["reviews"]}
    # llm 路径行：verdict/provider/confidence 便捷字段
    g_approved = next(r for r in res["reviews"] if r["symbol"] == "GOLD" and r["status"] == "EXECUTED")
    assert g_approved["verdict"] == "APPROVED"
    assert g_approved["provider"] == "local_jev"
    assert g_approved["confidence"] == 0.91
    # systemone.converge 兜底行（dict 形状）
    usdjpy_dict = next(r for r in res["reviews"] if r["symbol"] == "USDJPY" and r["order_type"] == "BUY")
    assert usdjpy_dict["verdict"] == "APPROVED"
    assert usdjpy_dict["provider"] == "typesafe_jev"
    assert usdjpy_dict["confidence"] is None
    # systemone.converge 兜底行（字符串形状）
    usdjpy_str = next(r for r in res["reviews"] if r["symbol"] == "USDJPY" and r["order_type"] == "SELL")
    assert usdjpy_str["verdict"] == "APPROVED"
    assert usdjpy_str["provider"] == "local_jev"
    # status 兜底行（review=None）
    no_review = next(r for r in res["reviews"] if r["symbol"] == "GOLD" and r["status"] == "REJECTED")
    assert no_review["verdict"] == "REJECTED"
    assert no_review["provider"] is None
    # review JSON 原样保留（向前兼容）
    assert g_approved["review"]["systemone"]["provider"] == "local_jev"
    assert by_id  # 顺手确认 id 都在


# ─── days 时间窗口 ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_days_cutoff(patched_session):
    from datetime import datetime

    now = datetime.utcnow()
    await _seed(patched_session, [
        _row(symbol="GOLD", created_at=now - timedelta(days=30),
             review={"llm": {"verdict": "APPROVED", "confidence": 0.9, "risk_flags": [],
                             "emotional_indicators": [], "reasoning": ""}}),
        _row(symbol="GOLD", created_at=now - timedelta(days=1),
             review={"llm": {"verdict": "APPROVED", "confidence": 0.9, "risk_flags": [],
                             "emotional_indicators": [], "reasoning": ""}}),
    ])

    recent = await _gate().list_reviews(days=7)
    assert recent["total"] == 1
    assert recent["reviews"][0]["created_at"] >= (now - timedelta(days=7)).isoformat()

    wide = await _gate().list_reviews(days=90)
    assert wide["total"] == 2


# ─── status / symbol 过滤 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_filter(patched_session):
    await _seed(patched_session, [
        _row(symbol="GOLD", status="REJECTED", review=None),
        _row(symbol="GOLD", status="EXECUTED",
             review={"llm": {"verdict": "APPROVED", "confidence": 0.9, "risk_flags": [],
                             "emotional_indicators": [], "reasoning": ""}}),
        _row(symbol="EURUSD", status="PENDING_CONFIRM",
             review={"llm": {"verdict": "CAUTION", "confidence": 0.6, "risk_flags": [],
                             "emotional_indicators": [], "reasoning": ""}}),
    ])

    res = await _gate().list_reviews(status="REJECTED")
    assert res["total"] == 1
    assert all(r["status"] == "REJECTED" for r in res["reviews"])


@pytest.mark.asyncio
async def test_symbol_filter(patched_session):
    await _seed(patched_session, [
        _row(symbol="GOLD", status="EXECUTED", review=None),
        _row(symbol="EURUSD", status="EXECUTED", review=None),
        _row(symbol="USDJPY", status="EXECUTED", review=None),
    ])

    res = await _gate().list_reviews(symbol="GOLD")
    assert res["total"] == 1
    assert all(r["symbol"] == "GOLD" for r in res["reviews"])


# ─── verdict 内存过滤（含 status 兜底行）────────────────────────────────


@pytest.mark.asyncio
async def test_verdict_filter(patched_session):
    await _seed(patched_session, [
        _row(symbol="GOLD", status="EXECUTED",
             review={"llm": {"verdict": "APPROVED", "confidence": 0.9, "risk_flags": [],
                             "emotional_indicators": [], "reasoning": ""}}),
        _row(symbol="GOLD", status="REJECTED", review=None),  # status 兜底 → REJECTED
        _row(symbol="EURUSD", status="PENDING_CONFIRM",
             review={"llm": {"verdict": "CAUTION", "confidence": 0.6, "risk_flags": [],
                             "emotional_indicators": [], "reasoning": ""}}),
        _row(symbol="EURUSD", status="PENDING_CONFIRM", review=None),  # status 兜底 → CAUTION
        _row(symbol="GOLD", status="PENDING_REVIEW", review=None),  # 无结论 → 任何 verdict 都不命中
    ])

    assert (await _gate().list_reviews(verdict="APPROVED"))["total"] == 1
    assert (await _gate().list_reviews(verdict="REJECTED"))["total"] == 1
    assert (await _gate().list_reviews(verdict="CAUTION"))["total"] == 2
    # 无 verdict 参数 = 不过滤
    assert (await _gate().list_reviews())["total"] == 5


# ─── 分页 / 统计 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pagination_and_stats(patched_session):
    await _seed(patched_session, [
        _row(symbol=f"S{i}", status="EXECUTED",
             review={"llm": {"verdict": "APPROVED", "confidence": 0.5 + i * 0.1,
                             "risk_flags": [], "emotional_indicators": [], "reasoning": ""}})
        for i in range(5)
    ])

    page1 = await _gate().list_reviews(limit=2, offset=0)
    assert page1["total"] == 5
    assert page1["stats"] == {"approved": 5, "caution": 0, "rejected": 0}
    assert len(page1["reviews"]) == 2

    page3 = await _gate().list_reviews(limit=2, offset=4)
    assert len(page3["reviews"]) == 1
    assert page3["total"] == 5  # total/stats 不受分页影响


# ─── 账号隔离 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_account_scope(patched_session):
    await _seed(patched_session, [
        _row(symbol="GOLD", account_login="10086", status="EXECUTED", review=None),
        _row(symbol="GOLD", account_login="999", status="EXECUTED", review=None),
    ])

    scoped = await _gate().list_reviews(account_login="10086")
    assert scoped["total"] == 1
    assert scoped["reviews"][0]["account_login"] == "10086"

    unscoped = await _gate().list_reviews()
    assert unscoped["total"] == 2
