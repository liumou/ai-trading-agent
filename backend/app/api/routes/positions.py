"""
Positions API routes (multi-symbol).
"""

import asyncio

from fastapi import APIRouter, Depends, Query, Request

from app.api.routes.bot import _get_engine, get_manager
from app.auth import require_auth
from app.services.position_close import close_position_gated

router = APIRouter(prefix="/api/positions", tags=["positions"], dependencies=[Depends(require_auth)])


@router.get("")
async def get_positions(symbol: str | None = Query(None)):
    mgr = get_manager()
    if symbol:
        engine = _get_engine(symbol)
        positions = await engine.executor.get_open_positions(engine.symbol)
        return {"positions": positions}

    # All symbols
    all_positions = []
    tasks = []
    for sym, engine in mgr.engines.items():
        tasks.append(engine.executor.get_open_positions(sym))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, list):
            all_positions.extend(result)
    return {"positions": all_positions}


@router.delete("/{ticket}")
async def close_position(ticket: int, request: Request):
    # 收口：手动平仓必须走闸门序列（switching 门禁 + rollout 拦截 + 记账），
    # 不得直连 executor（历史旁路：零检查平仓）。
    return await close_position_gated(request.app.state.connector, request.app.state.redis, ticket)
