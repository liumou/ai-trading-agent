"""Laya 6 问收敛器纯函数测试（Phase 3.0 设计冻结验收）。

覆盖收敛规则 7 级优先级：畸形/insufficient/低置信 → ESCALATE；
risk/execution/entry block → REJECTED；方向合取 → REJECTED；
全 pass → APPROVED；mixed/caution → CAUTION。
"""

import pytest
from unittest.mock import AsyncMock

from app.ai.laya_gate import (
    CONVERGENCE_QUESTIONS,
    LAYA_GATE_OPTIONS,
    VERDICT_APPROVED,
    VERDICT_CAUTION,
    VERDICT_ESCALATE,
    VERDICT_REJECTED,
    converge_laya_verdict,
    render_laya_state_prose,
)

from app.config import settings

MIN_CONF = 0.6


def _ans(label: str, conf: float = 0.9) -> dict:
    return {"label": label, "confidence": conf}


def _full(**overrides) -> dict:
    """构造 6 问全 pass 基线答案，可覆盖单问。"""
    base = {
        "data_quality": _ans("sufficient"),
        "signal_alignment": _ans("aligned"),
        "market_regime": _ans("favorable"),
        "risk_check": _ans("clear"),
        "execution_quality": _ans("clear"),
        "entry_decision": _ans("pass"),
    }
    base.update(overrides)
    return base


class TestEscalate:
    async def test_missing_question_escalates(self):
        answers = _full()
        del answers["risk_check"]
        d = converge_laya_verdict(answers, min_confidence=MIN_CONF)
        assert d.verdict == VERDICT_ESCALATE

    async def test_invalid_label_escalates(self):
        d = converge_laya_verdict(_full(risk_check=_ans("unexpected")), min_confidence=MIN_CONF)
        assert d.verdict == VERDICT_ESCALATE

    async def test_non_numeric_confidence_escalates(self):
        d = converge_laya_verdict(_full(entry_decision={"label": "pass", "confidence": "x"}), min_confidence=MIN_CONF)
        assert d.verdict == VERDICT_ESCALATE

    async def test_low_confidence_escalates(self):
        d = converge_laya_verdict(_full(entry_decision=_ans("pass", 0.5)), min_confidence=MIN_CONF)
        assert d.verdict == VERDICT_ESCALATE
        assert "confidence" in d.reasons[0]

    async def test_insufficient_evidence_escalates(self):
        d = converge_laya_verdict(_full(market_regime=_ans("insufficient")), min_confidence=MIN_CONF)
        assert d.verdict == VERDICT_ESCALATE
        assert "insufficient" in d.reasons[0]


class TestReject:
    async def test_risk_block_rejects(self):
        d = converge_laya_verdict(_full(risk_check=_ans("block")), min_confidence=MIN_CONF)
        assert d.verdict == VERDICT_REJECTED
        assert d.confidence == 0.9

    async def test_execution_block_rejects(self):
        d = converge_laya_verdict(_full(execution_quality=_ans("block")), min_confidence=MIN_CONF)
        assert d.verdict == VERDICT_REJECTED

    async def test_entry_reject_rejects(self):
        d = converge_laya_verdict(_full(entry_decision=_ans("reject")), min_confidence=MIN_CONF)
        assert d.verdict == VERDICT_REJECTED

    async def test_directional_conjunction_rejects(self):
        d = converge_laya_verdict(
            _full(signal_alignment=_ans("conflict"), market_regime=_ans("adverse")),
            min_confidence=MIN_CONF,
        )
        assert d.verdict == VERDICT_REJECTED
        assert "conflict" in d.reasons[0]

    async def test_signal_conflict_alone_is_caution(self):
        # 仅 signal=conflict、regime 正常 → 不构成方向合取，应 CAUTION 而非 REJECTED
        d = converge_laya_verdict(_full(signal_alignment=_ans("conflict")), min_confidence=MIN_CONF)
        assert d.verdict == VERDICT_CAUTION


