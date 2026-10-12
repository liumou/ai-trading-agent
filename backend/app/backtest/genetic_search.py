"""
Genetic Parameter Optimizer — 遗传算法搜索策略参数，替代/补充笛卡尔积穷举。

思路移植自开源项目 SETS（Self Evolving Trading System）的 evolution.js，
翻译为 Python 并适配本仓库的参数优化栈：
  - 染色体 = 一维基因数组（策略参数），每个基因有 [min, max] 边界，int 基因保持整型
  - 种群：精英保留 + 锦标赛选择 + 均匀交叉 + 高斯变异 + 随机"移民"
  - 评估：复用 BacktestEngine / score_result 计算 fitness（与 grid_search 同源打分）
  - 样本外（OOS）硬门控：与 SETS 的 passesGate 一致，只有通过 OOS 门禁的配置
    才可能成为 leader（防过拟合）。

设计要点（对齐本仓库约定）：
  - 确定性：seed 驱动 mulberry32 伪随机，同 seed 同结果（测试强制验证）
  - 边界钳位：变异后 clamp 到 [min, max]；int 基因 round
  - 精英保留：每代保留 top-k，保证 best fitness 单调不减（测试强制验证）
  - 复用 RiskManagerFactory，使回测 SL/TP 口径与实盘/品种配置一致
  - 不动 grid_search —— 本模块独立，供 API 层按需选择"穷举"或"遗传"
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd
from loguru import logger

from app.backtest.engine import BacktestEngine
from app.backtest.optimizer import score_result
from app.backtest.risk_factory import RiskManagerFactory
from app.risk.manager import RiskManager
from app.strategy import get_strategy

DEFAULT_POP_SIZE = 48
DEFAULT_GENERATIONS = 15
DEFAULT_ELITE = 4
DEFAULT_IMMIGRANTS = 4
DEFAULT_MUTATION_P = 0.18
DEFAULT_MUTATION_SCALE = 0.12

# 单次评估的最多个体数（防 API 滥用 / 耗时失控），类似 grid_search 的 MAX_COMBINATIONS。
MAX_EVALUATIONS = 5000

ParamRange = tuple[float, float]


def mulberry32(seed: int) -> Callable[[], float]:
    """种子伪随机数生成器（SETS 移植）。同 seed 产生完全相同的序列。"""

    def _next() -> float:
        nonlocal seed
        seed = (seed + 0x6D2B79F5) & 0xFFFFFFFF
        t = seed
        t = ((t ^ (t >> 15)) * (t | 1)) & 0xFFFFFFFF
        t = ((t ^ (t >> 7)) * (t | 61)) & 0xFFFFFFFF
        t ^= t >> 14
        return t / 4294967296

    return _next


def gauss(r: Callable[[], float]) -> float:
    """标准正态随机数（Box-Muller），由种子 RNG 驱动，保证确定性。"""
    u = 0.0
    while u == 0.0:
        u = r()
    return math.sqrt(-2.0 * math.log(u)) * math.cos(2.0 * math.pi * r())


def generate_param_ranges(param_ranges: dict[str, list | tuple]) -> dict[str, ParamRange]:
    """规范化 param_ranges：接受 [min,max] 列表或 (min,max) 元组，统一为元组。"""
    out: dict[str, ParamRange] = {}
    for key, val in param_ranges.items():
        low, high = val
        out[key] = (float(low), float(high))
    return out


def _derive_seed(base: int, slot: int) -> int:
    """派生 RNG 种子：对连续 slot 做 hash 扩散，避免 mulberry32 连续种子的首输出相关性。

    mulberry32 连续整型种子的第一个随机值存在正相关（实测 seed 0→0.7336、
    seed 1→0.6270），若直接用 ``seed + slot`` 作为局部种子，基因首值会偏向
    一起偏移，削弱初始种群方差。Goldilocks 常数 0x9E3779B9 保证 slot 微变
    → 派生种子突变，配合 nonlocal mulberry32 输出即去相关。
    """
    return (base + slot * 0x9E3779B9) & 0xFFFFFFFF


def _is_int_key(key: str, ranges: dict[str, ParamRange]) -> bool:
    """int 基因判定：边界均为整数 → 参数是离散整型（如 fast_period）。"""
    low, high = ranges[key]
    return float(low).is_integer() and float(high).is_integer()


def _clamp(v: float, low: float, high: float, *, is_int: bool) -> int | float:
    v = min(high, max(low, v))
    return int(round(v)) if is_int else float(v)


def random_individual(ranges: dict[str, ParamRange], seed: int) -> dict:
    """随机个体：每个基因在 [min, max] 内均匀采样，int 基因取整。"""
    r = mulberry32(seed)
    ind: dict = {}
    for key, (low, high) in ranges.items():
        is_int = _is_int_key(key, ranges)
        v = low + r() * (high - low)
        ind[key] = _clamp(v, low, high, is_int=is_int)
    return ind


def crossover(a: dict, b: dict, ranges: dict[str, ParamRange], seed: int) -> dict:
    """均匀交叉：每个基因以 50% 概率取父/母。"""
    r = mulberry32(seed)
    return {key: (a[key] if r() < 0.5 else b[key]) for key in ranges}


def gaussian_mutate(
    ind: dict,
    ranges: dict[str, ParamRange],
    seed: int,
    p: float = DEFAULT_MUTATION_P,
    scale: float = DEFAULT_MUTATION_SCALE,
) -> dict:
    """高斯变异：每个基因以 p 概率加高斯扰动（scale × 范围），钳位边界。"""
    r = mulberry32(seed)
    out = dict(ind)
    for key, (low, high) in ranges.items():
        if r() >= p:
            continue
        is_int = _is_int_key(key, ranges)
        perturbed = ind[key] + gauss(r) * scale * (high - low)
        out[key] = _clamp(perturbed, low, high, is_int=is_int)
    return out


def tournament_select(pop: list[dict], k: int = 3, seed: int = 0) -> dict:
    """锦标赛选择：从种群随机抽 k 个，返回 fitness 最高者。"""
    r = mulberry32(seed)
    best = None
    for _ in range(k):
        cand = pop[math.floor(r() * len(pop))]
        if best is None or cand["fitness"] > best["fitness"]:
            best = cand
    assert best is not None
    return best


def fitness_score(metrics: dict, min_trades: int = 10) -> float:
    """fitness 打分：复用 optimizer.score_result 的语义（sharpe × PF × win_rate）。"""
    # score_result 需要 BacktestResult 对象，这里接受 dict 直接算等价值。
    # 与 grid_search 保持同一打分公式，保证两种优化方式可比。
    from app.backtest.engine import BacktestResult

    br = BacktestResult(
        total_trades=metrics.get("total_trades", 0),
        sharpe_ratio=metrics.get("sharpe_ratio", 0.0),
        profit_factor=metrics.get("profit_factor", 0.0),
        win_rate=metrics.get("win_rate", 0.0),
    )
    return score_result(br, min_trades)


def passes_gate(m: dict) -> bool:
    """样本外硬门控（SETS passesGate 移植）：OOS 也必须盈利且低回撤。

    ret>1% 且 maxDD<10% 且 ≥3 笔 且 胜率≥50% —— 只有 OOS 也过关的配置
    才可能成为 leader，防止仅训练集过拟合被采纳。
    """
    return (
        m.get("ret", 0.0) > 0.01
        and m.get("maxDD", 1.0) < 0.1
        and m.get("trades", 0) >= 3
        and m.get("winRate", 0.0) >= 0.5
    )


@dataclass
class GeneticResult:
    best_params: dict = field(default_factory=dict)
    best_fitness: float = 0.0
    best_metrics: dict = field(default_factory=dict)
    leader_params: dict | None = None  # 通过 OOS 门禁的 leader（可能为 None）
    history: list[dict] = field(default_factory=list)  # per-gen: best/mean/survivors
    tested_individuals: int = 0
    total_generations: int = 0

    def to_dict(self) -> dict:
        return {
            "best_params": self.best_params,
            "best_fitness": round(self.best_fitness, 4),
            "best_metrics": self.best_metrics,
            "leader_params": self.leader_params,
            "history": self.history,
            "tested_individuals": self.tested_individuals,
            "total_generations": self.total_generations,
        }


def _evaluate(
    params: dict,
    strategy_name: str,
    df: pd.DataFrame,
    from_i: int,
    to_i: int,
    risk_manager_factory: RiskManagerFactory,
    initial_balance: float,
    min_trades: int,
    individual_id: int,
) -> dict | None:
    """评估一个个体在指定 K 线窗口上的 fitness，返回个体记录（失败返回 None）。"""
    try:
        strategy = get_strategy(strategy_name, params)
        engine = BacktestEngine(strategy, risk_manager_factory(), initial_balance)
        bt_result = engine.run(df.iloc[from_i:to_i])

        metrics = {
            "total_trades": bt_result.total_trades,
            "sharpe_ratio": bt_result.sharpe_ratio,
            # 钳位防 inf（全盈利时 engine 返回 float("inf")），与 grid_search 对齐，
            # 否则 GeneticResult.to_dict() → JSON 序列化必然 500。
            "profit_factor": min(bt_result.profit_factor, 99.0),
            "win_rate": bt_result.win_rate,
            "ret": (bt_result.total_profit / initial_balance) if initial_balance else 0.0,
            "maxDD": bt_result.max_drawdown,
        }
        return {
            "id": individual_id,
            "params": params,
            "fitness": fitness_score(metrics, min_trades),
            "metrics": metrics,
        }
    except Exception as e:  # noqa: BLE001 — 单个个体失败不阻断进化
        logger.warning(f"Genetic eval failed {individual_id}: {e}")
        return None


def genetic_search(
    strategy_name: str,
    df: pd.DataFrame,
    param_ranges: dict[str, list | tuple],
    pop_size: int = DEFAULT_POP_SIZE,
    generations: int = DEFAULT_GENERATIONS,
    seed: int = 0,
    elite: int = DEFAULT_ELITE,
    immigrants: int = DEFAULT_IMMIGRANTS,
    mutation_p: float = DEFAULT_MUTATION_P,
    mutation_scale: float = DEFAULT_MUTATION_SCALE,
    train_fraction: float = 0.7,
    initial_balance: float = 10000.0,
    risk_per_trade: float = 0.01,
    max_lot: float = 1.0,
    min_trades: int = 10,
    risk_manager_factory: RiskManagerFactory | None = None,
) -> GeneticResult:
    """遗传算法搜索策略参数。

    train 窗口内 fitness 排名；OOS 窗口（train 之后的 30%）跑 passes_gate 硬门控，
    leader 只在通过门禁的配置中选取（避免仅训练集过拟合被采纳）。

    ``param_ranges`` 形如 {"fast_period": (5, 50), "slow_period": (20, 200)}，
    亦接受 [min, max] 列表。int 基因（边界为整数）保持整型。
    """
    ranges = generate_param_ranges(param_ranges)
    # 防种群膨胀：elite 不得超过 pop_size-1，否则后代数为 0 且每代只增移民，
    # pop_size 语义被破坏、种群逐代扩大。
    elite = min(elite, pop_size - 1)
    total_bars = len(df)
    if total_bars < 200:
        logger.warning(f"Genetic search: insufficient data ({total_bars} bars)")
        return GeneticResult()

    split = int(total_bars * train_fraction)
    if split < 100 or total_bars - split < 50:
        logger.warning("Genetic search: train/OOS window too small")
        return GeneticResult()

    if risk_manager_factory is None:
        risk_manager = RiskManager(max_risk_per_trade=risk_per_trade, max_lot=max_lot)
        risk_manager_factory = lambda: risk_manager  # noqa: E731

    pop: list[dict] = []
    next_id = 0
    tested = 0

    def make(genome: dict) -> dict:
        nonlocal next_id, tested
        next_id += 1
        tested += 1
        evaluated = _evaluate(
            genome, strategy_name, df, 0, split, risk_manager_factory, initial_balance, min_trades, next_id
        )
        if evaluated is None:
            return {"id": next_id, "params": genome, "fitness": -999.0, "metrics": {}, "gen": -1}
        evaluated["gen"] = 0
        return evaluated

    # 初始种群（随机个体）
    for _ in range(pop_size):
        genome = random_individual(ranges, seed=_derive_seed(seed, next_id))
        pop.append(make(genome))

    # 按 fitness 排名
    pop.sort(key=lambda x: x["fitness"], reverse=True)

    # 初始种群全失败 → 返回空结果（避免把占位个体（-999）当 best 交付给 API）
    if all(ind["gen"] == -1 for ind in pop):
        logger.error("Genetic search: initial population all failed to evaluate")
        return GeneticResult()

    history: list[dict] = []
    leader_params: dict | None = None
    # OOS 结果缓存：elite 跨代保留且参数不变，其 OOS 结果无需重算。
    # key = tuple(sorted(params.items()))，value = OOS metrics 或 None（失败）。
    oos_cache: dict[tuple, dict | None] = {}

    # OOS 评估（带缓存）：同参数只评估一次，elite 跨代直接复用。
    def oos_metrics(ind_params: dict) -> dict | None:
        key = tuple(sorted(ind_params.items()))
        if key in oos_cache:
            return oos_cache[key]
        metrics = _evaluate_oos(ind_params, strategy_name, df, split, total_bars, risk_manager_factory, initial_balance)
        oos_cache[key] = metrics
        return metrics

    for gen in range(1, generations + 1):
        # 精英保留
        keep = pop[:elite]
        # 随机移民（种子与个体 id 对应：id 为 next_id+1..next_id+immigrants）
        immigrant_genomes = [
            random_individual(ranges, seed=_derive_seed(seed, next_id + i + 1)) for i in range(immigrants)
        ]
        immig = []
        for g in immigrant_genomes:
            next_id += 1
            tested += 1
            evaluated = _evaluate(
                g, strategy_name, df, 0, split, risk_manager_factory, initial_balance, min_trades, next_id
            )
            if evaluated is None:
                continue
            evaluated["gen"] = gen
            immig.append(evaluated)

        # 后代：锦标赛选择 + 交叉 + 变异
        offspring = []
        n_offspring = max(pop_size - len(keep) - len(immig), 0)
        for k in range(n_offspring):
            parent_a = tournament_select(pop, k=3, seed=_derive_seed(seed, next_id + k))
            parent_b = tournament_select(pop, k=3, seed=_derive_seed(seed, next_id + k + 1))
            child = crossover(parent_a["params"], parent_b["params"], ranges, seed=_derive_seed(seed, next_id + k + 2))
            child = gaussian_mutate(
                child, ranges, seed=_derive_seed(seed, next_id + k + 3), p=mutation_p, scale=mutation_scale
            )
            next_id += 1
            tested += 1
            evaluated = _evaluate(
                child, strategy_name, df, 0, split, risk_manager_factory, initial_balance, min_trades, next_id
            )
            if evaluated is None:
                continue
            evaluated["gen"] = gen
            offspring.append(evaluated)

        # 组装新种群
        pop = keep + offspring + immig
        pop.sort(key=lambda x: x["fitness"], reverse=True)

        # OOS 门控：所有个体的 OOS 窗口跑 backtest，选出 leader
        oos_passed = []
        for ind in pop:
            metrics = oos_metrics(ind["params"])
            if metrics and passes_gate(metrics):
                oos_passed.append((ind, metrics))

        # leader = 通过 OOS 门禁中 train fitness 最高者（SETS: survivors[0]）
        if oos_passed:
            oos_passed.sort(key=lambda x: x[0]["fitness"], reverse=True)
            candidate, _ = oos_passed[0]
            # 克隆不晋升：与上一代 leader 参数相同则不算晋升（但保留）。
            leader_params = candidate["params"]

        best_fitness = pop[0]["fitness"]
        mean_fitness = sum(x["fitness"] for x in pop) / len(pop) if pop else 0.0
        survivors = len(oos_passed)

        history.append(
            {
                "gen": gen,
                "best_fitness": round(best_fitness, 4),
                "mean_fitness": round(mean_fitness, 4),
                "survivors": survivors,
            }
        )

        # 防失控：超过评估上限即停止
        if tested >= MAX_EVALUATIONS:
            logger.warning(f"Genetic search: hit MAX_EVALUATIONS={MAX_EVALUATIONS} at gen {gen}, stopping")
            break

    best = pop[0]
    result = GeneticResult(
        best_params=best["params"],
        best_fitness=best["fitness"],
        best_metrics=best["metrics"],
        leader_params=leader_params,
        history=history,
        tested_individuals=tested,
        total_generations=len(history),
    )
    logger.info(
        f"Genetic search done: {strategy_name} gens={result.total_generations} tested={tested} "
        f"best_fitness={result.best_fitness:.4f} leader={'set' if leader_params else 'none'}"
    )
    return result


def _evaluate_oos(
    params: dict,
    strategy_name: str,
    df: pd.DataFrame,
    from_i: int,
    to_i: int,
    risk_manager_factory: RiskManagerFactory,
    initial_balance: float,
) -> dict | None:
    """评估个体在 OOS 窗口上的指标（供 passes_gate 使用）。失败返回 None。"""
    try:
        strategy = get_strategy(strategy_name, params)
        engine = BacktestEngine(strategy, risk_manager_factory(), initial_balance)
        bt_result = engine.run(df.iloc[from_i:to_i])
        return {
            "ret": (bt_result.total_profit / initial_balance) if initial_balance else 0.0,
            "maxDD": bt_result.max_drawdown,
            "trades": bt_result.total_trades,
            "winRate": bt_result.win_rate,
        }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"OOS eval failed {params}: {e}")
        return None
