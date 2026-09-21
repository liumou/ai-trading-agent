"""
Laya 交易决策引擎 —— 合成样本 + LightGBM 基线（Phase 3.8，第三轮调研落地）
========================================================================
目标：回答「给足够样本让 laya 做交易决策」在数据层面是否成立。

做法（调研结论：真实 trades 仅 ~10-30 行，撑不起 RLCD 微调；唯一可行是合成）：
  1. 取 GOLD OHLCV（来自 `ohlcv_data` 只读查询，或本地 CSV）
  2. `ml/features.py build_features()` 构建 40+ 特征（入场快照等价物）
  3. `build_labels()` 三重障碍标签（BUY/HOLD/SELL = 该状态是否值得交易/方向）
  4. LightGBM 分类 → 测 AUC vs 基率
  5. 输出「决策门」结论：AUC 显著高于基率 → 值得继续微调 laya；否则终止该轨道

用法（backend/ 下，只读、不写生产库）：
  .venv/bin/python scripts/laya_synth_baseline.py --db        # 直连生产库只读拉 ohlcv
  .venv/bin/python scripts/laya_synth_baseline.py --csv x.csv # 从本地 CSV 读 OHLCV

输出：stdout 报告（n_samples / base_rate / AUC / 决策门），不落生产库。
"""

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# 确保能 import app.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OHLCV_COLS = ["open", "high", "low", "close", "volume"]


def load_from_csv(path: str) -> pd.DataFrame:
    """从 CSV 读 OHLCV（要求列 open/high/low/close/volume + datetime 索引或 time 列）。"""
    df = pd.read_csv(path)
    if "time" in df.columns:
        df = df.set_index(pd.to_datetime(df["time"]))
    elif "datetime" in df.columns:
        df = df.set_index(pd.to_datetime(df["datetime"]))
    df.index = pd.DatetimeIndex(df.index)
    df = df[OHLCV_COLS]
    return df.dropna()


async def load_from_db(symbol: str = "GOLD", timeframe: str = "M15", limit: int = 200_000) -> pd.DataFrame:
    """只读查询 ohlcv_data（default_transaction_read_only 硬保证，绝不写库）。

    I3 修复：列名是 volume（非 MT5 的 tick_volume），且必须按 timeframe 过滤——
    ohlcv_data 按 (symbol, timeframe, time) 存储，混查会把多个 timeframe 拼到同一
    时间索引，EMA/RSI 全错算、TimeSeriesSplit 因果假设失效。
    """
    import os

    import asyncpg

    raw = os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL")
    if not raw:
        env_file = Path(__file__).resolve().parent.parent / ".env"
        for line in env_file.read_text().splitlines():
            if line.startswith("DATABASE_URL"):
                raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not raw:
        raise RuntimeError("未找到 DATABASE_URL（.env 缺失）")
    url = raw.replace("+asyncpg", "").replace("+psycopg2", "")

    limit = min(max(int(limit), 1), 1_000_000)  # clamp，防 --limit 0 / 超大
    conn = await asyncpg.connect(url, timeout=20, server_settings={"default_transaction_read_only": "on"})
    try:
        rows = await conn.fetch(
            "SELECT time, open, high, low, close, volume FROM ohlcv_data "
            "WHERE symbol = $1 AND timeframe = $2 ORDER BY time DESC LIMIT $3",
            symbol,
            timeframe,
            limit,
        )
    finally:
        await conn.close()
    if not rows:
        raise RuntimeError(f"ohlcv_data 无 {symbol} {timeframe} 数据")
    df = pd.DataFrame([dict(r) for r in rows])
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time").sort_index()
    return df[OHLCV_COLS]


