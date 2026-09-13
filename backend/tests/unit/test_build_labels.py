"""Triple-barrier 标签生成（app/ml/features.py::build_labels）单元测试。

回归背景：BTCUSD 训练曾报 "Training data is missing classes: ['HOLD']"。
两个已修复的缺陷：

1. 障碍尺度相对波动过小（配置问题，见 symbols.py 的护栏），使 HOLD 结构性为 0；
2. 同根 K 线内上下屏障都被触及时，旧的 ``long_hit <= short_hit`` 一律判 BUY，
   在障碍小于单根波幅时把大量样本伪造成 BUY（BTCUSD 实测该 tie 占比达 84.8%）。

这些测试锁定 build_labels 自身的语义（屏障对称、tie→HOLD、尾部 NaN），
不依赖外部行情数据。
"""

import numpy as np
import pandas as pd

from app.ml.features import build_labels


def _df(closes, highs=None, lows=None) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    highs = np.asarray(highs, dtype=float) if highs is not None else closes.copy()
    lows = np.asarray(lows, dtype=float) if lows is not None else closes.copy()
    return pd.DataFrame(
        {
            "time": pd.date_range("2025-01-01", periods=len(closes), freq="h"),
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": np.ones(len(closes)),
        }
    )


class TestBarrierSemantics:
    def test_up_only_is_buy(self):
        # entry=100；下一根 high 触及 105，low 未跌破 95 → BUY(1)
        df = _df([100.0, 101.0, 105.0, 105.0], highs=[100, 101, 105, 105], lows=[100, 99, 100, 100])
        labels = build_labels(df, forward_bars=3, tp_pips=5.0, sl_pips=5.0)
        assert labels.iloc[0] == 1

    def test_down_only_is_sell(self):
        # entry=100；下一根 low 跌破 95，high 未触及 105 → SELL(-1)
        df = _df([100.0, 99.0, 95.0, 95.0], highs=[100, 101, 100, 100], lows=[100, 99, 95, 95])
        labels = build_labels(df, forward_bars=3, tp_pips=5.0, sl_pips=5.0)
        assert labels.iloc[0] == -1

    def test_neither_is_hold(self):
        # 全程在 ±5 内震荡 → 时间屏障，HOLD(0)
        df = _df([100.0, 101.0, 102.0, 100.5], highs=[100, 102, 103, 102], lows=[100, 100, 101, 100])
        labels = build_labels(df, forward_bars=3, tp_pips=5.0, sl_pips=5.0)
        assert labels.iloc[0] == 0

    def test_barriers_are_symmetric_sl_pips_not_used(self):
        """sl_pips 当前不生效（保留给 meta-labeling）——屏障必须对称。

        entry=100, tp_pips=5 → 向下对称屏障 = 95。
        取 low=97：若误用 sl_pips=1（屏障 99）则触发 SELL；对称屏障 95 不触发 → HOLD。
        """
        df = _df([100.0, 100.0, 100.0], highs=[100, 100, 100], lows=[100, 97, 100])
        labels = build_labels(df, forward_bars=2, tp_pips=5.0, sl_pips=1.0)
        assert labels.iloc[0] == 0


class TestSameBarTie:
    """同根 K 线内上下屏障都被触及时必须判 HOLD，而非伪造 BUY。"""

    def test_same_bar_double_touch_is_hold(self):
        # 单根 K 线的 high/low 同时穿过上下屏障（OHLC 无法判断先后）→ HOLD
        df = _df([100.0, 100.0, 100.0], highs=[100, 110, 100], lows=[100, 90, 100])
        labels = build_labels(df, forward_bars=2, tp_pips=5.0, sl_pips=5.0)
        assert labels.iloc[0] == 0, "tie 不得被伪造成 BUY"

    def test_different_bar_touch_first_wins(self):
        # 不同根先后触发 → 先到者胜（此处先上后下 → BUY）
        df = _df(
            [100.0, 100.0, 100.0, 100.0],
            highs=[100, 106, 100, 100],
            lows=[100, 100, 100, 94],
        )
        labels = build_labels(df, forward_bars=3, tp_pips=5.0, sl_pips=5.0)
        assert labels.iloc[0] == 1

    def test_tiny_barrier_does_not_manufacture_buy_bias(self):
        """极端小屏障（贴近真实报错场景）下不应出现单边 BUY 洪水。"""
        rng = np.random.default_rng(0)
        closes = 100 + np.cumsum(rng.normal(0, 1.0, 400))
        highs = closes + np.abs(rng.normal(0, 1.5, 400))
        lows = closes - np.abs(rng.normal(0, 1.5, 400))
        df = _df(closes, highs, lows)
        labels = build_labels(df, forward_bars=5, tp_pips=0.05, sl_pips=0.05).dropna()
        buys = int((labels == 1).sum())
        sells = int((labels == -1).sum())
        holds = int((labels == 0).sum())
        # 极窄屏障下绝大多数窗口双边都触 → 应主要是 HOLD，而非 BUY 独占。
        assert holds > buys + sells, f"HOLD={holds} 应占多数，实际 BUY={buys} SELL={sells}"


class TestTailAndShape:
    def test_last_forward_bars_rows_are_nan(self):
        df = _df([100.0] * 20, highs=[100] * 20, lows=[100] * 20)
        labels = build_labels(df, forward_bars=5, tp_pips=5.0, sl_pips=5.0)
        assert labels.iloc[-5:].isna().all()
        assert labels.iloc[:-5].notna().all()

    def test_index_and_length_preserved(self):
        df = _df([100.0] * 30)
        labels = build_labels(df, forward_bars=10, tp_pips=5.0, sl_pips=5.0)
        assert len(labels) == len(df)
        assert labels.index.equals(df.index)

    def test_three_classes_can_coexist(self):
        """健康障碍尺度下三类必须都能出现（即修复前的报错不应再发生）。"""
        rng = np.random.default_rng(7)
        closes = 100 + np.cumsum(rng.normal(0, 2.0, 2000))
        highs = closes + np.abs(rng.normal(0, 3.0, 2000))
        lows = closes - np.abs(rng.normal(0, 3.0, 2000))
        df = _df(closes, highs, lows)
        labels = build_labels(df, forward_bars=5, tp_pips=6.0, sl_pips=6.0).dropna()
        counts = {c: int((labels == c).sum()) for c in (-1, 0, 1)}
        assert all(counts[c] > 0 for c in (-1, 0, 1)), f"三类应齐全，实际 {counts}"
