"""ML 障碍（triple-barrier）合理性校验 —— 共享纯函数。

品种管理页（symbols.py）与训练入口（ml.py）共用这一份判定逻辑，
避免两处阈值漂移（历史问题：护栏 [0.15, 6] 与 trainer 建议 [0.5, 1.5]
两套区间互相矛盾，运营者无所适从）。

判定原理：障碍是否合理，取决于它相对"单根 K 线实际波动"的大小，
而不是它的绝对价格数值。比值 = (pips × pip_value) / mean(high - low)。
- [0.15, 6.0]× 之外：结构性地产不出 BUY/SELL/HOLD 三类标签（拒绝）。
- [0.3, 3.0]× 之外：可疑但允许（告警）。
- [0.5, 1.5]×：实测能稳定产出三类（trainer 诊断建议区间的同源值）。
"""

from __future__ import annotations

from loguru import logger

# 结构性地产不出三类标签（硬拒绝）
_BARRIER_RATIO_REJECT = (0.15, 6.0)
# 可疑但允许（告警）
_BARRIER_RATIO_WARN = (0.3, 3.0)
# 推荐区间（trainer 诊断建议的同源值，见 ml/trainer.py 的 _barrier_diagnosis）
BARRIER_RATIO_RECOMMENDED = (0.5, 1.5)


def validate_ml_barriers(
    ml_tp_pips: float,
    ml_sl_pips: float,
    pip_value: float,
    mean_bar_range: float | None = None,
    *,
    symbol: str | None = None,
) -> tuple[bool, str | None]:
    """校验 ML 障碍相对单根 K 线波幅是否合理。

    参数：
        ml_tp_pips / ml_sl_pips：配置里的"点数"（pips）。
        pip_value：该品种点值（1 pip 对应的价格量）。
        mean_bar_range：近期 mean(high - low)；为 None 时退化为宽松量级校验
            （数据尚未回填时不阻塞配置）。
        symbol：仅用于告警日志，不影响判定。

    返回 (ok, message)：
        ok=False 时 message 为可操作的报错文案（含换算后的建议区间），
        可直接用于 HTTPException(detail=...) 或直接展示。
    """
    tp_delta = ml_tp_pips * pip_value
    sl_delta = ml_sl_pips * pip_value

    # 有行情数据：按 障碍/波幅 判定（与价格量级无关的正确尺度）。
    if mean_bar_range and mean_bar_range > 0:
        lo_rej, hi_rej = _BARRIER_RATIO_REJECT
        lo_warn, hi_warn = _BARRIER_RATIO_WARN
        for name, delta in (("ml_tp_pips", tp_delta), ("ml_sl_pips", sl_delta)):
            ratio = delta / mean_bar_range
            if ratio < lo_rej or ratio > hi_rej:
                return False, _reject_message(name, delta, ratio, mean_bar_range, pip_value)
            if ratio < lo_warn or ratio > hi_warn:
                logger.warning(
                    f"{symbol or ''} {name} × pip_value = {delta:g} is {ratio:.3g}× "
                    f"mean bar range ({mean_bar_range:g}) — outside recommended "
                    f"[{BARRIER_RATIO_RECOMMENDED[0]}, {BARRIER_RATIO_RECOMMENDED[1]}]× band; "
                    f"ML labeling may be skewed."
                )
        return True, None

    # 尚无行情数据 —— 只拦截明显的录入错误（如 0 或 1e6 量级的胖手指）。
    for name, delta in (("ml_tp_pips", tp_delta), ("ml_sl_pips", sl_delta)):
        if delta <= 0 or delta > 1_000_000:
            return False, _implausible_message(name, delta)
    return True, None


def _reject_message(name: str, delta: float, ratio: float, mean_bar_range: float, pip_value: float) -> str:
    lo_rej, hi_rej = _BARRIER_RATIO_REJECT
    lo_rec, hi_rec = BARRIER_RATIO_RECOMMENDED
    # 把"倍数区间"换算回运营者可填的 pips 区间。
    reject_lo = lo_rej * mean_bar_range / pip_value
    reject_hi = hi_rej * mean_bar_range / pip_value
    rec_lo = lo_rec * mean_bar_range / pip_value
    rec_hi = hi_rec * mean_bar_range / pip_value
    return (
        f"{name} × pip_value = {delta:g} is {ratio:.3g}× the symbol's "
        f"mean bar range ({mean_bar_range:g}). Barriers this far from typical "
        f"bar volatility cannot produce a 3-class (BUY/SELL/HOLD) training set. "
        f"Keep {name} within roughly [{lo_rej:g}, {hi_rej:g}]× mean bar range, "
        f"i.e. {name} ≈ [{reject_lo:g}, {reject_hi:g}] (recommended {rec_lo:g}–{rec_hi:g}) "
        f"for pip_value={pip_value:g}. "
        f"Note: this parameter only shapes training labels — it does NOT set live SL/TP; "
        f"use tp_atr_mult / sl_atr_mult for execution."
    )


def _implausible_message(name: str, delta: float) -> str:
    return (
        f"{name} × pip_value = {delta:g} is implausible. "
        f"Check ml_tp_pips / ml_sl_pips and pip_value."
    )
