"""engine 开仓侧 laya 影子观测报表 CLI（Phase 4）。

从 laya_engine_observations 聚合「laya vs TradeGate+确定性链」分歧描述性统计。
**不设 PASS/FAIL 验收门槛**（validation.md 的 n≥300/Wilson/Kappa 门槛只适用于
ManualGate 影子一致率）；Phase 4 只出统计与分歧案例，供人工复核与收敛决策。

用法（backend/ 下）：
  .venv/bin/python scripts/laya_engine_report.py --limit 500
  .venv/bin/python scripts/laya_engine_report.py --days 14
  .venv/bin/python scripts/laya_engine_report.py --channel sqlite
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai.laya_engine_report import build_engine_report


async def _load_rows(session, limit: int, days: int | None):
    from app.db.models import LayaEngineObservation

    stmt = __import__("sqlalchemy").select(LayaEngineObservation).order_by(
        LayaEngineObservation.id.desc()
    )
    if days is not None:
        since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
        stmt = stmt.where(LayaEngineObservation.created_at >= since)
    if limit:
        stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars())


def _to_rows(rows):
    out = []
    for r in rows:
        out.append({
            "signal_label": r.signal_label,
            "chain_can_trade": r.chain_can_trade,
            "chain_prob": r.chain_prob,
            "allowed": r.allowed,
            "laya_verdict": r.laya_verdict,
            "divergence_gate": r.divergence_gate,
            "divergence_final": r.divergence_final,
            "laya_latency_ms": r.laya_latency_ms,
        })
    return out


async def main() -> None:
    parser = argparse.ArgumentParser(description="laya engine 开仓侧观测分歧报表")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--days", type=int, default=None, help="仅统计最近 N 天")
    parser.add_argument("--channel", choices=["pg", "sqlite"], default="pg")
    args = parser.parse_args()

    from app.db.models import LayaEngineObservation  # noqa: F401 - 确保模型注册

    if args.channel == "sqlite":
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        db_path = Path(__file__).resolve().parent.parent / "engine_observations.db"
        engine = create_engine(f"sqlite:///{db_path}")
        from app.db.models import Base

        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        rows = (
            Session()
            .query(LayaEngineObservation)
            .order_by(LayaEngineObservation.id.desc())
            .limit(args.limit)
            .all()
        )
        data = _to_rows(rows)
    else:
        from app.db.session import async_session

        async with async_session() as s:
            rows = await _load_rows(s, args.limit, args.days)
        data = _to_rows(rows)

    if not data:
        print("laya_engine_observations 无数据（laya_gate_engine_shadow 未开启或尚无开仓许可检查）")
        return

    r = build_engine_report(data)
    n = r["n"]
    print("=== Laya engine 开仓侧分歧观测报表（Phase 4，描述性统计）===")
    print(f"有效样本 n={n}")
    print(f"laya 可得判定   : {r['laya_available']} ({r['laya_available'] / n:.3f})")
    print(f"laya UNAVAILABLE: {r['laya_unavailable']} ({r['laya_unavailable'] / n:.3f})")
    print(f"TradeGate 弃权  : {r['chain_abstain']} ({r['chain_abstain'] / n:.3f})")
    print(f"收紧分歧(gate 口径) : {r['tighten_gate']} ({r['tighten_gate'] / n:.3f})")
    print(f"放松分歧(gate 口径) : {r['loosen_gate']} ({r['loosen_gate'] / n:.3f})")
    print(f"收紧分歧(final 口径): {r['tighten_final']}")
    print(f"放松分歧(final 口径): {r['loosen_final']}")
    print(f"一致(gate 口径)     : {r['none_gate']} ({r['none_gate'] / n:.3f})")
    print(f"CAUTION(gate 口径)  : {r['caution_gate']} ({r['caution_gate'] / n:.3f})")
    print(f"laya 延迟 p50/p95: {r['laya_latency_ms']['p50']:.0f}/{r['laya_latency_ms']['p95']:.0f} ms")

    print("\n收紧分歧案例（前 20，人工复核）:")
    for c in r["tighten_cases"][:20]:
        print(f"  {c['signal_label']} | chain={c['chain']} allowed={c['allowed']} "
              f"prob={c['prob']} laya={c['laya']}")
    print("\n放松分歧案例（前 20，人工复核）:")
    for c in r["loosen_cases"][:20]:
        print(f"  {c['signal_label']} | chain={c['chain']} allowed={c['allowed']} "
              f"prob={c['prob']} laya={c['laya']}")

    print("\n按 signal_label 分组:")
    for label, slot in sorted(r["signal_labels"].items(), key=lambda kv: -kv[1]["total"]):
        print(f"  {label:<20} total={slot['total']:<5} tighten={slot['tighten']:<4} loosen={slot['loosen']}")
    print("\nPhase 4 无 PASS/FAIL 门槛：n/laya 可用率/分歧案例用于收敛决策与人工复核。")


if __name__ == "__main__":
    asyncio.run(main())
