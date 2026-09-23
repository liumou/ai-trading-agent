"""laya 真实数据回放 —— 离线分析（读 JSONL，无需 DB/模型）

对比变体 A（现状 8 字段摘要）与变体 B（增强输入）：
  verdict 分布/阈值扫描、逐问命中率、方向成功率、ECE 校准、分歧率。
用法：/tmp/laya-api-venv/bin/python scripts/laya_eval_analyze.py [目录]
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

OUT_DIR = Path(sys.argv[1] if len(sys.argv) > 1
               else Path(__file__).resolve().parent.parent.parent / ".planning" / "2026-09-22-laya-mt5" / "deliverables")

HORIZONS = {"2h": 8, "6h": 24, "1d": 96}
THRESHOLDS = (0.3, 0.4, 0.5, 0.6, 0.7)


def load(variant: str) -> list[dict]:
    p = OUT_DIR / f"laya_eval_samples_{variant}.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def converge(answers: dict, min_conf: float) -> str:
    """复刻 converge_laya_verdict 的纯函数语义（离线、轻量）。"""
    order = ("signal_alignment", "market_regime", "risk_check", "execution_quality", "entry_decision")
    checks = {}
    for q in order:
        a = answers.get(q) or {}
        label, conf = a.get("label"), a.get("confidence")
        if label not in ("aligned","mixed","conflict","insufficient","favorable","neutral","adverse",
                         "clear","caution","block","pass","reject") or not isinstance(conf, (int, float)):
            return "ESCALATE"
        conf = float(conf)
        checks[q] = (label, conf)
        if label == "insufficient":
            return "ESCALATE"
        if conf < min_conf:
            return "ESCALATE"
    r = checks["risk_check"][0]; e = checks["execution_quality"][0]; en = checks["entry_decision"][0]
    if r == "block" or e == "block" or en == "reject":
        return "REJECTED"
    if checks["signal_alignment"][0] == "conflict" and checks["market_regime"][0] == "adverse":
        return "REJECTED"
    if (en == "pass" and r == "clear" and e == "clear"
            and checks["signal_alignment"][0] == "aligned" and checks["market_regime"][0] == "favorable"):
        return "APPROVED"
    return "CAUTION"


def ece(conf, outcome, bins=10):
    if not conf or len(conf) != len(outcome):
        return float("nan")
    c, o = np.array(conf), np.array(outcome)
    edges = np.linspace(0, 1, bins + 1)
    tot = n = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (c >= lo) & (c <= hi if lo == 0.0 else c < hi)
        if m.sum() == 0: continue
        tot += m.sum() * abs(c[m].mean() - o[m].mean()); n += m.sum()
    return tot / n if n else float("nan")


def report(variant: str, n: int):
    rows = load(variant)
    n_avail = sum(1 for r in rows if r.get("decision") != "UNAVAILABLE")
    print(f"===== VARIANT {variant}  (samples={len(rows)}) =====")
    print(f"UNAVAILABLE={len(rows)-n_avail}")
    dist = Counter(r["decision"] for r in rows)
    print(f"decision_dist(conf=0.6): {dict(dist)}")
    for th in THRESHOLDS:
        d = Counter(converge(r.get("answers") or {}, th) for r in rows if r.get("answers"))
        print(f"  threshold={th}: {dict(d)}")
    # 逐问分布 + 置信度分布
    for q in ("data_quality","signal_alignment","market_regime","risk_check","execution_quality","entry_decision"):
        labels = Counter((r.get("answers") or {}).get(q, {}).get("label", "MISSING") for r in rows)
        confs = [(r.get("answers") or {}).get(q, {}).get("confidence") for r in rows]
        confs = [float(c) for c in confs if isinstance(c, (int, float))]
        print(f"  q[{q}]: dist={dict(labels)}  conf_mean={np.mean(confs):.3f} conf_max={np.max(confs):.3f} p(conf>=0.6)={np.mean([c>=0.6 for c in confs]):.3f}")
    # 方向成功率: 基率 vs entry_pass
    for h in HORIZONS:
        base = np.mean([r["labels"][h]["hit_rule"] for r in rows])
        ep = [r["labels"][h]["hit_rule"] for r in rows
              if (r.get("answers") or {}).get("entry_decision", {}).get("label") == "pass"]
        ed = [r["labels"][h]["hit_rule"] for r in rows
              if (r.get("answers") or {}).get("entry_decision", {}).get("label") == "reject"]
        print(f"  success[{h}]: base={base:.3f} (n={len(rows)})  entry_pass={np.mean(ep) if ep else float('nan'):.3f} (n={len(ep)})  entry_reject={np.mean(ed) if ed else float('nan'):.3f} (n={len(ed)})")
    # ECE per question（2h 方向命中）
    for q in ("signal_alignment", "market_regime", "entry_decision"):
        conf = [float((r.get("answers") or {}).get(q, {}).get("confidence")) for r in rows]
        outc = [r["labels"]["2h"]["hit_rule"] for r in rows]
        ok = [(c, o) for c, o in zip(conf, outc) if isinstance(c, float)]
        print(f"  ECE[{q},2h]={ece([c for c,_ in ok],[o for _,o in ok]):.4f} (n={len(ok)})")
    # 分歧率 vs 参照链（conf=0.6 口径）
    div = Counter()
    for r in rows:
        v = r.get("decision"); chain = r.get("chain_can_trade", False)
        if v == "ESCALATE": div["tighten" if chain else "laya_escalate"] += 1
        elif v == "REJECTED": div["tighten" if chain else "none"] += 1
        elif v == "APPROVED": div["none" if chain else "loosen"] += 1
        elif v == "CAUTION": div["caution"] += 1
        else: div["unavailable"] += 1
    print(f"  divergence(0.6): {dict(div)} chain_agree={div['none']/len(rows):.3f}" if rows else "  (no rows)")
    print()


def main():
    for v in ("a", "b"):
        try:
            report(v, 0)
        except FileNotFoundError as e:
            print(f"[skip] {e}")
    # A vs B 配对对比（仅对两者都有 answers 的样本）
    a, b = load("a"), load("b")
    amap = {r["idx"]: r for r in a}; bmap = {r["idx"]: r for r in b}
    common = sorted(set(amap) & set(bmap))
    if common:
        print(f"===== A vs B paired (n={len(common)}) — 阈值 0.4/0.5/0.6 下的判定变化 =====")
        for th in (0.4, 0.5, 0.6):
            va = [converge(amap[i].get("answers") or {}, th) for i in common]
            vb = [converge(bmap[i].get("answers") or {}, th) for i in common]
            flips = sum(1 for x, y in zip(va, vb) if x != y)
            stricter = sum(1 for x, y in zip(va, vb)
                           if {"APPROVED": 0, "CAUTION": 1, "REJECTED": 2, "ESCALATE": 3}[y] > {"APPROVED": 0, "CAUTION": 1, "REJECTED": 2, "ESCALATE": 3}[x])
            esc_a = sum(1 for x in va if x == "ESCALATE"); esc_b = sum(1 for x in vb if x == "ESCALATE")
            print(f"  th={th}: flips={flips} stricter_B={stricter} ESC(A)={esc_a} ESC(B)={esc_b}")


if __name__ == "__main__":
    main()
