"""Laya 影子报表 CLI（Phase 3.3）。

从 manual_shadow_reviews 聚合影子一致率报表（validation.md §1.2 指标集）。

用法（backend/ 下）：
  .venv/bin/python scripts/laya_gate_report.py --limit 500       # 最近 500 条
  .venv/bin/python scripts/laya_gate_report.py --days 7          # 最近 7 天
  .venv/bin/python scripts/laya_gate_report.py --channel sqlite  # SQLite 兜底（无 PG）

验收门槛（validation.md §2，达标才可考虑 Phase 4 enforce）：
  n ≥ 300；一致率 Wilson 下限 ≥ 0.85；致命分歧 0（95% 单侧 ≤1%）；
  兜底率 ≤5%；Kappa ≥ 0.70。
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai.laya_gate_report import build_report


async def _load_shadow_rows(session, limit: int, days: int | None):
    from app.db.models import ManualShadowReview

    stmt = __import__("sqlalchemy").select(ManualShadowReview).order_by(
        ManualShadowReview.id.desc()
    )
    if days is not None:
        since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
        stmt = stmt.where(ManualShadowReview.created_at >= since)
    if limit:
        stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars())


async def main() -> None:
    parser = argparse.ArgumentParser(description="laya 影子一致率报表")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--days", type=int, default=None, help="仅统计最近 N 天")
    parser.add_argument("--channel", choices=["pg", "sqlite"], default="pg")
    args = parser.parse_args()

    from app.db.models import ManualShadowReview  # noqa: F401 - 确保模型注册

    if args.channel == "sqlite":
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        db_path = Path(__file__).resolve().parent.parent / "shadow_reviews.db"
        engine = create_engine(f"sqlite:///{db_path}")
        from app.db.models import Base

        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
    else:
        from app.db.session import async_session

        session = None

    pairs, decisions, laya_lat, llm_lat, answers, llm_vs = [], [], [], [], [], []
    if args.channel == "pg":
        from sqlalchemy import select as sa_select

        async with async_session() as s:
            rows = await _load_shadow_rows(s, args.limit, args.days)
        for r in rows:
            pairs.append((r.laya_verdict or "", r.llm_verdict or ""))
            decisions.append(r.laya_verdict or "")
            laya_lat.append(float(r.laya_latency_ms or 0))
            llm_lat.append(0.0)  # 专表未存 LLM 延迟，跳过
            answers.append(r.laya_answers)
            llm_vs.append(r.llm_verdict or "")
    else:
        rows = (
            session.query(ManualShadowReview)
            .order_by(ManualShadowReview.id.desc())
            .limit(args.limit)
            .all()
        )
        for r in rows:
            pairs.append((r.laya_verdict or "", r.llm_verdict or ""))
            decisions.append(r.laya_verdict or "")
            laya_lat.append(float(r.laya_latency_ms or 0))
            llm_lat.append(0.0)
            answers.append(r.laya_answers)
            llm_vs.append(r.llm_verdict or "")

    if not pairs:
        print("manual_shadow_reviews 无数据（laya_gate_shadow 未开启或尚无 manual 单）")
        return

    report = build_report(pairs, decisions, laya_lat, llm_lat, answers, llm_vs)

    print("=== Laya 影子一致率报表 ===")
    print(f"有效样本 n={report['n']}")
    print(f"原始一致率     : {report['raw_agreement']:.3f} (Wilson 95% 下限 {report['agreement_wilson_lower']:.3f})")
    print(f"Cohen's Kappa  : {report['kappa']:.3f}")
    print(f"致命分歧(APPROVED&llm REJECTED): {report['dangerous_divergence_count']}")
    print(f"误杀(REJECTED&llm APPROVED)    : {report['false_kill_count']}")
    print(f"兜底率(ESCALATE/UNAVAILABLE)   : {report['fallback_rate']:.3f}")
    print(f"laya 延迟 p50/p95: {report['laya_latency_ms']['p50']:.0f}/{report['laya_latency_ms']['p95']:.0f} ms")
    print("\n混淆矩阵 (laya \\ llm):")
    hdr = "laya\\llm  " + "".join(f"{v:>10}" for v in ("APPROVED", "CAUTION", "REJECTED"))
    print(hdr)
    cm = report["confusion_matrix"]
    for a in ("APPROVED", "CAUTION", "REJECTED"):
        print(f"{a:>9} " + "".join(f"{cm[a][b]:>10}" for b in ("APPROVED", "CAUTION", "REJECTED")))

    print("\n验收门槛:")
    checks = [
        ("n ≥ 300", report["n"] >= 300, f"{report['n']}"),
        ("一致率 Wilson 下限 ≥ 0.85", report["agreement_wilson_lower"] >= 0.85,
         f"{report['agreement_wilson_lower']:.3f}"),
        ("致命分歧 = 0", report["dangerous_divergence_count"] == 0,
         f"{report['dangerous_divergence_count']}"),
        ("Kappa ≥ 0.70", report["kappa"] >= 0.70, f"{report['kappa']:.3f}"),
        ("兜底率 ≤ 5%", report["fallback_rate"] <= 0.05, f"{report['fallback_rate']:.3f}"),
    ]
    for name, ok, val in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {val}")
    if all(ok for _, ok, _ in checks):
        print("\n→ 全部达标：可评估进入灰度 vector（shadow → 10% → 50% → 100% enforce）")
    else:
        print("\n→ 未达标：继续影子收集（laya_gate_enforce 保持 False）")

    if "per_question" in report:
        print("\n逐问对齐 (映射到 LLM verdict):")
        for q, info in report["per_question"].items():
            agree = f"{info['mapped_agreement']:.3f}" if info["mapped_agreement"] is not None else "n/a"
            print(f"  {q:>20}: agreement={agree} n={info['n']} dist={info['distribution']}")


if __name__ == "__main__":
    asyncio.run(main())
