"""M1 时区与纪律基础设施测试（评审 4/7 验收）。

覆盖：
1. discipline.py：外汇日 key、周/月周期号（iso_year 陷阱）、until、休息日、时间收敛
   （新 bridge 带偏移 / 旧 bridge naive EET 两种格式）。
2. guardrails 日界：_daily_key/_hourly_key 走 22:00 UTC 外汇日。
3. config：discipline_* / guardrails 去硬编码字段存在且默认值正确。
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.services.discipline import (
    discipline_day_key,
    discipline_local_now,
    discipline_month_key,
    discipline_now,
    discipline_until_day,
    discipline_until_month,
    discipline_until_week,
    discipline_week_key,
    is_rest_day,
    parse_bridge_time_to_naive_utc,
)

# 固定参考时刻（naive UTC）：2026-10-10 10:00 UTC = 外汇日 10-09（22:00 日界前）
T_FIXED = datetime(2026, 10, 10, 10, 0, 0)


class TestDisciplineClock:
    def test_day_key_22h_shift(self):
        # 10-10 10:00 UTC - 22h = 10-09 12:00 → 外汇日 2026-10-09
        assert discipline_day_key(T_FIXED) == "2026-10-09"
        # 10-10 21:59 UTC 仍属 10-09；22:00 整切到 10-10
        assert discipline_day_key(datetime(2026, 10, 10, 21, 59)) == "2026-10-09"
        assert discipline_day_key(datetime(2026, 10, 10, 22, 0)) == "2026-10-10"

    def test_week_key_iso_year_trap(self):
        # 2025-12-30 00:00 UTC - 22h = 2025-12-29 02:00（周一）→ ISO 2026-W01。
        # 若误用 date.year=2025 会得 2025-W01，key 错位一整年。
        w = discipline_week_key(datetime(2025, 12, 30, 0, 0))
        assert w == "2026-W01"
        # 常规周
        assert discipline_week_key(T_FIXED) == "2026-W41"

    def test_month_key(self):
        # 10-10 10:00 UTC - 22h 仍在 10 月
        assert discipline_month_key(T_FIXED) == "2026-10"
        # 11-01 21:59 UTC 属 10 月外汇日；22:00 切 11 月
        assert discipline_month_key(datetime(2026, 11, 1, 21, 59)) == "2026-10"
        assert discipline_month_key(datetime(2026, 11, 1, 22, 0)) == "2026-11"

    def test_until_day_next_22h(self):
        u = discipline_until_day(T_FIXED)
        assert u == datetime(2026, 10, 10, 22, 0)

    def test_until_week_next_monday_22h(self):
        # 2026-10-10 属 W41（周一 10-05 起）→ until = 10-12 22:00 UTC
        u = discipline_until_week(T_FIXED)
        assert u == datetime(2026, 10, 12, 22, 0)

    def test_until_month_next_first_22h(self):
        assert discipline_until_month(T_FIXED) == datetime(2026, 11, 1, 22, 0)
        assert discipline_until_month(datetime(2026, 12, 31, 12, 0)) == datetime(2027, 1, 1, 22, 0)

    def test_rest_day_shanghai_friday(self):
        # 2026-10-09 是周五：上海 18:00（UTC 10:00）应为休息日
        assert is_rest_day(datetime(2026, 10, 9, 18, 0, tzinfo=timezone(timedelta(hours=8))))
        # 2026-10-08 周四：非休息日
        assert not is_rest_day(datetime(2026, 10, 8, 18, 0, tzinfo=timezone(timedelta(hours=8))))
        # 上海周五 00:00 = UTC 周四 16:00 → 本地判断应命中周五
        assert is_rest_day(datetime(2026, 10, 9, 0, 0, tzinfo=timezone(timedelta(hours=8))))


class TestParseBridgeTime:
    def test_new_bridge_aware_offset(self):
        # 新 bridge 输出带 +00:00
        assert parse_bridge_time_to_naive_utc("2026-10-10T10:00:00+00:00") == datetime(2026, 10, 10, 10, 0, 0)

    def test_new_bridge_z_suffix(self):
        assert parse_bridge_time_to_naive_utc("2026-10-10T10:00:00Z") == datetime(2026, 10, 10, 10, 0, 0)

    def test_old_bridge_naive_eet(self):
        # 旧 bridge naive 串（EET/EuropeAthens = UTC+2/+3）→ 转 UTC。
        # 2026-10-10 是夏令时后（EEST UTC+3）：13:00 EET → 10:00 UTC
        assert parse_bridge_time_to_naive_utc("2026-10-10T13:00:00") == datetime(2026, 10, 10, 10, 0, 0)

    def test_old_bridge_naive_winter(self):
        # 2026-01-10 冬令时（EET UTC+2）：12:00 EET → 10:00 UTC
        assert parse_bridge_time_to_naive_utc("2026-01-10T12:00:00") == datetime(2026, 1, 10, 10, 0, 0)

    def test_none_and_invalid(self):
        assert parse_bridge_time_to_naive_utc(None) is None
        assert parse_bridge_time_to_naive_utc("not-a-date") is None
        assert parse_bridge_time_to_naive_utc("") is None


class TestDisciplineConfig:
    def test_settings_exist(self):
        # 注意 conftest._pin_guardrail_defaults 会把 discipline_gate_enabled
        # 置 False（单元测试默认禁用纪律门禁，专项用例再启用）——这里只断言
        # 配置默认值语义（Asia/Shanghai、Europe/Athens 等冻结项）。
        assert settings.discipline_timezone == "Asia/Shanghai"
        assert settings.mt5_server_tz == "Europe/Athens"
        assert hasattr(settings, "discipline_gate_enabled")  # 存在即可，值为 conftest 覆盖
        assert settings.discipline_weekly_loss_limit == 0.07
        assert settings.discipline_monthly_loss_limit == 0.12
        assert settings.discipline_max_trades_per_day_manual == 3
        assert settings.discipline_flip_cooldown_minutes == 30
        assert settings.discipline_mandatory_rest_days == [4]

    def test_guardrails_env_fields(self):
        assert settings.guardrails_max_trades_per_hour == 5
        assert settings.guardrails_min_interval_seconds == 120
        assert settings.guardrails_max_daily_loss == 0.03


class TestGuardrailsDayKey:
    @pytest.mark.asyncio
    async def test_daily_key_uses_discipline_day(self):
        import mcp_server.guardrails as g

        # 固定纪律时钟：2026-10-10 10:00 UTC → 外汇日 10-09
        # 通过 monkeypatch discipline_day_key 验证 _daily_key 走外汇日
        import app.services.discipline as d

        orig = d.discipline_day_key
        d.discipline_day_key = lambda now=None: "2026-10-09"
        try:
            key = g._daily_key("trade_results")
            assert key == "guardrails:trade_results:2026-10-09"
        finally:
            d.discipline_day_key = orig
