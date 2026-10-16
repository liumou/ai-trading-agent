"""
Trade history API routes.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.cache import cached
from app.config import get_canonical_symbol
from app.db.models import Trade
from app.db.session import get_db
from app.services.discipline import parse_bridge_time_to_naive_utc


def _deal_matches_symbol(deal: dict, symbol: str | None) -> bool:
    """MT5 成交的 symbol 是券商名（GOLD_），symbol 参数是规范名（GOLD）。

    必须归一后比较，否则桥已经正确返回了 GOLD_ 的成交，却在这里被判为
    "不匹配"而整体丢弃 —— 表现为"明明有成交却查不到/盈亏为 0"。
    """
    if not symbol:
        return True
    return get_canonical_symbol(deal.get("symbol") or "") == symbol


async def _resolve_history_owned(db: AsyncSession) -> set[str]:
    """历史记录可见账号集合 = {当前活跃账号} ∪ {'0' 遗留桶}。

    - 当前活跃账号：唯一 `is_active && !is_deleted` 的 mt5_accounts.login
      （与 symbol_config_service.get_current_account_login 同口径）。
    - '0' 遗留桶：单账号期未标记账号的存量 trades（旧数据 account_login
      默认 '0'，必须保留否则老记录消失）。
    - DB 异常时退化为 {'0'}：不 fail-open（宁可少显示也不跨账号泄漏），
      '0' 桶兜底避免整页空。

    修复背景：此前 DB 查询不过滤账号，切换账号后仍能查到其它账号的 bot 单。
    """
    from sqlalchemy import select as _select

    from app.db.models import MT5Account

    owned: set[str] = {"0"}
    try:
        login = (await db.execute(
            _select(MT5Account.login).where(
                MT5Account.is_active.is_(True),
                MT5Account.is_deleted.is_(False),
            )
        )).scalar_one_or_none()
    except Exception:
        return owned
    if login is not None:
        owned.add(str(login))
    return owned


router = APIRouter(
    prefix="/api/history",
    tags=["history"],
    dependencies=[Depends(require_auth)],
)


@router.get("/trades")
async def get_trades(
    days: int = Query(30, ge=1, le=365),
    strategy: str | None = None,
    trade_type: str | None = None,
    symbol: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    from app.bot.manager import get_global_manager

    _manager = get_global_manager()
    from app.config import resolve_canonical_symbol

    if symbol:
        symbol = resolve_canonical_symbol(symbol)

    cutoff = datetime.utcnow() - timedelta(days=days)

    # 账号隔离：DB 只查当前活跃账号 + '0' 遗留桶（修复跨账号泄漏）
    owned = await _resolve_history_owned(db)

    # 1. Pull from DB (with fallback if new columns not yet migrated)
    query = select(Trade).where(
        Trade.open_time >= cutoff,
        Trade.is_archived.is_(False),
        Trade.account_login.in_(owned),
    )
    if strategy:
        query = query.where(Trade.strategy_name == strategy)
    if trade_type:
        query = query.where(Trade.type == trade_type.upper())
    if symbol:
        query = query.where(Trade.symbol == symbol)
    query = query.order_by(desc(Trade.open_time))

    try:
        result = await db.execute(query)
        db_trades = result.scalars().all()
    except Exception:
        # New columns not yet migrated — rollback and retry without ORM column mapping issue
        await db.rollback()
        from sqlalchemy import text

        owned_list = list(owned)
        raw = await db.execute(
            text(
                "SELECT id, ticket, symbol, type, lot, open_price, close_price, sl, tp, "
                "open_time, close_time, profit, strategy_name, ai_sentiment_score, ai_sentiment_label "
                "FROM trades WHERE open_time >= :cutoff AND account_login IN :owned ORDER BY open_time DESC"
            ),
            {"cutoff": cutoff, "owned": owned_list},
        )
        db_trades = raw.fetchall()

    db_tickets = {getattr(t, "ticket", t[1] if isinstance(t, tuple) else 0) for t in db_trades}

    def _trade_dict(t) -> dict:
        if hasattr(t, "id"):
            return {
                "id": t.id,
                "ticket": t.ticket,
                "symbol": t.symbol,
                "type": t.type,
                "lot": t.lot,
                "open_price": t.open_price,
                "close_price": t.close_price,
                "sl": t.sl,
                "tp": t.tp,
                "open_time": t.open_time.isoformat(),
                "close_time": t.close_time.isoformat() if t.close_time else None,
                "profit": t.profit,
                "strategy_name": t.strategy_name,
                "ai_sentiment_label": getattr(t, "ai_sentiment_label", None),
                "ai_sentiment_score": getattr(t, "ai_sentiment_score", None),
                "trade_reason": getattr(t, "trade_reason", None),
                "pre_trade_snapshot": getattr(t, "pre_trade_snapshot", None),
                "post_trade_analysis": getattr(t, "post_trade_analysis", None),
                "account_login": getattr(t, "account_login", None) or "0",
                "source": "bot",
            }
        else:
            return {
                "id": t[0],
                "ticket": t[1],
                "symbol": t[2],
                "type": t[3],
                "lot": t[4],
                "open_price": t[5],
                "close_price": t[6],
                "sl": t[7],
                "tp": t[8],
                "open_time": t[9].isoformat() if t[9] else None,
                "close_time": t[10].isoformat() if t[10] else None,
                "profit": t[11],
                "strategy_name": t[12],
                "ai_sentiment_score": t[13],
                "ai_sentiment_label": t[14],
                "trade_reason": None,
                "pre_trade_snapshot": None,
                "post_trade_analysis": None,
                "account_login": "0",  # raw SQL fallback 不查该列，兜底账号归属
                "source": "bot",
            }

    rows = [_trade_dict(t) for t in db_trades]

    # 2. Merge MT5 history (catches manual trades not in DB)
    if _manager is not None and not strategy:
        try:
            from app.api.routes.bot import _get_engine

            engine = _get_engine(symbol) if symbol else next(iter(_manager.engines.values()))
            # 手动单归属：bridge 只在当前活跃账号上取历史，故 deal 属于当前账号
            merge_account_login = getattr(engine, "account_login", "0") or "0"
            mt5_result = await engine.connector.get_history(days=days, symbol=symbol or None)
            if mt5_result.get("success"):
                for deal in mt5_result.get("data", []):
                    ticket = deal.get("ticket")
                    if ticket in db_tickets:
                        continue
                    try:
                        deal_time = parse_bridge_time_to_naive_utc(deal["time"]) or datetime.utcnow()
                    except Exception:
                        deal_time = datetime.utcnow()
                    if deal_time < cutoff:
                        continue
                    deal_type = deal.get("type", "").upper()
                    if trade_type and deal_type != trade_type.upper():
                        continue
                    if not _deal_matches_symbol(deal, symbol):
                        continue
                    # Bridge 新字段契约：open_price/open_time/sl/tp/close_time 由
                    # orders+deals 配对产出（Phase 3 重写 /history）。旧版 Bridge
                    # 只有 price/time（平仓语义）—— 字段缺失时回退，避免开仓价
                    # 错误显示成平仓价。
                    # Bridge open_time 已是 ISO 字符串（_iso_utc 输出）；旧版无
                    # open_time 字段时回落 deal_time（datetime）。统一转字符串。
                    open_time = deal.get("open_time") or deal_time
                    if isinstance(open_time, datetime):
                        open_time = open_time.isoformat()
                    rows.append(
                        {
                            "id": None,
                            "ticket": ticket,
                            "symbol": deal.get("symbol", ""),
                            "type": deal_type,
                            "lot": deal.get("lot", 0),
                            # 混合部署期：旧版 Bridge 无 open_price 字段时回落
                            # price（平仓价），与 open_time 回落 deal_time 一致，
                            # 避免开仓价显示 0。
                            "open_price": deal.get("open_price") or deal.get("price", 0),
                            "close_price": deal.get("close_price") or deal.get("price", 0),
                            "sl": deal.get("sl", 0),
                            "tp": deal.get("tp", 0),
                            "open_time": open_time,
                            "close_time": deal_time.isoformat(),
                            "profit": deal.get("net_profit", deal.get("profit", 0)),
                            "strategy_name": "manual",
                            "ai_sentiment_label": None,
                            "ai_sentiment_score": None,
                            "account_login": merge_account_login,
                            "source": "mt5",
                        }
                    )
        except Exception as _merge_err:
            import logging

            logging.getLogger(__name__).warning(f"[HIST-MERGE] {_merge_err!r}")

    # 3. Review-only rows — 复盘记录挂在 (ticket, account_login) 上，但订单
    # 数据本身可能已不可查（MT5 bridge 只回当前活跃账号，切换后原账号的手动
    # 单消失）。这类行以占位形态补出，保证复盘徽章在 /history 里可见。
    rows.extend(await _review_only_rows(
        cutoff, rows, symbol=symbol, trade_type=trade_type, strategy=strategy,
    ))

    # Sort by open_time desc, apply offset/limit
    rows.sort(key=lambda x: x["open_time"], reverse=True)
    paginated = rows[offset : offset + limit]

    return {"trades": paginated, "total": len(rows)}


async def _review_only_rows(
    cutoff: datetime,
    rows: list[dict],
    symbol: str | None = None,
    trade_type: str | None = None,
    strategy: str | None = None,
) -> list[dict]:
    """补出"有复盘、无订单数据"的行（source='review_only'）。

    按 (ticket, account_login) 与 /api/trade-reviews/by-ticket 的查询键一致，
    跨账号都能回看；open_time 用复盘记录的 open_time（与交易时间语义对齐）。
    缺 close_time → 前端按"持仓中"渲染，不进入复盘统计（profit 为 null）。

    复用 /trades 的 symbol / trade_type / strategy 过滤：review_only 行没有真实
    订单数据，trade_type 与 strategy 都未知（strategy_name 是占位符 "review_only"
    而非真实策略名）—— 因此按类型或策略筛选时整类不出现；按品种筛选时用复盘
    记录里的 symbol，空品种行不会出现（不确定的归属不该混进筛选结果）。

    账号边界：只扫用户自己登记的 mt5_accounts 登录号 + '0' 遗留桶。复盘只能为
    这些账号创建（POST 触发端点校验归属），且跨账号回看是本次修复明确支持的
    能力 —— 但"跨账号"不等于"任意账号"，未登记账号的复盘不该出现在结果里。
    顺带把扫描从全表收敛到账号集合，review 行数增长后不会拖慢 /history。

    注意（多用户边界）：当前是单 owner 部署，账号经鉴权的 /api/accounts 登记，
    故"所有已登记账号 + '0'"即合理边界。若未来支持每用户独立账号集，此处的
    owned 集合与 _resolve_owned_account 的注册检查都必须改为按 auth subject 收敛，
    否则跨用户可见成为 IDOR。

    刻意不过滤 is_deleted：复盘挂在 MT5 登录号而非 mt5_accounts.id 上，用户
    "删除后重建同一券商账号"会得到新 id，过滤已删行会让存量复盘变不可见。
    这与启动恢复侧（_restore_active_account 排除软删，避免重新激活废弃账号）
    是有意区分。
    """
    if trade_type or strategy:
        # 订单数据不可查 → 方向与策略都无从判断。此时若补出占位行，它们在
        # 已按该条件过滤的结果里会"看起来属于"该类型/策略，比完全不出现更糟
        # （错误的归属信息，而非无信息）。
        return []
    existing = {(r["ticket"], r.get("account_login") or "0") for r in rows}
    try:
        from sqlalchemy import select as _select

        from app.db.models import MT5Account, TradeReview
        from app.db.session import async_session

        async with async_session() as session:
            # '0' 遗留桶 + 全部登记账号（含已切换走、已禁用、已软删的）
            owned = {"0"} | {
                str(login) for login in (await session.execute(
                    _select(MT5Account.login)
                )).scalars().all()
            }
            reviews = (await session.execute(_select(TradeReview).where(
                TradeReview.open_time >= cutoff,
                TradeReview.account_login.in_(owned),
            ))).scalars().all()
    except Exception as e:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).warning(f"[HIST-REVIEWS] review-only scan failed: {e!r}")
        return []

    extras: list[dict] = []
    for rev in reviews:
        account = rev.account_login or "0"
        if (rev.ticket, account) in existing:
            continue
        if symbol and get_canonical_symbol(rev.symbol or "") != symbol:
            continue
        extras.append({
            "id": None,
            "ticket": rev.ticket,
            "symbol": rev.symbol or "",
            "type": "",
            "lot": 0,
            "open_price": 0,
            "close_price": None,
            "sl": 0,
            "tp": 0,
            "open_time": rev.open_time.isoformat(),
            "close_time": None,
            "profit": None,
            "strategy_name": "review_only",
            "ai_sentiment_label": None,
            "ai_sentiment_score": None,
            "trade_reason": None,
            "pre_trade_snapshot": None,
            "post_trade_analysis": None,
            "account_login": account,
            "source": "review_only",
        })
    return extras


@router.get("/daily-pnl")
async def get_daily_pnl(
    request: Request,
    symbol: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Today's closed trade P&L — merges MT5 live history + DB records."""
    from app.bot.manager import get_global_manager

    _manager = get_global_manager()
    from app.config import resolve_canonical_symbol

    if symbol:
        symbol = resolve_canonical_symbol(symbol)

    async def _fetch():
        return await _fetch_daily_pnl(symbol, db, _manager)

    # 缓存键含账号维度：切换账号后 15s 内不得读到旧账号的当日盈亏缓存
    current_login = str(getattr(_manager, "current_account_login", "0") or "0") if _manager else "0"
    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is None:
        return await _fetch()
    return await cached(
        redis_client,
        f"cache:daily_pnl:{current_login}:{symbol or 'all'}",
        15,
        _fetch,
    )