class TestApprove:
    async def test_all_pass_approves(self):
        d = converge_laya_verdict(_full(), min_confidence=MIN_CONF)
        assert d.verdict == VERDICT_APPROVED
        assert d.confidence == 0.9
        assert "all gates pass" in d.reasons

    async def test_approve_keeps_checks(self):
        d = converge_laya_verdict(_full(), min_confidence=MIN_CONF)
        assert set(d.checks.keys()) == set(CONVERGENCE_QUESTIONS)


class TestCaution:
    async def test_mixed_signals_caution(self):
        d = converge_laya_verdict(_full(signal_alignment=_ans("mixed")), min_confidence=MIN_CONF)
        assert d.verdict == VERDICT_CAUTION

    async def test_neutral_regime_caution(self):
        d = converge_laya_verdict(_full(market_regime=_ans("neutral")), min_confidence=MIN_CONF)
        assert d.verdict == VERDICT_CAUTION

    async def test_risk_caution_caution(self):
        d = converge_laya_verdict(_full(risk_check=_ans("caution")), min_confidence=MIN_CONF)
        assert d.verdict == VERDICT_CAUTION


class TestDataQualityAuditOnly:
    async def test_data_quality_insufficient_notes_but_approves(self):
        # data_quality 是审计问：insufficient 只附加 reason，不改变收敛判定
        d = converge_laya_verdict(_full(data_quality=_ans("insufficient")), min_confidence=MIN_CONF)
        assert d.verdict == VERDICT_APPROVED
        assert any("data_quality" in r for r in d.reasons)

    async def test_options_contract(self):
        # 白名单契约：data_quality 三值，entry_decision 二值
        assert LAYA_GATE_OPTIONS["data_quality"] == {"sufficient", "partial", "insufficient"}
        assert LAYA_GATE_OPTIONS["entry_decision"] == {"pass", "reject"}


class TestCrossRulePriority:
    """L8：跨规则优先级——insufficient/低置信 先于 block（规则 2/3 先于 4）。"""

    def test_insufficient_wins_over_risk_block(self):
        answers = _full(risk_check=_ans("block"), signal_alignment=_ans("insufficient"))
        d = converge_laya_verdict(answers, min_confidence=MIN_CONF)
        assert d.verdict == VERDICT_ESCALATE

    def test_low_confidence_wins_over_risk_block(self):
        answers = _full(risk_check=_ans("block", 0.5))
        d = converge_laya_verdict(answers, min_confidence=MIN_CONF)
        assert d.verdict == VERDICT_ESCALATE

    def test_structural_malformed_wins_over_block(self):
        answers = _full(risk_check=_ans("block"))
        answers["entry_decision"] = {"label": "pass"}  # 缺 confidence
        d = converge_laya_verdict(answers, min_confidence=MIN_CONF)
        assert d.verdict == VERDICT_ESCALATE


class TestConfidenceBoundary:
    """L8：阈值恰好 0.6 通过；越界/NaN → ESCALATE。"""

    def test_confidence_equal_threshold_passes(self):
        answers = _full(risk_check=_ans("clear", 0.6))
        d = converge_laya_verdict(answers, min_confidence=0.6)
        assert d.verdict == VERDICT_APPROVED

    def test_confidence_above_one_escalates(self):
        answers = _full(risk_check=_ans("clear", 1.5))
        d = converge_laya_verdict(answers, min_confidence=0.6)
        assert d.verdict == VERDICT_ESCALATE

    def test_confidence_nan_escalates(self):
        import math

        answers = _full(risk_check=_ans("clear", math.nan))
        d = converge_laya_verdict(answers, min_confidence=0.6)
        assert d.verdict == VERDICT_ESCALATE


