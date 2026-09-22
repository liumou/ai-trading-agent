"""Laya 6 问交易判定收敛器（Phase 3.0 设计冻结产物，veto-only 收紧层）。

语义（四路评审共识）：
- laya 只收紧、不放松：verdict 只能是 APPROVED / CAUTION / REJECTED / ESCALATE，
  APPROVED 不代表跳过 LLM 深析（调用方负责），REJECTED 须 LLM/确定性规则佐证后终局。
- 判据为 GOLD/M15/MT5 语义（勿照搬 crypto leverage/exposure 判据）。
- 收敛器是**纯函数**：输入 6 问解析结果，输出收敛判定，无 I/O、无状态。

收敛规则优先级（自上而下第一条命中即返回）：
1. 结构畸形（缺问/非法 label/非法 confidence）→ ESCALATE（交 LLM，fail-closed）
2. 任一强制问 label == "insufficient" → ESCALATE（证据不足不做判定）
3. 低置信（confidence < min_confidence）→ ESCALATE
4. 确定性 block（risk/execution/entry）→ REJECTED
5. 方向合取（signal=conflict ∧ regime=adverse）→ REJECTED
6. 全 pass 且高置信 → APPROVED
7. 其余（mixed/neutral/caution 组合）→ CAUTION

data_quality 为纯审计问：不参与收敛判定，但 insufficient 会附加 reasons。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.config import settings

# 6 问候选集（GOLD/M15/MT5 语义白名单）
LAYA_GATE_OPTIONS: Dict[str, set[str]] = {
    "data_quality": {"sufficient", "partial", "insufficient"},
    "signal_alignment": {"aligned", "mixed", "conflict", "insufficient"},
    "market_regime": {"favorable", "neutral", "adverse", "insufficient"},
    "risk_check": {"clear", "caution", "block", "insufficient"},
    "execution_quality": {"clear", "caution", "block", "insufficient"},
    "entry_decision": {"pass", "reject"},
}

# 参与收敛判定的强制问（data_quality 仅审计）
CONVERGENCE_QUESTIONS = (
    "signal_alignment",
    "market_regime",
    "risk_check",
    "execution_quality",
    "entry_decision",
)

VERDICT_APPROVED = "APPROVED"
VERDICT_CAUTION = "CAUTION"
VERDICT_REJECTED = "REJECTED"
VERDICT_ESCALATE = "ESCALATE"


@dataclass(frozen=True)
class LayaGateDecision:
    verdict: str  # APPROVED | CAUTION | REJECTED | ESCALATE
    confidence: Optional[float]  # 聚合置信度（min of 强制问 max-class 概率）；ESCALATE 时为 None
    reasons: list[str] = field(default_factory=list)
    checks: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def _escalate(reason: str) -> LayaGateDecision:
    return LayaGateDecision(verdict=VERDICT_ESCALATE, confidence=None, reasons=[reason])


def converge_laya_verdict(
    answers: Dict[str, Dict[str, Any]],
    *,
    min_confidence: float,
) -> LayaGateDecision:
    """6 问解析结果 → 三值 verdict 建议（纯函数）。

    answers: {question_key: {"label": str, "confidence": float, ...}}
    min_confidence: 本项目 max-class 概率刻度（非熵置信度刻度）。
    """
    checks: Dict[str, Dict[str, Any]] = {}
    confs: list[float] = []
    reasons: list[str] = []

    # 1) 结构畸形 → ESCALATE
    for q in CONVERGENCE_QUESTIONS:
        a = answers.get(q)
        if not isinstance(a, dict):
            return _escalate(f"{q}: missing answer")
        label = a.get("label")
        if label not in LAYA_GATE_OPTIONS[q]:
            return _escalate(f"{q}: invalid label {label!r}")
        conf = a.get("confidence")
        if not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0):
            return _escalate(f"{q}: invalid confidence {conf!r}")
        checks[q] = {"label": label, "confidence": float(conf)}
        confs.append(float(conf))

    # 2) 证据不足 → ESCALATE（fail-closed：不拿不足证据做判定）
    for q in CONVERGENCE_QUESTIONS:
        if checks[q]["label"] == "insufficient":
            return _escalate(f"{q}: insufficient evidence")

    # 3) 低置信 → ESCALATE
    for q in CONVERGENCE_QUESTIONS:
        if checks[q]["confidence"] < min_confidence:
            return _escalate(
                f"{q}: confidence {checks[q]['confidence']:.3f} < {min_confidence}"
            )

    # data_quality 审计问：insufficient 附加 reason（不改变判定）
    dq = answers.get("data_quality")
    if isinstance(dq, dict) and dq.get("label") == "insufficient":
        reasons.append("data_quality: insufficient (audit-only)")

    # 4) 确定性 block → REJECTED
    if checks["risk_check"]["label"] == "block":
        reasons.append("risk_check: block")
        return LayaGateDecision(VERDICT_REJECTED, min(confs), reasons, checks)
    if checks["execution_quality"]["label"] == "block":
        reasons.append("execution_quality: block")
        return LayaGateDecision(VERDICT_REJECTED, min(confs), reasons, checks)
    if checks["entry_decision"]["label"] == "reject":
        reasons.append("entry_decision: reject")
        return LayaGateDecision(VERDICT_REJECTED, min(confs), reasons, checks)

    # 5) 方向合取（双高置信已由步骤 3 保证）→ REJECTED
    if (
        checks["signal_alignment"]["label"] == "conflict"
        and checks["market_regime"]["label"] == "adverse"
    ):
        reasons.append("signal_alignment=conflict ∧ market_regime=adverse")
        return LayaGateDecision(VERDICT_REJECTED, min(confs), reasons, checks)

    # 6) 全 pass 且对齐 → APPROVED（不跳过 LLM，调用方负责）。
    #    要求 signal=aligned 且 regime=favorable——mixed/neutral 视为不完全对齐 → CAUTION。
    if (
        checks["entry_decision"]["label"] == "pass"
        and checks["risk_check"]["label"] == "clear"
        and checks["execution_quality"]["label"] == "clear"
        and checks["signal_alignment"]["label"] == "aligned"
        and checks["market_regime"]["label"] == "favorable"
    ):
        reasons.append("all gates pass")
        return LayaGateDecision(VERDICT_APPROVED, min(confs), reasons, checks)

    # 7) 其余（mixed/neutral/caution）→ CAUTION
    reasons.append("mixed or cautionary signals")
    return LayaGateDecision(VERDICT_CAUTION, min(confs), reasons, checks)


# ─── 6 问定义（GOLD/M15/MT5 语义，送 laya 推理用）───────────────────────────

LAYA_GATE_QUESTIONS: Dict[str, Dict[str, Any]] = {
    "data_quality": {
        "type": "choice",
        "instructions": "Assess whether the supplied point-in-time evidence is sufficient for a manual-order risk decision.",
        "criteria": {
            "sufficient": "market, account, position and recent-trade evidence are current and usable",
            "partial": "some evidence is missing or stale, but concrete risk checks remain possible",
            "insufficient": "the state lacks enough current evidence for a risk judgement",
        },
    },
    "signal_alignment": {
        "type": "choice",
        "instructions": "Compare the requested order direction with the supplied market snapshot and recent trades. Treat missing evidence as insufficient rather than conflict.",
        "criteria": {
            "aligned": "price level, spread context and recent momentum support the requested direction",
            "mixed": "evidence is usable but indicators disagree without a strong contradiction",
            "conflict": "current evidence materially contradicts the requested direction",
            "insufficient": "not enough current market evidence to judge alignment",
        },
    },
    "market_regime": {
        "type": "choice",
        "instructions": "Judge whether the current market conditions are suitable for this manual order entry.",
        "criteria": {
            "favorable": "trend, volatility and volume are reasonably supportive of the entry",
            "neutral": "conditions are mixed or range-bound but do not materially oppose the entry",
            "adverse": "conditions materially oppose the entry or show unstable spread/volatility",
            "insufficient": "market evidence is unavailable or too stale to judge",
        },
    },
    "risk_check": {
        "type": "choice",
        "instructions": "Assess position sizing, exposure, daily PnL, recent losses, stop-loss presence and protection.",
        "criteria": {
            "clear": "new exposure is proportionate and no material account risk is visible",
            "caution": "risk is elevated but within limits and does not require blocking",
            "block": "a concrete sizing, exposure, drawdown, loss-streak or missing-protection risk requires blocking",
            "insufficient": "account evidence is incomplete and no concrete blocking risk can be established",
        },
    },
    "execution_quality": {
        "type": "choice",
        "instructions": "Assess price freshness, spread, order type and execution conditions for this manual order.",
        "criteria": {
            "clear": "order can be submitted with current data and no material execution concern",
            "caution": "execution conditions are imperfect but do not justify blocking",
            "block": "stale price, extreme spread, or another concrete execution issue requires blocking",
            "insufficient": "execution evidence is incomplete and no concrete blocking issue can be established",
        },
    },
    "entry_decision": {
        "type": "choice",
        "instructions": "Make the final pre-trade decision. Reject only for a concrete contradiction, material account risk, or unsafe execution; missing evidence alone must not reject.",
        "criteria": {
            "pass": "entry is supported or mixed, stays within risk limits, and has no concrete blocking condition",
            "reject": "concrete supplied evidence makes this entry directionally contradictory, materially risky, or unsafe",
        },
    },
}


def build_laya_state(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """从 ManualOrderGate 快照组装 laya 推理 state（确定性预计算，评审 H4）。

    复用现有快照字段（order/account/positions/recent_trades/rule_flags/market）；
    OHLCV/regime 特征留待 Phase 4（engine 侧已有 build_features）。
    """
    return {
        "order": snapshot.get("order", {}),
        "account": snapshot.get("account", {}),
        "positions": snapshot.get("positions", [])[:5],
        "recent_trades": snapshot.get("recent_trades", [])[:5],
        "rule_flags": snapshot.get("rule_flags", []),
        "market": snapshot.get("market", {}),
    }


async def laya_gate_review(
    snapshot: Dict[str, Any],
    *,
    timeout: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """ManualGate 影子评审入口（veto-only）：6 问单次前向 + 收敛器。

    返回 None 表示 laya 不可用（调用方跳过影子）；否则返回审计友好 dict：
      {decision, confidence, reasons, checks, answers, engine:"laya", emotional_indicators:"not_assessed_by_laya"}
    畸形/低置信收敛为 ESCALATE（不抛错），由调用方记录。
    """
    from app.ai.laya_runtime import get_laya_runtime

    rt = get_laya_runtime()
    if not rt.available:
        return None
    state = build_laya_state(snapshot)
    answers = await rt.predict_choices(
        state,
        LAYA_GATE_QUESTIONS,
        allowed_labels=LAYA_GATE_OPTIONS,
        timeout=timeout,
    )
    decision = converge_laya_verdict(
        answers, min_confidence=settings.laya_gate_confidence_threshold
    )
    return {
        "decision": decision.verdict,
        "confidence": decision.confidence,
        "reasons": decision.reasons,
        "checks": decision.checks,
        "answers": answers,
        "engine": "laya",
        "emotional_indicators": "not_assessed_by_laya",
    }
