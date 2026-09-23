"""ManualGate Laya 影子集成测试（Phase 3.2，veto-only 影子语义）。

覆盖：
1. laya 未启用（shadow/enforce 均 False）→ 影子不跑、LLM 主路径不变
2. shadow 开启 + laya 可用 → stored["laya"] 落库（decision/engine/latency），verdict 不变
3. laya 故障（超时/异常）→ 影子 UNAVAILABLE 留痕，LLM 路径结果不变（H-3 非干扰性）
4. veto-only：laya REJECTED 不改变 LLM APPROVED 的执行
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.manual_order_gate import ManualOrderGate


def _make_gate():
    connector = MagicMock()
    redis = MagicMock()
    ai_client = MagicMock()
    return ManualOrderGate(connector, redis, ai_client)


def _snapshot():
    return {
        "order": {"review_id": 1, "symbol": "GOLD", "type": "BUY", "lot": 0.1, "sl": 100, "tp": 200},
        "account": {"balance": 10000, "equity": 10000, "floating_profit": 0, "realized_daily_pnl": 50},
        "positions": [],
        "recent_trades": [],
        "rule_flags": [],
        "market": {"bid": 4000.0, "ask": 4000.5, "spread": 0.5, "avg_spread": 0.6, "sentiment": None},
    }


class TestLayaShadowDisabled:
    async def test_disabled_skips_shadow(self):
        gate = _make_gate()
        with patch("app.services.manual_order_gate.settings") as mock_settings:
            mock_settings.laya_enabled = True
            mock_settings.laya_gate_shadow = False
            mock_settings.laya_gate_enforce = False
            result = await gate._laya_shadow_review(_snapshot())
            assert result is None  # 未启用 → 不跑影子

    async def test_laya_disabled_skips_shadow(self):
        """M8：laya_enabled=False 时整个影子链路跳过（不落噪音行）。"""
        gate = _make_gate()
        with patch("app.services.manual_order_gate.settings") as mock_settings:
            mock_settings.laya_enabled = False
            mock_settings.laya_gate_shadow = True
            mock_settings.laya_gate_enforce = False
            result = await gate._laya_shadow_review(_snapshot())
            assert result is None


class TestLayaShadowReview:
    async def test_shadow_enabled_stores_laya(self):
        gate = _make_gate()
        gate._load_audit = AsyncMock(return_value=MagicMock(review={}, status="PENDING_REVIEW"))
        gate._update_audit = AsyncMock()
        gate.ai_client.complete_json_async = AsyncMock(return_value={
            "verdict": "APPROVED", "confidence": 0.9,
            "risk_flags": [], "emotional_indicators": [], "reasoning": "ok",
        })
        gate._execute_approved = AsyncMock()

        fake_laya = {
            "decision": "APPROVED", "confidence": 0.9, "reasons": ["all gates pass"],
            "checks": {}, "answers": {}, "engine": "laya", "latency_ms": 250,
        }
        with (
            patch("app.services.manual_order_gate.settings") as mock_settings,
            patch.object(ManualOrderGate, "_laya_shadow_review", new=AsyncMock(return_value=fake_laya)),
        ):
            mock_settings.laya_gate_shadow = True
            mock_settings.laya_gate_enforce = False
            mock_settings.laya_gate_predict_timeout_s = 30
            await gate._review(1, _snapshot())

        stored = gate._update_audit.await_args.kwargs["review"]
        assert stored["laya"]["engine"] == "laya"
        assert stored["laya"]["decision"] == "APPROVED"
        assert stored["llm"]["verdict"] == "APPROVED"

    async def test_shadow_unavailable_records_error_not_llm(self):
        """laya 故障 → 影子 UNAVAILABLE 留痕，LLM 路径结果不变（H-3）。"""
        gate = _make_gate()
        gate._load_audit = AsyncMock(return_value=MagicMock(review={}, status="PENDING_REVIEW"))
        gate._update_audit = AsyncMock()
        gate.ai_client.complete_json_async = AsyncMock(return_value={
            "verdict": "APPROVED", "confidence": 0.9,
            "risk_flags": [], "emotional_indicators": [], "reasoning": "ok",
        })
        gate._execute_approved = AsyncMock()

        with (
            patch("app.services.manual_order_gate.settings") as mock_settings,
            patch.object(
                ManualOrderGate, "_laya_shadow_review",
                new=AsyncMock(return_value={"engine": "laya", "decision": "UNAVAILABLE", "error": "timeout"}),
            ),
        ):
            mock_settings.laya_gate_shadow = True
            mock_settings.laya_gate_enforce = False
            mock_settings.laya_gate_predict_timeout_s = 30
            await gate._review(1, _snapshot())

        stored = gate._update_audit.await_args.kwargs["review"]
        assert stored["laya"]["decision"] == "UNAVAILABLE"
        assert stored["llm"]["verdict"] == "APPROVED"
        gate._execute_approved.assert_awaited_once()  # laya 故障不影响执行

    async def test_veto_only_laya_reject_does_not_block_llm_approve(self):
        """veto-only：laya REJECTED 不改变 LLM APPROVED 的执行路径。"""
        gate = _make_gate()
        gate._load_audit = AsyncMock(return_value=MagicMock(review={}, status="PENDING_REVIEW"))
        gate._update_audit = AsyncMock()
        gate.ai_client.complete_json_async = AsyncMock(return_value={
            "verdict": "APPROVED", "confidence": 0.9,
            "risk_flags": [], "emotional_indicators": [], "reasoning": "ok",
        })
        gate._execute_approved = AsyncMock()

        fake_laya = {
            "decision": "REJECTED", "confidence": 0.8, "reasons": ["risk_check: block"],
            "checks": {}, "answers": {}, "engine": "laya", "latency_ms": 200,
        }
        with (
            patch("app.services.manual_order_gate.settings") as mock_settings,
            patch.object(ManualOrderGate, "_laya_shadow_review", new=AsyncMock(return_value=fake_laya)),
        ):
            mock_settings.laya_gate_shadow = True
            mock_settings.laya_gate_enforce = False
            mock_settings.laya_gate_predict_timeout_s = 30
            await gate._review(1, _snapshot())

        stored = gate._update_audit.await_args.kwargs["review"]
        assert stored["laya"]["decision"] == "REJECTED"
        assert stored["llm"]["verdict"] == "APPROVED"
        gate._execute_approved.assert_awaited_once()  # 影子 REJECTED 不拦截（enforce 关）


class TestShadowPersist:
    """Phase 3.3：影子明细落专表（best-effort，失败不影响主路径）。"""

    async def test_persist_writes_row(self):
        gate = _make_gate()
        audit = MagicMock()
        audit.id = 7
        audit.account_login = "0"
        audit.symbol = "GOLD"

        inserted = {}
        fake_session = MagicMock()

        class FakeSessionCtx:
            async def __aenter__(self):
                return fake_session

            async def __aexit__(self, *exc):
                return False

        with (
            patch("app.db.session.async_session", return_value=FakeSessionCtx()),
            patch.object(fake_session, "add", side_effect=lambda obj: inserted.update(vars(obj))),
            patch.object(fake_session, "commit", new=AsyncMock()),
        ):
            await gate._persist_shadow_review(
                audit,
                _snapshot(),
                {
                    "decision": "REJECTED", "confidence": 0.8, "reasons": ["risk block"],
                    "checks": {}, "answers": {"risk_check": {"label": "block"}},
                    "engine": "laya", "latency_ms": 210,
                },
                {"verdict": "APPROVED", "confidence": 0.9},
            )

        assert inserted["audit_id"] == 7
        assert inserted["laya_verdict"] == "REJECTED"
        assert inserted["llm_verdict"] == "APPROVED"
        assert inserted["agreement"] is False
        assert inserted["dangerous_divergence"] is False  # laya REJECTED 不是致命方向
        assert inserted["laya_latency_ms"] == 210
        assert inserted["state_snapshot"]["order"]["symbol"] == "GOLD"

    async def test_persist_dangerous_divergence(self):
        gate = _make_gate()
        audit = MagicMock()
        audit.id = 8
        audit.account_login = "0"
        audit.symbol = "GOLD"

        inserted = {}
        fake_session = MagicMock()

        class FakeSessionCtx:
            async def __aenter__(self):
                return fake_session

            async def __aexit__(self, *exc):
                return False

        with (
            patch("app.db.session.async_session", return_value=FakeSessionCtx()),
            patch.object(fake_session, "add", side_effect=lambda obj: inserted.update(vars(obj))),
            patch.object(fake_session, "commit", new=AsyncMock()),
        ):
            await gate._persist_shadow_review(
                audit,
                _snapshot(),
                {"decision": "APPROVED", "engine": "laya", "latency_ms": 100},
                {"verdict": "REJECTED", "confidence": 0.9},
            )

        assert inserted["dangerous_divergence"] is True  # laya 放行被 LLM 拒 = 致命
        assert inserted["agreement"] is False

    async def test_persist_skip_when_no_laya(self):
        gate = _make_gate()
        audit = MagicMock()
        audit.id = 9
        fake_session = MagicMock()

        class FakeSessionCtx:
            async def __aenter__(self):
                return fake_session

            async def __aexit__(self, *exc):
                return False

        with (
            patch("app.db.session.async_session", return_value=FakeSessionCtx()),
            patch.object(fake_session, "add") as m_add,
            patch.object(fake_session, "commit", new=AsyncMock()),
        ):
            await gate._persist_shadow_review(audit, _snapshot(), None, {"verdict": "APPROVED"})
        m_add.assert_not_called()  # laya 未跑不留半行

    async def test_persist_error_is_nonfatal(self):
        gate = _make_gate()
        audit = MagicMock()
        audit.id = 10
        audit.account_login = "0"
        audit.symbol = "GOLD"

        class BrokenSession:
            async def __aenter__(self):
                raise RuntimeError("db down")

            async def __aexit__(self, *exc):
                return False

        with patch("app.db.session.async_session", return_value=BrokenSession()):
            # 不抛错（best-effort）
            await gate._persist_shadow_review(
                audit, _snapshot(), {"decision": "APPROVED", "engine": "laya"}, {"verdict": "APPROVED"}
            )


class TestShadowAwaitBudget:
    """M1：laya 影子超过预算 → 订单路径立即放行，落库后台补写。"""

    @patch("app.services.manual_order_gate.LAYA_SHADOW_AWAIT_BUDGET_S", 0.05)
    async def test_slow_laya_does_not_block_order(self):
        gate = _make_gate()
        gate._load_audit = AsyncMock(return_value=MagicMock(review={}, status="PENDING_REVIEW"))
        gate._update_audit = AsyncMock()
        gate.ai_client.complete_json_async = AsyncMock(return_value={
            "verdict": "APPROVED", "confidence": 0.9,
            "risk_flags": [], "emotional_indicators": [], "reasoning": "ok",
        })
        gate._execute_approved = AsyncMock()
        deferred = AsyncMock()

        async def _slow_laya(self, _snap):
            await asyncio.sleep(0.2)
            return {"engine": "laya", "decision": "APPROVED", "latency_ms": 200}

        with (
            patch("app.services.manual_order_gate.settings") as mock_settings,
            patch.object(ManualOrderGate, "_laya_shadow_review", new=_slow_laya),
            patch.object(ManualOrderGate, "_deferred_persist_shadow", new=deferred),
        ):
            mock_settings.laya_gate_shadow = True
            mock_settings.laya_gate_enforce = False
            mock_settings.laya_gate_predict_timeout_s = 30
            await gate._review(1, _snapshot())

        # 订单在预算内放行并执行（不等 laya 慢任务）
        gate._execute_approved.assert_awaited_once()
        # 后台补写任务已调度（给事件循环一个 tick）
        await asyncio.sleep(0)
        assert deferred.await_count >= 1
        # 主路径 review 摘要里没有 laya（未在预算内返回）——不写半行
        stored = gate._update_audit.await_args.kwargs["review"]
        assert "laya" not in stored


class TestShadowReviewTimeoutBranch:
    """真实 _laya_shadow_review 的 TimeoutError 分支（评审缺口补测）。"""

    @patch("app.ai.laya_gate.laya_gate_review", new_callable=AsyncMock)
    async def test_timeout_returns_unavailable(self, m_review):
        gate = _make_gate()
        m_review.side_effect = asyncio.TimeoutError()  # 内部 predict 超时冒泡
        with patch("app.services.manual_order_gate.settings") as mock_settings:
            mock_settings.laya_enabled = True
            mock_settings.laya_gate_shadow = True
            mock_settings.laya_gate_enforce = False
            mock_settings.laya_gate_predict_timeout_s = 0.05
            result = await gate._laya_shadow_review(_snapshot())

        assert result["engine"] == "laya"
        assert result["decision"] == "UNAVAILABLE"
        assert result["error"] == "timeout"
