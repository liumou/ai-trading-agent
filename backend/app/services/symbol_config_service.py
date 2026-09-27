"""SymbolConfigService — DB-backed symbol profiles with Redis pub/sub hot-reload."""

from __future__ import annotations

import json
from datetime import datetime

import redis.asyncio as redis
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MT5Account, SymbolConfig

RELOAD_CHANNEL = "symbol_config_changed"

# 与 trades/bot_events 的 account_login 约定一致：'0' = 未知/无活跃账号。
UNKNOWN_ACCOUNT_LOGIN = "0"


def config_to_profile(cfg: SymbolConfig) -> dict:
    """Convert DB row to in-memory profile dict (matches SYMBOL_PROFILES shape)."""
    return {
        "display_name": cfg.display_name,
        "default_timeframe": cfg.default_timeframe,
        "pip_value": cfg.pip_value,
        "default_lot": cfg.default_lot,
        "max_lot": cfg.max_lot,
        "price_decimals": cfg.price_decimals,
        "sl_atr_mult": cfg.sl_atr_mult,
        "tp_atr_mult": cfg.tp_atr_mult,
        "sl_mode": cfg.sl_mode,
        "sl_floor": cfg.sl_floor,
        "sl_cap": cfg.sl_cap,
        "tp_mode": cfg.tp_mode,
        "target_r_multiple": cfg.target_r_multiple,
        "contract_size": cfg.contract_size,
        "volume_min": cfg.volume_min,
        "volume_max": cfg.volume_max,
        "volume_step": cfg.volume_step,
        "ml_tp_pips": cfg.ml_tp_pips,
        "ml_sl_pips": cfg.ml_sl_pips,
        "ml_forward_bars": cfg.ml_forward_bars,
        "ml_timeframe": cfg.ml_timeframe,
        "broker_alias": cfg.broker_alias,
        "asset_class": cfg.asset_class,
        "is_enabled": cfg.is_enabled,
        "ml_status": cfg.ml_status,
    }


async def get_current_account_login(db: AsyncSession) -> str:
    """返回当前活跃 MT5 账号的 login（字符串）；无活跃账号时返回 '0'。

    品种配置按账号隔离的统一解析入口 —— list/get/create 与内存 profiles
    加载都必须经过这里，保证 API 视角与引擎视角看到同一份账号数据。
    """
    result = await db.execute(
        select(MT5Account.login).where(
            MT5Account.is_active.is_(True),
            MT5Account.is_deleted.is_(False),
        )
    )
    login = result.scalar_one_or_none()
    return str(login) if login is not None else UNKNOWN_ACCOUNT_LOGIN


async def list_configs(
    db: AsyncSession,
    include_disabled: bool = True,
    account_login: str | None = None,
) -> list[SymbolConfig]:
    """列出品种配置。account_login 为 None 时解析当前活跃账号。"""
    if account_login is None:
        account_login = await get_current_account_login(db)
    stmt = select(SymbolConfig).where(
        SymbolConfig.account_login == account_login,
        SymbolConfig.is_deleted.is_(False),
    )
    if not include_disabled:
        stmt = stmt.where(SymbolConfig.is_enabled.is_(True))
    result = await db.execute(stmt.order_by(SymbolConfig.symbol))
    return list(result.scalars().all())


async def get_config(
    db: AsyncSession,
    symbol: str,
    account_login: str | None = None,
) -> SymbolConfig | None:
    """按品种名取配置。account_login 为 None 时解析当前活跃账号。"""
    if account_login is None:
        account_login = await get_current_account_login(db)
    result = await db.execute(
        select(SymbolConfig).where(
            SymbolConfig.account_login == account_login,
            SymbolConfig.symbol == symbol,
            SymbolConfig.is_deleted.is_(False),
        )
    )
    return result.scalar_one_or_none()


async def load_profiles_from_db(
    db: AsyncSession,
    account_login: str | None = None,
) -> dict[str, dict]:
    """Load non-deleted configs (当前活跃账号) as profile dict keyed by symbol and broker_alias."""
    configs = await list_configs(db, include_disabled=True, account_login=account_login)
    profiles: dict[str, dict] = {}
    for cfg in configs:
        profile = config_to_profile(cfg)
        profiles[cfg.symbol] = profile
        if cfg.broker_alias and cfg.broker_alias != cfg.symbol:
            alias_profile = profile.copy()
            alias_profile["canonical"] = cfg.symbol
            profiles[cfg.broker_alias] = alias_profile
    return profiles


async def load_profiles_into_memory() -> int:
    """把 DB symbol profiles（当前活跃账号的）加载到进程内存（SYMBOL_PROFILES）。

    返回生效的 profile 条目数；DB 不可用时返回 0 并保持静态默认值。

    必须在**每个**会调用 MT5 Bridge 的进程中执行 —— 包括 backend 主进程、
    MCP server stdio 子进程和 agent runner。别名解析 to_broker_alias() 依赖
    这份内存映射：进程没加载过，别名就全部退化成"原样返回"，行情请求会以
    规范名打到桥上，得到 "No tick/OHLCV data"（而 DB 里其实有数据）。

    账号隔离：只加载当前活跃账号的配置。切换账号后 account_switch 会重新
    调用本函数，旧账号的别名/品种不会泄漏到新账号视角。DB 可达但当前账号
    没有任何配置是正常状态（新账号待配置），不是错误 —— 此时 SYMBOL_PROFILES
    退回静态默认且标记已同步，reload_engines 不再启用静态兜底引擎。
    """
    from app.config import apply_db_symbol_profiles
    from app.db.session import async_session

    try:
        async with async_session() as session:
            account_login = await get_current_account_login(session)
            db_profiles = await load_profiles_from_db(session, account_login=account_login)

        # 空 dict 也要 apply：清掉上一个账号可能残留的别名映射，并置位同步标记。
        apply_db_symbol_profiles(db_profiles)
        enabled = [s for s, p in db_profiles.items() if p.get("is_enabled") and "canonical" not in p]
        if not db_profiles:
            logger.info(
                f"Symbol profiles: no configs for active account {account_login} "
                "(static defaults kept; add symbols via /symbols)"
            )
        else:
            logger.info(
                f"Symbol profiles loaded from DB [account {account_login}]: "
                f"{len(db_profiles)} entries, enabled: {enabled}"
            )
        return len(db_profiles)
    except Exception as e:
        logger.warning(f"Symbol profile DB load failed (using static defaults): {e}")
        return 0


async def publish_reload(redis_client: redis.Redis, symbol: str, action: str) -> None:
    """Publish reload event so BotManager and scheduler refresh engines."""
    try:
        payload = json.dumps(
            {
                "symbol": symbol,
                "action": action,
                "ts": datetime.utcnow().isoformat(),
            }
        )
        await redis_client.publish(RELOAD_CHANNEL, payload)
    except Exception as e:
        logger.warning(f"SymbolConfigService: publish_reload failed: {e}")
