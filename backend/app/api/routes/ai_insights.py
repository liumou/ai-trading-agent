"""
AI Insights API routes — sentiment and optimization.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.bot import _get_engine
from app.auth import require_auth
from app.cache import cached
from app.constants import BACKTEST_FORMULA_VERSION
from app.db.models import AIOptimizationLog, NewsSentiment
from app.db.session import get_db

router = APIRouter(
    prefix="/api/ai",
    tags=["ai"],
    dependencies=[Depends(require_auth)],
)


@router.get("/sentiment")
async def get_latest_sentiment(symbol: str | None = Query(None)):
    bot = _get_engine(symbol)
    if not bot.sentiment_analyzer:
        return {"label": "neutral", "score": 0, "confidence": 0, "key_factors": [], "source_count": 0}
    sentiment = await bot.sentiment_analyzer.get_latest_sentiment(symbol=bot.symbol)
    return {**sentiment.to_dict(), "symbol": bot.symbol}


@router.get("/sentiment/history")
async def get_sentiment_history(
    request: Request,
    days: int = Query(7, ge=1, le=30),
    db: AsyncSession = Depends(get_db),
):
    async def _fetch():
        cutoff = datetime.utcnow() - timedelta(days=days)
        result = await db.execute(
            select(NewsSentiment)
            .where(NewsSentiment.created_at >= cutoff)
            .order_by(desc(NewsSentiment.created_at))
            .limit(500)
        )
        records = result.scalars().all()
        return {
            "history": [
                {
                    "headline": r.headline,
                    "source": r.source,
                    "sentiment_label": r.sentiment_label,
                    "sentiment_score": r.sentiment_score,
                    "confidence": r.confidence,
                    "created_at": r.created_at.isoformat(),
                }
                for r in records
            ]
        }

    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is None:
        return await _fetch()
    return await cached(redis_client, f"cache:sentiment_history:{days}", 30, _fetch)


@router.get("/optimization/latest")
async def get_latest_optimization(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AIOptimizationLog).order_by(desc(AIOptimizationLog.created_at)).limit(1))
    log = result.scalar_one_or_none()
    if not log:
        return {"message": "No optimization runs yet"}
    import json

    return {
        "id": log.id,
        "period_start": log.period_start.isoformat(),
        "period_end": log.period_end.isoformat(),
        "current_params": json.loads(log.current_params),
        "suggested_params": json.loads(log.suggested_params),
        "rationale": log.rationale,
        "confidence": log.confidence,
        "applied": log.applied,
        "created_at": log.created_at.isoformat(),
    }


@router.get("/context")
async def get_ai_context():
    """Return the current AI context enrichment data."""
    bot = _get_engine()
    context = await bot.context_builder.build_full_context(bot.symbol, bot.timeframe)
    return context


@router.post("/optimization/run", dependencies=[Depends(require_auth)])
async def run_optimization(request: Request):
    bot = _get_engine()
    if not hasattr(bot, "_optimizer") or bot._optimizer is None:
        raise HTTPException(status_code=503, detail="Optimizer not configured")
    if bot.strategy is None:
        raise HTTPException(status_code=400, detail="Cannot optimize in AI Autonomous mode — select a strategy first")
    from app.ai.language import resolve_llm_lang

    result = await bot._optimizer.optimize(
        bot.strategy.get_params(),
        strategy_name=bot.strategy.name,
        symbol=bot.symbol,
        lang=resolve_llm_lang(request),
    )
    if result is None:
        raise HTTPException(status_code=500, detail="Optimization failed")
    return result.to_dict()


@router.post("/optimization/{log_id}/apply", dependencies=[Depends(require_auth)])
async def apply_optimization(log_id: int, db: AsyncSession = Depends(get_db)):
    bot = _get_engine()
    if bot.state.value == "RUNNING":
        raise HTTPException(status_code=400, detail="Stop the bot before applying optimization")

    result = await db.execute(select(AIOptimizationLog).where(AIOptimizationLog.id == log_id))
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Optimization log not found")

    # 回测口径版本门：旧口径（v1，contract_size 换算/品种配置读取缺失）算出的
    # suggested_params 不得在新口径下应用，防止"旧数据新用"。
    if log.backtest_formula_version != BACKTEST_FORMULA_VERSION:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Optimization log v{log.backtest_formula_version} was produced with an older "
                f"backtest formula (current: v{BACKTEST_FORMULA_VERSION}). Params are not "
                f"comparable — re-run optimization before applying."
            ),
        )

    import json

    suggested = json.loads(log.suggested_params)
    if bot.strategy is None:
        raise HTTPException(status_code=400, detail="Cannot apply optimization in AI Autonomous mode")
    await bot.update_strategy(bot.strategy.name, suggested)
    log.applied = True
    await db.commit()

    return {"status": "applied", "params": suggested}
