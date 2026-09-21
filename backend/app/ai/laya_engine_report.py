"""engine 开仓侧 laya 影子观测报表（Phase 4，纯函数，可单测）。

指标（phase4-observation.md §报表指标）：
- n：观测记录数
- laya 可得率 / UNAVAILABLE 率（兜底率）
- TradeGate 弃权率（chain_abstain）
- 收紧分歧数（gate 口径 = TradeGate 放行 ∧ laya REJECTED/ESCALATE；
   final 口径 = 最终放行 ∧ laya REJECTED/ESCALATE）
- 放松分歧数（gate 口径 = TradeGate 不放行 ∧ laya APPROVED；
   final 口径 = 最终不放行 ∧ laya APPROVED）
- 一致数（none）/ CAUTION 分布
- laya 延迟 p50/p95
- 按 signal_label 分组的收紧案例（供人工复核）

Phase 4 不设 PASS/FAIL 验收门槛（validation.md 的 n≥300/Wilson/Kappa 门槛
只适用于 ManualGate 影子一致率），只出描述性统计与分歧案例清单。
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence

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
)

TIGHTEN_KINDS = (DIV_TIGHTEN,)
LOOSEN_KINDS = (DIV_LOOSEN,)


def _pct(x: int, n: int) -> float:
    return (x / n) if n else 0.0


def percentiles(values: Sequence[float], ps: Sequence[float]) -> Dict[str, float]:
    """简单百分位（laya_gate_report 内联实现的等价物，n=0 返回 0.0）。"""
    if not values:
        return {f"p{int(p * 100)}": 0.0 for p in ps}
    xs = sorted(values)
    out: Dict[str, float] = {}
    for p in ps:
        idx = min(len(xs) - 1, int(math.ceil(p * len(xs))) - 1)
        out[f"p{int(round(p * 100))}"] = float(xs[max(0, idx)])
    return out


def build_engine_report(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """rows: [{divergence_gate, divergence_final, laya_verdict, chain_can_trade,
               allowed, laya_latency_ms, signal_label}] → 描述性统计。

    纯函数、无 I/O；行字段缺失/畸形按安全默认处理。
    """
    n = len(rows)
    if n == 0:
        return {
            "n": 0, "laya_available": 0, "laya_unavailable": 0, "chain_abstain": 0,
            "tighten_gate": 0, "loosen_gate": 0, "tighten_final": 0, "loosen_final": 0,
            "none_gate": 0, "caution_gate": 0, "tighten_cases": [], "loosen_cases": [],
            "laya_latency_ms": {"p50": 0.0, "p95": 0.0}, "signal_labels": {},
        }

    gate: Counter[str] = Counter((r.get("divergence_gate") or "") for r in rows)
    final: Counter[str] = Counter((r.get("divergence_final") or "") for r in rows)
    laya_verds: Counter[str] = Counter((r.get("laya_verdict") or "") for r in rows)

    tighten_gate = int(gate.get(DIV_TIGHTEN, 0))
    loosen_gate = int(gate.get(DIV_LOOSEN, 0))
    tighten_final = int(final.get(DIV_TIGHTEN, 0))
    loosen_final = int(final.get(DIV_LOOSEN, 0))
    none_gate = int(gate.get(DIV_NONE, 0))
    caution_gate = int(gate.get(DIV_CAUTION_ALLOW, 0) + gate.get(DIV_CAUTION_DENY, 0))

    tighten_cases = [
        {
            "signal_label": r.get("signal_label"),
            "chain": r.get("chain_can_trade"),
            "allowed": r.get("allowed"),
            "laya": r.get("laya_verdict"),
            "prob": r.get("chain_prob"),
        }
        for r in rows
        if (r.get("divergence_gate") or "") == DIV_TIGHTEN
    ][:100]
    loosen_cases = [
        {
            "signal_label": r.get("signal_label"),
            "chain": r.get("chain_can_trade"),
            "allowed": r.get("allowed"),
            "laya": r.get("laya_verdict"),
            "prob": r.get("chain_prob"),
        }
        for r in rows
        if (r.get("divergence_gate") or "") == DIV_LOOSEN
    ][:100]

    lat = [float(r.get("laya_latency_ms") or 0) for r in rows]

    labels: Dict[str, Any] = {}
    for r in rows:
        label = str(r.get("signal_label") or "?")
        slot = labels.setdefault(label, {"total": 0, DIV_TIGHTEN: 0, DIV_LOOSEN: 0})
        slot["total"] += 1
        if (r.get("divergence_gate") or "") == DIV_TIGHTEN:
            slot[DIV_TIGHTEN] += 1
        if (r.get("divergence_gate") or "") == DIV_LOOSEN:
            slot[DIV_LOOSEN] += 1

    return {
        "n": n,
        "laya_available": int(laya_verds.get("APPROVED", 0) + laya_verds.get("CAUTION", 0)
                             + laya_verds.get("REJECTED", 0) + laya_verds.get("ESCALATE", 0)),
        "laya_unavailable": int(laya_verds.get("UNAVAILABLE", 0)),
        "chain_abstain": int(gate.get(DIV_CHAIN_ABSTAIN, 0)),
        "tighten_gate": tighten_gate,
        "loosen_gate": loosen_gate,
        "tighten_final": tighten_final,
        "loosen_final": loosen_final,
        "none_gate": none_gate,
        "caution_gate": caution_gate,
        "laya_absent": int(gate.get(DIV_LAYA_ABSENT, 0)),
        "tighten_cases": tighten_cases,
        "loosen_cases": loosen_cases,
        "laya_latency_ms": percentiles(lat, (0.50, 0.95)),
        "signal_labels": labels,
    }
