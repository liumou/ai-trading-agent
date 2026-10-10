"""
历史订单 AI 深度复盘（trade_reviews）单元测试。

覆盖（Phase 5 测试计划）：
1. trade_reviewer：bot 单组装 / 手动单组装 / 行情窗口降级 / classification 服务端推导
2. 白名单校验：reasoning_correct 严格布尔、标签白名单、confidence 归一、截断
3. 服务端四分类推导（LLM 只出 reasoning_correct，代码推分类）
4. TradeReviewStore：状态机 / 幂等 / force 重审 / recover / IDOR 归属
5. 路由：CRUD / 幂等 / 配额 429 / batch / IDOR
6. 并发幂等（claim 二次 None；lease 过期 recover）
"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.ai.trade_reviewer import TradeReviewer
from app.constants import (
    REVIEW_CLASS_CORRECT_PROCESS,
    REVIEW_CLASS_LUCKY_WIN,
    REVIEW_CLASS_REAL_MISTAKE,
    REVIEW_CLASS_SKILLED_WIN,
)
from app.db.models import Trade, TradeReview
from app.services.trade_reviews import TradeReviewStore, now, trigger_review

# ─── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def reviewer(monkeypatch):
    """构造 TradeReviewer：AI 用宽松 AsyncMock（AIClient spec 太严，不能设置
    complete_json_async），collector 返回空（触发降级路径）。"""
    from app.ai.trade_accountability import TradeAccountabilityTracker

    class _EmptyCollector:
        async def load_from_db(self, *args, **kwargs):
            return None

        async def collect(self, *args, **kwargs):
            return None

    ai = AsyncMock()  # 宽松 AsyncMock，可任意设置 complete_json_async
    ai.complete_json_async = AsyncMock(return_value={"reasoning_correct": True, "confidence": 0.9})
    return TradeReviewer(
        ai_client=ai,
        market_data=None,
        collector=_EmptyCollector(),
        accountability=TradeAccountabilityTracker(),
    )


def _bot_trade(**overrides) -> Trade:
    base = dict(
        ticket=9001,
        symbol="GOLD",
        type="BUY",
        lot=0.1,
        open_price=2000.0,
        close_price=2010.0,
        sl=1990.0,
        tp=2020.0,
        open_time=datetime(2026, 10, 1, 10, 0),
        close_time=datetime(2026, 10, 1, 12, 0),
        profit=100.0,
        strategy_name="ema_crossover",
        trade_reason="趋势跟随",
        comment=None,
        account_login="0",
    )
    base.update(overrides)
    return Trade(**base)


# ─── 1. 输入组装 ──────────────────────────────────────────────────────────────


class TestInputAssembler:
    async def test_bot_trade_full_fields(self, reviewer):
        inp = await reviewer.build_input(trade=_bot_trade())
        assert inp is not None
        assert inp.symbol == "GOLD"
        assert inp.direction == "BUY"
        assert inp.profit == 100.0
        assert inp.source == "bot"
        assert inp.strategy_name == "ema_crossover"
        assert inp.market_context_degraded is True  # 空 collector → 显式降级

    async def test_bot_trade_missing_symbol_returns_none(self, reviewer):
        inp = await reviewer.build_input(trade=_bot_trade(symbol=""))
        assert inp is None

    async def test_manual_deal_assembly(self, reviewer):
        deal = {
            "ticket": 8001, "symbol": "GOLD", "type": "SELL", "lot": 0.2,
            "open_price": 2010.0, "price": 2000.0, "sl": 2020.0, "tp": 1980.0,
            "open_time": "2026-10-01T10:00:00+00:00",
            "time": "2026-10-01T11:00:00+00:00",
            "profit": 200.0, "net_profit": 199.5, "comment": "手动平仓",
        }
        inp = await reviewer.build_input(deal=deal, account_login="5555")
        assert inp is not None
        assert inp.source == "manual"
        assert inp.profit == 199.5  # 净额优先（含 commission/swap）
        assert inp.close_price == 2000.0
        assert inp.account_login == "5555"

    async def test_manual_deal_no_symbol_returns_none(self, reviewer):
        assert await reviewer.build_input(deal={"symbol": "", "type": "BUY"}) is None

    async def test_manual_behavior_signals_duration(self, reviewer):
        deal = {
            "symbol": "GOLD", "type": "BUY", "lot": 0.1,
            "open_price": 2000.0, "price": 2001.0,
            "open_time": "2026-10-01T10:00:00+00:00",
            "time": "2026-10-01T10:02:00+00:00",  # 2 分钟 → 持仓过短
            "profit": 10.0, "net_profit": 10.0,
        }
        inp = await reviewer.build_input(deal=deal)
        assert inp is not None
        flags = [s["flag"] for s in inp.behavior_signals]
        assert "持仓过短" in flags


# ─── 2. 白名单校验 ──────────────────────────────────────────────────────────


class TestWhitelist:
    def test_strict_bool_rejects_string(self, reviewer):
        from app.ai.trade_reviewer import _strict_bool

        ok, err = _strict_bool(True)
        assert ok is True and err is None
        ok, err = _strict_bool("true")
        assert ok is None and err is not None  # "true" 字符串被拒
        ok, err = _strict_bool(1)
        assert ok is None and err is not None  # 整数被拒

    def test_validate_causes_whitelist_filter(self, reviewer):
        from app.ai.trade_reviewer import _validate_causes

        # 亏损单：白名单内的标签保留、白名单外被过滤
        causes = _validate_causes(
            {"loss_causes": ["逆势开仓", "情绪化操作", "不是白名单标签"]}, profit=-50
        )
        assert "逆势开仓" in causes
        assert "情绪化操作" in causes
        assert "不是白名单标签" not in causes

    def test_clamp_confidence_out_of_range(self, reviewer):
        from app.ai.trade_reviewer import _clamp_confidence

        assert _clamp_confidence(1.7) is None  # 越界 → None（不 clamp 到边界，flagged）
        assert _clamp_confidence("high") is None  # 非数值 → None
        assert _clamp_confidence(0.8) == 0.8
        assert _clamp_confidence(-0.5) is None

    def test_confidence_or_fail_distinguishes_oob(self, reviewer):
        """越界/缺失区分：越界是模型输出畸形（调用方 fail-closed），
        缺失仅触发 flagged（评审 MEDIUM-5）。"""
        from app.ai.trade_reviewer import _confidence_or_fail

        v, oob = _confidence_or_fail(0.8)
        assert v == 0.8 and oob is False
        v, oob = _confidence_or_fail(None)
        assert v is None and oob is False  # 缺失 → 非 oob
        v, oob = _confidence_or_fail(1.7)
        assert v is None and oob is True  # 越界 → oob（fail-closed）
        v, oob = _confidence_or_fail("high")
        assert v is None and oob is False  # 非数值视为缺失
        v, oob = _confidence_or_fail(-0.5)
        assert v is None and oob is True


# ─── 3. 服务端四分类推导 ────────────────────────────────────────────────────


class TestClassificationDerivation:
    async def _run_with_ai(self, reviewer, ai_response: dict):
        reviewer.ai.complete_json_async = AsyncMock(return_value=ai_response)
        inp = await reviewer.build_input(trade=_bot_trade(profit=100.0))
        return await reviewer.run(inp)

    async def test_skilled_win_derived(self, reviewer):
        # pnl>0 + reasoning_correct=true → skilled_win
        result = await self._run_with_ai(reviewer, {"reasoning_correct": True, "confidence": 0.9})
        assert result.get("error") is None
        assert result["classification"] == REVIEW_CLASS_SKILLED_WIN
        assert result["flagged"] is False

    async def test_real_mistake_derived(self, reviewer):
        # pnl<0 + reasoning_correct=false → real_mistake
        reviewer.ai.complete_json_async = AsyncMock(return_value={"reasoning_correct": False, "confidence": 0.8})
        inp = await reviewer.build_input(trade=_bot_trade(profit=-50.0))
        result = await reviewer.run(inp)
        assert result.get("error") is None
        assert result["classification"] == REVIEW_CLASS_REAL_MISTAKE

    async def test_correct_process_derived(self, reviewer):
        # pnl<0 + reasoning_correct=true → correct_process
        reviewer.ai.complete_json_async = AsyncMock(return_value={"reasoning_correct": True, "confidence": 0.8})
        inp = await reviewer.build_input(trade=_bot_trade(profit=-50.0))
        result = await reviewer.run(inp)
        assert result["classification"] == REVIEW_CLASS_CORRECT_PROCESS

    async def test_lucky_win_derived(self, reviewer):
        # pnl>0 + reasoning_correct=false → lucky_win
        reviewer.ai.complete_json_async = AsyncMock(return_value={"reasoning_correct": False, "confidence": 0.8})
        inp = await reviewer.build_input(trade=_bot_trade(profit=100.0))
        result = await reviewer.run(inp)
        assert result["classification"] == REVIEW_CLASS_LUCKY_WIN

    async def test_model_self_report_mismatch_flagged(self, reviewer):
        # 模型自报 classification 与服务端推导不一致 → flagged（服务端为准）
        reviewer.ai.complete_json_async = AsyncMock(return_value={
            "reasoning_correct": False, "confidence": 0.9, "classification": "skilled_win",
        })
        inp = await reviewer.build_input(trade=_bot_trade(profit=-50.0))
        result = await reviewer.run(inp)
        assert result["classification"] == REVIEW_CLASS_REAL_MISTAKE  # 服务端推导
        assert result["flagged"] is True

    async def test_low_confidence_flagged(self, reviewer):
        # confidence 越界（None）→ flagged
        reviewer.ai.complete_json_async = AsyncMock(return_value={"reasoning_correct": True, "confidence": None})
        inp = await reviewer.build_input(trade=_bot_trade(profit=100.0))
        result = await reviewer.run(inp)
        assert result["flagged"] is True

    async def test_confidence_oob_fails_closed(self, reviewer):
        # confidence >1/<0 = 模型输出畸形 → fail-closed（error + 无分类），
        # 不产出部分结论（评审 MEDIUM-5）
        reviewer.ai.complete_json_async = AsyncMock(
            return_value={"reasoning_correct": True, "confidence": 999}
        )
        inp = await reviewer.build_input(trade=_bot_trade(profit=100.0))
        result = await reviewer.run(inp)
        assert result["error"]
        assert result["flagged"] is True
        assert result.get("classification") is None

    async def test_llm_none_fails_closed(self, reviewer):
        # complete_json_async 返回 None → fail-closed（error + flagged + 无 classification）
        reviewer.ai.complete_json_async = AsyncMock(return_value=None)
        inp = await reviewer.build_input(trade=_bot_trade())
        result = await reviewer.run(inp)
        assert result["error"]
        assert result["flagged"] is True
        assert result.get("classification") is None

    async def test_invalid_reasoning_correct_fails_closed(self, reviewer):
        # reasoning_correct 非严格布尔 → fail-closed
        reviewer.ai.complete_json_async = AsyncMock(return_value={"reasoning_correct": "true", "confidence": 0.9})
        inp = await reviewer.build_input(trade=_bot_trade())
        result = await reviewer.run(inp)
        assert result["error"]
        assert result["flagged"] is True

    async def test_llm_timeout_fails_closed(self, reviewer):
        # LLM 超时 → fail-closed
        async def _timeout(**kwargs):
            await asyncio.sleep(999)
            return {}

        reviewer.ai.complete_json_async = AsyncMock(side_effect=_timeout)
        reviewer.ai.complete_json_async = AsyncMock(side_effect=TimeoutError())
        inp = await reviewer.build_input(trade=_bot_trade())
        result = await reviewer.run(inp)
        assert result["error"]
        assert result["flagged"] is True

    async def test_llm_exception_fails_closed(self, reviewer):
        # LLM provider 抛异常 → fail-closed（AIClient 吞掉异常返回 None，reviewer 兜底）
        async def _boom(**kwargs):
            raise RuntimeError("provider down")

        reviewer.ai.complete_json_async = AsyncMock(side_effect=_boom)
        inp = await reviewer.build_input(trade=_bot_trade())
        result = await reviewer.run(inp)
        assert result["error"]
        assert result["flagged"] is True


# ─── 4. TradeReviewStore ─────────────────────────────────────────────────────


class TestStore:
    async def test_create_and_get_for_user_idor(self, db_engine):
        store = TradeReviewStore(factory=async_session_like(db_engine))
        row = await store.create(ticket=7001, account_login="0", symbol="GOLD",
                                 trade_id=1, open_time=now())
        assert row.status == "pending"

        # IDOR：不同账号查不到
        found = await store.get_for_user(row.id, "9999")
        assert found is None
        found = await store.get_for_user(row.id, "0")
        assert found is not None

    async def test_find_by_natural_key_idempotent(self, db_engine):
        store = TradeReviewStore(factory=async_session_like(db_engine))
        await store.create(ticket=7002, account_login="0", symbol="GOLD", trade_id=1, open_time=now())
        hit = await store.find_by_natural_key(7002, "0")
        assert hit is not None and hit.status == "pending"

    async def test_claim_twice_second_none(self, db_engine):
        """并发幂等：两次 claim 同一任务，第二次返回 None（原子 UPDATE 保护）。"""
        store = TradeReviewStore(factory=async_session_like(db_engine))
        await store.create(ticket=7003, account_login="0", symbol="GOLD", trade_id=1, open_time=now())
        first = await store.claim()
        assert first is not None
        second = await store.claim()
        assert second is None  # 已被抢占

    async def test_recover_expired_lease_marks_failed(self, db_engine):
        store = TradeReviewStore(factory=async_session_like(db_engine))
        row = await store.create(ticket=7004, account_login="0", symbol="GOLD", trade_id=1, open_time=now())
        # 直接置为 running 且 lease 过期
        await store.mark_running(row.id, "tok")
        async with async_session_like(db_engine)() as db:
            r = (await db.execute(
                __import__("sqlalchemy").select(TradeReview).where(TradeReview.id == row.id)
            )).scalar_one()
            r.lease_until = now() - timedelta(seconds=10)
            await db.commit()
        recovered = await store.recover()
        assert recovered == 1
        async with async_session_like(db_engine)() as db:
            r = (await db.execute(
                __import__("sqlalchemy").select(TradeReview).where(TradeReview.id == row.id)
            )).scalar_one()
            assert r.status == "failed"
            assert "lease expired" in (r.error or "")

    async def test_finish_force_keeps_history(self, db_engine):
        """force 重审：旧版 review 快照经 previous 保留进新记录的 review_history。

        store 是单行状态机（mark_running 只接受 pending→running，finish 推至
        completed 后不可再 claim 同一行）；force 重审在业务层创建新行
        （trigger_review(force=True)，见 test_trigger_force_creates_new），
        旧版快照由调用方在 finish 时以 previous 传入。此测试验证该机制本身。
        """
        store = TradeReviewStore(factory=async_session_like(db_engine))
        row = await store.create(ticket=7005, account_login="0", symbol="GOLD", trade_id=1, open_time=now())
        await store.mark_running(row.id, "tok")
        old = {"review": {"reasoning_correct": False, "loss_causes": ["趋势误判"], "summary": "旧版"}}
        ok = await store.finish(row.id, {"classification": REVIEW_CLASS_SKILLED_WIN,
                                         "review": {"reasoning_correct": True}},
                                token="tok", previous=old)
        assert ok is True
        async with async_session_like(db_engine)() as db:
            r = (await db.execute(
                __import__("sqlalchemy").select(TradeReview).where(TradeReview.id == row.id)
            )).scalar_one()
            assert r.classification == REVIEW_CLASS_SKILLED_WIN
            assert len(r.review_history or []) == 1  # 旧版保留
            assert r.review_history[0]["review"]["reasoning_correct"] is False

    async def test_reuse_or_create_force_reuses_row(self, db_engine):
        """force 重审 / failed 重试：复用同 id（无第二行），旧 review 保留
        在行上供 finish 并入 review_history（评审 CRITICAL-1 核心语义）。"""
        store = TradeReviewStore(factory=async_session_like(db_engine))
        row, prev = await store.reuse_or_create(
            ticket=7006, account_login="0", symbol="GOLD", trade_id=None, open_time=now(),
        )
        assert prev is None
        # 完成第一版（顶层 reasoning_correct 是 finish 重组 review 的数据源）。
        await store.mark_running(row.id, "tok")
        await store.finish(row.id, {"classification": REVIEW_CLASS_REAL_MISTAKE,
                                    "reasoning_correct": False}, token="tok")
        # force 重审复用：同 id，返回 previous=旧 review。
        row2, prev2 = await store.reuse_or_create(
            ticket=7006, account_login="0", symbol="GOLD", trade_id=None, open_time=now(),
        )
        assert row2.id == row.id
        assert row2.status == "pending"  # 重置回队列
        assert prev2 is not None and prev2["review"]["reasoning_correct"] is False
        # 二次完成：旧版进 review_history。
        await store.mark_running(row2.id, "tok2")
        await store.finish(row2.id, {"classification": REVIEW_CLASS_SKILLED_WIN,
                                     "reasoning_correct": True},
                           token="tok2", previous=prev2)
        async with async_session_like(db_engine)() as db:
            r = (await db.execute(
                __import__("sqlalchemy").select(TradeReview).where(TradeReview.id == row.id)
            )).scalar_one()
            assert r.classification == REVIEW_CLASS_SKILLED_WIN
            assert len(r.review_history or []) == 1
            assert r.review_history[0]["review"]["reasoning_correct"] is False

    async def test_summary_counts_real_mistake(self, db_engine):
        store = TradeReviewStore(factory=async_session_like(db_engine))
        for ticket in (7101, 7102):
            row = await store.create(ticket=ticket, account_login="0", symbol="GOLD", trade_id=1, open_time=now())
            await store.mark_running(row.id, f"tok{ticket}")
            await store.finish(row.id, {
                "classification": REVIEW_CLASS_REAL_MISTAKE,
                "loss_causes": ["趋势误判"],
            }, token=f"tok{ticket}")
        s = await store.summary("0", days=30)
        assert s["real_mistakes"] == 2
        assert s["breakdown"].get("real_mistake") == 2
        assert s["top_causes"][0]["cause"] == "趋势误判"


# ─── 4.5 手动单 bridge 回查 ────────────────────────────────────────────────


class TestManualDealFetch:
    async def test_fetch_manual_deal_found(self, monkeypatch):
        """worker 从 bridge history 回查手动单 deal（评审 HIGH-2 修复）。"""

        from app.services import trade_reviews as svc

        class _FakeConnector:
            async def get_history(self, days=90, symbol=None):
                return {"success": True, "data": [
                    {"ticket": 8001, "symbol": "GOLD", "type": "SELL", "lot": 0.2,
                     "open_price": 2010.0, "price": 2000.0, "sl": 2020.0, "tp": 1980.0,
                     "open_time": "2026-10-01T10:00:00+00:00",
                     "time": "2026-10-01T11:00:00+00:00",
                     "profit": 200.0, "net_profit": 199.5, "comment": "手动平仓"},
                    {"ticket": 9999, "symbol": "OIL", "type": "BUY", "lot": 1.0,
                     "open_price": 70.0, "price": 71.0,
                     "open_time": "2026-10-01T10:00:00+00:00",
                     "time": "2026-10-01T11:00:00+00:00",
                     "profit": 100.0, "net_profit": 99.0},
                ]}

        class _FakeEngine:
            connector = _FakeConnector()

        # _fetch_manual_deal 内是 `from app.api.routes.bot import _get_engine`，
        # 需 patch 源头模块（评审 HIGH-2 修复）。
        import app.api.routes.bot as bot_routes

        monkeypatch.setattr(bot_routes, "_get_engine", lambda symbol=None: _FakeEngine())

        deal = await svc._fetch_manual_deal(8001, "5555")
        assert deal is not None
        assert deal["ticket"] == 8001
        assert deal["symbol"] == "GOLD"
        assert deal["account_login"] == "5555"
        assert deal["net_profit"] == 199.5

    async def test_fetch_manual_deal_not_found(self, monkeypatch):
        """bridge 历史里查不到该 ticket → None（走 fail-closed）。"""

        from app.services import trade_reviews as svc

        class _FakeConnector:
            async def get_history(self, days=90, symbol=None):
                return {"success": True, "data": []}

        class _FakeEngine:
            connector = _FakeConnector()

        import app.api.routes.bot as bot_routes

        monkeypatch.setattr(bot_routes, "_get_engine", lambda symbol=None: _FakeEngine())
        assert await svc._fetch_manual_deal(8001, "5555") is None

    async def test_fetch_manual_deal_bridge_down(self, monkeypatch):
        """bridge 离线 / get_history 异常 → None（worker finish 走 invalid_trade_data）。"""
        from unittest.mock import AsyncMock

        from app.services import trade_reviews as svc

        class _FakeEngine:
            connector = AsyncMock()
            connector.get_history.side_effect = RuntimeError("bridge down")

        import app.api.routes.bot as bot_routes

        monkeypatch.setattr(bot_routes, "_get_engine", lambda symbol=None: _FakeEngine())
        assert await svc._fetch_manual_deal(8001, "5555") is None


class TestRunOneManualChain:
    """_run_one 手动单全链路：bridge 回查 deal → build_input → LLM → finish。

    覆盖 code-reviewer 测试盲区：手动单触发路径此前无端到端测试（symbol 空
    导致的 failed 是 HIGH-2 修复目标）。这里 monkeypatch async_session 指向
    测试引擎 + reviewer 注入 mock，验证手动单真实组装到 completed。
    """

    async def test_manual_chain_completes(self, db_engine, monkeypatch):
        from unittest.mock import AsyncMock

        from app.ai.trade_reviewer import TradeReviewer
        from app.services import trade_reviews as svc

        # 1) store 指向测试引擎（_run_one 用模块级 async_session 读写行）。
        monkeypatch.setattr(svc, "async_session", async_session_like(db_engine))
        store = TradeReviewStore(factory=async_session_like(db_engine))

        # 2) 排一个手动单任务（trade_id=None → 走 _fetch_manual_deal 分支）。
        row, _ = await store.reuse_or_create(
            ticket=8002, account_login="0", symbol="GOLD", trade_id=None, open_time=now(),
        )
        await store.mark_running(row.id, "tok")

        # 3) _fetch_manual_deal 返回真实 deal（bridge 回查）。
        async def _fake_fetch(ticket, account_login):
            return {
                "ticket": ticket, "symbol": "GOLD", "type": "BUY", "lot": 0.1,
                "open_price": 2000.0, "price": 2005.0, "sl": 1990.0, "tp": 2030.0,
                "open_time": "2026-10-01T10:00:00+00:00",
                "time": "2026-10-01T12:00:00+00:00",
                "profit": 50.0, "net_profit": 49.5, "account_login": account_login,
            }
        monkeypatch.setattr(svc, "_fetch_manual_deal", _fake_fetch)

        # 4) reviewer 用宽松 mock（build_input 真实走 deal 组装；LLM mock）。
        reviewer = TradeReviewer(
            ai_client=AsyncMock(),
            market_data=None,
            collector=AsyncMock(),
        )
        reviewer.ai.complete_json_async = AsyncMock(
            return_value={"reasoning_correct": True, "confidence": 0.9}
        )
        reviewer.collector.load_from_db = AsyncMock(return_value=None)  # 行情降级

        # 5) 执行 _run_one。
        await svc._run_one(row.id, "tok", reviewer=reviewer)

        # 6) 终态 completed + 服务端推导 skilled_win（pnl>0 + reasoning_correct）。
        async with async_session_like(db_engine)() as db:
            r = (await db.execute(
                __import__("sqlalchemy").select(TradeReview).where(TradeReview.id == row.id)
            )).scalar_one()
            assert r.status == "completed"
            assert r.classification == REVIEW_CLASS_SKILLED_WIN
            assert r.symbol == "GOLD"
            assert r.error is None


# ─── 5. 路由 ────────────────────────────────────────────────────────────────


def async_session_like(db_engine):
    """返回一个能用 async with 打开的 async_sessionmaker（绑定测试 AsyncEngine）。"""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    return async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)


class TestRoutes:
    async def test_trigger_review_creates_pending(self, db_engine, redis_client):
        store = TradeReviewStore(factory=async_session_like(db_engine))
        # db 参数用于审计写入（打开独立短连接），验证审计路径不炸
        async with async_session_like(db_engine)() as db:
            result = await trigger_review(
                store=store, username="owner", redis_client=redis_client,
                account_login="0", ticket=7201, symbol="GOLD", db=db,
                trade_id=None, open_time=now(),
            )
        assert result["status"] == "pending"
        assert result["ticket"] == 7201

    async def test_trigger_idempotent(self, db_engine, redis_client):
        store = TradeReviewStore(factory=async_session_like(db_engine))
        r1 = await trigger_review(
            store=store, username="owner", redis_client=redis_client,
            account_login="0", ticket=7202, symbol="GOLD",
        )
        r2 = await trigger_review(
            store=store, username="owner", redis_client=redis_client,
            account_login="0", ticket=7202, symbol="GOLD",
        )
        assert r1["id"] == r2["id"]  # 幂等复用

    async def test_trigger_force_creates_new(self, db_engine, redis_client):
        """force 重审：UPDATE 复用同 id（不产生第二行，PG partial unique index
        下不会 IntegrityError），旧版 review 保留在行上供 worker 并入 history。

        （评审 CRITICAL-1：手动单 (ticket, account_login) 在 PG 有唯一约束，
        直接 INSERT 第二行会 500；故 force 改为复用。）
        """
        store = TradeReviewStore(factory=async_session_like(db_engine))
        r1 = await trigger_review(
            store=store, username="owner", redis_client=redis_client,
            account_login="0", ticket=7203, symbol="GOLD",
        )
        # 完成第一版（拿到 review + classification → completed）。
        await store.mark_running(r1["id"], "tok")
        await store.finish(r1["id"], {"classification": REVIEW_CLASS_SKILLED_WIN,
                                      "review": {"reasoning_correct": True}},
                           token="tok")
        # force 重审：复用同 id（旧 review 保留在行上）。
        r2 = await trigger_review(
            store=store, username="owner", redis_client=redis_client,
            account_login="0", ticket=7203, symbol="GOLD", force=True,
        )
        assert r2["id"] == r1["id"]  # force 复用同 id（非新建）
        assert r2["status"] == "pending"  # 重置回工作队列

    async def test_quota_429(self, db_engine, redis_client, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "trade_review_daily_quota", 2)
        store = TradeReviewStore(factory=async_session_like(db_engine))
        for i in range(2):
            await trigger_review(
                store=store, username="owner", redis_client=redis_client,
                account_login="0", ticket=7200 + i, symbol="GOLD",
            )
        # 第 3 次超限 → 429
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await trigger_review(
                store=store, username="owner", redis_client=redis_client,
                account_login="0", ticket=7300, symbol="GOLD",
            )
        assert exc.value.status_code == 429
