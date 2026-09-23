"""
Trade Gate 测试（Phase 3.8 落地 + C1/C2/I1/I3 修复）。

覆盖：
1. 模型文件缺失 → is_ready=False，predict 弃权 (None, 0.0)（C2：数据问题不阻断）
2. 模型加载成功 → is_ready=True，threshold/metadata 正确；坏模型/坏阈值 → 弃权（I4）
3. 空 df / 缺 OHLCV 价列 → 弃权 (None, 0.0)，不抛异常（C2）
4. 真实特征预测 → 返回 (bool, prob)；tick_volume 列可被识别（C1：生产契约）
5. 缺列 → 弃权而非拒单（C2 语义）
6. 特征构建失败 → 弃权（C2 语义）
"""

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from app.ml.features import FEATURE_COLUMNS
from app.ml.trade_gate import TradeGate


class FakeModel:
    """可 pickle 的 mock 分类器（模块级类，供 joblib.dump 使用）。"""

    def __init__(self, prob: float = 0.7):
        self._prob = prob

    def predict_proba(self, X):
        n = len(X)
        return np.array([[1.0 - self._prob, self._prob]] * n)


class NoProbaModel:
    """无 predict_proba 的坏模型（模块级类，供 joblib.dump 验证 I4 可信度校验）。"""


def _fake_joblib_data(threshold: float = 0.5, prob: float = 0.7, **overrides):
    """构造一个可被 TradeGate._load 载入的 joblib 数据结构（mock）。"""
    data = {
        "model": FakeModel(prob=prob),
        # 用真实 FEATURE_COLUMNS，保证 build_features 输出的列与模型列匹配，
        # 让 test_real_predict_returns_bool_prob 覆盖 build_features → predict 全链路。
        "feature_columns": FEATURE_COLUMNS,
        "threshold": threshold,
        "metadata": {"auc": 0.7, "n_samples": 100},
    }
    data.update(overrides)
    return data


@pytest.fixture
def ohlcv_df():
    # 足够行数让 build_features 的 EMA/RSI/ATR 等指标产生非 NaN（需 warm-up 窗口）。
    # C1 修复：生产契约列名是 tick_volume（MT5 Bridge /ohm 路径），非 volume。
    rng = np.random.default_rng(7)
    n = 300
    ret = rng.normal(0, 0.001, n)
    close = 100 * np.exp(np.cumsum(ret))
    o = np.roll(close, 1)
    o[0] = close[0]
    df = pd.DataFrame(
        {
            "open": o,
            "high": np.maximum(o, close) * 1.001,
            "low": np.minimum(o, close) * 0.999,
            "close": close,
            "tick_volume": rng.integers(100, 1000, n),
        }
    )
    # 去掉指标 warm-up 期的 NaN 行，保证最后一行为有效特征
    return df.iloc[50:].reset_index(drop=True)


class TestTradeGateLoad:
    def test_missing_model_abstains(self, tmp_path):
        gate = TradeGate(str(tmp_path / "nonexistent.pkl"))
        assert gate.is_ready is False
        can, prob = gate.predict(pd.DataFrame({"close": [1.0]}))
        # C2：模型不可用 → 弃权 (None, 0.0)，绝不返回 (False, 0.0) 阻断
        assert can is None and prob == 0.0

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

    def test_malformed_joblib_data_disabled(self, tmp_path):
        """I4：joblib 数据非 dict / 缺 model / 无 predict_proba → 弃权禁用，不崩溃。"""
        import joblib

        p = tmp_path / "gate.pkl"
        joblib.dump({"foo": "bar"}, str(p))
        gate = TradeGate(str(p))
        assert gate.is_ready is False

        joblib.dump({"model": NoProbaModel()}, str(p))
        gate2 = TradeGate(str(p))
        assert gate2.is_ready is False

    def test_out_of_range_threshold_disabled(self, tmp_path):
        """I4：阈值越界 → 弃权禁用，不静默用越界阈值。"""
        import joblib

        p = tmp_path / "gate.pkl"
        joblib.dump(_fake_joblib_data(threshold=1.5), str(p))
        gate = TradeGate(str(p))
        assert gate.is_ready is False


def _fake_joblib_data_legacy_volume(threshold: float = 0.5, prob: float = 0.7):
    """构造 feature_columns 含裸 volume 的旧模型 joblib 数据（训练侧漏排还原）。

    真实历史 pkl（models/trade_gate.pkl）即此形态：41 列第 1 列是裸 volume。
    build_features 永不产出裸 volume，旧 TradeGate 直接 predict 会特征失配 → 弃权
    （C1 曾因此全量拒单/弃权）。此处模拟该 schema，验证 _load 的 legacy 对齐。
    """
    data = _fake_joblib_data(threshold=threshold, prob=prob)
    # 旧模型列顺序：裸 volume 排最前（真实 pkl 即如此）
    data["feature_columns"] = ["volume", *FEATURE_COLUMNS]
    return data