class TestGateReviewConfigWiring:
    """M7：laya_gate_review 使用 settings.laya_gate_confidence_threshold（非硬编码 0.6）。"""

    async def _review(self, threshold: float, confidence: float):
        from unittest.mock import AsyncMock, MagicMock, patch

        rt = MagicMock()
        rt.available = True
        rt.predict_choices = AsyncMock(return_value={
            "data_quality": {"label": "sufficient", "confidence": 0.9},
            "signal_alignment": {"label": "aligned", "confidence": confidence},
            "market_regime": {"label": "favorable", "confidence": 0.9},
            "risk_check": {"label": "clear", "confidence": 0.9},
            "execution_quality": {"label": "clear", "confidence": 0.9},
            "entry_decision": {"label": "pass", "confidence": 0.9},
        })
        with (
            patch("app.ai.laya_runtime.get_laya_runtime", return_value=rt),
            patch("app.ai.laya_gate.settings") as m_settings,
        ):
            from app.ai.laya_gate import laya_gate_review

            m_settings.laya_gate_confidence_threshold = threshold
            return await laya_gate_review({"x": 1}, timeout=5.0)

    async def test_higher_threshold_escalates_same_confidence(self):
        d = await self._review(threshold=0.75, confidence=0.7)
        assert d["decision"] == VERDICT_ESCALATE

    async def test_default_threshold_approves_same_confidence(self):
        d = await self._review(threshold=0.6, confidence=0.7)
        assert d["decision"] == VERDICT_APPROVED


def _snapshot() -> dict:
    """生产 6 问快照（engine 观测同构：order/account/positions/recent_trades/rule_flags/market）。"""
    return {
        "order": {"signal": 1, "signal_label": "sma_cross_momentum", "symbol": "GOLD",
                  "timeframe": "M15", "side": "BUY"},
        "account": {"balance": 10000.0, "positions_count": 0, "daily_pnl": 0.0,
                    "recent_win_rate": 0.55},
        "positions": [],
        "recent_trades": [],
        "rule_flags": [],
        "market": {"last_close": 3157.12, "change_1_pct": 0.3665, "change_5_pct": 0.8014,
                   "vol_14": 0.2383, "range_position_50": 1.0, "price_vs_sma9": 1.0061,
                   "price_vs_sma21": 1.0087, "symbol": "GOLD", "timeframe": "M15"},
    }


class TestProseStateRender:
    """prose 证据渲染（2026-09-22 真实数据实测：JSON→prose 后 evidence sufficient 0→61%）。"""

    def test_render_contains_key_evidence(self):
        text = render_laya_state_prose(_snapshot())
        assert "manual order review" in text
        assert "Order: BUY on GOLD M15" in text
        assert "signal=1 (sma_cross_momentum)" in text
        assert "balance 10000.0" in text
        assert "open positions 0" in text
        assert "recent win rate 0.55" in text
        assert "last close 3157.12" in text
        assert "change 0.3665% over the last bar" in text
        assert "change 0.8014% over 5 bars" in text
        assert "14-bar volatility 0.2383%" in text
        assert "price at 100% of the 50-bar range" in text
        assert "price 1.0061x SMA9" in text
        assert "price 1.0087x SMA21" in text

    def test_render_tolerates_missing_fields(self):
        text = render_laya_state_prose({"order": {}, "market": {}})
        assert "manual order review" in text
        assert "Account: no account data." in text
        assert "Market:" not in text  # 无 market 字段时不输出空 Market 行

    def test_render_positions_and_flags(self):
        snap = _snapshot()
        snap["positions"] = [{"symbol": "GOLD", "type": "BUY", "volume": 0.1, "profit": 12.5}] * 5
        snap["recent_trades"] = [{"symbol": "GOLD", "type": "SELL", "volume": 0.2, "profit": -3.0}] * 5
        snap["rule_flags"] = ["high_volatility"]
        text = render_laya_state_prose(snap)
        assert "Positions: GOLD BUY vol=0.1 profit=12.5 | GOLD BUY vol=0.1 profit=12.5" in text
        assert "Recent trades: GOLD SELL vol=0.2 profit=-3.0" in text
        assert "Rule flags: high_volatility" in text
        # 位置/成交各自最多 4 条 → 输出里最多 8 个 "vol=" 段
        assert text.count("vol=") <= 8

    def test_render_object_positions_tolerated(self):
        class _P:
            symbol = "GOLD"
            type = "BUY"
            volume = 0.1
            profit = 1.5

        text = render_laya_state_prose({"positions": [_P()], "recent_trades": [], "market": {}})
        assert "GOLD BUY vol=0.1 profit=1.5" in text

    def test_render_stays_within_token_budget(self):
        """512 token 硬上限（laya build_sequence）：最大快照需明显留余量。"""
        snap = _snapshot()
        snap["positions"] = [{"symbol": "GOLD", "type": "BUY", "volume": 0.1, "profit": 12.5}] * 4
        snap["recent_trades"] = [{"symbol": "GOLD", "type": "SELL", "volume": 0.2, "profit": -3.0}] * 4
        text = render_laya_state_prose(snap)
        # 预算护栏：900 字符 ≈ 350-375 token < 最坏 room 413（512-192-1 的保守口径留足余量）
        assert len(text) <= 900


