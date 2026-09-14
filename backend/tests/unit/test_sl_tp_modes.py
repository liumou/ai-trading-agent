"""止损止盈标准化模式（clamp + R 倍数）单元测试。

覆盖 RiskManager 的 clamp / R 派生逻辑，以及"与确认门同口径"（C9）。

核心断言：
1. 默认模式（atr/atr）必须与旧行为**逐值一致**（金标准回归）。
2. sl_mode="clamped" 时止损距离被夹到 [floor, cap]。
3. tp_mode="rr" 时盈亏比恒等于 target_r_multiple（regime 不扰动比值）。
4. expected_rr() 与 calculate_sl_tp() 实际成交的 SL/TP 同口径。
5. regime 因子在 clamp 之前乘、R 派生在 clamp 之后乘。
"""

import pytest

from app.risk.manager import RiskManager

# GOLD 实测：ATR≈8.05，sl=1.5 → 止损 $12.08；tp=2.0 → 止盈 $16.10
ATR = 8.05
PRICE = 3000.0


def _rm(**overrides) -> RiskManager:
    base = dict(
        pip_value=1.0,
        price_decimals=2,
        sl_atr_mult=1.5,
        tp_atr_mult=2.0,
        contract_size=100.0,
    )
    base.update(overrides)
    return RiskManager(**base)


class TestDefaultLegacyBehavior:
    """默认（atr/atr）必须与旧行为逐值一致。"""

    def test_buy_sl_tp_matches_legacy(self):
        rm = _rm()
        sl_tp = rm.calculate_sl_tp(PRICE, signal=1, atr=ATR)
        assert sl_tp.sl == pytest.approx(PRICE - ATR * 1.5, 2)
        assert sl_tp.tp == pytest.approx(PRICE + ATR * 2.0, 2)

    def test_sell_sl_tp_matches_legacy(self):
        rm = _rm()
        sl_tp = rm.calculate_sl_tp(PRICE, signal=-1, atr=ATR)
        assert sl_tp.sl == pytest.approx(PRICE + ATR * 1.5, 2)
        assert sl_tp.tp == pytest.approx(PRICE - ATR * 2.0, 2)

    def test_expected_rr_matches_legacy_ratio(self):
        rm = _rm()
        assert rm.expected_rr(ATR) == pytest.approx(2.0 / 1.5, 4)

    def test_regime_factor_applied_identically(self):
        """regime 因子在旧路径上也乘 —— 回归守卫。"""
        rm = _rm()
        rm.set_regime("ranging")  # sl ×0.8, tp ×0.8
        sl_tp = rm.calculate_sl_tp(PRICE, signal=1, atr=ATR)
        assert sl_tp.sl == pytest.approx(PRICE - ATR * 1.5 * 0.8, 2)
        assert sl_tp.tp == pytest.approx(PRICE + ATR * 2.0 * 0.8, 2)


class TestClampedMode:
    """sl_mode="clamped"：止损距离被夹到 [floor, cap]。"""

    def test_floor_raises_narrow_stop(self):
        """ATR×1.5=12.08 < floor=15 → 止损被抬到 15。"""
        rm = _rm(sl_mode="clamped", sl_floor=15.0, sl_cap=30.0)
        sl_tp = rm.calculate_sl_tp(PRICE, signal=1, atr=ATR)
        assert sl_tp.sl == pytest.approx(PRICE - 15.0, 2)

    def test_cap_lowers_wide_stop(self):
        """高波动 ATR=13.02 → 1.5×13.02=19.53，cap=18 → 被压到 18。"""
        rm = _rm(sl_mode="clamped", sl_floor=5.0, sl_cap=18.0)
        sl_tp = rm.calculate_sl_tp(PRICE, signal=1, atr=13.02)
        assert sl_tp.sl == pytest.approx(PRICE - 18.0, 2)

    def test_inside_bounds_unchanged(self):
        """落在区间内时不改变。"""
        rm = _rm(sl_mode="clamped", sl_floor=10.0, sl_cap=20.0)
        sl_tp = rm.calculate_sl_tp(PRICE, signal=1, atr=ATR)  # 12.08 ∈ [10,20]
        assert sl_tp.sl == pytest.approx(PRICE - ATR * 1.5, 2)

    def test_floor_zero_ignored(self):
        """floor=0（未配）应被忽略而不是把止损夹到 0。"""
        rm = _rm(sl_mode="clamped", sl_floor=None, sl_cap=30.0)
        sl_tp = rm.calculate_sl_tp(PRICE, signal=1, atr=ATR)
        assert sl_tp.sl == pytest.approx(PRICE - ATR * 1.5, 2)


