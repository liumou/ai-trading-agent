"""
Trade Gate — 「可否交易」门控（Phase 3.8 落地，LightGBM 基线）。

从 GOLD M15 OHLCV 合成样本（三重障碍标签）训练的 LightGBM 二分类门控，
在交易前判断当前状态「是否值得开仓」。模型由 scripts/laya_synth_baseline.py --save 产出
（joblib: {"model", "feature_columns", "threshold", "metadata"}）。

设计约束：
- 轻量运行时加载（joblib），风格对齐 app/ml/predictor.py 的 MLPredictor。
- 输入 df 需含 OHLCV 列（open/high/low/close/volume），内部 build_features 产出 41 特征。
- predict 只读「最后一根 bar」的状态，返回 (can_trade, prob)。
- 门控只做「可否交易」预筛，不决定方向/仓位——那些仍是策略与风控的职责。
"""

from pathlib import Path

import pandas as pd
from loguru import logger

from app.ml.features import FEATURE_COLUMNS, build_features


class TradeGate:
    """「可否交易」门控：加载 joblib 模型，预测当前 OHLCV 状态是否值得开仓。"""

    def __init__(self, model_path: str):
        self.model = None
        self.feature_columns = FEATURE_COLUMNS
        self.threshold = 0.5
        self.metadata: dict = {}
        self._load(model_path)

    def _load(self, path: str):
        if not Path(path).exists():
            logger.warning(f"Trade gate model not found at {path}")
            return
        import joblib

        data = joblib.load(path)
        self.model = data["model"]
        self.feature_columns = data.get("feature_columns", data.get("features", FEATURE_COLUMNS))
        self.threshold = float(data.get("threshold", 0.5))
        self.metadata = data.get("metadata", {})
        logger.info(f"Trade gate loaded from {path} (threshold={self.threshold:.3f})")

    @property
    def is_ready(self) -> bool:
        return self.model is not None

    def predict(self, df: pd.DataFrame) -> tuple[bool, float]:
        """预测最新 bar 是否可交易。

        Returns (can_trade, prob)：
        - can_trade: prob >= threshold
        - prob: 模型输出的"值得交易"概率（0~1）
        """
        if not self.is_ready:
            return False, 0.0

        # 缺 OHLCV 列 / 空 df → 降级为不可交易（不抛异常）
        required = {"open", "high", "low", "close", "volume"}
        if df is None or df.empty or not required.issubset(df.columns):
            logger.warning("Trade gate predict: missing OHLCV columns or empty df, -> can_trade=False")
            return False, 0.0

        features = build_features(df)
        available = [c for c in self.feature_columns if c in features.columns]
        # 用最后一根 bar 的状态（与 MLPredictor 一致）
        X = features[available].iloc[[-1]]

        if X.isna().any(axis=1).iloc[0]:
            logger.warning("Trade gate prediction has NaN features, defaulting to can_trade=False")
            return False, 0.0

        prob = float(self.model.predict_proba(X)[0, 1])  # P(可交易)
        return prob >= self.threshold, prob
