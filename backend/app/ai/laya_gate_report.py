"""Laya 影子报表聚合（Phase 3.3，纯函数，可单测）。

指标（validation.md §1.2 指标集）：
- 3×3 混淆矩阵（APPROVED/CAUTION/REJECTED）
- 原始三分类一致率 + Wilson 95% 下限
- Cohen's Kappa（3 类，去随机一致）
- 危险分歧率（laya=APPROVED & LLM=REJECTED，唯一致命方向）
- 误杀率（laya=REJECTED & LLM=APPROVED）
- 兜底率（laya=ESCALATE/UNAVAILABLE）
- 延迟 p50/p95（laya / LLM）
- 逐问 choice 分布 + 与 LLM verdict 映射一致率
"""

import math
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence

VERDICTS = ("APPROVED", "CAUTION", "REJECTED")


def confusion_matrix(pairs: Sequence[tuple[str, str]]) -> Dict[str, Dict[str, int]]:
    """laya vs LLM 3×3 混淆矩阵。仅计入两者都在三值内的行。"""
    m = {a: {b: 0 for b in VERDICTS} for a in VERDICTS}
    for laya_v, llm_v in pairs:
        if laya_v in VERDICTS and llm_v in VERDICTS:
            m[laya_v][llm_v] += 1
    return m


def raw_agreement(pairs: Sequence[tuple[str, str]]) -> float:
    """原始一致率（三值相等 / 总计入行）。无计入行返回 0.0。"""
    counted = [(a, b) for a, b in pairs if a in VERDICTS and b in VERDICTS]
    if not counted:
        return 0.0
    return sum(1 for a, b in counted if a == b) / len(counted)


def wilson_lower_bound(agree: int, n: int, z: float = 1.96) -> float:
    """单侧 Wilson 95% 下限。n=0 返回 0.0。"""
    if n <= 0:
        return 0.0
    p = agree / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - half) / denom)


def cohen_kappa(pairs: Sequence[tuple[str, str]]) -> float:
    """Cohen's Kappa（3 类）。无计入行或分母为 0 返回 0.0。"""
    counted = [(a, b) for a, b in pairs if a in VERDICTS and b in VERDICTS]
    n = len(counted)
    if n == 0:
        return 0.0
    cm = confusion_matrix(counted)
    po = sum(cm[a][a] for a in VERDICTS) / n
    row = {a: sum(cm[a][b] for b in VERDICTS) for a in VERDICTS}
    col = {b: sum(cm[a][b] for a in VERDICTS) for b in VERDICTS}
    pe = sum((row[a] / n) * (col[a] / n) for a in VERDICTS)
    if pe >= 1.0:
        return 0.0
    return (po - pe) / (1 - pe)


def divergences(pairs: Sequence[tuple[str, str]]) -> Dict[str, int]:
    """致命分歧（laya APPROVED & LLM REJECTED）与误杀（laya REJECTED & LLM APPROVED）。"""
    return {
        "dangerous_approve": sum(1 for a, b in pairs if a == "APPROVED" and b == "REJECTED"),
        "false_kill": sum(1 for a, b in pairs if a == "REJECTED" and b == "APPROVED"),
    }


def fallback_rate(decisions: Sequence[str]) -> float:
    """兜底率 = laya ESCALATE/UNAVAILABLE / 总数。空列表返回 0.0。"""
    if not decisions:
        return 0.0
    fb = sum(1 for d in decisions if d in ("ESCALATE", "UNAVAILABLE"))
    return fb / len(decisions)


def latency_percentiles(values: Sequence[float], p: float) -> float:
    """简单分位数（线性插值）。空列表返回 0.0。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    k = (len(ordered) - 1) * p
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return float(ordered[lo])
    return float(ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo))


def per_question_alignment(answers: Sequence[Dict[str, Any]], llm_verdicts: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """逐问 choice 分布 + 关键问与 LLM verdict 的映射一致率。

    映射（laya 问语义 → LLM 三值）：
      risk_check/execution_quality=block → 期望 LLM REJECTED
      entry_decision=reject → 期望 LLM REJECTED
      entry_decision=pass → 期望 LLM APPROVED
    返回 {question: {distribution: {label: count}, mapped_agreement: float, n: int}}。
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not answers:
        return out
    keys = set()
    for a in answers:
        if isinstance(a, dict):
            keys.update(a.keys())
    for q in sorted(keys):
        dist: Counter = Counter()
        mapped_ok = 0
        mapped_n = 0
        for ans, llm_v in zip(answers, llm_verdicts):
            if not isinstance(ans, dict):
                continue
            label = (ans.get(q) or {}).get("label") if isinstance(ans.get(q), dict) else None
            if label is None:
                continue
            dist[label] += 1
            expected = None
            if q == "risk_check" or q == "execution_quality":
                expected = "REJECTED" if label == "block" else None
            elif q == "entry_decision":
                expected = "REJECTED" if label == "reject" else ("APPROVED" if label == "pass" else None)
            if expected is not None and llm_v in VERDICTS:
                mapped_n += 1
                mapped_ok += 1 if llm_v == expected else 0
        out[q] = {
            "distribution": dict(dist),
            "mapped_agreement": (mapped_ok / mapped_n) if mapped_n else None,
            "n": mapped_n,
        }
    return out


def build_report(
    pairs: Sequence[tuple[str, str]],
    decisions: Sequence[str],
    laya_latencies: Sequence[float],
    llm_latencies: Sequence[float],
    answers: Optional[Sequence[Dict[str, Any]]] = None,
    llm_verdicts_for_qa: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """聚合完整影子报表（validation.md §1.2 指标集）。"""
    counted = [(a, b) for a, b in pairs if a in VERDICTS and b in VERDICTS]
    agree_n = sum(1 for a, b in counted if a == b)
    n = len(counted)
    cm = confusion_matrix(pairs)
    div = divergences(pairs)
    report = {
        "n": n,
        "raw_agreement": raw_agreement(pairs),
        "agreement_wilson_lower": wilson_lower_bound(agree_n, n),
        "kappa": cohen_kappa(pairs),
        "confusion_matrix": cm,
        "dangerous_divergence_count": div["dangerous_approve"],
        "false_kill_count": div["false_kill"],
        "fallback_rate": fallback_rate(decisions),
        "laya_latency_ms": {
            "p50": latency_percentiles(laya_latencies, 0.5),
            "p95": latency_percentiles(laya_latencies, 0.95),
        },
        "llm_latency_ms": {
            "p50": latency_percentiles(llm_latencies, 0.5),
            "p95": latency_percentiles(llm_latencies, 0.95),
        },
    }
    if answers is not None and llm_verdicts_for_qa is not None:
        report["per_question"] = per_question_alignment(answers, llm_verdicts_for_qa)
    return report
