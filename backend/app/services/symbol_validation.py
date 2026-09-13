"""券商侧品种校验 —— 供品种准入 API 与启动校验共用的唯一实现。

单一事实来源，回答"该品种此刻在券商侧是否存在、是否可交易"。与传输层解耦，
因此 HTTP 路由（fail-closed 准入）与 lifespan 启动校验（warn/strict）可以共用，
无需各自重复一套失败分类逻辑。
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Literal

from loguru import logger

# MT5 SYMBOL_TRADE_MODE 中"能查到规格但无法下单"的取值：0=DISABLED，3=CLOSEONLY。
_NON_TRADABLE_TRADE_MODES = (0, 3)

CheckKind = Literal["ok", "not_found", "not_tradable", "unreachable", "unexpected"]


@dataclass
class BrokerSymbolCheck:
    """单个品种在真实券商侧的校验结果。

    kind 取值含义：
      - ok:           规格校验通过且可交易；``spec`` 携带 MT5 规格
      - not_found:    券商明确回答：品种不存在 / 无法加入 Market Watch
      - not_tradable: 券商明确回答：trade_mode 为 DISABLED / CLOSEONLY
      - unreachable:  bridge 或 MT5 终端不可达 —— 无法判断
      - unexpected:   bridge 有响应但返回体不可用
    """

    kind: CheckKind
    spec: dict | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.kind == "ok"

    @property
    def broker_answered(self) -> bool:
        """券商是否给出了确定性答复（品种确实有问题），
        而非校验因基础设施故障而失败。"""
        return self.kind in ("ok", "not_found", "not_tradable")


async def check_broker_symbol(
    connector,
    broker_symbol: str,
    timeout: float = 3.0,
) -> BrokerSymbolCheck:
    """通过 MT5 bridge 向券商校验单个品种。

    ``timeout`` 用于约束准入/API 链路上的耗时 —— 仅 connector 自身的重试
    就可能耗时 8s×(1+重试次数)。
    """
    if connector is None:
        return BrokerSymbolCheck("unreachable", error="MT5 connector unavailable")
    try:
        result = await asyncio.wait_for(connector.get_symbol_spec(broker_symbol), timeout=timeout)
    except TimeoutError:
        return BrokerSymbolCheck("unreachable", error=f"timeout validating '{broker_symbol}'")
    except Exception as e:
        return BrokerSymbolCheck("unreachable", error=str(e))

    if not isinstance(result, dict) or not result.get("success"):
        error = str((result or {}).get("error") or "unknown bridge error")
        lowered = error.lower()
        # 来自本项目 mt5_bridge 的错误文案："Symbol X not found" /
        # "Symbol X not available after select" 属于券商的确定性答复；
        # "MT5 not connected" 表示终端掉线，属于基础设施故障。
        if "not found" in lowered or "not available" in lowered:
            return BrokerSymbolCheck(
                "not_found",
                error=f"Broker reports symbol '{broker_symbol}' does not exist: {error}",
            )
        return BrokerSymbolCheck("unreachable", error=error)

    data = result.get("data")
    if not isinstance(data, dict) or not data.get("symbol"):
        return BrokerSymbolCheck(
            "unexpected", error=f"Unexpected bridge response while validating '{broker_symbol}'"
        )
    trade_mode = data.get("trade_mode")
    if trade_mode is None:
        # 旧版 bridge 不返回 trade_mode —— 规格可查但可交易性未经验证。
        # 保持向后兼容，但必须显式暴露该情况。
        logger.warning(
            f"Bridge spec for '{broker_symbol}' omits trade_mode — tradability "
            f"not verified (update mt5_bridge for the trade_mode/path fields)"
        )
    elif int(trade_mode) in _NON_TRADABLE_TRADE_MODES:
        label = "DISABLED" if int(trade_mode) == 0 else "CLOSEONLY"
        return BrokerSymbolCheck(
            "not_tradable",
            spec=data,
            error=(
                f"Broker reports symbol '{broker_symbol}' is not tradable "
                f"(trade_mode={int(trade_mode)}: {label})"
            ),
        )
    return BrokerSymbolCheck("ok", spec=data)


def pip_value_suggestion(asset_class: str, digits: int, point: float) -> float:
    """按资产类别给出约定俗成的 pip 值，用于目录自动填充与 >10x 偏差校验。

    与静态 SYMBOL_PROFILES 的约定保持一致：
    GOLD(d2)=1.0、BTCUSD(d2)=1.0、EURUSD(d5)=0.0001、USDJPY(d3)=0.01。

    此前的"仅外汇"规则（3/5 位报价 → 10×point，其余 = point）会为
    XAUUSD(d2, point=0.01) 给出 0.01 —— 比系统其余部分标定的约定低 100 倍。
    """
    if asset_class == "forex":
        return point * 10 if digits in (3, 5) else point
    if asset_class in ("metal", "crypto"):
        # d<=2 的报价（XAUUSD、BTCUSD）：1 pip = 100 point；
        # 更细的报价（XAGUSD d3）：1 pip = 10 point。
        return point * 100 if digits <= 2 else point * 10
    if asset_class == "energy":
        return point * 10 if digits > 2 else point * 100
    # index / stock：1 个"pip" ≈ 1 个指数点
    return point


def normalize_lot_to_volume_grid(
    lot: float,
    volume_min: float | None = None,
    volume_max: float | None = None,
    volume_step: float | None = None,
) -> float | None:
    """把计算出的手数对齐到券商手数网格。

    bridge 会先按 ``volume_step`` 向下取整，再用 ``max(vol, volume_min)`` 静默
    上调；因此低于券商最小手数或偏离 step 网格的手数，实际成交规模会大于风险
    预算所假设的值（volume_min=1.0 的品种最多可放大 100 倍）。这里向下取整可
    保证实际风险 ≤ 计算风险；低于券商最小手数的手数不得发送，返回 None。

    由策略引擎（BotEngine）与 AI/MCP 下单工具共用，确保两条下单路径应用同一防线。
    未回填券商 volume 数据的品种（volume_min 与 volume_step 均为空）跳过该防线 ——
    保持原有行为。
    """
    if volume_min is None and not volume_step:
        return lot
    if volume_step and volume_step > 0:
        lot = round(math.floor(lot / volume_step) * volume_step, 10)
    if volume_max is not None and lot > volume_max:
        lot = volume_max
    if volume_min is not None and lot < volume_min:
        return None
    return lot


async def verify_enabled_symbols(
    connector,
    symbols: list[str],
    timeout: float = 3.0,
) -> dict[str, BrokerSymbolCheck]:
    """并发校验一组品种；本函数不会抛出异常。"""
    results: dict[str, BrokerSymbolCheck] = {}
    if not symbols:
        return results

    async def _one(symbol: str) -> None:
        results[symbol] = await check_broker_symbol(connector, symbol, timeout=timeout)

    try:
        await asyncio.wait_for(asyncio.gather(*(_one(s) for s in symbols)), timeout=timeout + 5.0)
    except TimeoutError:
        logger.warning(f"verify_enabled_symbols: batch timed out for {len(symbols)} symbols")
    except Exception as e:
        logger.warning(f"verify_enabled_symbols: batch failed: {e}")
    return results
