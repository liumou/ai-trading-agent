"""Laya 6 问收敛器纯函数测试（Phase 3.0 设计冻结验收）。

覆盖收敛规则 7 级优先级：畸形/insufficient/低置信 → ESCALATE；
risk/execution/entry block → REJECTED；方向合取 → REJECTED；
全 pass → APPROVED；mixed/caution → CAUTION。
"""

import pytest

from app.ai.laya_gate import (
    CONVERGENCE_QUESTIONS,
    LAYA_GATE_OPTIONS,
    VERDICT_APPROVED,
    VERDICT_CAUTION,
    VERDICT_ESCALATE,
    VERDICT_REJECTED,
    converge_laya_verdict,
)

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
