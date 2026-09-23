"""
Risk Manager — lot sizing, SL/TP calculation, trade permission checks.
"""

from dataclasses import dataclass
from datetime import UTC

from loguru import logger

from app.constants import (
    AI_MAX_THRESHOLD,
    AI_WORST_HOUR_THRESHOLD_BOOST,
    DEFAULT_COMMISSION_PCT,
    DEFAULT_SLIPPAGE_PIPS,
    HIGH_VOL_LOT_FACTOR,
    HIGH_VOL_THRESHOLD,
    KELLY_FRACTION,
    KELLY_MAX_RISK_MULT,
    KELLY_MIN_RISK,
    LOW_VOL_LOT_FACTOR,
    LOW_VOL_THRESHOLD,
    MIN_LOT,
    REGIME_LOT_MULTIPLIERS,
    STREAK_2_FACTOR,
    STREAK_3_FACTOR,
)


@dataclass
class VolatilityEstimate:
    """Abstraction for volatility source — allows swapping ATR for GARCH etc.

    Attributes:
        value: volatility value (e.g., ATR% = 0.5, GARCH conditional vol = 0.003)
        source: origin of the estimate ("atr", "garch", "ewma")
    """

    value: float
    source: str = "atr"

    @property
    def as_atr_pct(self) -> float:
        """Return value normalized to ATR% scale for threshold comparisons."""
        if self.source == "atr":
            return self.value
        if self.source == "garch":
            # GARCH conditional std → approximate ATR%: multiply by sqrt(period) * 100
            # 1-step daily vol 0.01 ≈ 1% ATR
            return self.value * 100
        # Default: assume already in ATR% scale
        return self.value


@dataclass
class SLTPResult:
    sl: float
    tp: float