async def _fetch_daily_pnl(symbol, db, _manager):
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    # 1. Try MT5 live history first (catches all trades — bot + manual)
    mt5_deals: list[dict] = []
    if _manager is not None:
        try:
            from app.api.routes.bot import _get_engine

            engine = _get_engine(symbol) if symbol else next(iter(_manager.engines.values()))
            result = await engine.connector.get_history(days=1, symbol=symbol or None)
            if result.get("success"):
                for deal in result.get("data", []):
                    try:
                        deal_time = parse_bridge_time_to_naive_utc(deal["time"]) or datetime.utcnow()
                    except Exception:
                        continue
                    if deal_time >= today and deal.get("profit") is not None:
                        if not _deal_matches_symbol(deal, symbol):
                            continue
                        mt5_deals.append(deal)
        except Exception:
            pass

    # 2. Also pull from DB (covers paper trades and offline history)
    #    账号隔离：只统计当前活跃账号 + '0' 遗留桶（修复跨账号盈亏混算）
    owned = await _resolve_history_owned(db)
    db_query = select(Trade).where(
        Trade.close_time >= today,
        Trade.profit.isnot(None),
        Trade.is_archived.is_(False),
        Trade.account_login.in_(owned),
    )
    if symbol:
        db_query = db_query.where(Trade.symbol == symbol)
    db_result = await db.execute(db_query)
    db_trades = db_result.scalars().all()

    # 3. Merge: prefer MT5 data; fallback to DB-only records not in MT5
    if mt5_deals:
        mt5_tickets = {d.get("ticket") for d in mt5_deals}
        extra_db = [t for t in db_trades if t.ticket not in mt5_tickets]
        # 净额优先（新 Bridge net_profit = profit+commission+swap，与 DB 引擎
        # 记账语义一致）；旧 Bridge 无 net_profit 回落毛额 profit。
        profits = [d.get("net_profit", d.get("profit", 0)) for d in mt5_deals] + [t.profit for t in extra_db]
    else:
        profits = [t.profit for t in db_trades]

    total = round(sum(profits), 2)
    wins = sum(1 for p in profits if p > 0)
    losses = sum(1 for p in profits if p <= 0)

    return {
        "daily_pnl": total,
        "trade_count": len(profits),
        "wins": wins,
        "losses": losses,
        "source": "mt5" if mt5_deals else "db",
    }


