"""
Trade Gate — 「可否交易」门控（Phase 3.8 落地，LightGBM 基线）。

从 GOLD M15 OHLCV 合成样本（三重障碍标签）训练的 LightGBM 二分类门控，
在交易前判断当前状态「是否值得开仓」。模型由 scripts/laya_synth_baseline.py --save 产出
（joblib: {"model", "feature_columns", "threshold", "metadata"}）。

设计约束：
- 轻量运行时加载（joblib），风格对齐 app/ml/predictor.py 的 MLPredictor。
- 输入 df 需含 OHLCV 列（open/high/low/close + tick_volume），内部 build_features 产出特征。
- predict 只读「最后一根 bar」的状态，返回 (can_trade, prob)。
- 门控只做「可否交易」预筛，不决定方向/仓位——那些仍是策略与风控的职责。
- **三种返回语义**（C2 修复）：(True, p) 通过；(False, p) 明确拒绝；
  返回 (None, 0.0) 表示「数据不足/模型不可用」= **弃权**，调用方必须放行，
  绝不允许数据 schema 问题被转译成全量拒单。
"""

from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger

from app.ml.features import FEATURE_COLUMNS, build_features

# OHLCV 必备价量列。生产链路（MT5 Bridge）返回 tick_volume（非 volume）——
# C1 修复：与 features.build_features 的契约对齐，兼容历史 volume 命名。
_BAR_COLUMNS = {"open", "high", "low", "close"}
# 可选的成交量列（缺任一都不算「缺列」——volume 特征会按 build_features 填充 0）
_VOLUME_COLUMNS = ("tick_volume", "volume")
# 历史模型的裸 volume 列：早期训练脚本未排除裸 volume 原始列（见
# scripts/laya_synth_baseline.py 排除元组），导致已训练的 pkl feature_columns 含
# 裸 volume。build_features 永不产出裸 volume（只产出 volume_sma_ratio），推理时
# 必须从 tick_volume 注入别名列（_load 里 schema 对齐），否则特征数失配 → 全量弃权。
_LEGACY_VOLUME_COLUMN = "volume"