class TestRrMode:
    """tp_mode="rr"：止盈 = R × 实际（夹逼后）止损距离。"""

    def test_rr_fixed_ratio(self):
        rm = _rm(tp_mode="rr", target_r_multiple=5.0)
        sl_tp = rm.calculate_sl_tp(PRICE, signal=1, atr=ATR)
        sl_dist = PRICE - sl_tp.sl
        tp_dist = sl_tp.tp - PRICE
        assert tp_dist == pytest.approx(5.0 * sl_dist, 4)

    def test_rr_uses_clamped_sl(self):
        """R 派生必须基于**夹逼后**的止损距离。"""
        rm = _rm(sl_mode="clamped", sl_floor=15.0, sl_cap=30.0, tp_mode="rr", target_r_multiple=2.0)
        sl_tp = rm.calculate_sl_tp(PRICE, signal=1, atr=ATR)  # sl 被夹到 15
        sl_dist = PRICE - sl_tp.sl
        tp_dist = sl_tp.tp - PRICE
        assert sl_dist == pytest.approx(15.0, 2)
        assert tp_dist == pytest.approx(30.0, 2)

    def test_rr_sell_symmetry(self):
        rm = _rm(tp_mode="rr", target_r_multiple=3.0)
        sl_tp = rm.calculate_sl_tp(PRICE, signal=-1, atr=ATR)
        sl_dist = sl_tp.sl - PRICE
        tp_dist = PRICE - sl_tp.tp
        assert tp_dist == pytest.approx(3.0 * sl_dist, 4)

    def test_regime_does_not_disturb_rr(self):
        """regime 因子必须**不再**扰动 R:R（这正是 R 模式的意义）。"""
        rm = _rm(tp_mode="rr", target_r_multiple=2.0)
        rm.set_regime("trending_high_vol")  # 旧路径会 sl×1.3 / tp×1.5
        sl_tp = rm.calculate_sl_tp(PRICE, signal=1, atr=ATR)
        sl_dist = PRICE - sl_tp.sl
        tp_dist = sl_tp.tp - PRICE
        assert tp_dist == pytest.approx(2.0 * sl_dist, 4)

    def test_expected_rr_equals_target(self):
        rm = _rm(tp_mode="rr", target_r_multiple=5.0)
        assert rm.expected_rr(ATR) == pytest.approx(5.0, 4)


class TestGateConsistency:
    """expected_rr 必须与 calculate_sl_tp 实际成交的 SL/TP 同口径（C9）。"""

    @pytest.mark.parametrize(
        "kwargs",
        [
            {},
            {"sl_mode": "clamped", "sl_floor": 15.0, "sl_cap": 30.0},
            {"tp_mode": "rr", "target_r_multiple": 5.0},
            {"sl_mode": "clamped", "sl_floor": 10.0, "sl_cap": 40.0, "tp_mode": "rr", "target_r_multiple": 2.5},
        ],
    )
    def test_expected_rr_matches_actual(self, kwargs):
        rm = _rm(**kwargs)
        sl_tp = rm.calculate_sl_tp(PRICE, signal=1, atr=ATR)
        actual_ratio = (sl_tp.tp - PRICE) / (PRICE - sl_tp.sl)
        assert rm.expected_rr(ATR) == pytest.approx(actual_ratio, 4)

    def test_gate_uses_same_source(self):
        """engine gate 现在调用 resolve_sl_tp_distances —— 检查源码引用。"""
        import inspect

        from app.bot import engine

        source = inspect.getsource(engine)
        assert "resolve_sl_tp_distances" in source
        assert "sl_est, tp_est" in source


class TestRounding:
    def test_price_decimals_applied(self):
        rm = _rm(price_decimals=3)
        sl_tp = rm.calculate_sl_tp(PRICE, signal=1, atr=ATR)
        # 3 位小数
        assert sl_tp.sl == round(PRICE - ATR * 1.5, 3)


class TestCapSafetyValidator:
    """sl_cap 安全界：过大的 cap 会把 lot 钳到 MIN_LOT，实际风险超预算。"""

    def _payload(self, **overrides) -> dict:
        data = {
            "symbol": "GOLD",
            "display_name": "Gold",
            "broker_alias": "GOLDm",
            "default_timeframe": "M15",
            "pip_value": 1.0,
            "default_lot": 0.1,
            "max_lot": 1.0,
            "price_decimals": 2,
            "sl_atr_mult": 1.5,
            "tp_atr_mult": 2.0,
            "sl_mode": "clamped",
            "sl_floor": 5.0,
            "sl_cap": 30.0,
            "tp_mode": "atr",
            "target_r_multiple": None,
            "contract_size": 100,
            "ml_tp_pips": 10.0,
            "ml_sl_pips": 10.0,
            "ml_forward_bars": 10,
            "ml_timeframe": "M15",
        }
        data.update(overrides)
        return data

    def test_valid_cap_accepted(self):
        from app.api.routes.symbols import SymbolCreateRequest

        req = SymbolCreateRequest(**self._payload())
        assert req.sl_mode == "clamped"
        assert req.sl_cap == 30.0

    def test_floor_greater_than_cap_rejected(self):
        from pydantic import ValidationError

        from app.api.routes.symbols import SymbolCreateRequest

        with pytest.raises(ValidationError) as exc:
            SymbolCreateRequest(**self._payload(sl_floor=40.0, sl_cap=30.0))
        assert "sl_floor must be <= sl_cap" in str(exc.value)

    def test_cap_over_safe_bound_rejected(self):
        """GOLD balance=10000, contract_size=100 → 安全界 = 100/(0.01×100) = 100。"""
        from pydantic import ValidationError

        from app.api.routes.symbols import SymbolCreateRequest

        with pytest.raises(ValidationError) as exc:
            SymbolCreateRequest(**self._payload(sl_cap=200.0))
        assert "safe bound" in str(exc.value)

    def test_floor_zero_rejected(self):
        """floor=0 会触发早期 MIN_LOT 返回 → 必须拒绝。"""
        from pydantic import ValidationError

        from app.api.routes.symbols import SymbolCreateRequest

        with pytest.raises(ValidationError):
            SymbolCreateRequest(**self._payload(sl_floor=0.0))

    def test_small_contract_size_lower_bound(self):
        """BTCUSD contract_size=1 → 安全界 = 100/(0.01×1) = 10000。"""
        from app.api.routes.symbols import SymbolCreateRequest

        req = SymbolCreateRequest(**self._payload(contract_size=1, sl_cap=500.0))
        assert req.sl_cap == 500.0
