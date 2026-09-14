"""
Unit tests for walk-forward optimization — backend/app/backtest/walk_forward.py.
Covers the grid-search result contract between optimizer (producer) and
walk_forward (consumer).
"""

from app.backtest.walk_forward import walk_forward_test

# Small grid keeps the test fast (4 combos × n_splits backtests).
_PARAM_GRID = {"fast_period": [5, 10], "slow_period": [20, 30]}


def test_walk_forward_runs_and_populates_windows(make_ohlcv_df):
    """walk_forward_test must complete and populate every window.

    Regression: the consumer read ``opt_result.results``, a field that never
    existed (the producer stores the ranked list as ``all_results``). That
    AttributeError was raised on every split and swallowed by the API layer,
    silently dropping walk-forward from overfitting-score.
    """
    df = make_ohlcv_df(rows=900)  # 900 bars → 3 splits of 300, test window ≥ 50
    result = walk_forward_test(
        strategy_name="ema_crossover",
        df=df,
        param_grid=_PARAM_GRID,
        n_splits=3,
        train_pct=0.7,
    )

    assert result.n_splits == 3
    assert len(result.windows) == 3
    assert len(result.best_params_stability) == 3

    for window in result.windows:
        assert {"split", "best_params", "in_sample_sharpe", "oos_sharpe"} <= set(window)
        assert window["test_bars"] >= 50


def test_walk_forward_insufficient_data(make_ohlcv_df):
    """Fewer than 200 bars short-circuits to an empty result."""
    df = make_ohlcv_df(rows=150)
    result = walk_forward_test(
        strategy_name="ema_crossover",
        df=df,
        param_grid=_PARAM_GRID,
        n_splits=5,
    )

    assert result.n_splits == 0
    assert result.windows == []