class RiskManager:
    def __init__(
        self,
        max_risk_per_trade: float = 0.01,
        max_daily_loss: float = 0.03,
        max_concurrent_trades: int = 3,
        max_lot: float = 1.0,
        use_ai_filter: bool = True,
        ai_confidence_threshold: float = 0.7,
        pip_value: float = 1.0,
        price_decimals: int = 2,
        sl_atr_mult: float = 1.5,
        tp_atr_mult: float = 2.0,
        contract_size: float = 100.0,
        sl_mode: str = "atr",
        sl_floor: float | None = None,
        sl_cap: float | None = None,
        tp_mode: str = "atr",
        target_r_multiple: float | None = None,
    ):
        self.max_risk_per_trade = max_risk_per_trade
        self.max_daily_loss = max_daily_loss
        self.max_concurrent_trades = max_concurrent_trades
        self.max_lot = max_lot
        self.use_ai_filter = use_ai_filter
        self.ai_confidence_threshold = ai_confidence_threshold
        self.pip_value = pip_value
        self.price_decimals = price_decimals
        self.sl_atr_mult = sl_atr_mult
        self.tp_atr_mult = tp_atr_mult
        # Contract size is the per-lot multiplier MT5 reports for the symbol.
        # P&L per lot per price unit = contract_size, so accurate lot sizing
        # MUST go through it. Older code multiplied pip_value*100 and assumed
        # that equalled contract_size — only true for GOLD, broken for other
        # symbols (OIL ×10 undersize, BTC ×100 undersize, USDJPY ×10 oversize).
        self.contract_size = contract_size
        # 止损/止盈标准化模式（默认全部等价旧行为，可逐品种灰度）：
        #   sl_mode="clamped" → sl_distance 被夹在 [sl_floor, sl_cap]（价格单位）
        #   tp_mode="rr"      → tp_distance = target_r_multiple × 实际止损距离
        self.sl_mode = sl_mode
        self.sl_floor = sl_floor
        self.sl_cap = sl_cap
        self.tp_mode = tp_mode
        self.target_r_multiple = target_r_multiple
        self.current_regime = "normal"
        self.regime_lot_multiplier = 1.0

    def set_regime(self, regime) -> None:
        """Update current regime and apply corresponding lot multiplier.

        Accepts str or RegimeResult (backward compatible).
        """
        old = self.current_regime
        self.current_regime = str(regime)
        self.regime_lot_multiplier = REGIME_LOT_MULTIPLIERS.get(str(regime), 1.0)
        if old != regime:
            logger.info(f"Regime changed: {old} → {regime} (lot mult: {self.regime_lot_multiplier})")

    def calculate_lot_size(
        self,
        balance: float,
        sl_distance: float,
        pip_value: float | None = None,
        atr_pct: float | None = None,
        slippage_pips: float = DEFAULT_SLIPPAGE_PIPS,
        commission_pct: float = DEFAULT_COMMISSION_PCT,
    ) -> float:
        """Compute lot size from a stop-loss distance in **price units** (not pips).

        Identity: P&L_per_lot = sl_distance × contract_size. Lot count therefore
        equals risk_budget ÷ that quantity. The legacy ``pip_value × 100`` form
        only matched contract_size for GOLD; this version is correct for any
        broker symbol whose contract_size is set in SYMBOL_PROFILES.
        """
        if sl_distance <= 0:
            return MIN_LOT
        pv = pip_value if pip_value is not None else self.pip_value

        # Convert slippage (in pips) to a price-unit cushion before adding.
        effective_sl = sl_distance + slippage_pips * pv

        risk_budget = (balance * self.max_risk_per_trade) * (1 - commission_pct)
        lot = risk_budget / (effective_sl * self.contract_size)

        if atr_pct is not None:
            if atr_pct > HIGH_VOL_THRESHOLD:
                lot *= HIGH_VOL_LOT_FACTOR
            elif atr_pct < LOW_VOL_THRESHOLD:
                lot *= LOW_VOL_LOT_FACTOR

        lot *= self.regime_lot_multiplier

        lot = round(min(lot, self.max_lot), 2)
        return max(lot, MIN_LOT)

    def calculate_kelly_size(
        self,
        balance: float,
        sl_distance: float,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        pip_value: float | None = None,
    ) -> float:
        """Kelly Criterion position sizing (fractional Kelly = 0.25x for safety).

        ``sl_distance`` is a price-unit value matching ``calculate_lot_size``.
        """
        if avg_loss <= 0 or win_rate <= 0:
            return self.calculate_lot_size(balance, sl_distance, pip_value)

        b = avg_win / avg_loss
        q = 1 - win_rate
        kelly = (win_rate * b - q) / b

        kelly = max(kelly * KELLY_FRACTION, KELLY_MIN_RISK)
        kelly = min(kelly, self.max_risk_per_trade * KELLY_MAX_RISK_MULT)

        if sl_distance <= 0:
            return MIN_LOT
        lot = (balance * kelly) / (sl_distance * self.contract_size)
        lot = round(min(lot, self.max_lot), 2)
        return max(lot, MIN_LOT)

    def adjust_for_streak(self, base_lot: float, consecutive_losses: int, consecutive_wins: int) -> float:
        """Reduce lot after consecutive losses, restore after wins."""
        if consecutive_losses >= 3:
            return round(base_lot * STREAK_3_FACTOR, 2)
        elif consecutive_losses >= 2:
            return round(base_lot * STREAK_2_FACTOR, 2)
        return max(base_lot, MIN_LOT)

    def resolve_sl_tp_distances(
        self,
        atr: float,
        sl_mult: float | None = None,
        tp_mult: float | None = None,
    ) -> tuple[float, float]:
        """按当前配置解析 (止损距离, 止盈距离)，单位：价格单位。

        这是下单与确认门的**唯一**口径来源（避免 gate 用未 clamp 的倍数
        估算 R:R 与实际成交不一致）：
          - sl_mode="atr"（默认）：sl_distance = atr × sl_mult × regime_sl
          - sl_mode="clamped"：同上再夹到 [sl_floor, sl_cap]
          - tp_mode="atr"（默认）：tp_distance = atr × tp_mult × regime_tp
          - tp_mode="rr"：tp_distance = target_r_multiple × 实际止损距离，
            盈亏比恒等于 R（regime 不再扰动比值）
        """
        from app.strategy.regime import REGIME_ADJUSTMENTS

        adj = REGIME_ADJUSTMENTS.get(self.current_regime, {})
        sl_m = (sl_mult if sl_mult is not None else self.sl_atr_mult) * adj.get("sl_atr_mult_factor", 1.0)
        sl_distance = atr * sl_m

        if self.sl_mode == "clamped":
            if self.sl_floor is not None and self.sl_floor > 0:
                sl_distance = max(sl_distance, self.sl_floor)
            if self.sl_cap is not None and self.sl_cap > 0:
                sl_distance = min(sl_distance, self.sl_cap)

        if self.tp_mode == "rr" and self.target_r_multiple is not None and self.target_r_multiple > 0:
            tp_distance = self.target_r_multiple * sl_distance
        else:
            tp_m = (tp_mult if tp_mult is not None else self.tp_atr_mult) * adj.get("tp_atr_mult_factor", 1.0)
            tp_distance = atr * tp_m

        return sl_distance, tp_distance

    def expected_rr(self, atr: float) -> float:
        """预期盈亏比（TP/SL），与 gate 与 UI 展示共用同一口径。"""
        sl_distance, tp_distance = self.resolve_sl_tp_distances(atr)
        if sl_distance <= 0:
            return 0.0
        return tp_distance / sl_distance

    def calculate_sl_tp(
        self,
        entry_price: float,
        signal: int,
        atr: float,
        sl_mult: float | None = None,
        tp_mult: float | None = None,
    ) -> SLTPResult:
        sl_distance, tp_distance = self.resolve_sl_tp_distances(atr, sl_mult, tp_mult)
        if signal == 1:  # BUY
            sl = entry_price - sl_distance
            tp = entry_price + tp_distance
        else:  # SELL
            sl = entry_price + sl_distance
            tp = entry_price - tp_distance
        return SLTPResult(
            sl=round(sl, self.price_decimals),
            tp=round(tp, self.price_decimals),
        )

    def compute_effective_confidence(
        self,
        session_boost: float = 0.0,
        regime: str = "normal",
        recent_win_rate: float | None = None,
        drawdown_pct: float = 0.0,
    ) -> float:
        """Dynamic confidence threshold based on market conditions + performance."""
        from app.constants import (
            CONFIDENCE_DRAWDOWN_5_BOOST,
            CONFIDENCE_DRAWDOWN_10_BOOST,
            CONFIDENCE_LOW_WINRATE_BOOST,
            CONFIDENCE_LOW_WINRATE_THRESHOLD,
            CONFIDENCE_RANGING_BOOST,
            CONFIDENCE_TRENDING_HV_DISCOUNT,
        )

        threshold = self.ai_confidence_threshold + session_boost

        # Drawdown-based tightening
        if drawdown_pct > 0.10:
            threshold += CONFIDENCE_DRAWDOWN_10_BOOST
        elif drawdown_pct > 0.05:
            threshold += CONFIDENCE_DRAWDOWN_5_BOOST

        # Regime adjustment
        if regime == "ranging":
            threshold += CONFIDENCE_RANGING_BOOST
        elif regime == "trending_high_vol":
            threshold -= CONFIDENCE_TRENDING_HV_DISCOUNT

        # Recent performance
        if recent_win_rate is not None and recent_win_rate < CONFIDENCE_LOW_WINRATE_THRESHOLD:
            threshold += CONFIDENCE_LOW_WINRATE_BOOST

        return min(max(threshold, self.ai_confidence_threshold * 0.8), AI_MAX_THRESHOLD)

    def can_open_trade(
        self,
        current_positions: int,
        daily_pnl: float,
        balance: float,
        signal: int = 0,
        ai_sentiment: dict | None = None,
        trade_patterns: dict | None = None,
        effective_threshold: float | None = None,
    ) -> tuple[bool, str]:
        # Check max concurrent trades
        if current_positions >= self.max_concurrent_trades:
            return False, f"Max concurrent trades reached ({self.max_concurrent_trades})"

        # Check daily loss limit
        max_loss = balance * self.max_daily_loss
        if daily_pnl <= -max_loss:
            return False, f"Daily loss limit reached ({daily_pnl:.2f} <= -{max_loss:.2f})"

        # Use adaptive threshold if provided, otherwise fallback to existing logic
        if effective_threshold is not None:
            eff_threshold = effective_threshold
        else:
            eff_threshold = self.ai_confidence_threshold
            if trade_patterns:
                from datetime import datetime

                current_hour = datetime.now(UTC).hour
                worst_hours = trade_patterns.get("worst_hours", [])
                if current_hour in worst_hours:
                    eff_threshold = min(eff_threshold + AI_WORST_HOUR_THRESHOLD_BOOST, AI_MAX_THRESHOLD)

        # AI sentiment filter (optional)
        # C4 修复：仅当情绪来自 Claude 深析（engine=="llm" 或字段缺失的旧缓存）时才应用过滤。
        # laya 预筛行的 confidence 是 max-class 概率（非 Claude 自报置信度），刻度不同，
        # 若直接参与本闸门会静默改变通过率（laya 命中率远高于 Claude 行）——概率模型
        # 不得作风控拦截依据（调研不变量）。laya 行已在 news_sentiment 内用 laya_confidence_threshold
        # 自行把关，此处不再重复拦截。
        if self.use_ai_filter and ai_sentiment and signal != 0:
            engine = ai_sentiment.get("engine")
            if engine == "laya":
                return True, "OK"
            confidence = ai_sentiment.get("confidence", 0)
            label = ai_sentiment.get("label", "neutral")

            if confidence >= eff_threshold:
                if signal == 1 and label == "bearish":
                    return False, f"AI sentiment bearish (confidence: {confidence:.0%}) — BUY signal filtered"
                if signal == -1 and label == "bullish":
                    return False, f"AI sentiment bullish (confidence: {confidence:.0%}) — SELL signal filtered"

        return True, "OK"
