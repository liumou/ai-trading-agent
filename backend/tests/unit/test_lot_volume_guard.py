"""订单侧券商手数防线（BotEngine._normalize_lot_to_broker）单元测试。"""

import pytest

from app.bot.engine import BotEngine


def _engine(profile: dict) -> BotEngine:
    """构造引擎时不接触 broker/db —— 只测试手数防线本身。"""
    return BotEngine(
        connector=None,
        db_session=None,
        redis_client=None,
        symbol="TEST",
        symbol_profile=profile,
    )


class TestNormalizeLotToBroker:
    def test_no_volume_data_skips_guard(self):
        """存量行未回填 volume 数据时保持原有行为。"""
        engine = _engine({"pip_value": 1.0})
        assert engine._normalize_lot_to_broker(0.03) == 0.03

    def test_on_grid_lot_unchanged(self):
        engine = _engine({"volume_min": 0.01, "volume_step": 0.01, "volume_max": 100.0})
        assert engine._normalize_lot_to_broker(0.15) == pytest.approx(0.15)

    def test_off_grid_lot_floors_down(self):
        engine = _engine({"volume_min": 0.01, "volume_step": 0.1, "volume_max": 100.0})
        # 0.55 → 向下取整为 0.5（绝不向上 —— 风险不得放大）
        assert engine._normalize_lot_to_broker(0.55) == pytest.approx(0.5)

    def test_below_broker_min_rejected(self):
        engine = _engine({"volume_min": 1.0, "volume_step": 0.1, "volume_max": 100.0})
        # bridge 会把 0.5 静默放大到 1.0（风险 2 倍）：必须拒单。
        assert engine._normalize_lot_to_broker(0.5) is None

    def test_tiny_lot_rounded_to_zero_rejected(self):
        engine = _engine({"volume_min": 0.01, "volume_step": 0.01, "volume_max": 100.0})
        assert engine._normalize_lot_to_broker(0.005) is None

    def test_above_broker_max_capped(self):
        engine = _engine({"volume_min": 0.01, "volume_step": 0.01, "volume_max": 5.0})
        assert engine._normalize_lot_to_broker(7.5) == pytest.approx(5.0)

    def test_step_only_no_min(self):
        engine = _engine({"volume_step": 0.1})
        assert engine._normalize_lot_to_broker(0.29) == pytest.approx(0.2)

    def test_min_only_no_step(self):
        engine = _engine({"volume_min": 0.1})
        assert engine._normalize_lot_to_broker(0.05) is None
        assert engine._normalize_lot_to_broker(0.1) == pytest.approx(0.1)


class TestSharedGridFunction:
    """直接测试 services.symbol_validation.normalize_lot_to_volume_grid ——
    AI/MCP 下单工具现在共用同一套防线。"""

    @pytest.fixture(autouse=True)
    def _import(self):
        from app.services.symbol_validation import normalize_lot_to_volume_grid

        self.fn = normalize_lot_to_volume_grid

    def test_skip_when_no_broker_data(self):
        assert self.fn(0.03) == 0.03

    def test_floor_to_step(self):
        assert self.fn(0.55, volume_min=0.01, volume_step=0.1) == pytest.approx(0.5)

    def test_cap_at_max(self):
        assert self.fn(7.5, volume_min=0.01, volume_step=0.01, volume_max=5.0) == pytest.approx(5.0)

    def test_below_min_returns_none(self):
        assert self.fn(0.5, volume_min=1.0, volume_step=0.1) is None

    def test_above_min_unchanged(self):
        assert self.fn(0.15, volume_min=0.01, volume_step=0.01) == pytest.approx(0.15)