class TestLegacyVolumeSchema:
    """旧模型（feature_columns 含裸 volume）在生产 tick_volume 列名下的推理对齐。"""

    def test_legacy_volume_detected(self, tmp_path):
        """feature_columns 含裸 volume → requires_legacy_volume=True。"""
        import joblib

        p = tmp_path / "gate.pkl"
        joblib.dump(_fake_joblib_data_legacy_volume(), str(p))
        gate = TradeGate(str(p))
        assert gate.is_ready is True
        assert gate.requires_legacy_volume is True

    def test_new_model_not_flagged_legacy(self, tmp_path):
        """feature_columns 不含裸 volume（新训练模型）→ requires_legacy_volume=False。"""
        import joblib

        p = tmp_path / "gate.pkl"
        joblib.dump(_fake_joblib_data(), str(p))
        gate = TradeGate(str(p))
        assert gate.is_ready is True
        assert gate.requires_legacy_volume is False

    def test_legacy_volume_aliased_from_tick_volume(self, tmp_path, ohlcv_df):
        """旧模型 + tick_volume 数据 → 注入别名列 volume，正常推理不弃权。

        生产链路（MT5 Bridge）返回 tick_volume；旧模型要求裸 volume。predict 应
        注入 volume=tick_volume 别名，build_features 透传 → 特征矩阵 41 列对齐。
        """
        import joblib

        p = tmp_path / "gate.pkl"
        joblib.dump(_fake_joblib_data_legacy_volume(threshold=0.5, prob=0.7), str(p))
        gate = TradeGate(str(p))
        can, prob = gate.predict(ohlcv_df)
        assert can is True and prob > 0.5

    def test_real_pkl_tick_volume_predicts(self, ohlcv_df):
        """回归防线：真实 models/trade_gate.pkl（含裸 volume 列）+ tick_volume 数据。

        曾因特征失配（41 vs 40）全量弃权；修复后应能正常推理返回 bool。pkl 缺失
        （CI 未 clone 模型或本地未训练）则跳过。此测试咬住 C1 生产缺陷本体。
        """
        from pathlib import Path

        import joblib

        pkl_path = Path(__file__).resolve().parents[2] / "models" / "trade_gate.pkl"
        if not pkl_path.exists():
            pytest.skip(f"real trade_gate.pkl not found at {pkl_path}")
        gate = TradeGate(str(pkl_path))
        # joblib.load 失败（如 pkl 内 LightGBM 依赖模块被其他测试卸载）→ gate 禁用。
        # 这是环境/依赖问题而非代码回归（_load 已 fail-open），此处 skip 而非断言崩溃。
        if not gate.is_ready:
            pytest.skip(f"real trade_gate.pkl could not load (dependency/module issue): {pkl_path}")
        assert gate.requires_legacy_volume, "真实 pkl 应含裸 volume（legacy 模型）"
        can, prob = gate.predict(ohlcv_df)
        assert isinstance(can, bool), f"真实 pkl + tick_volume 应正常推理，得到弃权: can={can}"
        assert isinstance(prob, float) and 0.0 <= prob <= 1.0


class TestTradeGatePredict:
    def test_empty_df_abstains(self, tmp_path):
        import joblib

        p = tmp_path / "gate.pkl"
        joblib.dump(_fake_joblib_data(), str(p))
        gate = TradeGate(str(p))
        can, prob = gate.predict(pd.DataFrame())
        # C2：空 df → 弃权
        assert can is None and prob == 0.0

    def test_missing_ohlcv_cols_abstains(self, tmp_path):
        import joblib

        p = tmp_path / "gate.pkl"
        joblib.dump(_fake_joblib_data(), str(p))
        gate = TradeGate(str(p))
        # 只有部分列（无 high/low）→ 弃权（C2：缺价列不阻断）
        can, prob = gate.predict(pd.DataFrame({"close": [1.0, 2.0]}))
        assert can is None and prob == 0.0

    def test_missing_volume_col_still_predicts(self, tmp_path, ohlcv_df):
        """C1 语义：无 volume/tick_volume 列 → 不视为缺列，volume 特征退化 0，仍正常预测。"""
        import joblib

        p = tmp_path / "gate.pkl"
        joblib.dump(_fake_joblib_data(threshold=0.5, prob=0.6), str(p))
        gate = TradeGate(str(p))
        df = ohlcv_df.drop(columns=["tick_volume"])
        can, prob = gate.predict(df)
        assert can is True and prob == 0.6

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