@router.get("/performance")
async def get_performance(
    days: int = Query(30, ge=1, le=365),
    symbol: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    from app.bot.manager import get_global_manager

    _manager = get_global_manager()

    if symbol:
        from app.config import resolve_canonical_symbol

        symbol = resolve_canonical_symbol(symbol)

    cutoff = datetime.utcnow() - timedelta(days=days)
    owned = await _resolve_history_owned(db)
    query = select(Trade).where(
        Trade.open_time >= cutoff,
        Trade.profit.isnot(None),
        Trade.is_archived.is_(False),
        Trade.account_login.in_(owned),
    )
    if symbol:
        query = query.where(Trade.symbol == symbol)
    result = await db.execute(query)
    db_trades = result.scalars().all()

    db_tickets = {t.ticket for t in db_trades}
    profits: list[float] = [t.profit for t in db_trades]

    # Merge MT5 history (catches manual trades not in DB)
    if _manager is not None:
        try:
            from app.api.routes.bot import _get_engine

            engine = _get_engine(symbol) if symbol else next(iter(_manager.engines.values()))
            mt5_result = await engine.connector.get_history(days=days, symbol=symbol or None)
            if mt5_result.get("success"):
                for deal in mt5_result.get("data", []):
                    ticket = deal.get("ticket")
                    if ticket in db_tickets:
                        continue
                    try:
                        deal_time = parse_bridge_time_to_naive_utc(deal["time"]) or datetime.utcnow()
                    except Exception:
                        continue
                    if deal_time < cutoff:
                        continue
                    if not _deal_matches_symbol(deal, symbol):
                        continue
                    deal_profit = deal.get("net_profit", deal.get("profit"))
                    if deal_profit is None:
                        continue
                    profits.append(deal_profit)
        except Exception:
            pass

    if not profits:
        return {"total_trades": 0, "win_rate": 0, "total_profit": 0, "avg_profit": 0}

    wins = [p for p in profits if p > 0]
    total_profit = sum(profits)

    return {
        "total_trades": len(profits),
        "win_rate": round(len(wins) / len(profits), 4),
        "total_profit": round(total_profit, 2),
        "avg_profit": round(total_profit / len(profits), 2),
    }
