"""laya 领域微调数据集生成（Phase 3-A Step 3A.1，只读，无模型推理）

数据：Postgres ohlcv_data 真实 GOLD M15（MT5 collector 写入，只读）。
标签：确定性合成规则（无泄漏）——
  - signal_alignment / market_regime 只用决策时点可见的证据（change_5_pct / price_vs_sma21 / vol_14）；
  - entry_decision 用 6h 未来方向（容差 0.05%）作为预测目标；
  - risk_check 用注入的账户扰动（consecutive_losses / daily_pnl）按规则标注；
  - execution_quality / data_quality 用模拟 staleness 标注。
账户扰动与模拟 staleness 是合成特征（真实回放无历史账户/陈旧标记），仅用于教会模型
"读到什么就判什么"；最终技能验收仍以持出集方向命中为准。

用法（backend/ 下）：
  PYTHONPATH=. /tmp/laya-api-venv/bin/python scripts/laya_finetune_data.py [--n 3000] [--seed 42]

输出：backend/models/laya_ft_data/train.jsonl + val.jsonl（时序留出最后 20% 月份）
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import laya_real_data_eval as lre  # noqa: E402  复用加载/回放/抽样

OUT_DIR = ROOT / "models" / "laya_ft_data"
OUT_DIR.mkdir(parents=True, exist_ok=True)

FWD_6H_BARS = 24
FWD_2H_BARS = 8
ENTRY_RET_TH = 0.0005   # 6h 方向容差 0.05%
MOMENTUM_TH = 0.02      # change_5_pct 绝对值阈值（%）
STALE_PROB = 0.20       # 20% 样本模拟数据陈旧
STALE_SHIFT_BARS = 8    # 陈旧 = 最后 bar 距今 > 3*15min → index 前移 8 根

RISK_PNL_CLEAR = -50.0
RISK_PNL_BLOCK = -300.0
RISK_LOSSES_BLOCK = 3
RISK_POSITIONS_BLOCK = 5


def account_perturbation(rng: random.Random) -> tuple[list[float], float]:
    """合成账户扰动：连续亏损模式 + 当日盈亏。"""
    losses = rng.randint(0, 4)
    profits = [round(rng.uniform(-15.0, -5.0), 2) for _ in range(losses)]
    profits.append(round(rng.uniform(2.0, 15.0), 2))  # 一次盈利结束连亏
    daily_pnl = rng.choice([-400.0, -200.0, -50.0, 0.0, 100.0, 250.0])
    return profits, daily_pnl


def label_row(snapshot: dict, sig: int, f6h: float, stale: bool,
              daily_pnl: float, profits: list[float]) -> dict:
    m = snapshot.get("market") or {}
    change5 = float(m.get("change_5_pct") or 0.0)
    sma21 = float(m.get("price_vs_sma21") or 1.0)
    cons_losses = int((snapshot.get("account") or {}).get("consecutive_losses") or 0)
    n_pos = int((snapshot.get("account") or {}).get("positions_count") or 0)

    # data_quality
    if not m:
        dq = "insufficient"
    elif stale or m.get("is_stale"):
        dq = "partial"
    else:
        dq = "sufficient"

    # signal_alignment（决策时点可见证据，无未来泄漏）
    if abs(change5) < MOMENTUM_TH:
        align = "mixed"
    elif (change5 > 0) == (sig > 0):
        align = "aligned"
    else:
        align = "conflict"

    # market_regime
    if sma21 > 1.0 and (change5 > 0) == (sig > 0):
        regime = "favorable"
    elif sma21 < 1.0 and (change5 > 0) != (sig > 0):
        regime = "adverse"
    else:
        regime = "neutral"

    # risk_check（账户规则）
    if cons_losses >= RISK_LOSSES_BLOCK or daily_pnl <= RISK_PNL_BLOCK or n_pos >= RISK_POSITIONS_BLOCK:
        risk = "block"
    elif cons_losses == 0 and daily_pnl >= RISK_PNL_CLEAR and n_pos <= 2:
        risk = "clear"
    else:
        risk = "caution"

    # execution_quality
    exec_q = "block" if stale else "clear"

    # entry_decision（预测目标：6h 未来方向，容差 0.05%）
    if sig > 0 and f6h > ENTRY_RET_TH:
        entry = "pass"
    elif sig < 0 and f6h < -ENTRY_RET_TH:
        entry = "pass"
    else:
        entry = "reject"

    return {
        "data_quality": dq,
        "signal_alignment": align,
        "market_regime": regime,
        "risk_check": risk,
        "execution_quality": exec_q,
        "entry_decision": entry,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--min-start", type=int, default=lre.MIN_START)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    m15 = lre.load_gold_m15()
    print(f"[data] GOLD M15 rows={len(m15):,} {m15.index[0]} -> {m15.index[-1]}")

    valid = []
    for i in range(args.min_start, len(m15) - lre.MIN_END):
        hist = m15.iloc[: i + 1]
        sig, _ = lre.replay_signal(hist)
        if sig == 0:
            continue
        valid.append(i)
    months = sorted({(m15.index[i].year, m15.index[i].month) for i in valid})
    per_month = max(1, math.ceil(args.n / len(months)))
    chosen = []
    for ym in months:
        idxs = [i for i in valid if (m15.index[i].year, m15.index[i].month) == ym]
        if per_month >= len(idxs):
            chosen.extend(idxs)
        else:
            step = len(idxs) / per_month
            chosen.extend(idxs[int(k * step)] for k in range(per_month))
    chosen = sorted(chosen)[: args.n]
    print(f"[data] sampled bars={len(chosen):,}")

    from app.ai.laya_gate import render_laya_state_prose
    from app.ai.laya_engine_observation import build_laya_engine_snapshot

    close = m15["close"].astype(float)
    rows = []
    for k, i in enumerate(chosen, 1):
        hist = m15.iloc[: i + 1]
        sig, sig_label = lre.replay_signal(hist)
        stale = rng.random() < STALE_PROB
        # 新鲜度语义对齐生产：决策时刻最后 bar 就是最新一根。回放数据停在历史日期，
        # 若原样进入 build_market_summary 会判 is_stale=True（距 now 数天）。
        # 因此 fresh 样本把 index 归一化到"现在"（决策时刻视角）；stale 样本显式前移。
        df = hist.copy()
        if stale:
            df.index = df.index - np.timedelta64(STALE_SHIFT_BARS * 15, "m")
        else:
            now = pd.Timestamp.utcnow().replace(tzinfo=None) - pd.Timedelta(minutes=15)
            df.index = pd.date_range(end=now, periods=len(df), freq="15min")
        profits, daily_pnl = account_perturbation(rng)
        snapshot = build_laya_engine_snapshot(
            symbol="GOLD", timeframe="M15", signal=sig, signal_label=sig_label,
            balance=10000.0, df=df, positions=[], daily_pnl=daily_pnl,
            recent_wr=None, recent_profits=profits,
        )
        text = render_laya_state_prose(snapshot)
        f6h = float(close.iloc[i + FWD_6H_BARS] / close.iloc[i] - 1.0)
        labels = label_row(snapshot, sig, f6h, stale, daily_pnl, profits)
        rows.append({
            "idx": int(i),
            "time": str(m15.index[i]),
            "signal": int(sig),
            "stale": bool(stale),
            "text": text,
            "labels": labels,
            "meta": {
                "consecutive_losses": int((snapshot["account"] or {}).get("consecutive_losses") or 0),
                "daily_pnl": daily_pnl,
                "change_5_pct": (snapshot.get("market") or {}).get("change_5_pct"),
                "price_vs_sma21": (snapshot.get("market") or {}).get("price_vs_sma21"),
            },
        })
        if k % 500 == 0 or k == len(chosen):
            print(f"  ... {k}/{len(chosen)} samples")

    # 时序留出：最后 20% 月份为 val
    row_months = sorted({r["time"][:7] for r in rows})
    val_months = set(row_months[-max(1, int(len(row_months) * 0.2)):])
    train = [r for r in rows if r["time"][:7] not in val_months]
    val = [r for r in rows if r["time"][:7] in val_months]

    def dump(path, rs):
        with open(path, "w") as f:
            for r in rs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        dist = Counter(l for r in rs for l in r["labels"].values())
        lens = [len(r["text"]) for r in rs]
        print(f"[out] {path.name}: n={len(rs)}  chars: min={min(lens)} med={int(np.median(lens))} max={max(lens)}")
        print(f"      label dist: {dict(dist)}")

    dump(OUT_DIR / "train.jsonl", train)
    dump(OUT_DIR / "val.jsonl", val)


if __name__ == "__main__":
    main()
