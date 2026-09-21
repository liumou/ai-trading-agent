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
