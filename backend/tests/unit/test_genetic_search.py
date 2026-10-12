"""
Unit tests for genetic parameter optimizer — backend/app/backtest/genetic_search.py.

Ports the SETS (Self Evolving Trading System) genetic-search idea to this
codebase's parameter optimization stack, reusing BacktestEngine for fitness.

Key invariants tested here (mirroring SETS's own tests, translated to pytest):
  - Determinism: same seed → identical evolution history.
  - Gene bounds: heavy mutation never leaves a gene's [min, max]; int genes stay int.
  - Elite preservation: best fitness never decreases across generations.
  - OOS gate: leader is chosen among configs that pass the out-of-sample gate.
  - No look-ahead in the underlying backtest is inherited from BacktestEngine.
  - Serialization: all-all-profit metrics never leak inf/nan to JSON (CRITICAL fix).
"""

import json

from app.backtest.genetic_search import (
    crossover,
    fitness_score,
    gaussian_mutate,
    generate_param_ranges,
    genetic_search,
    passes_gate,
    random_individual,
    tournament_select,
)


def _ranges():
    """Minimal param ranges for fast tests (2 params, small populations)."""
    return {"fast_period": (5, 50), "slow_period": (20, 200)}


def test_random_individual_stays_in_bounds():
    """random_individual must sample every gene inside [min, max]; int genes integral."""
    ranges = _ranges()
    for _ in range(200):
        ind = random_individual(ranges, seed=7)
        assert 5 <= ind["fast_period"] <= 50 and isinstance(ind["fast_period"], int)
        assert 20 <= ind["slow_period"] <= 200 and isinstance(ind["slow_period"], int)


def test_crossover_blends_but_keeps_bounds():
    """Uniform crossover picks per-gene from either parent, never out of range."""
    ranges = _ranges()
    a = {"fast_period": 10, "slow_period": 30}
    b = {"fast_period": 45, "slow_period": 180}
    for _ in range(100):
        child = crossover(a, b, ranges, seed=11)
        assert child["fast_period"] in (10, 45)
        assert child["slow_period"] in (30, 180)


def test_mutation_never_leaves_bounds_and_keeps_int():
    """Gaussian mutation is clamped to [min, max]; int genes stay integral."""
    ranges = _ranges()
    ind = {"fast_period": 20, "slow_period": 50}
    for _ in range(500):
        out = gaussian_mutate(ind, ranges, seed=3, p=1.0)  # always mutate
        assert 5 <= out["fast_period"] <= 50 and isinstance(out["fast_period"], int)
        assert 20 <= out["slow_period"] <= 200 and isinstance(out["slow_period"], int)


def test_tournament_select_picks_high_fitness():
    """Tournament selection must return the fittest of the sampled candidates."""
    pops = [
        {"id": 0, "params": {}, "fitness": 0.1},
        {"id": 1, "params": {}, "fitness": 5.0},
        {"id": 2, "params": {}, "fitness": 3.0},
    ]
    # k=3 → samples the whole pool each time → id 1 (fittest) always wins.
    for _ in range(50):
        winner = tournament_select(pops, k=3, seed=5)
        assert winner["id"] == 1


def test_fitness_and_gate_relations():
    """fitness_score rejects too-few-trades; passes_gate requires OOS profitability."""
    assert (
        fitness_score({"total_trades": 3, "sharpe_ratio": 1.0, "profit_factor": 2.0, "win_rate": 0.5}, min_trades=5)
        == -999.0
    )
    # Gate: ret>1%, dd<10%, trades≥3, winrate≥50%
    assert passes_gate({"ret": 0.02, "maxDD": 0.05, "trades": 5, "winRate": 0.6})
    assert not passes_gate({"ret": 0.005, "maxDD": 0.05, "trades": 5, "winRate": 0.6})
    assert not passes_gate({"ret": 0.02, "maxDD": 0.15, "trades": 5, "winRate": 0.6})
    assert not passes_gate({"ret": 0.02, "maxDD": 0.05, "trades": 2, "winRate": 0.6})
    assert not passes_gate({"ret": 0.02, "maxDD": 0.05, "trades": 5, "winRate": 0.4})