def _manual_gate_snapshot() -> dict:
    """ManualGate 路径快照（manual_order_gate._build_snapshot 形状）。"""
    return {
        "order": {"review_id": 123, "symbol": "GOLD", "type": "market",
                  "lot": 0.1, "sl": 3120.0, "tp": 3180.0},
        "account": {"balance": 5000.0, "equity": 5050.0, "floating_profit": 50.0,
                    "realized_daily_pnl": -20.0},
        "positions": [{"symbol": "GOLD", "type": "BUY", "lot": 0.05, "profit": 8.0}],
        "recent_trades": [{"symbol": "GOLD", "type": "SELL", "lot": 0.1, "profit": -4.0}],
        "rule_flags": [{"flag": "no_stop_loss", "severity": "warn", "detail": "Order has no stop loss"}],
        "market": {"bid": 3152.0, "ask": 3152.4, "spread": 0.4, "avg_spread": 0.38,
                   "sentiment": {"label": "bullish", "score": 0.7}},
    }


class TestProseRenderManualGateKeys:
    """Phase 0：prose 渲染兼容 ManualGate 快照键（修复静默丢证据缺陷）。"""

    def test_renders_order_size_and_protection(self):
        text = render_laya_state_prose(_manual_gate_snapshot())
        assert "lot 0.1" in text
        assert "type market" in text
        assert "stop loss 3120.0" in text
        assert "take profit 3180.0" in text

    def test_renders_account_equity_and_pnl(self):
        text = render_laya_state_prose(_manual_gate_snapshot())
        assert "balance 5000.0" in text
        assert "equity 5050.0" in text
        assert "floating profit 50.0" in text
        assert "realized daily pnl -20.0" in text

    def test_renders_market_quotes_and_sentiment(self):
        text = render_laya_state_prose(_manual_gate_snapshot())
        assert "bid 3152.0" in text
        assert "ask 3152.4" in text
        assert "spread 0.4" in text
        assert "avg_spread 0.38" in text
        assert "news sentiment bullish" in text

    def test_renders_dict_rule_flags_and_lot_positions(self):
        text = render_laya_state_prose(_manual_gate_snapshot())
        assert "Positions: GOLD BUY vol=0.05 profit=8.0" in text
        assert "Recent trades: GOLD SELL vol=0.1 profit=-4.0" in text
        assert "Rule flags: Order has no stop loss" in text

    def test_manual_gate_max_shape_within_token_budget(self):
        snap = _manual_gate_snapshot()
        snap["positions"] = [dict(snap["positions"][0])] * 4
        snap["recent_trades"] = [dict(snap["recent_trades"][0])] * 4
        text = render_laya_state_prose(snap)
        assert len(text) <= 900

    def test_budget_guard_drops_low_priority_lines(self):
        """超预算时优先丢 flags→trades→positions，保住 order/account/market 核心证据。"""
        snap = _manual_gate_snapshot()
        snap["positions"] = [dict(snap["positions"][0])] * 4
        snap["recent_trades"] = [dict(snap["recent_trades"][0])] * 4
        snap["rule_flags"] = [{"flag": "x", "severity": "warn",
                               "detail": "very long rule detail " * 20}] * 8
        snap["market"].update({
            "rsi14": 61.2, "atr14": 8.4, "atr_pct": 0.27, "macd_state": "positive",
            "ma5": 3102.0, "ma10": 3098.0, "ma20": 3090.0,
            "support": 3085.0, "resistance": 3120.0,
            "data_age_seconds": 45.0, "is_stale": False,
            "change_5_pct": 0.8, "vol_14": 0.23, "range_position_50": 0.8,
            "price_vs_sma9": 1.001, "price_vs_sma21": 1.004, "last_close": 3105.0,
            "change_1_pct": 0.1,
        })
        text = render_laya_state_prose(snap)
        assert len(text) <= 900
        # 核心证据行保留
        assert "Order details: lot 0.1" in text
        assert "Account:" in text
        assert "Market:" in text


