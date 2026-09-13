"""Symbol Config API — CRUD + toggle + MT5 validation + ML retrain trigger."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import log_audit
from app.auth import require_auth
from app.db.models import OHLCVData, SymbolConfig
from app.db.session import async_session, get_db
from app.services import symbol_config_service as svc
from app.services.symbol_validation import check_broker_symbol
from app.services.symbol_validation import pip_value_suggestion as _pip_value_suggestion

router = APIRouter(prefix="/api/symbols", tags=["symbols"])


Timeframe = Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
MLStatus = Literal["pending", "training", "ready", "failed"]
# Pattern for the canonical symbol identifier. Allows broker-style names like
# "USDJPYm", "OILCash", "GOLD.fx". The disallowed substring guard below blocks
# ``..`` so the symbol cannot be used to escape a directory when interpolated
# into a model file path (``models/{symbol}_signal.pkl``).
_SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9._-]{2,32}$")


def _validate_symbol_name(symbol: str) -> str:
    if not _SYMBOL_PATTERN.match(symbol):
        raise ValueError("symbol must be alphanumeric (2-32 chars, . _ - allowed)")
    if ".." in symbol or symbol.startswith(".") or symbol.startswith("-"):
        raise ValueError("symbol must not start with '.'/'-' or contain '..'")
    return symbol


# ─── Schemas ──────────────────────────────────────────────────────────────────


class SymbolBase(BaseModel):
    display_name: str = Field(min_length=1, max_length=64)
    broker_alias: str | None = None
    asset_class: str = Field(default="forex", max_length=16)
    default_timeframe: Timeframe = "M15"
    # pip_value / contract_size 为可选项：省略时服务端从 MT5 实时规格回填
    # （以券商为准）。PUT 时传 None 表示"不修改"，而非把已存值清空。
    pip_value: float | None = Field(default=None, gt=0)
    default_lot: float = Field(gt=0)
    max_lot: float = Field(gt=0)
    price_decimals: int = Field(ge=0, le=8, default=2)
    sl_atr_mult: float = Field(gt=0, le=10, default=1.5)
    tp_atr_mult: float = Field(gt=0, le=10, default=2.0)
    contract_size: float | None = Field(default=None, gt=0)
    ml_tp_pips: float = Field(gt=0)
    ml_sl_pips: float = Field(gt=0)
    ml_forward_bars: int = Field(ge=1, le=100, default=10)
    ml_timeframe: Timeframe = "M15"

    @field_validator("asset_class")
    @classmethod
    def _check_asset_class(cls, v: str) -> str:
        from app.market.sessions import supported_asset_classes

        if v.lower() not in supported_asset_classes():
            raise ValueError(f"asset_class must be one of {supported_asset_classes()}; got {v!r}")
        return v.lower()

    @model_validator(mode="after")
    def _check_lot_bounds(self) -> SymbolBase:
        if self.default_lot > self.max_lot:
            raise ValueError("default_lot must be <= max_lot")
        return self

    @model_validator(mode="after")
    def _check_pip_value_sane(self) -> SymbolBase:
        """拒绝与 price_decimals 量级不匹配的 pip_value。

        ML 标注把 `entry ± (tp_pips × pip_value)` 当作 TP/SL 障碍；若 pip_value
        量级严重偏离（例如以"分"计价的品种填了 10.0），每根 K 线都会被标注为
        HOLD，训练将因 "missing classes ['SELL','BUY']" 失败。这里按
        price_decimals 推导出的合理区间做钳制。
        """
        if self.pip_value is None:
            return self
        try:
            _ensure_pip_value_sane(self.pip_value, self.price_decimals)
        except ValueError as e:
            raise ValueError(str(e)) from e
        return self

    @model_validator(mode="after")
    def _check_ml_barrier_sane(self) -> SymbolBase:
        """请求级最低限度的障碍量级校验（无 DB，故只能挡明显录入错误）。

        真正的合理性判定需要该品种的实际波动率，由路由层用近期 OHLCV 的
        ``mean(high-low)`` 完成（见 ``_symbol_mean_bar_range``）；请求级只拦截
        ``pip_value`` 与障碍的乘积荒谬至此的情形。
        """
        if self.pip_value is None:
            return self
        try:
            _ensure_ml_barriers_sane(self.ml_tp_pips, self.ml_sl_pips, self.pip_value)
        except ValueError as e:
            raise ValueError(str(e)) from e
        return self


class SymbolCreateRequest(SymbolBase):
    # 可选：省略时（目录驱动流程）由服务端从券商品种名派生规范名，
    # 剔除规范标识符无法承载的字符（见 _derive_canonical）。
    symbol: str | None = Field(default=None, min_length=2, max_length=32)
    confirm_pip_value: bool = False

    @field_validator("symbol")
    @classmethod
    def _check_symbol(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_symbol_name(v)


class SymbolUpdateRequest(SymbolBase):
    confirm_pip_value: bool = False


class SymbolResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    display_name: str
    broker_alias: str | None
    asset_class: str
    is_enabled: bool
    default_timeframe: str
    pip_value: float
    default_lot: float
    max_lot: float
    price_decimals: int
    sl_atr_mult: float
    tp_atr_mult: float
    contract_size: float
    volume_min: float | None = None
    volume_max: float | None = None
    volume_step: float | None = None
    ml_tp_pips: float
    ml_sl_pips: float
    ml_forward_bars: int
    ml_timeframe: str
    ml_status: str
    ml_last_trained_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None

    @field_serializer("ml_last_trained_at", "created_at", "updated_at")
    def _ser_dt(self, v: datetime | None) -> str | None:
        return v.isoformat() if v else None


class SymbolSpecResponse(BaseModel):
    ok: bool
    message: str
    spec: dict | None = None


# ─── Helpers ──────────────────────────────────────────────────────────────────


async def _require_config(db: AsyncSession, symbol: str) -> SymbolConfig:
    cfg = await svc.get_config(db, symbol)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Symbol '{symbol}' not found")
    return cfg


async def _audit(
    db: AsyncSession,
    request: Request,
    action: str,
    symbol: str,
    detail: dict | None = None,
) -> None:
    await log_audit(
        db,
        action,
        resource=f"symbol:{symbol}",
        detail=detail,
        ip=request.client.host if request.client else None,
        auto_commit=False,
    )


async def _publish(request: Request, symbol: str, action: str) -> None:
    redis_client = getattr(request.app.state, "redis", None)
    if redis_client:
        await svc.publish_reload(redis_client, symbol, action)


async def _reload_engines_direct(request: Request) -> None:
    """直接触发 BotManager.reload_engines。

    Redis pubsub 订阅者未运行或消息丢失（如订阅者重连）时的安全网。
    吞掉异常，使 reload 出问题时 API 请求不会失败 —— pubsub 会重试。
    """
    manager = getattr(request.app.state, "manager", None)
    if manager is None:
        return
    try:
        from app.config import apply_db_symbol_profiles

        async with async_session() as _s:
            db_profiles = await svc.load_profiles_from_db(_s)
        apply_db_symbol_profiles(db_profiles)
        await manager.reload_engines()
    except Exception as e:
        logger.warning(f"Direct engine reload failed (pubsub will retry): {e}")


# 限制并发引导任务数，避免突发 /symbols POST 请求耗尽 DB 连接池。每个任务都要
# 跑数分钟的 ML 重训并在期间占用一个连接；没有这道闸门时，5 个并发新增会吃掉
# 约 20 个连接中的 5 个。
_BOOTSTRAP_SEMAPHORE = asyncio.Semaphore(2)


async def _bootstrap_new_symbol(app_state, symbol: str, timeframe: str, days: int = 90) -> None:
    """为新建品种回填历史 OHLCV 并触发 ML 重训。

    以后台任务运行 —— 调用方的 HTTP 响应立即返回。collector 内部处理缺数据与
    部分拉取的情况；ML 重训任务完成后把 ``ml_status`` 更新为 ``ready`` 或
    ``failed``。

    当 ``app_state`` 上未注册 ``hist_collector`` 时整体跳过 —— 那表示非生产装配
    （测试、部分引导），此时执行回填/重训会与测试自身的断言竞争。

    在花费数分钟回填之前，先把 ``ml_status`` 翻转为 ``'training'`` 抢占该行：
    避免显式 /retrain 已针对同品种启动时仍付出 collector 成本。

    通过 ``_BOOTSTRAP_SEMAPHORE`` 串行化，避免重训任务堆积。
    """
    from datetime import timedelta

    collector = getattr(app_state, "hist_collector", None)
    scheduler = getattr(app_state, "scheduler", None)
    manager = getattr(app_state, "manager", None)

    if collector is None or scheduler is None or manager is None:
        return

    async with _BOOTSTRAP_SEMAPHORE:
        engine = manager.get_engine(symbol)
        if engine is None:
            return

        async with async_session() as session:
            result = await session.execute(
                SymbolConfig.__table__.update()
                .where(SymbolConfig.symbol == symbol, SymbolConfig.ml_status == "pending")
                .values(ml_status="training", updated_at=datetime.utcnow(), updated_by="bootstrap")
            )
            await session.commit()
        if result.rowcount == 0:
            logger.info(f"Bootstrap [{symbol}] skipped — ml_status already advanced")
            return

        now = datetime.utcnow()
        from_date = (now - timedelta(days=days)).strftime("%Y-%m-%d")
        to_date = now.strftime("%Y-%m-%d")
        try:
            stats = await collector.collect(symbol, timeframe, from_date, to_date)
            logger.info(f"Bootstrap seed [{symbol}]: {stats['new_bars_inserted']} new bars")
        except Exception as e:
            logger.warning(f"Bootstrap seed [{symbol}] failed: {e}")

        try:
            await scheduler._ml_retrain_symbol(symbol, engine)
        except Exception as e:
            logger.error(f"Bootstrap retrain [{symbol}] failed: {e}")


_PATH_TO_CLASS: tuple[tuple[str, str], ...] = (
    ("cryptocurrenc", "crypto"),
    ("crypto", "crypto"),
    ("metal", "metal"),
    ("energ", "energy"),
    ("ind", "index"),
    ("share", "stock"),
    ("stock", "stock"),
    ("equit", "stock"),
    ("forex", "forex"),
)


def _infer_asset_class(path: str) -> str:
    """把 MT5 品种路径（如 "Forex\\Majors\\EURUSD"）映射为受支持的资产类别。"""
    if not path:
        return "forex"
    first = path.split("\\")[0].lower()
    for needle, cls in _PATH_TO_CLASS:
        if needle in first:
            return cls
    return "forex"


def _ensure_pip_value_sane(pip_value: float, price_decimals: int) -> None:
    """允许区间：[10^-price_decimals, 10^-(price_decimals-2)]。"""
    max_pip = 10 ** (-(price_decimals - 2)) if price_decimals >= 2 else 100.0
    min_pip = 10 ** (-price_decimals) if price_decimals > 0 else 0.01
    if not (min_pip <= pip_value <= max_pip):
        suggested = 10 ** (-(price_decimals - 1)) if price_decimals >= 1 else 1.0
        raise ValueError(
            f"pip_value={pip_value} is out of range for price_decimals={price_decimals}. "
            f"Expected [{min_pip}, {max_pip}]. Suggested: {suggested}."
        )


# 障碍是否合理，取决于它相对"单根 K 线实际波动"的大小，而不是它的绝对
# 价格数值。实测 BTCUSD H1（mean(high-low)≈521，均价≈88 990）：
#   出厂默认 500 ≈ 0.96× 单根波幅 → 三类分布健康；
#   被改坏的 15 ≈ 0.03× 单根波幅 → 前向窗口必触屏，HOLD 坍缩为 0。
# 旧的"绝对 50 价格单位"上限同时犯两个错：放行了 15，却拒绝了 500。
_BARRIER_RATIO_REJECT = (0.15, 6.0)  # 结构性地产不出三类标签
_BARRIER_RATIO_WARN = (0.3, 3.0)  # 可疑但允许（例如刻意的宽/窄屏障策略）


def _ensure_ml_barriers_sane(
    ml_tp_pips: float,
    ml_sl_pips: float,
    pip_value: float,
    mean_bar_range: float | None = None,
    enforce: bool = True,
) -> None:
    """校验 ML 障碍相对单根 K 线波动是否合理。

    ``mean_bar_range`` 为该品种近期 ``mean(high - low)``（同训练所用 timeframe）：

      - **有行情数据**：按 ``barrier / mean_bar_range`` 判定 —— 这才是与价格量级
        无关的正确尺度。落在 [0.15, 6] 之外结构性地产不出 BUY/SELL/HOLD 三类
        标签（拒绝）；落在 [0.3, 3] 之外仅告警（可疑但允许）。
      - **无行情数据**：退化为宽松的量级校验，只拦截明显的录入错误（如 0 或
        1e6 量级的胖手指），避免数据尚未回填时阻塞品种配置。

    ``enforce=False`` 用于"编辑与本参数无关的字段"的场景：此时历史遗留的坏配置
    只告警、不拒绝，避免操作员被无法一次性修好的旧数据锁死。
    """
    tp_delta = ml_tp_pips * pip_value
    sl_delta = ml_sl_pips * pip_value

    if mean_bar_range and mean_bar_range > 0:
        lo_rej, hi_rej = _BARRIER_RATIO_REJECT
        lo_warn, hi_warn = _BARRIER_RATIO_WARN
        for name, delta in (("ml_tp_pips", tp_delta), ("ml_sl_pips", sl_delta)):
            ratio = delta / mean_bar_range
            if ratio < lo_rej or ratio > hi_rej:
                message = (
                    f"{name} × pip_value = {delta:g} is {ratio:.3g}× the symbol's "
                    f"mean bar range ({mean_bar_range:g}). Barriers this far from typical "
                    f"bar volatility cannot produce a 3-class (BUY/SELL/HOLD) training set. "
                    f"Keep the barrier within roughly [{lo_rej}, {hi_rej}]× mean bar range."
                )
                if enforce:
                    raise ValueError(message)
                logger.warning(f"{message} (unchanged parameter — allowed for now)")
                continue
            if ratio < lo_warn or ratio > hi_warn:
                logger.warning(
                    f"{name} × pip_value = {delta:g} is {ratio:.3g}× mean bar range "
                    f"({mean_bar_range:g}) — outside the recommended [{lo_warn}, {hi_warn}]× "
                    f"band; ML labeling may be skewed."
                )
        return

    # 尚无行情数据 —— 只拦截明显的录入错误。
    for name, delta in (("ml_tp_pips", tp_delta), ("ml_sl_pips", sl_delta)):
        if delta <= 0 or delta > 1_000_000:
            message = (
                f"{name} × pip_value = {delta:g} is implausible. "
                f"Check ml_tp_pips / ml_sl_pips and pip_value."
            )
            if enforce:
                raise ValueError(message)
            logger.warning(f"{message} (unchanged parameter — allowed for now)")


async def _symbol_mean_bar_range(
    db: AsyncSession, symbol: str, timeframe: str, bars: int = 500
) -> float | None:
    """近期 mean(high - low)，用于把 ML 障碍尺度与真实波动率对齐。

    无行情数据时返回 None（调用方据此走宽松校验，不阻塞配置）。
    """
    recent = (
        select((OHLCVData.high - OHLCVData.low).label("rng"))
        .where(OHLCVData.symbol == symbol, OHLCVData.timeframe == timeframe)
        .order_by(OHLCVData.time.desc())
        .limit(bars)
        .subquery()
    )
    value = (await db.execute(select(func.avg(recent.c.rng)))).scalar_one_or_none()
    return float(value) if value is not None else None


_CANONICAL_INVALID_CHARS = re.compile(r"[^A-Za-z0-9]")


def _derive_canonical(broker_symbol: str) -> str:
    """从券商品种名派生规范内部名。

    券商名常携带券商专属字符（"GOLDm#"、"EURUSD.a"、"+GOLD"），不得进入规范
    标识符 —— 因为它会被插值进 ML 模型文件路径（``models/{symbol}_signal.pkl``）。
    只保留字母数字；券商原始名保存在 ``broker_alias`` 中，由 MT5 边界的
    ``to_broker_alias()`` 使用。
    """
    candidate = _CANONICAL_INVALID_CHARS.sub("", broker_symbol).strip("._-")
    try:
        return _validate_symbol_name(candidate)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot derive a valid canonical name from broker symbol "
                f"'{broker_symbol}' (got '{candidate}'). Provide an explicit symbol."
            ),
        ) from None


async def _require_broker_spec(request: Request, broker_symbol: str) -> dict:
    """品种准入路径上的 fail-closed 券商校验。

    返回券商 spec 字典。抛错：
      - 503：bridge/MT5 终端不可达 —— 无法核实的品种不得创建（"不会配错"
              要求先验证，asset_class 交叉校验也不得静默退化为 fail-open）。
      - 502：bridge 有响应但返回体不可用。
      - 400：券商明确报告品种不存在或不可交易。
    """
    connector = getattr(request.app.state, "connector", None)
    check = await check_broker_symbol(connector, broker_symbol)
    if check.kind == "ok":
        assert check.spec is not None
        return check.spec
    if check.kind == "unreachable":
        raise HTTPException(
            status_code=503,
            detail=f"MT5 bridge unavailable while validating '{broker_symbol}': {check.error}",
        )
    if check.kind == "unexpected":
        raise HTTPException(
            status_code=502,
            detail=f"Unexpected bridge response while validating '{broker_symbol}'",
        )
    raise HTTPException(status_code=400, detail=check.error or "symbol rejected by broker")


def _cross_check_asset_class(spec: dict, declared: str) -> None:
    """把声明的 asset_class 与券商品种路径比对。不一致抛 400。"""
    path = spec.get("path") or ""
    if not path:
        return  # 旧版 bridge 无 path 字段 —— 无法交叉校验
    inferred = _infer_asset_class(path)
    if inferred != declared:
        raise HTTPException(
            status_code=400,
            detail=(
                f"asset_class={declared!r} does not match broker path {path!r} "
                f"(inferred {inferred!r}). Use {inferred!r} or pick a different symbol."
            ),
        )


def _check_lot_against_volume(default_lot: float, volume_min: float | None, volume_step: float | None) -> None:
    """拒绝会被券商静默放大的手数。

    bridge 会在按 step 向下取整后用 ``max(vol, volume_min)`` 钳制，因此低于
    volume_min 或偏离 step 网格的计算手数会被静默放大成交，超出风险预算假设
    （volume_min=1.0 的品种可放大 100 倍）。改在准入阶段拦截。
    """
    if volume_min is not None and default_lot < volume_min:
        raise HTTPException(
            status_code=400,
            detail=(
                f"default_lot={default_lot} is below the broker minimum volume "
                f"{volume_min} — the bridge would silently upsize the order and "
                f"exceed the risk budget. Raise default_lot."
            ),
        )
    if volume_step and volume_step > 0:
        rounded = round(round(default_lot / volume_step) * volume_step, 10)
        if abs(rounded - default_lot) > 1e-9:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"default_lot={default_lot} is not a multiple of the broker "
                    f"volume_step={volume_step} (would be rounded to {rounded}). "
                    f"Align default_lot to the step grid."
                ),
            )


async def _ensure_no_alias_collision(
    db: AsyncSession,
    symbol: str,
    broker_alias: str | None,
) -> None:
    """品种与券商别名之间的跨行唯一性。

    load_profiles_from_db() 同时以 symbol 与 broker_alias 为键写入 profile；
    发生撞车会静默覆盖 SYMBOL_PROFILES 中其他行的条目（后写覆盖先写），
    因此在准入阶段拦截。
    """
    if broker_alias:
        if broker_alias != symbol:
            other = await db.execute(
                select(SymbolConfig).where(
                    SymbolConfig.symbol == broker_alias,
                    SymbolConfig.is_deleted.is_(False),
                    SymbolConfig.symbol != symbol,
                )
            )
            if other.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"broker_alias '{broker_alias}' collides with an existing "
                        f"symbol — the alias profile would overwrite it"
                    ),
                )
    other_alias = await db.execute(
        select(SymbolConfig).where(
            SymbolConfig.broker_alias == symbol,
            SymbolConfig.is_deleted.is_(False),
            SymbolConfig.symbol != symbol,
        )
    )
    clash = other_alias.scalar_one_or_none()
    if clash is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"symbol '{symbol}' collides with the broker_alias of existing "
                f"symbol '{clash.symbol}' — the alias profile would overwrite it"
            ),
        )


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.get("", dependencies=[Depends(require_auth)])
async def list_symbols(db: AsyncSession = Depends(get_db)) -> list[SymbolResponse]:
    configs = await svc.list_configs(db)
    return [SymbolResponse.model_validate(c) for c in configs]


@router.get("/broker-catalog", dependencies=[Depends(require_auth)])
async def broker_catalog(request: Request) -> dict:
    """Live XM broker catalog — used by Add Symbol dialog for searchable dropdown + autofill.

    Cached 1h in Redis. Bypasses cache when Redis unavailable.
    """
    connector = getattr(request.app.state, "connector", None)
    if connector is None:
        raise HTTPException(status_code=503, detail="MT5 connector unavailable")

    async def _fetch() -> dict:
        result = await connector.list_symbols()
        if not result.get("success"):
            raise HTTPException(
                status_code=502,
                detail=result.get("error") or "bridge error",
            )
        raw_items = (result.get("data") or {}).get("items", [])
        return {
            "refreshed_at": datetime.utcnow().isoformat(),
            "count": len(raw_items),
            "items": [
                {
                    "symbol": it["symbol"],
                    "path": it.get("path") or "",
                    "description": it.get("description") or "",
                    "asset_class": _infer_asset_class(it.get("path") or ""),
                    "price_decimals": int(it["digits"]),
                    "pip_value": _pip_value_suggestion(
                        _infer_asset_class(it.get("path") or ""),
                        int(it["digits"]),
                        float(it["point"]),
                    ),
                    "contract_size": float(it["trade_contract_size"]),
                    "volume_min": float(it["volume_min"]),
                    "volume_max": float(it["volume_max"]),
                    "volume_step": float(it.get("volume_step") or 0.01),
                    "currency_base": it.get("currency_base") or "",
                    "currency_profit": it.get("currency_profit") or "",
                }
                for it in raw_items
            ],
        }

    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is not None:
        from app.cache import cached

        return await cached(redis_client, "xm:catalog:v1", 3600, _fetch)
    return await _fetch()


@router.get("/{symbol}", dependencies=[Depends(require_auth)])
async def get_symbol(symbol: str, db: AsyncSession = Depends(get_db)) -> SymbolResponse:
    cfg = await _require_config(db, symbol)
    return SymbolResponse.model_validate(cfg)


@router.post("", dependencies=[Depends(require_auth)])
async def create_symbol(
    req: SymbolCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SymbolResponse:
    # ── 准入闸门（fail-closed）：品种在进入系统前必须存在于券商侧且可交易。───
    broker_name = (req.broker_alias or "").strip() or (req.symbol or "").strip()
    if not broker_name:
        raise HTTPException(status_code=422, detail="symbol or broker_alias is required")
    spec = await _require_broker_spec(request, broker_name)

    canonical = (req.symbol or "").strip() or _derive_canonical(broker_name)
    if await svc.get_config(db, canonical):
        raise HTTPException(status_code=409, detail=f"Symbol '{canonical}' already exists")
    await _ensure_no_alias_collision(db, canonical, (req.broker_alias or "").strip() or None)

    declared_class = req.asset_class
    _cross_check_asset_class(spec, declared_class)

    # ── 以 MT5 为准的规格回填 ──────────────────────────────────────────────────
    digits = int(spec.get("digits", req.price_decimals))
    point = float(spec.get("point") or 0.0)
    suggestion = _pip_value_suggestion(declared_class, digits, point) if point else None
    pip_value = req.pip_value
    if pip_value is None:
        pip_value = suggestion
        if pip_value is None:
            raise HTTPException(
                status_code=502,
                detail=f"Bridge spec for '{broker_name}' lacks digits/point — cannot derive pip_value",
            )
    elif suggestion and suggestion > 0:
        ratio = max(pip_value / suggestion, suggestion / pip_value)
        if ratio > 10 and not req.confirm_pip_value:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"pip_value={pip_value} is >10x off the broker-convention "
                    f"suggestion {suggestion} for {declared_class} (digits={digits}, "
                    f"point={point}). Fix pip_value or re-send with "
                    f"confirm_pip_value=true to override."
                ),
            )
    price_decimals = digits
    contract_size = float(spec.get("trade_contract_size") or req.contract_size or 1.0)

    # 用解析后的值做最终合理性检查（请求级校验器只见过原始请求，
    # 无法预知回填后的小数位）。障碍按该品种已有行情的真实波动做相对判定；
    # 新品种通常尚无 OHLCV，此时自动退化为量级校验。
    try:
        _ensure_pip_value_sane(pip_value, price_decimals)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    mean_range = await _symbol_mean_bar_range(db, canonical, req.ml_timeframe)
    try:
        _ensure_ml_barriers_sane(
            req.ml_tp_pips,
            req.ml_sl_pips,
            pip_value,
            mean_bar_range=mean_range,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    _check_lot_against_volume(
        req.default_lot,
        spec.get("volume_min"),
        spec.get("volume_step"),
    )

    values = req.model_dump(exclude={"confirm_pip_value"})
    values.update(
        symbol=canonical,
        pip_value=pip_value,
        price_decimals=price_decimals,
        contract_size=contract_size,
        volume_min=spec.get("volume_min"),
        volume_max=spec.get("volume_max"),
        volume_step=spec.get("volume_step"),
    )

    # 若存在同 symbol 的软删除行则复活而非 INSERT（DB 对 `symbol` 有唯一约束，
    # 之前删除的行还在时，直接 INSERT 会抛 IntegrityError）。
    existing_deleted = await db.execute(
        select(SymbolConfig).where(
            SymbolConfig.symbol == canonical,
            SymbolConfig.is_deleted.is_(True),
        )
    )
    cfg = existing_deleted.scalar_one_or_none()
    action = "symbol_created"
    if cfg is not None:
        for field, value in values.items():
            setattr(cfg, field, value)
        cfg.is_deleted = False
        cfg.is_enabled = False
        cfg.ml_status = "pending"
        cfg.ml_last_trained_at = None
        cfg.updated_at = datetime.utcnow()
        cfg.updated_by = "owner"
        action = "symbol_revived"
    else:
        cfg = SymbolConfig(**values, is_enabled=False, ml_status="pending")
        db.add(cfg)

    await _audit(
        db,
        request,
        action,
        canonical,
        {
            "broker_alias": values.get("broker_alias"),
            "spec_backfill": {
                "contract_size": contract_size,
                "price_decimals": price_decimals,
                "volume_min": spec.get("volume_min"),
                "volume_max": spec.get("volume_max"),
                "volume_step": spec.get("volume_step"),
            },
        },
    )
    await db.commit()
    await db.refresh(cfg)
    await _publish(request, canonical, "created")
    await _reload_engines_direct(request)

    # 触发历史回填 + ML 重训，使新品种在下一根 K 线收盘时即可产出信号，
    # 不必等到周一批量重训。以后台任务运行 —— API 响应立即返回。
    from app.bot.engine import _spawn_background

    _spawn_background(
        _bootstrap_new_symbol(request.app.state, canonical, values["ml_timeframe"]),
        name=f"symbol_bootstrap:{canonical}",
    )

    logger.info(f"Symbol {action}: {canonical}")
    return SymbolResponse.model_validate(cfg)


@router.put("/{symbol}", dependencies=[Depends(require_auth)])
async def update_symbol(
    symbol: str,
    req: SymbolUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SymbolResponse:
    cfg = await _require_config(db, symbol)
    values = req.model_dump(exclude={"confirm_pip_value"})

    new_alias = (values.get("broker_alias") or "").strip() or None
    old_alias = cfg.broker_alias
    effective_broker = new_alias or old_alias or cfg.symbol

    # 规格类字段以券商为准：当它们（或券商别名）发生变更时，重新向 MT5 校验并
    # 回填，使操作员无法事后通过编辑绕过准入时的回填。
    spec_class_touched = new_alias != old_alias or any(
        values.get(f) is not None and values.get(f) != getattr(cfg, f)
        for f in ("pip_value", "contract_size", "price_decimals")
    )
    spec: dict | None = None
    if spec_class_touched:
        spec = await _require_broker_spec(request, effective_broker)
        declared_class = values.get("asset_class") or cfg.asset_class
        _cross_check_asset_class(spec, declared_class)

        digits = int(spec.get("digits", cfg.price_decimals))
        point = float(spec.get("point") or 0.0)
        suggestion = _pip_value_suggestion(declared_class, digits, point) if point else None
        pip_value = values.get("pip_value")
        if pip_value is None:
            # 换品种（别名变更）：采用券商约定建议值；同品种：保留已存值。
            values["pip_value"] = suggestion if new_alias != old_alias else cfg.pip_value
        elif suggestion and suggestion > 0:
            ratio = max(pip_value / suggestion, suggestion / pip_value)
            if ratio > 10 and not req.confirm_pip_value:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"pip_value={pip_value} is >10x off the broker-convention "
                        f"suggestion {suggestion} for {declared_class} (digits={digits}, "
                        f"point={point}). Fix pip_value or re-send with "
                        f"confirm_pip_value=true to override."
                    ),
                )
        values["price_decimals"] = digits
        values["contract_size"] = float(spec.get("trade_contract_size") or cfg.contract_size)

        try:
            _ensure_pip_value_sane(values["pip_value"], digits)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    # 障碍合理性检查必须**无条件**执行（含 spec_class_touched=False 的纯 ML 参数
    # 编辑）：此前它被包在上面分支内，于是"只改 ml_tp_pips / ml_sl_pips"时完全
    # 绕过校验 —— BTCUSD 被写成 ml_tp_pips=15（≈0.03× 单根波幅）致 HOLD 坍缩
    # 正是这条路径。现在按该品种近期真实波动做相对判定；无行情数据时退化为量级校验。
    #
    # 仅当本次请求确实要改动障碍参数时才严格拒绝：否则历史遗留的坏配置会让
    # "编辑 display_name" 之类的无关操作也无端 400，把操作员锁死。
    eff_pip_value = values.get("pip_value") or cfg.pip_value
    eff_tf = values.get("ml_timeframe") or cfg.ml_timeframe
    mean_range = await _symbol_mean_bar_range(db, cfg.symbol, eff_tf)
    barriers_touched = any(
        values.get(f) is not None and values.get(f) != getattr(cfg, f)
        for f in ("ml_tp_pips", "ml_sl_pips", "pip_value")
    )
    try:
        _ensure_ml_barriers_sane(
            values.get("ml_tp_pips") or cfg.ml_tp_pips,
            values.get("ml_sl_pips") or cfg.ml_sl_pips,
            eff_pip_value,
            mean_bar_range=mean_range,
            enforce=barriers_touched,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # 可选字段传 None 表示"不修改"（对券商回填字段采用部分更新语义；
    # 其余字段仍按整表单 PUT）。
    for field, value in values.items():
        if value is None and field in ("pip_value", "contract_size", "broker_alias"):
            continue
        setattr(cfg, field, value)
    if spec is not None:
        cfg.volume_min = spec.get("volume_min")
        cfg.volume_max = spec.get("volume_max")
        cfg.volume_step = spec.get("volume_step")
    cfg.updated_at = datetime.utcnow()
    cfg.updated_by = "owner"

    if new_alias and new_alias != old_alias:
        await _ensure_no_alias_collision(db, cfg.symbol, new_alias)
    if spec is not None:
        _check_lot_against_volume(cfg.default_lot, cfg.volume_min, cfg.volume_step)

    await _audit(db, request, "symbol_updated", symbol)
    await db.commit()
    await db.refresh(cfg)
    await _publish(request, symbol, "updated")
    await _reload_engines_direct(request)

    return SymbolResponse.model_validate(cfg)


@router.delete("/{symbol}", dependencies=[Depends(require_auth)])
async def delete_symbol(
    symbol: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    cfg = await _require_config(db, symbol)
    cfg.is_enabled = False
    cfg.is_deleted = True
    cfg.updated_at = datetime.utcnow()
    cfg.updated_by = "owner"

    await _audit(db, request, "symbol_deleted", symbol)
    await db.commit()
    await _publish(request, symbol, "deleted")
    await _reload_engines_direct(request)

    return {"status": "deleted", "symbol": symbol}


@router.post("/{symbol}/toggle", dependencies=[Depends(require_auth)])
async def toggle_symbol(
    symbol: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SymbolResponse:
    cfg = await _require_config(db, symbol)
    if not cfg.is_enabled:
        # 启用即上线闸门：品种此刻必须存在于券商侧且可交易（自创建以来可能
        # 已被下架或改为 CLOSEONLY）。禁用方向不做拦截。
        broker_name = cfg.broker_alias or cfg.symbol
        spec = await _require_broker_spec(request, broker_name)
        _cross_check_asset_class(spec, cfg.asset_class)
        _check_lot_against_volume(cfg.default_lot, cfg.volume_min, cfg.volume_step)
        # 回填以券商为准的规格字段，使订单侧手数防线生效。存量行的 volume 列
        # 为 NULL；不做回填它们将永远得不到防线保护（create/PUT 均会回填）。
        cfg.contract_size = float(spec.get("trade_contract_size") or cfg.contract_size)
        cfg.price_decimals = int(spec.get("digits", cfg.price_decimals))
        cfg.volume_min = spec.get("volume_min", cfg.volume_min)
        cfg.volume_max = spec.get("volume_max", cfg.volume_max)
        cfg.volume_step = spec.get("volume_step", cfg.volume_step)

    cfg.is_enabled = not cfg.is_enabled
    cfg.updated_at = datetime.utcnow()
    cfg.updated_by = "owner"

    action = "symbol_enabled" if cfg.is_enabled else "symbol_disabled"
    await _audit(db, request, action, symbol)
    await db.commit()
    await db.refresh(cfg)
    await _publish(request, symbol, "toggled")
    await _reload_engines_direct(request)

    logger.info(f"Symbol {symbol} -> enabled={cfg.is_enabled}")
    return SymbolResponse.model_validate(cfg)


@router.post("/{symbol}/validate", dependencies=[Depends(require_auth)])
async def validate_symbol(
    symbol: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SymbolSpecResponse:
    cfg = await svc.get_config(db, symbol)
    alias = cfg.broker_alias if cfg and cfg.broker_alias else symbol
    connector = getattr(request.app.state, "connector", None)
    if connector is None:
        raise HTTPException(status_code=503, detail="MT5 connector unavailable")

    try:
        # 没有这道超时上限时，bridge 挂掉会让手动点击"校验"变成 24 秒以上的
        # 卡死（connector 超时 8s × 3 次尝试）。
        result = await asyncio.wait_for(connector.get_symbol_spec(alias), timeout=10.0)
    except TimeoutError:
        return SymbolSpecResponse(ok=False, message=f"MT5 bridge timeout validating {alias}")
    except Exception as e:
        return SymbolSpecResponse(ok=False, message=f"MT5 bridge error: {e}")
    if not result.get("success"):
        return SymbolSpecResponse(ok=False, message=result.get("error") or "unknown error")
    return SymbolSpecResponse(ok=True, message=f"Validated {alias}", spec=result.get("data"))


@router.post("/{symbol}/retrain", dependencies=[Depends(require_auth)])
async def retrain_symbol(
    symbol: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    cfg = await _require_config(db, symbol)
    if cfg.ml_status == "training":
        raise HTTPException(status_code=409, detail=f"Symbol '{symbol}' already training")

    scheduler = getattr(request.app.state, "scheduler", None)
    manager = getattr(request.app.state, "manager", None)
    engine = manager.get_engine(symbol) if manager else None
    if scheduler is None or engine is None:
        raise HTTPException(status_code=503, detail="Scheduler or engine unavailable")

    cfg.ml_status = "training"
    cfg.updated_at = datetime.utcnow()
    cfg.updated_by = "owner"
    await db.commit()

    task = asyncio.create_task(scheduler._ml_retrain_symbol(symbol, engine))
    _retrain_tasks.add(task)
    task.add_done_callback(_on_retrain_done)

    return {"status": "training", "symbol": symbol}


_retrain_tasks: set[asyncio.Task] = set()


def _on_retrain_done(task: asyncio.Task) -> None:
    _retrain_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(f"retrain task raised: {exc!r}")


@router.get("/{symbol}/ml-status", dependencies=[Depends(require_auth)])
async def get_ml_status(symbol: str, db: AsyncSession = Depends(get_db)) -> dict:
    cfg = await _require_config(db, symbol)
    return {
        "symbol": symbol,
        "status": cfg.ml_status,
        "last_trained_at": cfg.ml_last_trained_at.isoformat() if cfg.ml_last_trained_at else None,
    }
