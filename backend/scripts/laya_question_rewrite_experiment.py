"""问句重写实验（真实 laya + 真实 GOLD M15）：验证重写能否修好 aligned/多空判定。

重写三问：signal_alignment（方向跟随）、market_regime（方向相对）、entry_decision（显式矛盾）。
对照基线用 C1 prose 结果（laya_eval_samples_c1.jsonl）。
"""
import asyncio, os, sys, json
os.environ.setdefault("HF_ENDPOINT","https://hf-mirror.com"); os.environ.setdefault("TOKENIZERS_PARALLELISM","false"); os.environ.setdefault("USE_TF","0")
sys.path.insert(0, ".")
sys.path.insert(0, "/Users/liumou/PycharmProjects/ai-trading-agent/backend/scripts")
import pandas as pd, psycopg2, random, math
from pathlib import Path
from collections import Counter

REWRITTEN = {
    "signal_alignment": {
        "type": "choice",
        "instructions": "The system wants to open an order in the requested direction (BUY means expecting the price to rise, SELL means expecting it to fall). Does the current price trend support that direction?",
        "criteria": {
            "aligned": "price trend and momentum move in the same direction as the order",
            "mixed": "some evidence supports and some opposes the direction, with no strong contradiction",
            "conflict": "price trend and momentum clearly move against the requested order direction",
            "insufficient": "not enough current market evidence to judge the direction",
        },
    },
    "market_regime": {
        "type": "choice",
        "instructions": "Is the current market condition suitable for opening in the REQUESTED direction (BUY prefers up-moves, SELL prefers down-moves)?",
        "criteria": {
            "favorable": "trend, volatility and spread conditions support entering in the requested direction",
            "neutral": "conditions are range-bound or mixed and do not materially oppose the entry",
            "adverse": "trend clearly moves against the requested direction, or volatility/spread makes entry unsafe",
            "insufficient": "market evidence is unavailable or too stale to judge",
        },
    },
    "entry_decision": {
        "type": "choice",
        "instructions": "Make the final pre-trade decision for the requested order direction. Reject when the market clearly moves against the order, a concrete account risk is breached, or execution is unsafe. Missing evidence alone must not reject.",
        "criteria": {
            "pass": "no concrete contradiction, risk limit breach or unsafe execution is present",
            "reject": "the market clearly moves against the requested direction, or a concrete risk/execution problem requires blocking",
        },
    },
}
ALLOWED = {
    "signal_alignment": {"aligned","mixed","conflict","insufficient"},
    "market_regime": {"favorable","neutral","adverse","insufficient"},
    "entry_decision": {"pass","reject"},
}

def converge3(ans, min_conf):
    checks = {}
    for q in ("signal_alignment","market_regime","entry_decision"):
        a = ans.get(q) or {}
        lab, conf = a.get("label"), a.get("confidence")
        if not isinstance(conf,(int,float)): return "ESCALATE"
        conf = float(conf); checks[q] = (lab, conf)
        if lab == "insufficient": return "ESCALATE"
        if conf < min_conf: return "ESCALATE"
    if checks["entry_decision"][0] == "reject": return "REJECTED"
    if checks["signal_alignment"][0]=="conflict" and checks["market_regime"][0]=="adverse": return "REJECTED"
    if (checks["entry_decision"][0]=="pass" and checks["signal_alignment"][0]=="aligned"
        and checks["market_regime"][0]=="favorable"): return "APPROVED"
    return "CAUTION"

def replay_signal(hist):
    closes = hist["close"]
    sma9, sma21 = closes.tail(9).mean(), closes.tail(21).mean()
    d1 = closes.iloc[-1]/closes.iloc[-2]-1.0
    if sma9 > sma21 and d1 > 0: return 1
    if sma9 < sma21 and d1 < 0: return -1
    return 0

PROBES = [
    ("P1 强多头+BUY", 1, "Gold M15 manual order review.\nOrder: BUY GOLD M15 (momentum breakout).\nMarket: last close 3200.50; change +1.2% last bar, +2.5% over 5 bars; 14-bar volatility 0.45%; price at 85% of the 50-bar range; price 1.03x SMA9 and 1.05x SMA21, all above rising averages.\nAccount: balance 10000; no open positions; daily pnl +120."),
    ("P2 强空头+SELL", -1, "Gold M15 manual order review.\nOrder: SELL GOLD M15 (momentum breakdown).\nMarket: last close 2800.20; change -1.3% last bar, -2.7% over 5 bars; 14-bar volatility 0.50%; price at 15% of the 50-bar range; price 0.97x SMA9 and 0.95x SMA21, all below falling averages.\nAccount: balance 10000; no open positions; daily pnl +90."),
    ("P3 强多头+SELL(应拒)", -1, "Gold M15 manual order review.\nOrder: SELL GOLD M15 (momentum breakdown).\nMarket: last close 3200.50; change +1.2% last bar, +2.5% over 5 bars; 14-bar volatility 0.45%; price at 85% of the 50-bar range; price 1.03x SMA9 and 1.05x SMA21, all above rising averages.\nAccount: balance 10000; no open positions; daily pnl +120."),
]