def run_baseline(
    df: pd.DataFrame, forward_bars: int = 10, tp_pips: float = 5.0, save_path: str | None = None
) -> float | None:
    """构建特征 + 标签，训 LightGBM，输出 AUC vs 基率。

    若 AUC ≥ 0.6 且传入 save_path：用全量数据重训一个可落地的「可否交易」门控模型
    （joblib 保存 model + feature_columns + threshold），供 app/ml/trade_gate.py 运行时加载。

    返回 mean_auc（或 None 当无法计算）。
    """
    import joblib

    from app.ml.features import build_features, build_labels

    from lightgbm import LGBMClassifier
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import TimeSeriesSplit

    # 合成入场快照特征 + 三重障碍标签（状态 → 是否值得交易/方向）
    features = build_features(df)
    labels = build_labels(df, forward_bars=forward_bars, tp_pips=tp_pips, sl_pips=None)

    # 对齐（build_labels 的 HOLD=0 可能占多数，保留三分类看方向判别；再折叠为 binary 看"可否交易"）
    fdf = features.loc[labels.index].copy()
    y3 = labels  # -1/0/1（SELL/HOLD/BUY）
    y_binary = (y3 != 0).astype(int)  # 1=该状态值得交易（BUY 或 SELL），0=HOLD

    # 去掉全 NaN 列与不参与预测的原始列
    cols = [c for c in fdf.columns if fdf[c].notna().any() and c not in ("open", "high", "low", "close", "tick_volume")]
    X = fdf[cols].fillna(0)

    # 仅保留有标签的行
    mask = y3.notna()
    X, y3, y_binary = X[mask], y3[mask], y_binary[mask]

    print(f"\n=== 合成样本基线 ===")
    print(f"样本数: {len(X):,}   特征数: {len(cols)}")
    if set(y3.unique()) <= {-1, 0, 1}:
        print(f"标签分布 (SELL/HOLD/BUY): {np.bincount((y3 + 1).astype(int))}")
    print(f"基率（可交易占比）: {y_binary.mean():.3f}")

    # 时间序列交叉验证（不 shuffle，防泄漏）
    tscv = TimeSeriesSplit(n_splits=3)
    aucs, base, skipped = [], [], 0
    for tr_idx, te_idx in tscv.split(X):
        y_te = y_binary.iloc[te_idx]
        if y_te.nunique() < 2:
            # 验证集只有单一类别时 ROC AUC 未定义（sklearn 抛 ValueError）——跳过该折
            skipped += 1
            continue
        clf = LGBMClassifier(n_estimators=100, learning_rate=0.05, verbose=-1)
        clf.fit(X.iloc[tr_idx], y_binary.iloc[tr_idx])
        proba = clf.predict_proba(X.iloc[te_idx])[:, 1]
        aucs.append(roc_auc_score(y_te, proba))
        base.append(y_te.mean())

    if not aucs:
        print("\n所有折的验证集都只有单一类别，ROC AUC 无法计算——请检查标签分布或调小 barrier（tp_pips）。")
        return
    mean_auc = float(np.mean(aucs))
    mean_base = float(np.mean(base))
    print(f"\nLightGBM AUC (3-fold TS): {mean_auc:.3f} ± {np.std(aucs):.3f}"
          + (f"（{skipped} 折因单类别跳过）" if skipped else ""))
    print(f"基率 (可交易占比)      : {mean_base:.3f}")

    # 决策门：AUC 显著高于 0.5 才算有可学习信号
    print(f"\n=== 决策门 ===")
    if mean_auc >= 0.6:
        print(f"AUC={mean_auc:.3f} ≥ 0.6 → 信号显著高于随机，**值得继续**（微调 laya 有数据基础）")
        print("建议下一步：3.9 Kaggle RLCD 微调 laya（用同一批合成样本的 JSONL 导出）")
        # 落地：AUC 达标且指定保存路径 → 全量重训可落地门控模型
        if save_path:
            print(f"\n=== 落地「可否交易」门控 ===")
            clf = LGBMClassifier(n_estimators=200, learning_rate=0.05, verbose=-1)
            clf.fit(X, y_binary)
            threshold = float(np.mean(base))  # 基率作默认阈值（可交易占比）
            import os

            os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
            joblib.dump(
                {
                    "model": clf,
                    "feature_columns": cols,
                    "threshold": threshold,
                    "metadata": {
                        "symbol": "GOLD",
                        "forward_bars": forward_bars,
                        "tp_pips": tp_pips,
                        "auc": mean_auc,
                        "n_samples": int(len(X)),
                        "created_at": datetime.now().isoformat(),
                    },
                },
                save_path,
            )
            print(f"门控模型已保存: {save_path}")
            print(f"  特征数: {len(cols)}  阈值(可交易概率): {threshold:.3f}  训练样本: {len(X):,}")
    elif mean_auc >= 0.55:
        print(f"AUC={mean_auc:.3f} ∈ [0.55, 0.6) → 信号存在但弱，建议扩大样本/调参后复测")
    else:
        print(f"AUC={mean_auc:.3f} < 0.55 → 信号接近随机，**终止该轨道**（微调只是记数字，不值）")
        print("提示：真实 trades 仅 ~10-30 行、pre_trade_snapshot 特征偏薄；若合成数据也带不来可预测信号，")
        print("     则 laya 交易决策微调（3.9 轨道）缺乏数据基础，建议冻结该方向，聚焦情绪预筛集成。")
        print("注意：标签窗口与训练样本存在重叠（bar i 的标签引用 i+1..i+10 的价格，这些未来 bar")
        print("     同时是后续训练样本），TimeSeriesSplit 无法消除该信息泄漏，AUC 可能系统性虚高。")
    return mean_auc


def main() -> None:
    parser = argparse.ArgumentParser(description="laya 交易决策合成样本 + LightGBM 基线")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--db", action="store_true", help="只读连接生产库拉 ohlcv_data")
    src.add_argument("--csv", metavar="PATH", help="从本地 CSV 读 OHLCV（列 open/high/low/close/volume）")
    parser.add_argument("--symbol", default="GOLD")
    parser.add_argument("--timeframe", default="M15", help="--db 模式下按 timeframe 过滤（默认 M15）")
    parser.add_argument("--forward-bars", type=int, default=10)
    parser.add_argument("--tp-pips", type=float, default=5.0)
    parser.add_argument("--limit", type=int, default=200_000)
    parser.add_argument(
        "--save", metavar="PATH", default="models/trade_gate.pkl",
        help="AUC≥0.6 时保存「可否交易」门控模型到此路径（用 --no-save 禁用）",
    )
    parser.add_argument("--no-save", action="store_true", help="不保存门控模型（仅评估基线）")
    args = parser.parse_args()

    if args.db:
        df = asyncio.run(load_from_db(args.symbol, args.timeframe, args.limit))
    else:
        df = load_from_csv(args.csv)
    print(f"OHLCV 行数: {len(df):,}  范围: {df.index.min()} → {df.index.max()}")

    save_path = None if args.no_save else args.save
    run_baseline(df, forward_bars=args.forward_bars, tp_pips=args.tp_pips, save_path=save_path)


if __name__ == "__main__":
    main()
