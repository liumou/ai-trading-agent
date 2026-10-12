"""验证修复建议 5.1：exec block 增加置信二次门槛后的行为。

模拟:exec_choice=block 且 exec_conf=0.25（#296 场景）。
- 现状（无门槛）: REJECTED
- 建议（BLOCK_MIN_CONF=0.35）: CAUTION（人在环确认）+ reason=jev_entry_low_confidence
"""
import asyncio

from app.config import settings
from app.services.typesafe_jev import JEV_CHECK_OPTIONS, TypesafeJevProvider

ANSWERS = {
    "data_quality": {"choice": "partial", "confidence": 0.43},
    "signal_alignment": {"choice": "conflict", "confidence": 0.95},
    "market_regime": {"choice": "adverse", "confidence": 0.92},
    "risk_check": {"choice": "clear", "confidence": 0.60},
    "execution_quality": {"choice": "block", "confidence": 0.25},
}


def build_results(provider, answers):
    results = {}
    for name, ans in answers.items():
        options = JEV_CHECK_OPTIONS[name]
        probs = {o: 0.01 for o in sorted(options)}
        probs[ans["choice"]] = 1.0 - 0.01 * (len(options) - 1)
        results[name] = (ans["choice"], probs, ans["confidence"])
    return results


async def main():
    p = TypesafeJevProvider()
    settings.manual_review_typesafe_conf_floor = 0.15
    settings.manual_review_min_confidence = 0.55
    results = build_results(p, ANSWERS)

    # 1) 现状
    verdict, conf, reason, _, _ = p._converge(results)
    print(f"现状 (无 exec block 置信门槛):  verdict={verdict} conf={conf}")
    print(f"  reason={reason[:80]}...")

    # 2) 建议：exec block 且 conf < 0.35 → CAUTION
    exec_conf = results["execution_quality"][2]
    if results["execution_quality"][0] == "block" and exec_conf < 0.35:
        verdict, reason = "CAUTION", "jev_entry_low_confidence"
        print(f"\n建议 (BLOCK_MIN_CONF=0.35):")
        print(f"  exec_conf={exec_conf} < 0.35 → verdict={verdict} reason={reason}")
        print(f"  → 订单进入人工二次确认，而非直接拦截（人在环）")


asyncio.run(main())