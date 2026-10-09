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

# 引擎自产 UTC 行的 strategy_name（绝不来自 bridge，open_time 为真 UTC，不能换算）。
# 引擎策略单（strategy.name）、部分平仓重开（partial_tp_from_*）、挂单恢复都写这些；
# 挂单恢复的 strategy_name 继承原策略名，同样为 UTC。只对 bridge 来源的行换算。
#
# Review CRITICAL：此前脚本对所有行按 mt5_server_tz 换算，把引擎自产的正确 UTC
# 行（strategy_name=具体策略名 / partial_tp_from_*）偏移 3h —— 修好的数据被改坏。
# 现改为**白名单式只转换 bridge 来源行**：孤儿回填（strategy_name=adopted_from_mt5）
# 的 open_time 来自 bridge（M1 前 EET naive 直接落库），才需要换算。其余一律不动。
_BRIDGE_SOURCE_STRATEGIES = {"adopted_from_mt5", "orphan"}


def _is_bridge_source(row) -> bool:
    """判断 Trade 行是否源自 bridge（时间语义为 MT5 宿主机 EET naive）。

    只有明确 bridge 来源的行（strategy_name=adopted_from_mt5/orphan）才转换；
    引擎策略单 / partial_tp_from_* / 挂单恢复（strategy_name=策略名或继承名）
    的 open_time 是 engine 自产 `_naive_utc()`（正确 UTC），绝不换算。
    无法可靠判别来源的行保守跳过（宁可漏修，不把正确数据改坏；--dry-run 核对）。
    """
    if not row.strategy_name:
        return False
    return str(row.strategy_name) in _BRIDGE_SOURCE_STRATEGIES


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
        skipped = 0  # 非 bridge 来源（引擎自产 UTC 行），不换算
        for row in rows:
            if not _is_bridge_source(row):
                skipped += 1
                continue
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
        f"open_time shifted={changed_open} close_time shifted={changed_close} "
        f"skipped(engine-UTC)={skipped}"
    )
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="回填 trades 表 EET naive 时间为 UTC")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
