"""laya 6 问闸门 —— 真实 MT5 历史数据回放评估（Phase 2/3，只读，不用 mock）

回答：调用 laya 时喂了什么数据？成功率多少？数据够不够？提高空间在哪？
数据：Postgres ohlcv_data（MT5 collector 写入的真实 GOLD M15/H1，只读）。
模型：真实 laya（convaiinnovations/laya，本地权重缓存，隔离 venv 运行）。

用法（backend/ 下）：
  PYTHONPATH=. HF_ENDPOINT=https://hf-mirror.com \\
    /tmp/laya-api-venv/bin/python scripts/laya_real_data_eval.py [--n 480] [--variants a,b] [--seed 42]

输出：stdout 指标表 + deliverables/laya_eval_results_{variant}.json（不落生产库）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("USE_TF", "0")

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT.parent / ".planning" / "2026-09-22-laya-mt5" / "deliverables"
OUT_DIR.mkdir(parents=True, exist_ok=True)

HORIZONS_BARS = {"2h": 8, "6h": 24, "1d": 96}   # M15 根数
LOOKBACK = 200                                   # 生产 engine DEFAULT_OHLCV_BARS 同口径
MIN_START = LOOKBACK + 10                        # 需要完整回看窗口
MIN_END = 96 + 10                                # 需要 1d 标签


def load_env(name: str) -> str:
    env_file = ROOT / ".env"
    for line in env_file.read_text().splitlines():
        if line.startswith(name):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(f"{name} 缺失（.env）")


def load_gold_m15(symbol: str = "GOLD", timeframe: str = "M15") -> pd.DataFrame:
    raw = load_env("DATABASE_URL_SYNC").replace("postgresql+psycopg2://", "postgresql://")
    conn = psycopg2.connect(raw, connect_timeout=15)
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()
    cur.execute(
        "SELECT time, open, high, low, close, volume FROM ohlcv_data "
        "WHERE symbol=%s AND timeframe=%s ORDER BY time",
        (symbol, timeframe),
    )
    df = pd.DataFrame(cur.fetchall(), columns=["time", "open", "high", "low", "close", "volume"])
    cur.close(); conn.close()
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df[["open", "high", "low", "close", "volume"]]


def resample_h1(m15: pd.DataFrame) -> pd.DataFrame:
    h1 = m15.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return h1.dropna(subset=["close"])


def replay_signal(hist: pd.DataFrame) -> tuple[int, str]:
    """确定性回放信号（真实数据上的规则，非 mock）：SMA9/21 交叉 + 近 1 根动量。

    生产 engine 由策略栈产出 signal；历史无存储的 signal 记录，故用同源规则复现
    “系统此刻想开仓”的判定输入，语义与生产确定性链一致（文档化）。
    """
    closes = hist["close"]
    if len(closes) < 21:
        return 0, "insufficient"
    sma9 = closes.tail(9).mean()
    sma21 = closes.tail(21).mean()
    d1 = closes.iloc[-1] / closes.iloc[-2] - 1.0 if closes.iloc[-2] > 0 else 0.0
    if sma9 > sma21 and d1 > 0:
        return 1, "sma_cross_momentum"
    if sma9 < sma21 and d1 < 0:
        return -1, "sma_cross_momentum"
    return 0, "sma_cross_momentum_flat"


def deterministic_chain(signal: int, summary: dict) -> tuple[bool, list[str]]:
    """参照链（确定性，复现 TradeGate 主要规则语义）：chain_can_trade + 拒因列表。

    规则：信号非 0 且 5 根动量不过度、波动不过度（99 分位）、价格区间位置非极端。
    """
    reasons = []
    can = True
    if signal == 0:
        can = False; reasons.append("no_signal")
    chg5 = abs(float(summary.get("change_5_pct", 0.0)))
    if chg5 > 3.0:
        can = False; reasons.append("momentum_extreme")
    vol = float(summary.get("vol_14", 0.0)) * 100.0
    if vol > 1.2:
        can = False; reasons.append("volatility_extreme")
    pos = float(summary.get("range_position_50", 0.5))
    if pos > 0.95 or pos < 0.05:
        can = False; reasons.append("range_extreme")
    if can:
        reasons.append("chain_pass")
    return can, reasons


def enrich_state_b(snapshot: dict, hist: pd.DataFrame, h1: pd.DataFrame) -> dict:
    """变体 B：增强输入（真实数据确定性预计算）——多周期摘要 + 指标 + 近期原始价格。

    仅加“生产侧已存在或确定性可算”的字段，不引入新依赖。
    """
    from app.ai.laya_engine_observation import build_market_summary
    import pandas as pd

    s = json.loads(json.dumps(snapshot))  # 深拷贝（dict 含嵌套）
    m = dict(s.get("market", {}))
    closes = hist["close"].astype(float)

    # + H1 多周期摘要（由同一 M15 源确定性聚合，生产可算）
    h1_sum = build_market_summary(h1)
    for k, v in h1_sum.items():
        m[f"h1_{k}"] = v

    # + 指标（真实数据）
    rets = closes.pct_change().dropna()
    if len(closes) >= 15:
        tr = pd.concat([
            hist["high"] - hist["low"],
            (hist["high"] - hist["close"].shift()).abs(),
            (hist["low"] - hist["close"].shift()).abs(),
        ], axis=1).max(axis=1).dropna()
        m["atr14"] = round(float(tr.tail(14).mean()), 4)
    if len(rets) >= 14:
        up = rets.tail(14).clip(lower=0).mean()
        dn = (-rets.tail(14).clip(upper=0)).mean()
        m["rsi14"] = round(100.0 - 100.0 / (1.0 + up / dn) if dn > 0 else 100.0, 2)
    vol = closes.pct_change().dropna().tail(28)
    if len(vol) >= 28:
        m["vol_trend_14_14"] = round(float(vol.tail(14).std() / vol.head(14).std() - 1.0) * 100.0, 3)
    # + 近期原始价格（最近 12 根 close，文本上下文）
    m["last_closes"] = [round(float(x), 4) for x in closes.tail(12).tolist()]

    s["market"] = m
    return s


def state_variant_c1_c2(snapshot: dict, hist: pd.DataFrame, *, with_bars: bool) -> str:
    """变体 C：把证据渲染成自然语言文本（非 JSON），验证模型是否“吃”文字版证据。

    C1：market 摘要 + order + account 均以短句呈现（总 token 远小于 512）。
    C2：C1 + 最近 20 根真实 OHLCV 文本行（价格方向/波动模式可直接被读取）。
    """
    from app.ai.laya_engine_observation import build_market_summary
    m = build_market_summary(hist)
    order = snapshot.get("order", {})
    acct = snapshot.get("account", {})
    lines = [
        "Gold M15 manual order review.",
        f"Order: {order.get('side')} on {order.get('symbol')} {order.get('timeframe')} (signal: {order.get('signal')}, {order.get('signal_label')}).",
        f"Account: balance {acct.get('balance', 0.0)}; open positions {acct.get('positions_count', 0)}; daily pnl {acct.get('daily_pnl', 'n/a')}.",
        f"Market: last close {m.get('last_close')}; change {m.get('change_1_pct')}% over the last bar, {m.get('change_5_pct')}% over 5 bars.",
        f"Volatility: 14-bar std {m.get('vol_14')}%; price at {round(float(m.get('range_position_50', 0.5)) * 100)}% of the 50-bar range.",
        f"Trend: price {m.get('price_vs_sma9')}x SMA9 and {m.get('price_vs_sma21')}x SMA21.",
    ]
    if with_bars:
        recent = hist.tail(20)
        rows = []
        for ts, r in recent.iterrows():
            rows.append(f"{ts.strftime('%m-%d %H:%M')} close {r['close']:.2f} diff {r['close']/r['open']-1:+.2%}")
        lines.append("Recent 20 candles (time close bar-change): " + " | ".join(rows))
    return "\n".join(lines)


def state_variant_c4(snapshot: dict, h1: pd.DataFrame) -> str:
    """变体 C4：生产 prose（Phase 1 增强后的 build_market_summary 字段）+ H1 周期摘要。

    与 C3 同一渲染器（render_laya_state_prose），仅追加一行 H1 证据（离线实验用，
    生产影子暂不喂多周期——H1 拉取属于额外 I/O）。
    """
    from app.ai.laya_gate import render_laya_state_prose
    from app.ai.laya_engine_observation import build_market_summary

    text = render_laya_state_prose(snapshot)
    h1_sum = build_market_summary(h1, timeframe="H1")
    if not h1_sum:
        return text
    bits = []
    for k in ("last_close", "change_1_pct", "change_5_pct", "rsi14", "atr_pct",
              "macd_state", "data_age_seconds", "is_stale", "range_position_50"):
        if h1_sum.get(k) is None:
            continue
        bits.append(f"{k} {h1_sum[k]}")
    return text + ("\nH1 evidence: " + "; ".join(bits) + "." if bits else "")


def state_variant_c5(snapshot: dict, hist: pd.DataFrame) -> str:
    """变体 C5：生产 prose + 新鲜度归一化（与微调训练分布同构）。

    回放数据停在历史日期，build_market_summary 会判 is_stale（距 now 数天）；
    生产语义是"决策时刻最后 bar 即最新"。C5 把 index 归一化到决策时刻（fresh），
    用于微调后模型（FT）与基线的同分布评估。
    """
    from app.ai.laya_gate import render_laya_state_prose
    from app.ai.laya_engine_observation import build_market_summary

    snap = json.loads(json.dumps(snapshot))
    df_fresh = hist.copy()
    now = pd.Timestamp.utcnow().replace(tzinfo=None) - pd.Timedelta(minutes=15)
    df_fresh.index = pd.date_range(end=now, periods=len(df_fresh), freq="15min")
    fresh_market = build_market_summary(df_fresh, timeframe="M15")
    fresh_market.update({"symbol": "GOLD", "timeframe": "M15"})
    snap["market"] = fresh_market
    return render_laya_state_prose(snap)


async def run_one(rt, snapshot: dict, timeout: float) -> dict | None:
    from app.ai.laya_gate import laya_gate_review
    started = time.perf_counter()
    try:
        review = await asyncio.wait_for(
            laya_gate_review(snapshot, timeout=timeout), timeout=timeout + 5.0
        )
        latency = int((time.perf_counter() - started) * 1000)
        if review is None:
            return {"decision": "UNAVAILABLE", "latency_ms": latency}
        review["latency_ms"] = latency
        return review
    except Exception as e:  # noqa: BLE001
        return {"decision": "UNAVAILABLE", "error": str(e)[:200], "latency_ms": int((time.perf_counter() - started) * 1000)}


async def review_text(rt, state: str, timeout: float) -> dict:
    """把文本 evidence 直接走 predict_choices + 收敛器（与 c1/c2 路径同构）。"""
    from app.ai.laya_gate import LAYA_GATE_QUESTIONS, LAYA_GATE_OPTIONS, converge_laya_verdict
    from app.config import settings

    started = time.perf_counter()
    try:
        answers = await asyncio.wait_for(
            rt.predict_choices(state, LAYA_GATE_QUESTIONS,
                               allowed_labels=LAYA_GATE_OPTIONS, timeout=timeout),
            timeout=timeout + 5.0,
        )
        decision = converge_laya_verdict(
            answers, min_confidence=settings.laya_gate_confidence_threshold
        )
        return {
            "decision": decision.verdict,
            "confidence": decision.confidence,
            "reasons": decision.reasons,
            "checks": decision.checks,
            "answers": answers,
            "engine": "laya",
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
    except Exception as e:  # noqa: BLE001
        return {"decision": "UNAVAILABLE", "error": str(e)[:200],
                "latency_ms": int((time.perf_counter() - started) * 1000)}


def ece_binary(conf: list[float], outcome: list[int], bins: int = 10) -> float:
    """期望校准误差：按置信度分箱，|平均置信 - 实际命中率| 加权平均。"""
    if not conf or len(conf) != len(outcome):
        return float("nan")
    conf = np.array(conf); outcome = np.array(outcome)
    edges = np.linspace(0.0, 1.0, bins + 1)
    tot = 0.0; n = 0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (conf >= lo) & (conf < hi)
        if lo == 0.0:
            m = (conf >= lo) & (conf <= hi)
        if m.sum() == 0:
            continue
        acc = outcome[m].mean()
        tot += m.sum() * abs(conf[m].mean() - acc)
        n += m.sum()
    return tot / n if n else float("nan")


def summarize(variant: str, rows: list[dict]) -> dict:
    """聚合指标：verdict 分布、逐问命中率、成功率（方向命中）、ECE、与参照链分歧。"""
    from collections import Counter
    out: dict = {"variant": variant, "n": len(rows)}
    out["verdict_dist"] = dict(Counter(r["review"]["decision"] for r in rows))
    out["latency_ms_median"] = float(np.median([r["review"].get("latency_ms", 0) for r in rows]))
    out["per_question"] = {}
    for q in ("data_quality", "signal_alignment", "market_regime", "risk_check", "execution_quality", "entry_decision"):
        labels = Counter(r["review"].get("answers", {}).get(q, {}).get("label", "MISSING") for r in rows)
        out["per_question"][q] = dict(labels)

    # 方向成功率：entry_decision=pass 且方向与未来实际一致的比例（各 horizon）
    for hname, hbars in HORIZONS_BARS.items():
        base = [r["labels"][hname]["hit_rule"] for r in rows]
        base_rate = float(np.mean(base)) if base else float("nan")
        hit_by_verdict = {}
        for v in ("APPROVED", "CAUTION", "REJECTED", "ESCALATE"):
            hits = [r["labels"][hname]["hit_rule"] for r in rows if r["review"]["decision"] == v]
            hit_by_verdict[v] = (float(np.mean(hits)), len(hits)) if hits else (float("nan"), 0)
        entry_pass = [r["labels"][hname]["hit_rule"] for r in rows
                      if r["review"].get("answers", {}).get("entry_decision", {}).get("label") == "pass"]
        entry_pass_rate = float(np.mean(entry_pass)) if entry_pass else float("nan")
        out[f"success_{hname}"] = {
            "base_rate": base_rate,
            "entry_pass_rate": entry_pass_rate,
            "by_verdict": hit_by_verdict,
        }

    # 校准：entry_decision 置信度 vs 2h 方向命中
    conf = [r["review"].get("answers", {}).get("entry_decision", {}).get("confidence")
            for r in rows if r["review"].get("answers", {}).get("entry_decision")]
    outc = [r["labels"]["2h"]["hit_rule"] for r in rows if r["review"].get("answers", {}).get("entry_decision")]
    out["ece_entry_2h"] = ece_binary([float(c) for c in conf if c is not None], outc)

    # 与参照链分歧（tighten/loosen 口径，复用观测器语义）
    div = Counter()
    for r in rows:
        chain = r["chain_can_trade"]; laya_v = r["review"]["decision"]
        if laya_v == "UNAVAILABLE":
            div["laya_unavailable"] += 1
        elif laya_v == "REJECTED":
            div["tighten" if chain else "none"] += 1
        elif laya_v == "APPROVED":
            div["none" if chain else "loosen"] += 1
        elif laya_v == "ESCALATE":
            div["tighten" if chain else "laya_escalate"] += 1
        else:
            div["caution"] += 1
    out["divergence"] = dict(div)
    out["chain_agree_rate"] = (div["none"] / len(rows)) if rows else float("nan")
    return out


def threshold_sweep(rows: list[dict], thresholds=(0.3, 0.4, 0.5, 0.6, 0.7)) -> dict:
    """离线阈值扫描：不改推理，用已保存的逐问 label/confidence 重算收敛判定（纯函数）。"""
    from app.ai.laya_gate import converge_laya_verdict
    from collections import Counter
    out = {}
    for th in thresholds:
        dist = Counter()
        for r in rows:
            answers = r.get("review", {}).get("answers")
            if not answers:
                dist["UNAVAILABLE"] += 1
                continue
            d = converge_laya_verdict(answers, min_confidence=th)
            dist[d.verdict] += 1
        out[str(th)] = dict(dist)
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=480)
    ap.add_argument("--variants", default="a,b")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--model-dir", default="", help="加载微调 checkpoint 目录（laya.load 兼容）")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    m15 = load_gold_m15()
    h1 = resample_h1(m15)
    print(f"[data] GOLD M15 rows={len(m15):,} {m15.index[0]} -> {m15.index[-1]}; H1 rows={len(h1):,}")

    valid = []
    for i in range(MIN_START, len(m15) - MIN_END):
        hist = m15.iloc[: i + 1]
        sig, label = replay_signal(hist)
        if sig == 0:
            continue
        valid.append(i)
    print(f"[data] valid signal bars={len(valid):,}")
    # 分层抽样：按月等距，固定种子
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
    chosen = sorted(chosen)
    if len(chosen) > args.n:
        chosen = chosen[: args.n]
    print(f"[data] sampled bars={len(chosen):,} ({chosen[0]}..{chosen[-1]})")

    from app.ai.laya_runtime import LayaRuntime
    from app.ai.laya_engine_observation import build_market_summary, build_laya_engine_snapshot
    from app.config import settings
    import laya
    from huggingface_hub import snapshot_download

    print("[laya] loading real model (local cache)...")
    if args.model_dir:
        agent = laya.load(args.model_dir, device="cpu")
        print(f"[laya] loaded fine-tuned model from {args.model_dir}")
    else:
        cache_dir = snapshot_download("convaiinnovations/laya", local_files_only=True)
        agent = laya.load(cache_dir, device="cpu")
    rt = LayaRuntime()
    rt._agent = agent
    import app.ai.laya_runtime as laya_mod
    laya_mod._runtime = rt
    laya_mod.settings.laya_enabled = True
    print("[laya] loaded OK")

    variants = args.variants.split(",")
    results: dict[str, list[dict]] = {v: [] for v in variants}
    samples_files = {v: open(OUT_DIR / f"laya_eval_samples_{v}.jsonl", "w") for v in variants}
    balance = 10000.0

    for k, i in enumerate(chosen, 1):
        hist = m15.iloc[: i + 1]
        sig, sig_label = replay_signal(hist)
        summary = build_market_summary(hist)
        chain_can, chain_reasons = deterministic_chain(sig, summary)
        h1_hist = h1[h1.index <= m15.index[i]]

        snapshot = build_laya_engine_snapshot(
            symbol="GOLD", timeframe="M15", signal=sig, signal_label=sig_label,
            balance=balance, df=hist, positions=[], daily_pnl=0.0, recent_wr=None,
        )
        # 真实 future 标签
        labels = {}
        for hname, hbars in HORIZONS_BARS.items():
            fwd = float(m15["close"].iloc[i + hbars])
            now = float(m15["close"].iloc[i])
            labels[hname] = {
                "fwd_close": round(fwd, 4), "now_close": round(now, 4),
                "ret_pct": round((fwd / now - 1.0) * 100.0, 4),
                "hit_rule": int((sig > 0 and fwd > now) or (sig < 0 and fwd < now)),
            }

        row = {"idx": i, "time": str(m15.index[i]), "signal": sig,
               "chain_can_trade": chain_can, "chain_reasons": chain_reasons, "labels": labels}

        for v in variants:
            from app.ai.laya_gate import LAYA_GATE_QUESTIONS, LAYA_GATE_OPTIONS, converge_laya_verdict
            if v == "a":
                settings.laya_state_prose = False  # 审计口径：A=生产旧 JSON 形态（prose 已上线默认）
                snap = snapshot
                review = await run_one(rt, snap, args.timeout)
            elif v == "b":
                settings.laya_state_prose = False  # B 同为 JSON 形态，仅增量字段
                snap = enrich_state_b(snapshot, hist, h1_hist)
                review = await run_one(rt, snap, args.timeout)
            elif v in ("c1", "c2"):  # 证据渲染为自然语言文本，直接走 predict_choices + 收敛器
                state_str = state_variant_c1_c2(snapshot, hist, with_bars=(v == "c2"))
                snap = state_str
                review = await review_text(rt, state_str, args.timeout)
            elif v == "c3":  # Phase 1 增强后的生产 prose（build_market_summary 扩展字段）
                settings.laya_state_prose = True
                from app.ai.laya_gate import render_laya_state_prose

                state_str = render_laya_state_prose(snapshot)
                snap = state_str
                review = await review_text(rt, state_str, args.timeout)
            elif v == "c4":  # C3 + H1 周期摘要（离线实验，生产影子暂不喂多周期）
                settings.laya_state_prose = True
                state_str = state_variant_c4(snapshot, h1_hist)
                snap = state_str
                review = await review_text(rt, state_str, args.timeout)
            elif v == "c5":  # 生产 prose + 新鲜度归一化（FT/基线同分布评估）
                settings.laya_state_prose = True
                state_str = state_variant_c5(snapshot, hist)
                snap = state_str
                review = await review_text(rt, state_str, args.timeout)
            else:
                raise ValueError(f"unknown variant: {v}")
            row[f"review_{v}"] = review
            results[v].append({**{kk: row[kk] for kk in ("idx", "time", "signal", "chain_can_trade", "chain_reasons", "labels")},
                               "review": review})
            samples_files[v].write(json.dumps({
                "idx": i, "time": str(m15.index[i]), "signal": sig,
                "chain_can_trade": chain_can, "labels": labels,
                "decision": review.get("decision"),
                "answers": {q: {"label": a.get("label"), "confidence": a.get("confidence")}
                            for q, a in (review.get("answers") or {}).items()},
                "market_keys": list(snap.get("market", {}).keys()) if isinstance(snap, dict) else ["TEXT"],
            }, ensure_ascii=False) + "\n")
        if k % 50 == 0 or k == len(chosen):
            print(f"  ... {k}/{len(chosen)} samples done")

    for v in variants:
        rep = summarize(v, results[v])
        print(f"\n===== VARIANT {v} =====")
        print(f"n={rep['n']} verdict={rep['verdict_dist']}")
        print(f"latency_ms median={rep['latency_ms_median']}")
        for hname in HORIZONS_BARS:
            s = rep[f"success_{hname}"]
            print(f"  success[{hname}]: base={s['base_rate']:.3f} entry_pass={s['entry_pass_rate']:.3f} "
                  f"by_verdict={ {k: (round(v[0],3) if v[1] else '-') for k, v in s['by_verdict'].items()} }")
        print(f"  ECE(entry,2h)={rep['ece_entry_2h']:.4f}  divergence={rep['divergence']}  chain_agree={rep['chain_agree_rate']:.3f}")
        print(f"  threshold_sweep: {threshold_sweep(results[v])}")
        for q, dist in rep["per_question"].items():
            print(f"  q[{q}] = {dist}")
        with open(OUT_DIR / f"laya_eval_results_{v}.json", "w") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2, default=str)
        samples_files[v].close()
    print(f"\n[out] results -> {OUT_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
