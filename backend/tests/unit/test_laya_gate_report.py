"""Laya 影子报表纯函数测试（Phase 3.3，validation.md §1.2 指标集）。

覆盖：混淆矩阵、原始一致率、Wilson 下限、Kappa、致命分歧/误杀、
兜底率、延迟分位、逐问对齐。
"""

from app.ai.laya_gate_report import (
    build_report,
    cohen_kappa,
    confusion_matrix,
    divergences,
    fallback_rate,
    latency_percentiles,
    per_question_alignment,
    raw_agreement,
    wilson_lower_bound,
)


def _pairs():
    # 10 行：8 一致（6 APPROVED + 2 REJECTED）+ 1 致命分歧（APPROVED vs REJECTED）+ 1 误杀
    return [
        ("APPROVED", "APPROVED"),
        ("APPROVED", "APPROVED"),
        ("APPROVED", "APPROVED"),
        ("APPROVED", "APPROVED"),
        ("APPROVED", "APPROVED"),
        ("APPROVED", "APPROVED"),
        ("REJECTED", "REJECTED"),
        ("REJECTED", "REJECTED"),
        ("APPROVED", "REJECTED"),  # 致命分歧
        ("REJECTED", "APPROVED"),  # 误杀
    ]


class TestConfusionMatrix:
    def test_counts(self):
        m = confusion_matrix(_pairs())
        assert m["APPROVED"]["APPROVED"] == 6
        assert m["REJECTED"]["REJECTED"] == 2
        assert m["APPROVED"]["REJECTED"] == 1
        assert m["REJECTED"]["APPROVED"] == 1

    def test_excludes_outside_verdicts(self):
        m = confusion_matrix([("ESCALATE", "APPROVED"), ("APPROVED", "APPROVED")])
        assert sum(sum(row.values()) for row in m.values()) == 1


class TestAgreement:
    def test_raw_agreement(self):
        assert raw_agreement(_pairs()) == 8 / 10

    def test_empty(self):
        assert raw_agreement([]) == 0.0

    def test_wilson_lower_bound(self):
        # n=300 全一致 → 下限应高（0/300 致命 + 一致率高）
        lb = wilson_lower_bound(300, 300)
        assert lb > 0.95
        # n=10, 8 一致
        assert 0.4 < wilson_lower_bound(8, 10) < 0.98


class TestKappa:
    def test_perfect_agreement(self):
        pairs = [("APPROVED", "APPROVED")] * 5 + [("REJECTED", "REJECTED")] * 5
        assert cohen_kappa(pairs) == 1.0

    def test_random_agreement_low(self):
        pairs = [("APPROVED", "REJECTED")] * 5 + [("REJECTED", "APPROVED")] * 5
        assert cohen_kappa(pairs) < 0.0  # 负一致

    def test_empty(self):
        assert cohen_kappa([]) == 0.0


class TestDivergences:
    def test_counts(self):
        d = divergences(_pairs())
        assert d["dangerous_approve"] == 1  # 唯一致命方向
        assert d["false_kill"] == 1


class TestFallbackRate:
    def test_rate(self):
        decisions = ["APPROVED", "ESCALATE", "UNAVAILABLE", "REJECTED"]
        assert fallback_rate(decisions) == 0.5

    def test_empty(self):
        assert fallback_rate([]) == 0.0


class TestLatency:
    def test_p50_p95(self):
        vals = [100.0, 200.0, 300.0, 400.0, 500.0]
        assert latency_percentiles(vals, 0.5) == 300.0
        assert latency_percentiles(vals, 0.95) == 480.0

    def test_empty(self):
        assert latency_percentiles([], 0.5) == 0.0


class TestPerQuestionAlignment:
    def test_entry_decision_mapping(self):
        answers = [
            {"entry_decision": {"label": "reject"}, "risk_check": {"label": "clear"}},
            {"entry_decision": {"label": "pass"}, "risk_check": {"label": "clear"}},
            {"entry_decision": {"label": "pass"}, "risk_check": {"label": "block"}},
        ]
        llm = ["REJECTED", "APPROVED", "REJECTED"]
        qa = per_question_alignment(answers, llm)
        # entry_decision: reject→REJECTED ✓, pass→APPROVED ✓, pass→APPROVED ✗(实际 REJECTED) = 2/3
        assert qa["entry_decision"]["mapped_agreement"] == 2 / 3
        # risk_check: block→REJECTED ✓（1 例 mapped）
        assert qa["risk_check"]["mapped_agreement"] == 1.0
        assert qa["risk_check"]["n"] == 1


class TestBuildReport:
    def test_report_shape(self):
        r = build_report(
            _pairs(),
            decisions=["APPROVED", "ESCALATE"],
            laya_latencies=[200.0, 400.0],
            llm_latencies=[2000.0, 3000.0],
            answers=[{"entry_decision": {"label": "pass"}}],
            llm_verdicts_for_qa=["APPROVED"],
        )
        assert r["n"] == 10
        assert r["raw_agreement"] == 8 / 10
        assert r["dangerous_divergence_count"] == 1
        assert r["fallback_rate"] == 0.5
        assert r["laya_latency_ms"]["p50"] == 300.0
        assert "per_question" in r


class TestBoundaryCases:
    """L8：Kappa/Wilson/分母语义边界。"""

    def test_kappa_n1_single_class_guard(self):
        # n=1 全单类 → pe→1，守卫返回 0.0（避免除零；n 太小时 Kappa 无意义）
        assert cohen_kappa([("APPROVED", "APPROVED")]) == 0.0

    def test_kappa_single_class_all_agree_guard(self):
        # 全单类（row/col 只在一个类）→ pe→1，守卫返回 0.0（避免除零）
        pairs = [("APPROVED", "APPROVED")] * 5
        assert cohen_kappa(pairs) == 0.0

    def test_wilson_zero_agreement(self):
        assert wilson_lower_bound(0, 10) == 0.0

    def test_wilson_n1_is_conservative(self):
        # n=1 的 Wilson 95% 单侧下限保守（≈0.21），不等于 1——小样本不能宣称高一致
        v = wilson_lower_bound(1, 1)
        assert 0.0 < v < 1.0

    def test_raw_agreement_excludes_escalate(self):
        # ESCALATE/UNAVAILABLE 排除出分母（三值口径），不计为不一致
        pairs = [("APPROVED", "APPROVED"), ("ESCALATE", "APPROVED"), ("UNAVAILABLE", "REJECTED")]
        assert raw_agreement(pairs) == 1.0

    def test_percentiles_single_value(self):
        assert latency_percentiles([100.0], 0.95) == 100.0

    def test_percentiles_empty(self):
        assert latency_percentiles([], 0.95) == 0.0

    def test_fallback_rate_mixed(self):
        assert fallback_rate(["APPROVED", "ESCALATE", "UNAVAILABLE"]) == 2 / 3
