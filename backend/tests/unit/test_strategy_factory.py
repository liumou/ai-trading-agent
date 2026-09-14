"""
Unit tests for the strategy factory parameter filtering.

The optimizer's LLM suggestions may include params a given strategy does not
accept (e.g. ``rsi_period`` suggested onto ``ema_crossover``). get_strategy must
drop those instead of raising "unexpected keyword argument".
"""

import pytest

from app.strategy import get_strategy


class TestGetStrategyParamFiltering:
    def test_ema_ignores_foreign_params(self):
        strategy = get_strategy(
            "ema_crossover",
            {"fast_period": 12, "slow_period": 26, "rsi_period": 14, "rsi_overbought": 75},
        )
        assert strategy.name == "ema_crossover"
        assert strategy.get_params()["fast_period"] == 12
        # foreign params must be dropped, not passed through
        assert "rsi_period" not in strategy.get_params()

    def test_ema_keeps_accepted_params(self):
        strategy = get_strategy("ema_crossover", {"fast_period": 8, "slow_period": 21})
        assert strategy.get_params()["fast_period"] == 8
        assert strategy.get_params()["slow_period"] == 21

    def test_rsi_strategy_keeps_rsi_params(self):
        strategy = get_strategy(
            "rsi_filter",
            {"rsi_period": 14, "rsi_overbought": 80, "rsi_oversold": 20, "fast_period": 5},
        )
        params = strategy.get_params()
        assert "rsi_period" in params

    def test_unknown_strategy_raises(self):
        with pytest.raises(ValueError):
            get_strategy("not_a_strategy", {})

    def test_no_params_still_builds(self):
        strategy = get_strategy("ema_crossover")
        assert strategy.name == "ema_crossover"
