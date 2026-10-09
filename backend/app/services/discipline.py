"""交易纪律门禁基础设施（docs/optimization/trading-discipline-enhancement.md 3.0/M1）。

职责：
1. 纪律时钟收敛：所有纪律计数（次数/熔断/冷却/休息日）统一走 discipline_now()。
2. 日/周/月边界（用户已冻结口径，2026-10-10）：
   - 日界 = 22:00 UTC 外汇日（= 上海 06:00）；上海 00:00-06:00 计入前一日。
   - 周/月周期号按外汇日时钟 (utcnow-22h) 生成：周 = isocalendar（iso_year 非
     date.year）、月 = strftime("%Y-%m")。
   - 休息日/展示 = discipline_timezone（默认 Asia/Shanghai）本地时刻。
3. 时间收敛 helper：bridge 时间串（带 +00:00 或旧 naive EET）→ naive UTC，
   供 engine/history/analytics 落库使用（评审 5 F2）。

纪律计数用 UTC 时刻 + 22h 偏移（外汇日），与存储/传输层 naive UTC 一致；
休息日判断用 Asia/Shanghai。禁止散点 datetime.now(UTC)/time.time() 参与纪律计数。
"""

from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

from app.config import settings

# 外汇日偏移：22:00 UTC 为日界切换点
_DISCIPLINE_DAY_SHIFT = _dt.timedelta(hours=22)

_zone_cache: dict[str, ZoneInfo] = {}


def discipline_zone() -> ZoneInfo:
    """discipline_timezone 的 ZoneInfo（缓解析，Asia/Shanghai 无 DST）。"""
    tz = settings.discipline_timezone
    if tz not in _zone_cache:
        _zone_cache[tz] = ZoneInfo(tz)
    return _zone_cache[tz]


def discipline_now() -> _dt.datetime:
    """纪律时钟：当前 UTC 时刻（naive，与 DB 存储口径一致）。"""
    return _dt.datetime.now(_dt.UTC).replace(tzinfo=None)


def discipline_local_now() -> _dt.datetime:
    """纪律时区（默认 Asia/Shanghai）当前本地时刻 —— 休息日判断用。"""
    return _dt.datetime.now(_dt.UTC).astimezone(discipline_zone())


def discipline_day_key(now: _dt.datetime | None = None) -> str:
    """外汇日 key：22:00 UTC 日界下的日期字符串（如 '2026-10-10'）。"""
    now = now or discipline_now()
    return (now - _DISCIPLINE_DAY_SHIFT).strftime("%Y-%m-%d")


def discipline_week_key(now: _dt.datetime | None = None) -> str:
    """外汇周 key：'{iso_year}-W{ww:02d}'（iso_year 非 date.year，2025-12-29→2026-W01）。"""
    now = now or discipline_now()
    iso = (now - _DISCIPLINE_DAY_SHIFT).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def discipline_month_key(now: _dt.datetime | None = None) -> str:
    """外汇月 key：'YYYY-MM'（22:00 UTC 日界下的月份）。"""
    now = now or discipline_now()
    return (now - _DISCIPLINE_DAY_SHIFT).strftime("%Y-%m")


def discipline_until_day(now: _dt.datetime | None = None) -> _dt.datetime:
    """rest-of-period 日熔断 until：下一 22:00 UTC（绝对时刻）。"""
    now = now or discipline_now()
    shifted = now - _DISCIPLINE_DAY_SHIFT
    next_day = (shifted.replace(hour=0, minute=0, second=0, microsecond=0) + _dt.timedelta(days=1))
    return next_day + _DISCIPLINE_DAY_SHIFT


def discipline_until_week(now: _dt.datetime | None = None) -> _dt.datetime:
    """rest-of-period 周熔断 until：下一外汇周起点（= 下周一 22:00 UTC）。

    周期号 key 用 (utcnow-22h).isocalendar() 判周，其周界切换发生在
    (utcnow-22h) 跨过周一 00:00 即 UTC 周一 22:00 —— until 与之对齐。
    """
    now = now or discipline_now()
    shifted = now - _DISCIPLINE_DAY_SHIFT
    # ISO 周从周一开始；(utcnow-22h) 所属 ISO 周的下一周周一
    iso = shifted.isocalendar()
    monday = _dt.date.fromisocalendar(iso[0], iso[1], 1)
    next_monday = monday + _dt.timedelta(days=7)
    return _dt.datetime.combine(next_monday, _dt.time(0, 0)) + _DISCIPLINE_DAY_SHIFT


def discipline_until_month(now: _dt.datetime | None = None) -> _dt.datetime:
    """rest-of-period 月熔断 until：下月 1 日 22:00 UTC。"""
    now = now or discipline_now()
    shifted = now - _DISCIPLINE_DAY_SHIFT
    year, month = shifted.year, shifted.month
    if month == 12:
        next_first = _dt.datetime(year + 1, 1, 1)
    else:
        next_first = _dt.datetime(year, month + 1, 1)
    return next_first + _DISCIPLINE_DAY_SHIFT


def is_rest_day(now_local: _dt.datetime | None = None) -> bool:
    """强制休息日判断：Asia/Shanghai 本地周几 ∈ discipline_mandatory_rest_days。"""
    now_local = now_local or discipline_local_now()
    return now_local.weekday() in settings.discipline_mandatory_rest_days


# ─── 时间收敛 helper（评审 5 F2）：bridge 时间串 → naive UTC ───

def parse_bridge_time_to_naive_utc(iso: str | None) -> _dt.datetime | None:
    """bridge 时间串 → naive UTC datetime（落库用）。

    - 带偏移（+00:00，新 bridge）：astimezone(UTC).replace(tzinfo=None)。
    - naive（旧 bridge，EET 宿主机）：按 mt5_server_tz 转换后取 UTC。
    - None/空 → None（调用方自行兜底）。
    """
    if not iso:
        return None
    raw = iso
    if isinstance(iso, _dt.datetime):
        raw = iso.isoformat()
    try:
        dt = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        # 旧 bridge naive 串：按 MT5 宿主机时区解释 → UTC
        try:
            dt = dt.replace(tzinfo=ZoneInfo(settings.mt5_server_tz))
        except Exception:  # noqa: BLE001 — 时区表缺失时保守按 UTC
            dt = dt.replace(tzinfo=_dt.UTC)
    return dt.astimezone(_dt.UTC).replace(tzinfo=None)