class TestGateReviewUsesProse:
    async def test_prose_enabled_passes_text_state(self, monkeypatch):
        fake_rt = AsyncMock()
        fake_rt.available = True
        fake_rt.predict_choices.return_value = {
            "data_quality": {"label": "sufficient", "confidence": 0.9},
            "signal_alignment": {"label": "aligned", "confidence": 0.9},
            "market_regime": {"label": "favorable", "confidence": 0.9},
            "risk_check": {"label": "clear", "confidence": 0.9},
            "execution_quality": {"label": "clear", "confidence": 0.9},
            "entry_decision": {"label": "pass", "confidence": 0.9},
        }
        captured = {}

        async def _capture(state, questions, **kw):
            captured["state"] = state
            return fake_rt.predict_choices.return_value

        fake_rt.predict_choices.side_effect = _capture
        monkeypatch.setattr("app.ai.laya_runtime.get_laya_runtime", lambda: fake_rt)
        monkeypatch.setattr(settings, "laya_state_prose", True)
        monkeypatch.setattr(settings, "laya_gate_confidence_threshold", 0.6)

        from app.ai.laya_gate import laya_gate_review

        review = await laya_gate_review(_snapshot())
        assert isinstance(captured["state"], str)
        assert "manual order review" in captured["state"]
        assert review["decision"] == VERDICT_APPROVED

    async def test_prose_disabled_passes_json_state(self, monkeypatch):
        fake_rt = AsyncMock()
        fake_rt.available = True
        fake_rt.predict_choices.return_value = {
            "data_quality": {"label": "sufficient", "confidence": 0.9},
            "signal_alignment": {"label": "aligned", "confidence": 0.9},
            "market_regime": {"label": "favorable", "confidence": 0.9},
            "risk_check": {"label": "clear", "confidence": 0.9},
            "execution_quality": {"label": "clear", "confidence": 0.9},
            "entry_decision": {"label": "pass", "confidence": 0.9},
        }
        captured = {}

        async def _capture(state, questions, **kw):
            captured["state"] = state
            return fake_rt.predict_choices.return_value

        fake_rt.predict_choices.side_effect = _capture
        monkeypatch.setattr("app.ai.laya_runtime.get_laya_runtime", lambda: fake_rt)
        monkeypatch.setattr(settings, "laya_state_prose", False)
        monkeypatch.setattr(settings, "laya_gate_confidence_threshold", 0.6)

        from app.ai.laya_gate import laya_gate_review

        review = await laya_gate_review(_snapshot())
        assert isinstance(captured["state"], dict)
        assert review["decision"] == VERDICT_APPROVED
