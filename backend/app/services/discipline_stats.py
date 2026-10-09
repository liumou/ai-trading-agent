"""复盘统计聚合服务（4c，评审 1/8 重新定位：非激活死代码，新建只读聚合）。

读 OrderAudit(source)/Trade/BotEvent 统计纪律指标：
- 开仓次数（按通道/日/周）
- 方向切换次数（flip key 或 OrderAudit 反手序列）
- 违规次数（TRADE_BLOCKED 事件）
- 胜率 / 最大回撤 / 纪律评分

纪律评分独立存储/纯前端聚合（不污染 OrderAudit.review，评审 3）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from loguru import logger


async def get_discipline_stats(db, *, account_login: str, days: int = 30) -> dict:
    """最近 N 天纪律统计（复盘页 /discipline 用）。

    返回：开仓次数（通道分池）、违规次数、胜率、最大回撤、纪律评分。
    """
    from sqlalchemy import func, select

    from app.db.models import BotEvent, BotEventType, OrderAudit, Trade

    cutoff = datetime.utcnow() - timedelta(days=days)
    out = {
        "days": days,
        "account_login": account_login,
        "opens": {"manual": 0, "engine": 0, "ai_agent": 0, "strategy": 0},
        "blocked_count": 0,
        "wins": 0,
        "losses": 0,
        "max_drawdown_pct": 0.0,
        "discipline_score": 100,
        "violations": [],
    }
    try:
        # 开仓次数：OrderAudit 已执行（EXECUTED/FILLED），按 source 分通道
        rows = (
            await db.execute(
                select(OrderAudit.source, func.count())
                .where(
                    OrderAudit.account_login == account_login,
                    OrderAudit.status.in_(["EXECUTED", "FILLED"]),
                    OrderAudit.created_at >= cutoff,
                )
                .group_by(OrderAudit.source)
            )
        ).all()
        for src, cnt in rows:
            key = src if src in out["opens"] else "strategy"
            out["opens"][key] = int(cnt)

        # 违规：TRADE_BLOCKED 事件（M2 起带 account_login）
        blocked_rows = (
            await db.execute(
                select(BotEvent.message)
                .where(
                    BotEvent.account_login == account_login,
                    BotEvent.event_type == BotEventType.TRADE_BLOCKED,
                    BotEvent.created_at >= cutoff,
                )
                .order_by(BotEvent.created_at.desc())
                .limit(20)
            )
        ).all()
        out["blocked_count"] = len(blocked_rows)
        out["violations"] = [m for (m,) in blocked_rows]

        # 胜率 / 最大回撤：Trade 表
        trades = (
            await db.execute(
                select(Trade.profit)
                .where(
                    Trade.account_login == account_login,
                    Trade.close_time >= cutoff,
                )
            )
        ).all()
        profits = [p for (p,) in trades if p is not None]
        if profits:
            out["wins"] = sum(1 for p in profits if p > 0)
            out["losses"] = sum(1 for p in profits if p <= 0)
            peak = 0.0
            equity = 0.0
            max_dd = 0.0
            for p in profits:
                equity += p
                peak = max(peak, equity)
                if peak > 0:
                    max_dd = max(max_dd, (peak - equity) / peak)
            out["max_drawdown_pct"] = round(max_dd * 100, 2)

        # 纪律评分：100 - 违规分 - 高频开仓分 - 连亏分（简单启发式）
        total_opens = sum(out["opens"].values())
        score = 100.0
        score -= min(out["blocked_count"] * 3, 30)  # 违规：每次 3 分
        if total_opens > days * 3:  # 超日 3 笔基准
            score -= min((total_opens - days * 3) * 1, 15)
        if out["wins"] + out["losses"] > 0:
            win_rate = out["wins"] / (out["wins"] + out["losses"])
            if win_rate < 0.3:
                score -= 10
        out["discipline_score"] = max(0, round(score, 1))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Discipline stats failed: {e!r}")
    return out