async def main():
    raw = None
    for line in Path('.env').read_text().splitlines():
        if line.startswith('DATABASE_URL_SYNC'): raw = line.split('=',1)[1].strip().strip('"').strip("'"); break
    conn = psycopg2.connect(raw.replace('postgresql+psycopg2://','postgresql://'), connect_timeout=15)
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()
    cur.execute("SELECT time,open,high,low,close,volume FROM ohlcv_data WHERE symbol='GOLD' AND timeframe='M15' ORDER BY time")
    rows = cur.fetchall(); cur.close(); conn.close()
    df = pd.DataFrame(rows, columns=["time","open","high","low","close","volume"])
    df["time"] = pd.to_datetime(df["time"]); df = df.set_index("time").sort_index()

    from app.ai.laya_runtime import LayaRuntime
    from app.ai.laya_engine_observation import build_market_summary, build_laya_engine_snapshot
    from scripts.laya_real_data_eval import state_variant_c1_c2
    import laya
    from huggingface_hub import snapshot_download
    cache_dir = snapshot_download("convaiinnovations/laya", local_files_only=True)
    rt = LayaRuntime(); rt._agent = laya.load(cache_dir, device="cpu")
    import app.ai.laya_runtime as lm; lm._runtime = rt; lm.settings.laya_enabled = True

    # 探针
    print("=== 重写后探针 ===")
    for name, sig, st in PROBES:
        ans = await rt.predict_choices(st, REWRITTEN, allowed_labels=ALLOWED, timeout=60)
        for q in ("signal_alignment","market_regime","entry_decision"):
            a = ans.get(q) or {}
            print(f"  {name} | {q}: {a.get('label')} conf={a.get('confidence',0):.3f}" if a else f"  {name} | {q}: None")

    # 80 真实样本（种子 42，与 C1 同源）
    rng = random.Random(42)
    valid = []
    for i in range(210, len(df)-106):
        if replay_signal(df.iloc[:i+1]) != 0: valid.append(i)
    months = sorted({(df.index[i].year, df.index[i].month) for i in valid})
    per = max(1, math.ceil(80/len(months)))
    chosen = []
    for ym in months:
        idxs = [i for i in valid if (df.index[i].year, df.index[i].month)==ym]
        step = max(1, len(idxs)/per)
        chosen.extend(idxs[int(k*step)] for k in range(min(per, len(idxs))))
    chosen = sorted(chosen)[:80]
    print(f"\n=== 重写后 80 真实样本 ===")
    rows = []
    for i in chosen:
        hist = df.iloc[:i+1]
        sig = replay_signal(hist)
        side = "BUY" if sig>0 else "SELL"
        snap = build_laya_engine_snapshot(symbol="GOLD", timeframe="M15", signal=sig,
            signal_label="sma_cross_momentum", balance=10000.0, df=hist, positions=[], daily_pnl=0.0, recent_wr=None)
        st = state_variant_c1_c2(snap, hist, with_bars=False)
        ans = await rt.predict_choices(st, REWRITTEN, allowed_labels=ALLOWED, timeout=60)
        labels = {}
        for h, hb in (("2h",8),("6h",24),("1d",96)):
            fwd = float(df["close"].iloc[i+hb]); now = float(df["close"].iloc[i])
            labels[h] = int((sig>0 and fwd>now) or (sig<0 and fwd<now))
        rows.append({"side": side, "sig": sig, "answers": ans, "labels": labels})
        print(f"  {i} {df.index[i]} {side} | " + " ".join(f"{q}={ans.get(q,{}).get('label')}({ans.get(q,{}).get('confidence',0):.2f})" for q in ("signal_alignment","market_regime","entry_decision")))

    for q in REWRITTEN:
        print(f"\nq[{q}]: {dict(Counter((x['answers'].get(q) or {}).get('label','MISSING') for x in rows))}")
    print("按方向拆分 market_regime favorable / signal_alignment aligned:")
    for side in ("BUY","SELL"):
        sub = [x for x in rows if x["side"]==side]
        fav = sum(1 for x in sub if (x['answers'].get('market_regime') or {}).get('label')=='favorable')
        ali = sum(1 for x in sub if (x['answers'].get('signal_alignment') or {}).get('label')=='aligned')
        ok  = sum(1 for x in sub if (x['answers'].get('entry_decision') or {}).get('label')=='pass')
        print(f"  {side}: n={len(sub)} regime_fav={fav} aligned={ali} entry_pass={ok}")
    for th in (0.3, 0.35, 0.4):
        dist = Counter(converge3(x["answers"], th) for x in rows)
        print(f"  th={th}: {dict(dist)}")
        base = sum(x['labels']['2h'] for x in rows)/len(rows)
        for v in ("APPROVED","CAUTION","REJECTED","ESCALATE"):
            sub = [x for x in rows if converge3(x["answers"], th)==v]
            if sub:
                print(f"    {v} n={len(sub)} 2h_hit={sum(x['labels']['2h'] for x in sub)/len(sub):.3f} (base 2h={base:.3f})")

asyncio.run(main())
