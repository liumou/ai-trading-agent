"""复现 #296 的 _converge 决策：验证 floor=0.15 vs 0.30 下 exec block 的不同结果。

模拟 #296 的 JEV 应答（checks 全通过 floor 校验）：
- data_quality=partial (0.43)
- signal_alignment=conflict (0.95)
- market_regime=adverse (0.92)
- risk_check=clear (0.60)
- execution_quality=block (0.25)
"""
import asyncio

from app.config import settings
from app.services.typesafe_jev import TypesafeJevProvider

# #296 的精确应答
ANSWERS = {
    "data_quality": {"choice": "partial", "confidence": 0.43},
    "signal_alignment": {"choice": "conflict", "confidence": 0.95},
    "market_regime": {"choice": "adverse", "confidence": 0.92},
    "risk_check": {"choice": "clear", "confidence": 0.60},
    "execution_quality": {"choice": "block", "confidence": 0.25},
}


def build_results(provider, answers):
    """构造 _validate 期望的 results 结构。"""
    results = {}
    for name, ans in answers.items():
        options = __import__("app.services.typesafe_jev", fromlist=["JEV_CHECK_OPTIONS"]).JEV_CHECK_OPTIONS[name]
        probs = {o: 0.01 for o in sorted(options)}
        probs[ans["choice"]] = 1.0 - 0.01 * (len(options) - 1)
        results[name] = (ans["choice"], probs, ans["confidence"])
    return results


async def main():
    p = TypesafeJevProvider()
    results = build_results(p, ANSWERS)

    for floor in (0.30, 0.15):
        settings.manual_review_typesafe_conf_floor = floor
        settings.manual_review_min_confidence = 0.55
        print(f"\n=== conf_floor={floor} ===")
        try:
            verdict, confidence, reason, checks, converge = p._converge(results)
            print(f"verdict={verdict} confidence={confidence}")
            print(f"reason={reason}")
        except ValueError as e:
            print(f"ProviderUnavailable（降级链）: {e}")


asyncio.run(main())