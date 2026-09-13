"""
Macro data API routes — FRED economic indicators and correlations.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.config import settings
from app.db.session import get_db

router = APIRouter(prefix="/api/macro", tags=["macro"], dependencies=[Depends(require_auth)])

_macro_service = None
_event_calendar = None


def set_macro_deps(macro_service, event_calendar):
    global _macro_service, _event_calendar
    _macro_service = macro_service
    _event_calendar = event_calendar


@router.get("/latest")
async def get_latest_macro(db: AsyncSession = Depends(get_db)):
    if _macro_service is None:
        raise HTTPException(status_code=503, detail="Macro service not initialized")
    from app.data.macro import MacroDataService

    svc = MacroDataService(db)
    return await svc.get_latest_snapshot()


@router.get("/correlations")
async def get_correlations(
    days: int = 90,
    symbol: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """单个品种的宏观相关性。默认取第一个活跃引擎，而非遗留的单品种
    环境变量默认值。"""
    if _macro_service is None:
        raise HTTPException(status_code=503, detail="Macro service not initialized")
    from app.config import get_active_symbols, resolve_canonical_symbol
    from app.data.macro import MacroDataService

    svc = MacroDataService(db)
    canonical = symbol or (get_active_symbols() or [None])[0]
    if not canonical:
        raise HTTPException(status_code=404, detail="No active symbol configured")
    return await svc.compute_correlations(
        resolve_canonical_symbol(canonical), settings.timeframe, days
    )


@router.get("/events")
async def get_upcoming_events(days: int = 7):
    if _event_calendar is None:
        raise HTTPException(status_code=503, detail="Event calendar not initialized")
    return _event_calendar.get_upcoming_events(days)


@router.post("/collect", dependencies=[Depends(require_auth)])
async def collect_macro_data(from_date: str | None = None, to_date: str | None = None):
    if _macro_service is None:
        raise HTTPException(status_code=503, detail="Macro service not initialized")
    if not _macro_service.is_configured:
        return {"error": "FRED API key not configured. Set FRED_API_KEY in environment."}
    stats = await _macro_service.collect_all(from_date, to_date)
    return {"status": "collected", "series": stats}
