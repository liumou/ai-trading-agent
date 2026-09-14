"""回测用 RiskManager 工厂 —— 让回测读取品种配置（SL/TP 倍数、合约规模、点值）。

历史问题：回测各处直接 `RiskManager(...)` 用 dataclass 默认值
（sl=1.5 / tp=2.0 / contract_size=100 / pip_value=1.0），导致：
  - 回测的止盈止损与实盘（逐字段取品种 profile）不一致，无法验证
    运营者新配的盈亏比；
  - BTCUSD（contract_size=1）PnL 被 ×100 高估、USDJPY（=100000）
    被 ×1000 低估（配合 _calc_profit 的硬编码，见 backtest/engine.py）。

本模块是回测侧读取品种配置的**唯一**入口，避免 8 处构造点各自实现。
"""

from __future__ import annotations

from typing import Callable

from app.config import SYMBOL_PROFILES
from app.risk.manager import RiskManager


def risk_manager_for_symbol(
    symbol: str,
    *,
    risk_per_trade: float = 0.01,
    max_lot: float = 1.0,
) -> RiskManager:
    """按品种配置构造回测用 RiskManager。

    从 SYMBOL_PROFILES 读取实盘同源参数（含新增的 clamp/R 模式），
    使回测与实盘的 SL/TP 口径一致。profile 缺失时回退到默认值
    （与旧行为一致，不会因数据缺失而改变回测结果）。
    """
    profile = SYMBOL_PROFILES.get(symbol, {}) or {}
    return RiskManager(
        max_risk_per_trade=risk_per_trade,
        max_lot=max_lot,
        pip_value=profile.get("pip_value", 1.0),
        price_decimals=profile.get("price_decimals", 2),
        sl_atr_mult=profile.get("sl_atr_mult", 1.5),
        tp_atr_mult=profile.get("tp_atr_mult", 2.0),
        contract_size=profile.get("contract_size", 100.0),
        sl_mode=profile.get("sl_mode") or "atr",
        sl_floor=profile.get("sl_floor"),
        sl_cap=profile.get("sl_cap"),
        tp_mode=profile.get("tp_mode") or "atr",
        target_r_multiple=profile.get("target_r_multiple"),
    )


# 库层函数（grid_search / walk_forward_test 等）接受的可选工厂类型：
# 默认 None 时保持旧的裸 RiskManager 行为（向后兼容），调用方传入
# 该工厂即可让内部构造点也读取品种配置。
RiskManagerFactory = Callable[[], RiskManager]
