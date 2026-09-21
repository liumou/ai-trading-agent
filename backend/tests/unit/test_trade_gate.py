"""
Trade Gate 测试（Phase 3.8 落地）。

覆盖：
1. 模型文件缺失 → is_ready=False，predict 降级 (False, 0.0)
2. 模型加载成功 → is_ready=True，threshold/metadata 正确
3. 空 df / 缺 OHLCV 列 → 降级 (False, 0.0)，不抛异常
4. 真实特征预测 → 返回 (bool, prob)
"""

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from app.ml.trade_gate import TradeGate


def _fake_joblib_data(threshold: float = 0.5, prob: float = 0.7):
    """构造一个可被 TradeGate._load 载入的 joblib 数据结构（mock）。"""
    class FakeModel:
        def predict_proba(self, X):
            return np.array([[1.0 - prob, prob]])
    return {
        "model": FakeModel(),
        "feature_columns": ["close", "rsi_14", "adx"],
        "threshold": threshold,
        "metadata": {"auc": 0.7, "n_samples": 100},
    }


@pytest.fixture
def ohlcv_df():
    rng = np.random.default_rng(7)
    n = 30
    ret = rng.normal(0, 0.001, n)
    close = 100 * np.exp(np.cumsum(ret))
    o = np.roll(close, 1)
    o[0] = close[0]
    return pd.DataFrame(
        {
            "open": o,
            "high": np.maximum(o, close) * 1.001,
            "low": np.minimum(o, close) * 0.999,
            "close": close,
            "volume": rng.integers(100, 1000, n),
        }
    )


class TestTradeGateLoad:
    def test_missing_model_falls_back(self, tmp_path):
        gate = TradeGate(str(tmp_path / "nonexistent.pkl"))
        assert gate.is_ready is False
        can, prob = gate.predict(pd.DataFrame({"close": [1.0]}))
        assert can is False and prob == 0.0

    def test_load_success(self, tmp_path):
        import joblib
        p = tmp_path / "gate.pkl"
        joblib.dump(_fake_joblib_data(threshold=0.6), str(p))
        gate = TradeGate(str(p))
        assert gate.is_ready is True
        assert gate.threshold == 0.6
        assert gate.metadata["auc"] == 0.7

    def test_default_dirs_missing_model(self, tmp_path):
        """默认 FEATURE_COLUMNS 兜底 + model=None → 不吃模型文件也能构造。"""
        gate = TradeGate(str(tmp_path / "x.pkl"))
        assert gate.feature_columns  # FEATURE_COLUMNS 非空


class TestTradeGatePredict:
    def test_empty_df_falls_back(self, tmp_path):
        import joblib
        p = tmp_path / "gate.pkl"
        joblib.dump(_fake_joblib_data(), str(p))
        gate = TradeGate(str(p))
        can, prob = gate.predict(pd.DataFrame())
        assert can is False and prob == 0.0

    def test_missing_ohlcv_cols_falls_back(self, tmp_path):
        import joblib
        p = tmp_path / "gate.pkl"
        joblib.dump(_fake_joblib_data(), str(p))
        gate = TradeGate(str(p))
        # 只有部分列（无 high/low/volume）→ 降级
        can, prob = gate.predict(pd.DataFrame({"close": [1.0, 2.0]}))
        assert can is False and prob == 0.0

    def test_real_predict_returns_bool_prob(self, tmp_path, ohlcv_df):
        import joblib
        p = tmp_path / "gate.pkl"
        joblib.dump(_fake_joblib_data(threshold=0.5, prob=0.7), str(p))
        gate = TradeGate(str(p))
        can, prob = gate.predict(ohlcv_df)
        assert isinstance(can, bool)
        assert isinstance(prob, float)
        # 合成数据上 build_features 会成功产出特征；prob=0.7 > threshold 0.5
        assert prob > 0.5
        assert can is True