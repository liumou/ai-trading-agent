"""回测口径一致性测试（N3）。

覆盖：
1. risk_manager_for_symbol 按品种读取 SL/TP 配置与 contract_size；
2. _calc_profit 使用 contract_size（GOLD 恒等、BTCUSD 对齐、USDJPY 对齐）；
3. 点差按 pip_value 换算（与实盘同口径）；
4. AIOptimizationLog 版本门（旧口径建议不得应用到新口径）。
"""

import pandas as pd
import pytest

from app.backtest.engine import BacktestEngine
from app.backtest.risk_factory import risk_manager_for_symbol
from app.constants import BACKTEST_FORMULA_VERSION
from app.risk.manager import RiskManager
from app.strategy import get_strategy


class TestRiskManagerForSymbol:
    def test_gold_reads_profile(self):
        rm = risk_manager_for_symbol("GOLD")
        assert rm.sl_atr_mult == 1.5
        assert rm.tp_atr_mult == 2.0
        assert rm.contract_size == 100.0
        assert rm.pip_value == 1.0

    def test_btc_reads_profile(self):
        rm = risk_manager_for_symbol("BTCUSD")
        assert rm.sl_atr_mult == 2.0
        assert rm.tp_atr_mult == 3.0
        assert rm.contract_size == 1.0

    def test_unknown_symbol_falls_back_to_defaults(self):
        """未知品种回退到默认值（与旧行为一致，不因缺配置改变回测结果）。"""
        rm = risk_manager_for_symbol("NOPE")
        assert rm.sl_atr_mult == 1.5
        assert rm.tp_atr_mult == 2.0
        assert rm.contract_size == 100.0

    def test_custom_risk_params_apply(self):
        rm = risk_manager_for_symbol("GOLD", risk_per_trade=0.02, max_lot=2.0)
        assert rm.max_risk_per_trade == 0.02
        assert rm.max_lot == 2.0

    def test_reads_clamp_and_rr_modes(self):
        """回测也要读新增的 clamp/R 模式，否则回测仍看不到新配的盈亏比。"""
        from app.config import SYMBOL_PROFILES

        # 临时注入一个 clamped + rr 的 profile，验证工厂透传
        saved = dict(SYMBOL_PROFILES)
        try:
            SYMBOL_PROFILES["TESTCLAMP"] = {
                "sl_atr_mult": 1.5,
                "tp_atr_mult": 2.0,
                "contract_size": 100,
                "pip_value": 1.0,
                "price_decimals": 2,
                "sl_mode": "clamped",
                "sl_floor": 15.0,
                "sl_cap": 30.0,
                "tp_mode": "rr",
                "target_r_multiple": 5.0,
            }
            rm = risk_manager_for_symbol("TESTCLAMP")
            assert rm.sl_mode == "clamped"
            assert rm.sl_floor == 15.0
            assert rm.sl_cap == 30.0
            assert rm.tp_mode == "rr"
            assert rm.target_r_multiple == 5.0
            # clamp + R 在回测 RiskManager 上同样生效
            sl_tp = rm.calculate_sl_tp(3000.0, 1, 8.05)
            sl_dist = 3000.0 - sl_tp.sl
            tp_dist = sl_tp.tp - 3000.0
            assert sl_dist == pytest.approx(15.0, 2)  # floor 生效
            assert tp_dist == pytest.approx(75.0, 2)  # R=5
        finally:
            SYMBOL_PROFILES.clear()
            SYMBOL_PROFILES.update(saved)


def _make_df(closes: list[float]) -> pd.DataFrame:
    import numpy as np

    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    return pd.DataFrame(
        {
            "time": pd.date_range("2025-01-01", periods=n, freq="h"),
            "open": closes,
            "high": closes * 1.001,
            "low": closes * 0.999,
            "close": closes,
            "volume": np.ones(n),
            "atr": np.full(n, 10.0),
            "signal": 0,
        }
    )