def test_generate_param_ranges_accepts_lists_or_tuples():
    """param_ranges may be given as [min,max] lists or (min,max) tuples."""
    ranges = generate_param_ranges({"fast_period": [5, 50], "slow_period": (20, 200)})
    assert ranges == {"fast_period": (5, 50), "slow_period": (20, 200)}


def test_genetic_search_deterministic_same_seed(make_ohlcv_df):
    """Same seed → identical best params and identical history."""
    df = make_ohlcv_df(rows=400)
    ranges = {"fast_period": (5, 50), "slow_period": (20, 200)}

    r1 = genetic_search(
        strategy_name="ema_crossover",
        df=df,
        param_ranges=ranges,
        pop_size=12,
        generations=4,
        seed=42,
        min_trades=5,
    )
    r2 = genetic_search(
        strategy_name="ema_crossover",
        df=df,
        param_ranges=ranges,
        pop_size=12,
        generations=4,
        seed=42,
        min_trades=5,
    )

    assert r1.best_params == r2.best_params
    assert [h["best_fitness"] for h in r1.history] == [h["best_fitness"] for h in r2.history]
    assert r1.best_params != {}  # 4 gens on an up-trend df should find something


def test_genetic_search_best_fitness_never_decreases(make_ohlcv_df):
    """Elite preservation: best fitness is monotonic non-decreasing across generations."""
    df = make_ohlcv_df(rows=400, trend="up")
    ranges = {"fast_period": (5, 50), "slow_period": (20, 200)}

    r = genetic_search(
        strategy_name="ema_crossover",
        df=df,
        param_ranges=ranges,
        pop_size=12,
        generations=5,
        seed=1,
        min_trades=5,
    )

    fits = [h["best_fitness"] for h in r.history]
    # best fitness 每代不下降（值越大越好）→ 序列单调不减（升序）
    assert fits == sorted(fits)
    assert r.history[-1]["best_fitness"] >= r.history[0]["best_fitness"]


def test_genetic_search_result_contract(make_ohlcv_df):
    """GeneticResult.to_dict must expose a stable API for the frontend."""
    df = make_ohlcv_df(rows=400)
    r = genetic_search(
        strategy_name="ema_crossover",
        df=df,
        param_ranges={"fast_period": (5, 50), "slow_period": (20, 200)},
        pop_size=8,
        generations=3,
        seed=0,
        min_trades=5,
    )
    d = r.to_dict()
    assert set(d) >= {"best_params", "best_fitness", "history", "tested_individuals", "total_generations"}
    assert isinstance(d["history"], list) and len(d["history"]) == 3
    assert d["tested_individuals"] > 0


def test_all_profit_metrics_never_leak_inf_to_json(make_ohlcv_df):
    """CRITICAL regression: all-profit backtest yields profit_factor=inf upstream;
    the optimizer must clamp it so GeneticResult.to_dict() stays JSON-serializable
    (Starlette/JSONResponse uses allow_nan=False → inf would 500).
    """
    df = make_ohlcv_df(rows=400, trend="up")
    ranges = {"fast_period": (5, 50), "slow_period": (20, 200)}

    r = genetic_search(
        strategy_name="ema_crossover",
        df=df,
        param_ranges=ranges,
        pop_size=8,
        generations=3,
        seed=0,
        min_trades=5,
    )
    d = r.to_dict()

    # best_metrics 与 history 的每个浮点都必须可被严格 JSON 序列化（无 inf/nan）。
    payload = json.dumps(d, allow_nan=False)  # 与 Starlette 行为一致
    assert "Infinity" not in payload and "NaN" not in payload