class TradeGate:
    """「可否交易」门控：加载 joblib 模型，预测当前 OHLCV 状态是否值得开仓。"""

    def __init__(self, model_path: str):
        self.model = None
        self.feature_columns = FEATURE_COLUMNS
        # 兼容旧模型：feature_columns 含裸 volume 时置真，predict 注入别名列
        self.requires_legacy_volume = False
        self.threshold = 0.5
        self.metadata: dict = {}
        self._load(model_path)

    def _load(self, path: str):
        if not Path(path).exists():
            logger.warning(f"Trade gate model not found at {path}")
            return
        import joblib

        try:
            data = joblib.load(path)
        except Exception as e:
            # C2 语义补漏：joblib.load 反序列化失败（如 pkl 依赖的模块不可用/损坏）必须
            # fail-open——禁用 gate 而非崩溃。曾实测：pkl 内 LightGBM 对象依赖某模块被
            # 卸载后，find_class 的 __import__ 抛异常 → TradeGate() 构造直接崩溃。
            logger.warning(f"Trade gate model at {path} failed to load, disabled: {e}")
            return
        if not isinstance(data, dict) or "model" not in data:
            logger.warning(f"Trade gate model at {path} is malformed (expected dict with 'model')")
            return
        model = data["model"]
        # I4 修复：加载期最小可信度校验——模型必须有 predict_proba、阈值必须合法。
        if not hasattr(model, "predict_proba"):
            logger.warning(f"Trade gate model at {path} lacks predict_proba; disabled")
            return
        threshold = float(data.get("threshold", 0.5))
        if not (0.0 <= threshold <= 1.0):
            logger.warning(f"Trade gate threshold {threshold} out of range; disabled")
            return
        self.model = model
        self.feature_columns = data.get("feature_columns", data.get("features", FEATURE_COLUMNS))
        # C1 schema 对齐：旧模型 feature_columns 含裸 volume（训练脚本漏排除的原始列），
        # build_features 永不产出裸 volume（只产出 volume_sma_ratio）。含则记录需要注入别名列。
        self.requires_legacy_volume = _LEGACY_VOLUME_COLUMN in self.feature_columns
        self.threshold = threshold
        self.metadata = data.get("metadata", {})
        logger.info(
            f"Trade gate loaded from {path} (threshold={self.threshold:.3f}, "
            f"features={len(self.feature_columns)}, legacy_volume={self.requires_legacy_volume})"
        )

    @property
    def is_ready(self) -> bool:
        return self.model is not None

    def predict(self, df: Optional[pd.DataFrame]) -> tuple[Optional[bool], float]:
        """预测最新 bar 是否可交易（三种返回语义，见模块 docstring）。

        - 模型不可用 / df 为空 / 缺 OHLCV 价列 → **弃权** (None, 0.0)，调用方放行。
        - 特征构建失败 / NaN 比例异常 → **弃权** (None, 0.0)（数据问题不阻断）。
        - 正常 → (can_trade, prob)。
        """
        if df is None or df.empty:
            logger.warning("Trade gate predict: empty df -> abstain")
            return None, 0.0

        missing = _BAR_COLUMNS - set(df.columns)
        if missing:
            logger.warning(f"Trade gate predict: missing OHLC columns {missing} -> abstain")
            return None, 0.0

        # 成交量列：生产链路是 tick_volume，历史契约是 volume——缺则交给 build_features
        # 的 fillna(0)（volume_sma_ratio 等特征会退化为 0）。这是特征退化而非「数据缺失」，
        # 与 build_features 训练时的语义一致（见其 docstring：tick_volume 可选）。
        if not any(col in df.columns for col in _VOLUME_COLUMNS):
            logger.warning("Trade gate predict: no volume column (tick_volume/volume); volume features will be 0")
        # C1 schema 对齐：旧模型要求裸 volume 列，而生产链路只有 tick_volume。
        # 注入别名列让 build_features 透传它 → 特征矩阵含裸 volume，模型特征数对齐。
        elif (
            self.requires_legacy_volume
            and _LEGACY_VOLUME_COLUMN not in df.columns
            and "tick_volume" in df.columns
        ):
            df = df.copy()
            df[_LEGACY_VOLUME_COLUMN] = df["tick_volume"]
            logger.info("Trade gate predict: legacy model needs bare volume; aliased tick_volume -> volume")

        try:
            features = build_features(df)
            available = [c for c in self.feature_columns if c in features.columns]
            if not available:
                logger.warning("Trade gate predict: no feature columns available -> abstain")
                return None, 0.0
            # 用最后一根 bar 的状态（与 MLPredictor 一致）
            X = features[available].iloc[[-1]]
            # 训练时 X 已 fillna(0)，这里填充保持一致（最后一根 bar 常落在指标窗口尾端
            # 而有少量 NaN，如 volume_sma_ratio、atr_percentile（rolling(100)）。
            # 注意：fillna(0) 会让异常比例高的特征变成训练分布外常数值——
            # 若最后一行的 NaN 比例过高，弃权（避免门控基于退化特征做硬否决）。
            # 检查必须在 fillna 之前做（fillna(0) 后 isna 恒为 0，检查将是死代码）。
            if X.iloc[0].isna().mean() > 0.3:
                logger.warning(
                    f"Trade gate predict: last bar has {X.iloc[0].isna().mean():.0%} NaN before fill -> abstain"
                )
                return None, 0.0
            X = X.fillna(0)

            prob = float(self.model.predict_proba(X)[0, 1])  # P(可交易)
            return (prob >= self.threshold), prob
        except Exception as e:
            # 特征构建/推理异常 → 弃权（数据问题不阻断，与 C2 语义一致）
            logger.warning(f"Trade gate predict failed, abstaining: {e}")
            return None, 0.0