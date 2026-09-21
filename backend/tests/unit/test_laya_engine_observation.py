"""engine 开仓侧 laya 影子观测测试（Phase 4，只观测不改行为）。

覆盖：
1. 市场摘要/快照纯函数（确定性预计算）
2. 分歧分类矩阵（gate/final 双口径）
3. 观测器生命周期：account/chain 注入、laya 并行、最终结果落库、故障零影响
4. engine wrapper：保持返回值、finally 必调 finish、inner 异常时 finish(None)
5. 观测未启用 → 零开销（无观测对象）
6. 报表纯函数聚合
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from app.ai.laya_engine_observation import (
    DIV_CAUTION_ALLOW,
    DIV_CAUTION_DENY,
    DIV_CHAIN_ABSTAIN,
    DIV_LAYA_ABSENT,
    DIV_LAYA_ESCALATE,
    DIV_LAYA_UNAVAILABLE,
    DIV_LOOSEN,
    DIV_NONE,
    DIV_TIGHTEN,
    EngineLayaObservation,
    build_laya_engine_snapshot,
    build_market_summary,
    classify_divergence,
    start_engine_observation,
)
from app.ai.laya_engine_report import build_engine_report


def _df(n=60):
    import numpy as np

    rng = np.random.default_rng(42)
    closes = 100.0 + np.cumsum(rng.normal(0, 0.5, n))
    return pd.DataFrame({
        "open": closes, "high": closes + 0.5, "low": closes - 0.5,
        "close": closes, "tick_volume": 1000,
    })


class TestMarketSummary:
    def test_full_df(self):
        s = build_market_summary(_df())
        assert s["last_close"] > 0
        for key in ("change_1_pct", "change_5_pct", "vol_14", "range_position_50"):
            assert key in s
        assert 0.0 <= s["range_position_50"] <= 1.0

    def test_short_df_returns_empty(self):
        assert build_market_summary(pd.DataFrame({"close": [1.0]})) == {}

    def test_bad_df_returns_empty(self):
        assert build_market_summary(None) == {}
        assert build_market_summary({"close": [1, 2, 3]}) == {}

    def test_missing_close_returns_empty(self):
        assert build_market_summary(pd.DataFrame({"open": [1.0, 2.0]})) == {}


class TestSnapshot:
    def test_structure_and_compaction(self):
        class P:
            symbol = "GOLD"
            type = "BUY"
            volume = 0.1
            profit = 3.2

        snap = build_laya_engine_snapshot(
            symbol="GOLD", timeframe="M15", signal=1, signal_label="STRONG_BUY",
            balance=10000.0, df=_df(), positions=[P()] * 6, daily_pnl=-50.0,
            recent_wr=0.6,
        )
        assert snap["order"]["side"] == "BUY"
        assert snap["order"]["signal"] == 1
        assert snap["account"]["balance"] == 10000.0
        assert snap["account"]["positions_count"] == 6
        assert snap["account"]["daily_pnl"] == -50.0
        assert snap["account"]["recent_win_rate"] == 0.6
        assert len(snap["positions"]) == 5  # 压缩到前 5
        assert snap["positions"][0]["symbol"] == "GOLD"
        assert snap["market"]["symbol"] == "GOLD"
        assert snap["market"]["timeframe"] == "M15"
        assert "last_close" in snap["market"]

    def test_signal_signs(self):
        s1 = build_laya_engine_snapshot(
            symbol="G", timeframe="M15", signal=-1, signal_label="SELL", balance=1,
            df=_df(), positions=[], daily_pnl=None, recent_wr=None,
        )
        assert s1["order"]["side"] == "SELL"


class TestClassifyDivergence:
    @pytest.mark.parametrize("chain,laya,gate_kind", [
        (True, "REJECTED", DIV_TIGHTEN),
        (True, "ESCALATE", DIV_TIGHTEN),
        (True, "APPROVED", DIV_NONE),
        (False, "REJECTED", DIV_NONE),
        (False, "APPROVED", DIV_LOOSEN),
        (None, "APPROVED", DIV_CHAIN_ABSTAIN),
        (True, "UNAVAILABLE", DIV_LAYA_UNAVAILABLE),
        (True, "CAUTION", DIV_CAUTION_ALLOW),
        (False, "CAUTION", DIV_CAUTION_DENY),
        (True, None, DIV_LAYA_ABSENT),
        (False, "ESCALATE", DIV_LAYA_ESCALATE),
    ])
    def test_gate_kinds(self, chain, laya, gate_kind):
        d = classify_divergence(chain, laya)
        assert d["gate"] == gate_kind

    def test_final_override(self):
        # TradeGate 放行但最终确定性链不放行 → gate=收紧分歧, final=一致
        d = classify_divergence(True, "REJECTED", final_allowed=False)
        assert d["gate"] == DIV_TIGHTEN
        assert d["final"] == DIV_NONE

    def test_final_fallback(self):
        d = classify_divergence(True, "REJECTED", final_allowed=None)
        assert d["final"] == DIV_TIGHTEN


class TestStartObservation:
    @patch("app.ai.laya_engine_observation.settings")
    def test_disabled_returns_none(self, m_settings):
        m_settings.laya_gate_engine_shadow = False
        obs = start_engine_observation(
            symbol="GOLD", timeframe="M15", signal=1, signal_label="BUY",
            balance=1.0, df=None,
        )
        assert obs is None

    @patch("app.ai.laya_engine_observation.settings")
    async def test_enabled_returns_observer(self, m_settings):
        m_settings.laya_gate_engine_shadow = True
        obs = start_engine_observation(
            symbol="GOLD", timeframe="M15", signal=1, signal_label="BUY",
            balance=1.0, df=None,
        )
        assert isinstance(obs, EngineLayaObservation)
        obs.finish(True)  # 清理后台任务
        obs._task.cancel()
        try:
            await obs._task
        except asyncio.CancelledError:
            pass


class TestObservationLifecycle:
    @patch("app.ai.laya_engine_observation.settings")
    @patch("app.ai.laya_gate.laya_gate_review", new_callable=AsyncMock)
    async def test_records_tighten_and_persists(self, m_review, m_settings):
        m_settings.laya_gate_engine_shadow = True
        m_settings.laya_gate_predict_timeout_s = 5.0
        m_review.return_value = {
            "decision": "REJECTED", "confidence": 0.8, "reasons": ["risk_check: block"],
            "checks": {}, "answers": {}, "engine": "laya",
        }
        obs = EngineLayaObservation(
            symbol="GOLD", timeframe="M15", signal=1, signal_label="BUY",
            balance=10000.0, df=_df(), recent_wr=0.6,
        )
        obs.set_account([], -50.0)
        obs.set_chain(True, 0.72)
        obs.finish(True)

        persist = AsyncMock()
        with patch.object(obs, "_persist", persist):
            await obs._task

        assert persist.await_count == 1
        snapshot, review, latency, divergence = persist.await_args.args
        assert review["decision"] == "REJECTED"
        assert divergence["gate"] == DIV_TIGHTEN
        assert divergence["final"] == DIV_TIGHTEN
        assert latency >= 0

    @patch("app.ai.laya_engine_observation.settings")
    @patch("app.ai.laya_gate.laya_gate_review", new_callable=AsyncMock)
    async def test_laya_failure_records_unavailable(self, m_review, m_settings):
        """laya 崩溃 → UNAVAILABLE 留痕，任务不抛错（H-3 非干扰性）。"""
        m_settings.laya_gate_engine_shadow = True
        m_settings.laya_gate_predict_timeout_s = 5.0
        m_review.side_effect = RuntimeError("boom")
        obs = EngineLayaObservation(
            symbol="GOLD", timeframe="M15", signal=1, signal_label="BUY",
            balance=10000.0, df=_df(), recent_wr=None,
        )
        obs.set_account([], 0.0)
        obs.set_chain(True, 0.7)
        obs.finish(False)

        persist = AsyncMock()
        with patch.object(obs, "_persist", persist):
            await obs._task  # 不应抛异常

        assert persist.await_count == 1
        _snap, review, _lat, divergence = persist.await_args.args
        assert review["decision"] == "UNAVAILABLE"
        assert divergence["gate"] == DIV_LAYA_UNAVAILABLE

    @patch("app.ai.laya_engine_observation.settings")
    @patch("app.ai.laya_gate.laya_gate_review", new_callable=AsyncMock)
    async def test_missing_contexts_proceeds(self, m_review, m_settings):
        """inner 异常导致 account/chain 永不到 → 超时降级，仍落 UNAVAILABLE 行。"""
        m_settings.laya_gate_engine_shadow = True
        m_settings.laya_gate_predict_timeout_s = 5.0
        m_review.side_effect = RuntimeError("no model")
        obs = EngineLayaObservation(
            symbol="GOLD", timeframe="M15", signal=1, signal_label="BUY",
            balance=10000.0, df=None, recent_wr=None,
        )
        # 不 set account/chain；只 finish
        obs.finish(True)

        persist = AsyncMock()
        with patch.object(obs, "_persist", persist):
            await asyncio.wait_for(obs._task, timeout=15)

        assert persist.await_count == 1
        _snap, review, _lat, divergence = persist.await_args.args
        assert review["decision"] == "UNAVAILABLE"
        assert divergence["gate"] == DIV_CHAIN_ABSTAIN

    @patch("app.ai.laya_engine_observation.settings")
    @patch("app.ai.laya_gate.laya_gate_review", new_callable=AsyncMock)
    async def test_persist_failure_ignored(self, m_review, m_settings):
        m_settings.laya_gate_engine_shadow = True
        m_settings.laya_gate_predict_timeout_s = 5.0
        m_review.return_value = {"decision": "APPROVED", "confidence": 0.9,
                                 "reasons": [], "checks": {}, "answers": {}, "engine": "laya"}
        obs = EngineLayaObservation(
            symbol="GOLD", timeframe="M15", signal=1, signal_label="BUY",
            balance=10000.0, df=_df(), recent_wr=None,
        )
        obs.set_account([], 0.0)
        obs.set_chain(True, 0.7)
        obs.finish(True)

        async def _boom(*a, **k):
            raise RuntimeError("db down")

        with patch.object(obs, "_persist", _boom):
            await obs._task  # 落库失败只记日志，不抛


class TestEngineWrapper:
    def _engine(self):
        from app.bot.engine import BotEngine

        eng = BotEngine.__new__(BotEngine)
        eng.symbol = "GOLD"
        eng.timeframe = "M15"
        return eng

    async def test_wrapper_preserves_result_and_finishes(self):
        eng = self._engine()

        class FakeObs:
            def __init__(self):
                self.finished = []

            def finish(self, allowed):
                self.finished.append(allowed)

        fake = FakeObs()
        with (
            patch("app.ai.laya_engine_observation.start_engine_observation", return_value=fake),
            patch.object(eng, "_check_trade_permission_inner", new=AsyncMock(return_value=True)),
        ):
            result = await eng._check_trade_permission(1, "BUY", 10000.0, None, df=_df())

        assert result is True
        assert fake.finished == [True]

    async def test_wrapper_finishes_none_on_inner_error(self):
        eng = self._engine()

        class FakeObs:
            def __init__(self):
                self.finished = []

            def finish(self, allowed):
                self.finished.append(allowed)

        fake = FakeObs()
        async def _boom(*a, **k):
            raise RuntimeError("risk manager crashed")

        with (
            patch("app.ai.laya_engine_observation.start_engine_observation", return_value=fake),
            patch.object(eng, "_check_trade_permission_inner", new=_boom),
        ):
            with pytest.raises(RuntimeError):
                await eng._check_trade_permission(1, "BUY", 10000.0, None, df=_df())

        assert fake.finished == [None]

    async def test_disabled_no_observation(self):
        eng = self._engine()
        with (
            patch("app.ai.laya_engine_observation.start_engine_observation", return_value=None),
            patch.object(eng, "_check_trade_permission_inner", new=AsyncMock(return_value=False)),
        ):
            result = await eng._check_trade_permission(1, "BUY", 10000.0, None, df=_df())
        assert result is False

    async def test_inner_injects_account_and_chain(self):
        """inner 在 gather 与 TradeGate predict 后注入上下文（同步、不 await）。"""
        eng = self._engine()
        obs = MagicMock()
        eng.executor = MagicMock()
        eng.executor.get_open_positions = AsyncMock(return_value=[])
        eng.circuit_breaker = MagicMock()
        eng.circuit_breaker.get_daily_pnl = AsyncMock(return_value=10.0)
        eng._trade_gate = MagicMock()
        eng._trade_gate.is_ready = True
        eng._trade_gate.predict = MagicMock(return_value=(True, 0.66))
        eng.market_data = MagicMock()
        eng.market_data.get_ohlcv = AsyncMock(return_value=_df(70))
        eng.risk_manager = MagicMock()
        eng.risk_manager.can_open_trade = MagicMock(return_value=(True, ""))
        eng.risk_manager.compute_effective_confidence = MagicMock(return_value=0.5)
        eng._multi_tf_regime = None
        eng._last_regime = None
        eng._log_event = AsyncMock()
        eng._push_event = AsyncMock()
        eng.notifier = None
        eng._manager = None
        eng.context_builder = None
        eng._ai_context = None
        eng.redis = MagicMock()
        eng.redis.get = AsyncMock(return_value=None)
        eng.db = None

        import asyncio as _a2

        # settings 补丁：trade_gate shadow 开（走 predict 分支），laya 观测关
        with patch("app.bot.engine.settings") as m_settings:
            m_settings.trade_gate_shadow = True
            m_settings.trade_gate_enforce = False
            m_settings.trade_gate_model_path = "x.pkl"
            result = await eng._check_trade_permission_inner(
                1, "BUY", 10000.0, None, recent_wr_prefetched=0.6, df=_df(), _engine_obs=obs,
            )
        assert result is True
        assert obs.set_account.call_count == 1
        assert obs.set_chain.call_count == 1
        assert obs.set_chain.call_args.args == (True, 0.66)


class TestReport:
    def _rows(self):
        return [
            {"signal_label": "BUY", "chain_can_trade": True, "chain_prob": 0.7, "allowed": True,
             "laya_verdict": "REJECTED", "divergence_gate": DIV_TIGHTEN,
             "divergence_final": DIV_TIGHTEN, "laya_latency_ms": 100},
            {"signal_label": "BUY", "chain_can_trade": True, "chain_prob": 0.8, "allowed": True,
             "laya_verdict": "APPROVED", "divergence_gate": DIV_NONE,
             "divergence_final": DIV_NONE, "laya_latency_ms": 200},
            {"signal_label": "SELL", "chain_can_trade": False, "chain_prob": 0.1, "allowed": False,
             "laya_verdict": "APPROVED", "divergence_gate": DIV_LOOSEN,
             "divergence_final": DIV_LOOSEN, "laya_latency_ms": 300},
        ]

    def test_counts(self):
        r = build_engine_report(self._rows())
        assert r["n"] == 3
        assert r["tighten_gate"] == 1
        assert r["loosen_gate"] == 1
        assert r["none_gate"] == 1
        assert r["laya_available"] == 3
        assert r["laya_latency_ms"]["p50"] == 200.0
        assert len(r["tighten_cases"]) == 1
        assert r["signal_labels"]["BUY"]["total"] == 2
        assert r["signal_labels"]["BUY"]["tighten"] == 1

    def test_empty(self):
        r = build_engine_report([])
        assert r["n"] == 0
        assert r["laya_latency_ms"] == {"p50": 0.0, "p95": 0.0}