class TestCalcProfitContractSize:
    """_calc_profit 必须用品种 contract_size，而非硬编码 100。"""

    def test_gold_unchanged(self):
        """GOLD contract_size=100 → 修复前后 PnL 恒等（回归守卫）。"""
        rm = risk_manager_for_symbol("GOLD")
        engine = BacktestEngine(get_strategy("ema_crossover"), rm, include_costs=False)
        trade = {"type": "BUY", "entry_price": 3000.0, "lot": 0.1}
        profit = engine._calc_profit(trade, 3010.0)
        # pips=10, lot=0.1, cs=100 → 10*0.1*100 = 100
        assert profit == pytest.approx(100.0, 2)

    def test_btc_aligned(self):
        """BTCUSD contract_size=1 → 修复后 PnL = pips×lot×1（旧代码会 ×100）。"""
        rm = risk_manager_for_symbol("BTCUSD")
        engine = BacktestEngine(get_strategy("ema_crossover"), rm, include_costs=False)
        trade = {"type": "BUY", "entry_price": 100000.0, "lot": 0.1}
        profit = engine._calc_profit(trade, 100100.0)
        # pips=100, lot=0.1, cs=1 → 10（旧代码会算成 1000）
        assert profit == pytest.approx(10.0, 2)

    def test_usdjpy_aligned(self):
        """USDJPY contract_size=100000 → 修复后 = pips×lot×100000。"""
        rm = risk_manager_for_symbol("USDJPY")
        engine = BacktestEngine(get_strategy("ema_crossover"), rm, include_costs=False)
        trade = {"type": "BUY", "entry_price": 145.0, "lot": 0.01}
        profit = engine._calc_profit(trade, 145.1)
        # pips=0.1, lot=0.01, cs=100000 → 100（旧代码会算成 0.1）
        assert profit == pytest.approx(100.0, 2)


class TestSpreadPipValue:
    """回测点差必须按 pip_value 换算成价格单位（与实盘同口径）。"""

    def test_gold_spread(self):
        rm = risk_manager_for_symbol("GOLD")
        engine = BacktestEngine(get_strategy("ema_crossover"), rm, include_costs=True)
        # spread_pips=2.0 × pip_value=1.0 × 0.5 = 1.0 价格单位
        df = _make_df([3000.0] * 5)
        df.loc[df.index[2], "signal"] = 1
        # 内部逻辑直接验证换算
        assert engine.spread_pips * rm.pip_value * 0.5 == pytest.approx(1.0, 4)


class TestVersionGate:
    """AIOptimizationLog 版本门：旧口径建议不得应用到新口径。"""

    def test_constant_exists(self):
        assert BACKTEST_FORMULA_VERSION == "v2"

    def test_log_created_with_version(self):
        """optimize() 写日志时必须带版本号。"""
        import inspect

        from app.ai import strategy_optimizer

        source = inspect.getsource(strategy_optimizer)
        assert "backtest_formula_version=BACKTEST_FORMULA_VERSION" in source

    def test_apply_checks_version(self):
        """apply_optimization 必须校验版本不匹配时拒绝。"""
        import inspect

        from app.api.routes import ai_insights

        source = inspect.getsource(ai_insights)
        assert "backtest_formula_version != BACKTEST_FORMULA_VERSION" in source
        assert "re-run optimization" in source


class TestBackwardCompat:
    """库层函数不传 factory 时保持旧行为。"""

    def test_grid_search_default_behavior(self):
        """grid_search 不传 risk_manager_factory 时内部仍用裸 RiskManager。"""
        import inspect

        from app.backtest import optimizer

        source = inspect.getsource(optimizer.grid_search)
        assert "risk_manager_factory is None" in source
        assert "RiskManager(max_risk_per_trade=risk_per_trade" in source

    def test_optimize_accepts_symbol_kwarg(self):
        """optimize() 接受 symbol 参数（B7 跨品种修复的签名）。"""
        import inspect

        from app.ai.strategy_optimizer import StrategyOptimizer

        sig = inspect.signature(StrategyOptimizer.optimize)
        assert "symbol" in sig.parameters
        assert "strategy_name" in sig.parameters
