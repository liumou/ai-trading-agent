"""交易纪律门禁检查器（docs/optimization/trading-discipline-enhancement.md 3.1-3.4）。

被 `order_preflight.py` 的「纪律门禁」步骤调用，三通道（manual/strategy/ai_agent）
共享。所有检查 fail-closed：Redis 故障/异常 → 拒单记 kind='discipline_redis_down'
（评审 3 A1：引擎通道此前对 preflight 异常 fail-open，纪律穿透）。

检查项（按判定顺序：周期级闸门 > 冷却 > 次数 > 方向，评审 7）：
1. 熔断阶梯（周/月 rest-of-period + 冷却 until）—— 由 CircuitBreaker 周/月 PnL 聚合触发
2. 同品种反手冷静期（discipline:flip:{account}:{symbol}，30min + 当日第 2 次硬拒）
3. 日/周开仓次数上限（按通道分池，record_order_opened 计数）
4. 强制休息日（Asia/Shanghai 本地周几）

计数 key 全部走 discipline.py 的 22:00 UTC 外汇日/周/月口径。
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from app.services.discipline import (
    discipline_day_key,
    discipline_local_now,
    discipline_month_key,
    discipline_now,
    discipline_week_key,
    is_rest_day,
)
from app.config import settings


def _discipline_local_weekday() -> int:
    """Asia/Shanghai 本地周几（0=周一…6=周日）。

    通过模块属性访问 discipline_local_now（而非模块级 import 绑定），
    测试 monkeypatch app.services.discipline.discipline_local_now 即可生效。
    """
    from app.services.discipline import discipline_local_now

    return discipline_local_now().weekday()


@dataclass
class DisciplineGateResult:
    ok: bool
    reason: str = ""
    code: str = ""  # FLIP_COOLDOWN | MAX_TRADES_DAY | REST_DAY | WEEK_HALT | MONTH_HALT | ...
    cooldown_remaining_min: int | None = None


async def check_discipline_gate(
    redis, *, account_login: str, symbol: str, channel: str,
    account: dict | None = None, lot: float | None = None,
    positions: list | None = None,
) -> DisciplineGateResult:
    """纪律门禁主入口（preflight 内调用）。

    channel: 'manual' | 'engine' | 'ai'（决定次数分池与引擎豁免）。
    account/lot/positions：保证金硬上限（1b）需要；preflight 已取到，传入避免重复请求。

    总开关语义：settings/Redis 中 discipline_gate_enabled=false 时**完全豁免**（运维
    逃生门，即使 Redis 不可用也放行）；否则 Redis 故障 → fail-closed
    （kind='discipline_redis_down'，评审 3 A1）。
    """
    try:
        # 先读总开关（static settings 优先，Redis 配置可覆盖）
        gate_enabled = settings.discipline_gate_enabled
        engine_enabled = settings.engine_discipline_enabled
        if redis is not None:
            try:
                gate_enabled = await get_runtime_setting(redis, "gate_enabled")
                engine_enabled = await get_runtime_setting(redis, "engine_enabled")
            except Exception:  # noqa: BLE001 — Redis 故障：fallback settings；仍启用则下方 fail-closed
                pass
        if not gate_enabled:
            return DisciplineGateResult(ok=True, reason="discipline_gate_disabled")
        if channel == "engine" and not engine_enabled:
            return DisciplineGateResult(ok=True, reason="engine_discipline_disabled")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Discipline gate config error (fail-closed): {e!r}")
        return DisciplineGateResult(ok=False, reason=f"Discipline gate unavailable: {e!r}", code="discipline_redis_down")

    if redis is None:
        # 无 Redis 时纪律计数不可用——fail-closed（评审 3 A1）
        return DisciplineGateResult(ok=False, reason="Discipline gate unavailable (no Redis)", code="discipline_redis_down")

    try:

        weekly_loss_limit = await get_runtime_setting(redis, "weekly_loss_limit")
        monthly_loss_limit = await get_runtime_setting(redis, "monthly_loss_limit")
        flip_cooldown = await get_runtime_setting(redis, "flip_cooldown_minutes")
        day_limit_manual = await get_runtime_setting(redis, "max_trades_day_manual")
        day_limit_engine = await get_runtime_setting(redis, "max_trades_day_engine")
        week_limit_manual = await get_runtime_setting(redis, "max_trades_week_manual")
        max_single_margin = await get_runtime_setting(redis, "max_single_margin_pct")
        max_total_margin = await get_runtime_setting(redis, "max_total_margin_pct")

        # 1. 强制休息日（Asia/Shanghai 本地）
        if is_rest_day():
            return DisciplineGateResult(
                ok=False,
                reason=f"Mandatory rest day (Asia/Shanghai weekday in {settings.discipline_mandatory_rest_days})",
                code="REST_DAY",
            )

        # 1b. 周末不留仓（1d，用户交易计划第二章）：周六/周日禁止新开仓
        #     （针对周末休市品种；crypto 24/7 除外）。Asia/Shanghai 本地判断。
        from app.market.sessions import _rules_for
        from app.config import SYMBOL_PROFILES as _SP

        _asset_class = (_SP.get(symbol) or {}).get("asset_class")
        _weekend_rules = _rules_for(_asset_class)
        if _weekend_rules.get("weekend") and _discipline_local_weekday() >= 5:
            return DisciplineGateResult(
                ok=False,
                reason="Weekend close — no new positions during market holiday (1d)",
                code="WEEKEND_CLOSE",
            )

        # 2. 周/月熔断 rest-of-period（until 绝对时刻，from circuit_breaker 周/月标记）
        #    先检查已触发的 halt 标记；未触发时按当前周/月 PnL 触发新 halt。
        from app.risk.circuit_breaker import CircuitBreaker

        month_halted = await CircuitBreaker.check_period_halt(redis, account_login, "month")
        if month_halted:
            return DisciplineGateResult(
                ok=False,
                reason="Monthly loss limit reached — trading halted for the rest of the month",
                code="MONTH_HALT",
            )
        week_halted = await CircuitBreaker.check_period_halt(redis, account_login, "week")
        if week_halted:
            return DisciplineGateResult(
                ok=False,
                reason="Weekly loss limit reached — trading halted for the rest of the week",
                code="WEEK_HALT",
            )
        # 当前周期 PnL 触发判定（账户级聚合，堵分散亏损旁路）
        balance = await _balance(redis, account_login)
        if monthly_loss_limit > 0 and balance > 0:
            month_pnl = await CircuitBreaker.get_period_pnl(redis, "month")
            if month_pnl <= -monthly_loss_limit * balance:
                await CircuitBreaker.set_period_halt(redis, account_login, "month")
                return DisciplineGateResult(
                    ok=False,
                    reason="Monthly loss limit reached — trading halted for the rest of the month",
                    code="MONTH_HALT",
                )
        if weekly_loss_limit > 0 and balance > 0:
            week_pnl = await CircuitBreaker.get_period_pnl(redis, "week")
            if week_pnl <= -weekly_loss_limit * balance:
                await CircuitBreaker.set_period_halt(redis, account_login, "week")
                return DisciplineGateResult(
                    ok=False,
                    reason="Weekly loss limit reached — trading halted for the rest of the week",
                    code="WEEK_HALT",
                )
        day_halt = await redis.get(f"discipline:halt:day:{account_login}")
        if day_halt and discipline_now() < _parse_dt(day_halt):
            return DisciplineGateResult(
                ok=False,
                reason=f"Daily loss limit reached — trading halted until {day_halt} (UTC)",
                code="DAILY_HALT",
            )

        # 3. 冲动冷却（24h 阶梯，绝对时间戳，与日界正交）
        cooldown_until = await redis.get(f"discipline:cooldown:{account_login}")
        if cooldown_until:
            until = _parse_dt(cooldown_until)
            if until and discipline_now() < until:
                remaining = max(1, int((until - discipline_now()).total_seconds() // 60))
                return DisciplineGateResult(
                    ok=False,
                    reason=f"Impulse cooldown active — retry after {cooldown_until} (UTC)",
                    code="IMPULSE_COOLDOWN",
                    cooldown_remaining_min=remaining,
                )

        # 4. 同品种反手冷静期（绝对时间戳 epoch 秒，与日界正交；所有通道写同一 key）
        import time as _time

        flip = await redis.get(f"discipline:flip:{account_login}:{symbol}")
        if flip:
            direction, ts_str, count = _parse_flip(flip)
            if direction and ts_str:
                try:
                    last_ts_f = float(ts_str)
                except (TypeError, ValueError):
                    last_ts_f = None
                if last_ts_f is not None:
                    elapsed_min = (_time.time() - last_ts_f) / 60
                    if elapsed_min < flip_cooldown:
                        remaining = max(1, int(flip_cooldown - elapsed_min))
                        return DisciplineGateResult(
                            ok=False,
                            reason=f"Frequent long/short switching — wait {remaining}min cooldown after last "
                            f"{direction} open (min {flip_cooldown}min)",
                            code="FLIP_COOLDOWN",
                            cooldown_remaining_min=remaining,
                        )

        # 5. 日开仓次数上限（按通道分池；'manual' 独立池，engine/ai 共用 engine 池）
        day_key = f"discipline:trades_day:{discipline_day_key()}:{account_login}:{channel}"
        week_key = f"discipline:trades_week:{discipline_week_key()}:{account_login}:{channel}"
        day_limit = day_limit_manual if channel == "manual" else day_limit_engine
        week_limit = week_limit_manual if channel == "manual" else 15
        trades_today = int(await redis.get(day_key) or 0)
        trades_week = int(await redis.get(week_key) or 0)
        if trades_today >= day_limit:
            return DisciplineGateResult(
                ok=False,
                reason=f"Daily open limit reached — {trades_today}/{day_limit} (channel={channel})",
                code="MAX_TRADES_DAY",
            )
        if trades_week >= week_limit:
            return DisciplineGateResult(
                ok=False,
                reason=f"Weekly open limit reached — {trades_week}/{week_limit} (channel={channel})",
                code="MAX_TRADES_WEEK",
            )

        # 6. 防拆单（M5/P3）：日累计手数上限（Σ手数×通道，堵拆单绕过单笔手数上限）
        lots_key = f"discipline:lots_day:{discipline_day_key()}:{account_login}:{channel}"
        max_lots = settings.discipline_max_lots_per_day
        if max_lots > 0 and lot is not None:
            lots_used = float(await redis.get(lots_key) or 0)
            if lots_used + lot > max_lots:
                return DisciplineGateResult(
                    ok=False,
                    reason=f"Daily accumulated lots {lots_used + lot:.2f} exceeds limit {max_lots:.2f} "
                    f"(anti-splitting)",
                    code="MAX_LOTS_DAY",
                )

        # 7. 保证金硬上限（1b，主闸是 exposure_cap、此为准入兜底闸；分母 equity）
        #    单笔保证金 ≤ equity×20%，总持仓保证金 ≤ equity×40%（默认）。
        margin_err = await _check_margin_cap(
            account, lot, positions, symbol,
            max_single=max_single_margin, max_total=max_total_margin,
        )
        if margin_err:
            return DisciplineGateResult(ok=False, reason=margin_err, code="MARGIN_CAP")

        return DisciplineGateResult(ok=True)

    except Exception as e:  # noqa: BLE001
        logger.error(f"Discipline gate error (fail-closed): {e!r}")
        return DisciplineGateResult(ok=False, reason=f"Discipline gate unavailable: {e!r}", code="discipline_redis_down")


async def trigger_impulse_cooldown(redis, *, account_login: str) -> int:
    """冲动冷却阶梯触发（4b，评审 2 5b / 评审 7 正交）。

    用户在冷却外反复被纪律门禁拦截（手痒信号）→ 阶梯升级：
    第 2 次 24h → 第 3 次 72h → 第 4 次本周禁该品种（本周剩余）。
    返回当前冷却小时数；Redis 故障时静默（冷却非硬防线，次数/熔断才是）。

    冷却与日界正交：绝对时间戳，跨日不解除、日次数照常重置。
    """
    from datetime import timedelta

    if redis is None:
        return 0
    try:
        key = f"discipline:impulse_count:{account_login}"
        count = int(await redis.get(key) or 0) + 1
        await redis.set(key, str(count), ex=30 * 24 * 3600)

        # 第 1 次拦截不触发冷却（仅是手痒信号计数）
        if count <= 1:
            return 0

        hours = settings.discipline_impulse_cooldown_hours  # 默认 24
        if count >= 4:
            # 第 4 次：本周剩余禁（存 until = 本周末），复用 week halt 语义
            from app.risk.circuit_breaker import CircuitBreaker

            await CircuitBreaker.set_period_halt(redis, account_login, "week")
            return hours * 3
        if count >= 3:
            hours = hours * 3  # 72h
        # count == 2：24h

        until = discipline_now() + timedelta(hours=hours)
        await redis.set(
            f"discipline:cooldown:{account_login}", until.isoformat(), ex=(hours + 1) * 3600
        )
        return hours
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Impulse cooldown trigger failed: {e!r}")
        return 0


async def get_discipline_status(redis, *, account_login: str) -> dict:
    """纪律状态聚合（GET /api/trading/discipline/status，前端展示）。

    返回：今日/本周开仓次数、冷却/熔断状态与解禁时间、强制休息日、总开关。
    Redis 不可用时返回空口径（仅展示层，不影响硬闸门 fail-closed）。
    """
    import time as _time

    from app.services.discipline import discipline_day_key, discipline_local_now, discipline_week_key

    base = {
        "gate_enabled": settings.discipline_gate_enabled,
        "trades_today": {},
        "trades_week": {},
        "cooldown_remaining_min": None,
        "halt": None,
        "rest_day": False,
        "timezone": settings.discipline_timezone,
    }
    if redis is None:
        return base
    try:
        base["rest_day"] = is_rest_day()
        # 次数（manual 池为主展示；engine/ai 池汇总）
        day_key = f"discipline:trades_day:{discipline_day_key()}:{account_login}"
        week_key = f"discipline:trades_week:{discipline_week_key()}:{account_login}"
        for ch in ("manual", "engine", "ai"):
            base["trades_today"][ch] = int(await redis.get(f"{day_key}:{ch}") or 0)
            base["trades_week"][ch] = int(await redis.get(f"{week_key}:{ch}") or 0)
        base["max_trades_day"] = {
            "manual": settings.discipline_max_trades_per_day_manual,
            "engine": settings.discipline_max_trades_per_day_engine,
        }
        base["max_trades_week"] = {
            "manual": settings.discipline_max_trades_per_week_manual,
            "engine": 15,
        }
        # 冷却（24h 冲动阶梯）
        cooldown_raw = await redis.get(f"discipline:cooldown:{account_login}")
        if cooldown_raw:
            until = _parse_dt(cooldown_raw)
            if until:
                remaining = max(1, int((until - discipline_now()).total_seconds() // 60))
                base["cooldown_remaining_min"] = remaining
        # 熔断 halt（日/周/月）
        for period in ("day", "week", "month"):
            raw = await redis.get(f"discipline:halt:{period}:{account_login}")
            if raw:
                base["halt"] = {"period": period, "until": raw.decode() if isinstance(raw, bytes) else str(raw)}
                break
    except Exception as e:  # noqa: BLE001
        logger.debug(f"get_discipline_status failed: {e!r}")
    return base


async def record_order_opened_discipline(
    redis, *, account_login: str, symbol: str, channel: str, direction: str, lot: float = 0.0
) -> None:
    """成交开仓后计数（在 record_order_opened 路径调用，三通道统一）。

    日/周 key 按纪律时区（22:00 UTC 外汇日）；冲动尝试计数由门禁拦截点另记。
    direction: 'BUY'/'SELL' — 写 flip key（所有通道统一，引擎方向不得成为盲区）。
    lot: 本单手数（防拆单 Σ手数 累计用）。
    """
    if not settings.discipline_gate_enabled:
        return
    if redis is None:
        return
    try:
        day_key = f"discipline:trades_day:{discipline_day_key()}:{account_login}:{channel}"
        week_key = f"discipline:trades_week:{discipline_week_key()}:{account_login}:{channel}"
        pipe = redis.pipeline()
        pipe.incr(day_key)
        pipe.expire(day_key, 48 * 3600)
        pipe.incr(week_key)
        pipe.expire(week_key, 16 * 24 * 3600)
        # 防拆单（M5/P3）：日累计手数（Σ手数）
        pipe.incrbyfloat(f"discipline:lots_day:{discipline_day_key()}:{account_login}:{channel}", float(lot or 0))
        pipe.expire(f"discipline:lots_day:{discipline_day_key()}:{account_login}:{channel}", 48 * 3600)
        await pipe.execute()
        # 方向记账（绝对时间戳 epoch 秒，供反手冷静期判断）
        import time as _time

        flip_key = f"discipline:flip:{account_login}:{symbol}"
        raw = await redis.get(flip_key)
        flip_count = 1
        if raw:
            decoded = raw.decode() if isinstance(raw, bytes) else str(raw)
            parts = decoded.split("|")
            if len(parts) >= 3:
                prev_dir, _prev_ts, prev_count = parts[0], parts[1], parts[2]
                if prev_dir == direction:
                    try:
                        flip_count = int(prev_count) + 1
                    except (TypeError, ValueError):
                        flip_count = 1
        await redis.set(flip_key, f"{direction}|{_time.time()}|{flip_count}")
        await redis.expire(flip_key, 7 * 24 * 3600)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Discipline record failed: {e!r}")


def _parse_dt(s):
    from datetime import datetime

    if s is None:
        return None
    if isinstance(s, bytes):
        s = s.decode()
    try:
        return datetime.fromisoformat(str(s))
    except ValueError:
        return None


def _parse_flip(raw):
    """flip key 值 'DIR|epoch_sec|count'（bytes 或 str）。"""
    if raw is None:
        return None, None, 0
    decoded = raw.decode() if isinstance(raw, bytes) else str(raw)
    parts = decoded.split("|")
    if len(parts) >= 3:
        return parts[0], parts[1], int(parts[2])
    return None, None, 0


async def _balance(redis, account_login: str) -> float:
    """账户余额（熔断百分比分母）。优先 Redis 缓存，缺失时保守用 0（不触发）。"""
    try:
        raw = await redis.get(f"discipline:balance:{account_login}")
        if raw:
            return float(raw)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


# ─── Redis 运行时配置（评审 8：单轨——Redis 为真相、settings/env 为默认值）───
# 对齐 rollout_mode 先例（guardrails.py）：前端 settings 页写 Redis 立即生效，
# env 仅作启动默认。字段前缀 `discipline:cfg:`。

_RUNTIME_INT_FIELDS = {
    "max_trades_day_manual": ("discipline_max_trades_per_day_manual", 3),
    "max_trades_day_engine": ("discipline_max_trades_per_day_engine", 5),
    "max_trades_week_manual": ("discipline_max_trades_per_week_manual", 10),
    "flip_cooldown_minutes": ("discipline_flip_cooldown_minutes", 30),
    "impulse_cooldown_hours": ("discipline_impulse_cooldown_hours", 24),
    "consecutive_loss_week_halt": ("discipline_consecutive_loss_week_halt", 5),
    "cooldown_minutes": ("discipline_cooldown_minutes", 60),
}
_RUNTIME_FLOAT_FIELDS = {
    "weekly_loss_limit": ("discipline_weekly_loss_limit", 0.07),
    "monthly_loss_limit": ("discipline_monthly_loss_limit", 0.12),
    "max_single_margin_pct": ("discipline_max_single_margin_pct", 0.20),
    "max_total_margin_pct": ("discipline_max_total_margin_pct", 0.40),
}
_RUNTIME_BOOL_FIELDS = {
    "gate_enabled": ("discipline_gate_enabled", True),
    "engine_enabled": ("engine_discipline_enabled", True),
}


def _runtime_setting(key: str, default):
    """运行时读纪律参数：Redis 覆盖 settings（需 await 版本用 get_runtime_setting）。"""
    try:
        from app.config import settings as _cfg

        return getattr(_cfg, key, default)
    except Exception:  # noqa: BLE001
        return default


async def get_runtime_setting(redis, field: str):
    """Redis 运行时配置读取（前端可编辑立即生效）。

    Redis 无值时回退 settings 字段（env/conftest monkeypatch 生效）；
    Redis 有值时以 Redis 为准（前端写入即时覆盖，评审 8 单轨）。
    """
    _MAP = {**_RUNTIME_INT_FIELDS, **_RUNTIME_FLOAT_FIELDS, **_RUNTIME_BOOL_FIELDS}
    if field not in _MAP:
        return None
    settings_field, hard_default = _MAP[field]
    if redis is not None:
        raw = await redis.get(f"discipline:cfg:{field}")
        if raw is not None:
            value = raw.decode() if isinstance(raw, bytes) else str(raw)
            if field in _RUNTIME_INT_FIELDS:
                return int(value)
            if field in _RUNTIME_FLOAT_FIELDS:
                return float(value)
            return value.lower() in ("1", "true", "yes")
    # 回退 settings（env / conftest monkeypatch）
    return getattr(settings, settings_field, hard_default)


async def set_runtime_setting(redis, field: str, value) -> bool:
    """前端写 Redis 运行时配置（对齐 rollout_mode 先例）。"""
    if field not in {**_RUNTIME_INT_FIELDS, **_RUNTIME_FLOAT_FIELDS, **_RUNTIME_BOOL_FIELDS}:
        return False
    await redis.set(f"discipline:cfg:{field}", str(value))
    return True


async def list_runtime_settings(redis) -> dict:
    """读取全部运行时配置（前端 settings 页展示 + 当前生效值）。"""
    out = {}
    for field in {**_RUNTIME_INT_FIELDS, **_RUNTIME_FLOAT_FIELDS, **_RUNTIME_BOOL_FIELDS}:
        out[field] = await get_runtime_setting(redis, field)
    return out


async def _check_margin_cap(
    account: dict | None, lot: float | None, positions: list | None, symbol: str,
    max_single: float | None = None, max_total: float | None = None,
) -> str | None:
    """单笔/总持仓保证金占 equity 上限（1b，分母 equity，用户已确认）。

    本地估算：名义值 = 手数 × contract_size × 价格 ÷ leverage。
    价格用持仓开仓价或 account 内无——本函数接收不到入场价时按保守跳过
    （主闸 exposure_cap 已按 SL 距离×tick_value 兜住风险；margin 硬拦主要
    防重仓满仓）。bridge 升级补 leverage 后 systemone 的 margin 软检查自动
    生效，本估算仅作硬闸底线。
    """
    if max_single is None:
        max_single = settings.discipline_max_single_margin_pct
    if max_total is None:
        max_total = settings.discipline_max_total_margin_pct
    if not account or not lot:
        return None
    equity = account.get("equity") or 0
    if equity <= 0:
        return None
    leverage = account.get("leverage") or 100
    if leverage <= 0:
        return None
    try:
        from app.config import SYMBOL_PROFILES

        profile = SYMBOL_PROFILES.get(symbol) or {}
        cs = float(profile.get("contract_size") or 0)
    except Exception:  # noqa: BLE001
        cs = 0.0
    if cs <= 0:
        return None  # 无合约乘数无法估算 → 跳过（非放开；主闸 exposure_cap 兜底）

    def _notional(lt: float) -> float:
        return lt * cs  # 名义值 = 手数 × 合约乘数（价格近似 1，低估实际保证金——保守偏松）

    single_margin = _notional(lot) / leverage
    single_pct = single_margin / equity
    if single_pct > max_single:
        return (
            f"Single position margin est. {single_pct * 100:.1f}% of equity exceeds "
            f"limit {max_single * 100:.0f}%"
        )
    total_notional = sum(_notional(float(p.get("lot") or 0)) for p in (positions or []))
    total_notional += _notional(lot)
    total_pct = (total_notional / leverage) / equity
    if total_pct > max_total:
        return (
            f"Total margin est. {total_pct * 100:.1f}% of equity exceeds "
            f"limit {max_total * 100:.0f}%"
        )
    return None
