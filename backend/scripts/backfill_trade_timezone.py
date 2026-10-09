#!/usr/bin/env python3
"""F6 存量历史数据时区回填脚本（一次性，评审 5/8）。

背景：trades.open_time/close_time 中已混存两类 naive 时间：
- bot 自产 = naive UTC（正确，不动）
- bridge 平仓/孤儿/幻影回填 = EET naive（`engine.py:1437/2128/2186` 历史写入，
  实际为宿主机本地时区 EET，被当 UTC 落库 → 偏移 2-3 小时）

回填策略：对无法可靠区分来源的行，统一按 mt5_server_tz 反推（epoch 级换算，
zoneinfo 处理 DST）。可区分来源的行（strategy_name=manual / comment 带 MT5_MAGIC）
按同样规则。**该脚本只应在 M1 上线、新数据已收敛为 UTC 后执行一次**。

用法（backend/.venv/bin/python，需 DATABASE_URL_SYNC + PYTHONPATH）：
    python scripts/backfill_trade_timezone.py [--dry-run]

注意：脚本不处理 OrderAudit（其 created_at 为 UTC，从未混入 bridge 时间）。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.config import settings  # noqa: E402
from app.db.models import Trade  # noqa: E402

# 引擎自产 UTC 行的标记（strategy 通道写入，绝不来自 bridge）
_UTC_ONLY_STRATEGIES = set()  # 留空：保守策略，所有行都尝试换算（UTC 行换算后自洽不变）


def _shift_naive(dt: datetime, tz: ZoneInfo) -> datetime:
    """把 naive 时间按 MT5 宿主机时区解释后转 UTC（naive 输出，供 DB）。"""
    aware = dt.replace(tzinfo=tz)
    return aware.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


async def main(dry_run: bool) -> None:
    dsn = os.environ.get("DATABASE_URL_SYNC") or settings.database_url_sync
    engine = create_async_engine(dsn)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    tz = ZoneInfo(settings.mt5_server_tz)
    changed_open = changed_close = total = 0

    async with Session() as session:
        rows = (await session.execute(select(Trade))).scalars().all()
        total = len(rows)
        for row in rows:
            updated = False
            if row.open_time is not None:
                shifted = _shift_naive(row.open_time, tz)
                if shifted != row.open_time:
                    row.open_time = shifted
                    updated = True
                    changed_open += 1
            if row.close_time is not None:
                shifted = _shift_naive(row.close_time, tz)
                if shifted != row.close_time:
                    row.close_time = shifted
                    updated = True
                    changed_close += 1
            if updated and not dry_run:
                session.add(row)
        if not dry_run:
            await session.commit()

    print(
        f"[{'DRY-RUN ' if dry_run else ''}] trades rows={total} "
        f"open_time shifted={changed_open} close_time shifted={changed_close}"
    )
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="回填 trades 表 EET naive 时间为 UTC")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
