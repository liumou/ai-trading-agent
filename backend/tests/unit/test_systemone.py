"""SystemOne 确定性决策引擎单元测试（JEV 式 5 检查 + 收敛器）。

锁定行为：
- 数据质量三档分诊（provider 失败 / partial / sufficient）
- 规则阈值语义（warn/block 边界、死规则隔离）
- 收敛矩阵（block→REJECTED；conflict 单独→CAUTION；双 TF 逆势→REJECTED）
- 确定性金样（同输入两次输出全等）
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.services.systemone import LocalRuleEngine, ProviderUnavailable

SPEC = {"trade_tick_value": 1.0, "trade_tick_size": 0.01, "trade_contract_size": 100}


def _df(rows=120, base=2000.0, per_bar=0.0, vol=1.0, seed=7):
    """构造 OHLCV DataFrame。per_bar=每根漂移（>0 强趋势可抬 ADX≥25），vol 控制噪声。"""
    rng = np.random.default_rng(seed)
    close = base + np.arange(rows) * per_bar + rng.normal(0, vol, rows)
    high = close + np.abs(rng.normal(0, vol * 0.5, rows)) + vol * 0.5
    low = close - np.abs(rng.normal(0, vol * 0.5, rows)) - vol * 0.5
    open_ = close + rng.normal(0, vol * 0.3, rows)
    idx = pd.date_range("2026-01-05 09:00", periods=rows, freq="15min")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


def _tick_time(df, offset_s=60):
    """bridge 同源时钟：最后一根 K 线 + offset（tick 时间永远晚于最后 K 线）。"""
    ts = df.index[-1] + pd.Timedelta(seconds=offset_s)
    return str(ts)


def _ctx(entry=2000.0, sl=1985.0, tp=2050.0, lot=0.1, equity=10000.0, balance=10000.0,
         m15=None, account_daily_pnl=None, profile=None, positions=None, sentiment=None):
    return SimpleNamespace(
        symbol="GOLD", entry_ref=entry, sl=sl, tp=tp, lot=lot, spread=0.5,
        account={"balance": balance, "equity": equity},
        account_daily_pnl=account_daily_pnl,
        profile=profile or {}, positions=positions or [],
        tick={"bid": entry - 0.25, "ask": entry + 0.25,
              "time": _tick_time(m15) if m15 is not None else None},
    )


def _snapshot(direction="BUY", rule_flags=None, sentiment=None):
    return {
        "order": {"type": direction, "symbol": "GOLD", "lot": 0.1, "sl": 1985.0, "tp": 2050.0},
        "market": {"bid": 1999.75, "ask": 2000.25, "spread": 0.5, "sentiment": sentiment},
        "rule_flags": rule_flags or [],
    }


def _market_data(m15=None, h1=None, spec=SPEC, deals=None, raise_error=False):
    md = SimpleNamespace()

    async def get_ohlcv(symbol, tf, count):
        if raise_error:
            raise RuntimeError("bridge down")
        return m15 if tf == "M15" else h1

    async def get_symbol_spec(symbol):
        if spec is None:
            return {"success": False}
        return {"success": True, "data": spec}

    async def get_history(days=14):
        return {"success": True, "data": deals if deals is not None else []}

    md.get_ohlcv = get_ohlcv
    md.connector = SimpleNamespace(get_symbol_spec=get_symbol_spec, get_history=get_history)
    return md


def _engine(m15=None, h1=None, spec=SPEC, deals=None, raise_error=False):
    if m15 is None:
        m15 = _df()
    if h1 is None:
        h1 = _df(rows=120, base=2000.0, per_bar=0.3, vol=1.0, seed=11)
    return LocalRuleEngine(_market_data(m15, h1, spec, deals, raise_error))


# ─── 收敛矩阵 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_clear_approves():
    """健康数据 + 常规单（risk 1.5% < 2% warn 线）→ 全 clear → APPROVED。"""
    m15 = _df()
    ctx = _ctx(sl=1985.0, m15=m15)  # loss = 15/0.01×1×0.1 = $150 = 1.5%
    d = await _engine(m15=m15).evaluate(_snapshot(), ctx)
    assert d.verdict == "APPROVED"
    choices = {c["name"]: c["choice"] for c in d.checks}
    assert choices["data_quality"] == "sufficient"
    assert choices["risk_check"] == "clear"
    assert choices["execution_quality"] == "clear"
    assert d.provider == "local_jev"
    assert 0.0 < d.confidence <= 1.0


@pytest.mark.asyncio
async def test_determinism_golden():
    """确定性金样：同输入两次输出全等（含 reasoning/checks/confidence）。"""
    m15, h1 = _df(), _df(rows=120, base=2000.0, per_bar=0.3, vol=1.0, seed=11)
    eng = _engine(m15=m15, h1=h1)
    ctx = _ctx(m15=m15)
    snap = _snapshot()
    d1, d2 = await eng.evaluate(snap, ctx), await eng.evaluate(snap, ctx)
    assert d1.verdict == d2.verdict
    assert d1.confidence == d2.confidence
    assert d1.reasoning == d2.reasoning
    assert d1.checks == d2.checks
    assert d1.risk_flags == d2.risk_flags


@pytest.mark.asyncio
async def test_risk_block_rejects():
    """exposure block（risk 5.5% > 5%）→ risk_check=block → REJECTED。"""
    m15 = _df()
    ctx = _ctx(sl=1944.0, m15=m15)  # loss = 56/0.01×1×0.1 = $560 = 5.6%
    d = await _engine(m15=m15).evaluate(_snapshot(), ctx)
    assert d.verdict == "REJECTED"
    assert {c["name"]: c["choice"] for c in d.checks}["risk_check"] == "block"
    assert any("敞口上限" in f for f in d.risk_flags)


@pytest.mark.asyncio
async def test_risk_warn_caution():
    """exposure warn（risk 2.2% 在 2~5% 区间）→ CAUTION，不是 REJECTED。"""
    m15 = _df()
    ctx = _ctx(sl=1977.8, m15=m15)  # loss = 22.2/0.01×1×0.1 = $222 = 2.22%
    d = await _engine(m15=m15).evaluate(_snapshot(), ctx)
    assert d.verdict == "CAUTION"
    assert {c["name"]: c["choice"] for c in d.checks}["risk_check"] == "caution"


@pytest.mark.asyncio
async def test_mtf_conflict_alone_is_caution_not_rejected():
    """单 TF 反向共识 conflict（H1 强下行、M15 平盘无趋势）→ CAUTION。
    这是 v3 关键改判：conflict 单独不 REJECTED（=LLM 基线，防误杀回调单）。"""
    m15 = _df(per_bar=0.0, vol=1.0, seed=5)  # 平盘：trend=0（EMA ±0.05% 带内）
    h1 = _df(per_bar=-30.0, vol=8.0, seed=3)  # 强下行 ADX≈100
    ctx = _ctx(m15=m15)
    d = await _engine(m15=m15, h1=h1).evaluate(_snapshot("BUY"), ctx)
    assert {c["name"]: c["choice"] for c in d.checks}["signal_alignment"] == "conflict"
    assert d.verdict == "CAUTION"


@pytest.mark.asyncio
async def test_mtf_both_tf_against_strong_adx_rejects():
    """双 TF 同向逆向且 ADX≥25 → 升级 REJECTED（v3 新增的增量拒单条件）。"""
    m15 = _df(per_bar=-2.0, vol=1.0, seed=3)  # 强下行 ADX≈94
    h1 = _df(per_bar=-30.0, vol=8.0, seed=3)
    ctx = _ctx(m15=m15)
    d = await _engine(m15=m15, h1=h1).evaluate(_snapshot("BUY"), ctx)
    assert d.verdict == "REJECTED", f"converge={d.converge}"
    assert "mtf_conflict" in d.converge.get("upgrade", "")


@pytest.mark.asyncio
async def test_execution_warn_caution():
    """rr_sanity warn（TP 太近 RR<0.25）→ execution_quality=caution → CAUTION。"""
    m15 = _df()
    ctx = _ctx(sl=1985.0, tp=2002.0, m15=m15)  # RR = 2/15 ≈ 0.13
    d = await _engine(m15=m15).evaluate(_snapshot(), ctx)
    assert d.verdict == "CAUTION"
    assert {c["name"]: c["choice"] for c in d.checks}["execution_quality"] == "caution"


# ─── 数据质量三档分诊 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_error_is_provider_unavailable():
    eng = _engine(raise_error=True)
    with pytest.raises(ProviderUnavailable):
        await eng.evaluate(_snapshot(), _ctx(m15=_df()))


@pytest.mark.asyncio
async def test_both_tf_empty_is_provider_unavailable():
    eng = _engine(m15=pd.DataFrame(), h1=pd.DataFrame())
    with pytest.raises(ProviderUnavailable):
        await eng.evaluate(_snapshot(), _ctx(m15=_df()))


@pytest.mark.asyncio
async def test_both_tf_too_short_is_provider_unavailable():
    """双 TF 根数均 < min_bars → 规则全盲 → 视同 provider 失败降级 LLM。"""
    eng = _engine(m15=_df(rows=30), h1=_df(rows=30))
    with pytest.raises(ProviderUnavailable):
        await eng.evaluate(_snapshot(), _ctx(m15=_df()))


@pytest.mark.asyncio
async def test_single_tf_missing_partial_caution():
    """单 TF 缺（H1 空）→ partial → CAUTION（另一 TF 仍出证据）。"""
    m15 = _df()
    eng = _engine(m15=m15, h1=pd.DataFrame())
    d = await eng.evaluate(_snapshot(), _ctx(m15=m15))
    assert {c["name"]: c["choice"] for c in d.checks}["data_quality"] == "partial"
    assert d.verdict == "CAUTION"


@pytest.mark.asyncio
async def test_stale_bars_caution_not_degrade():
    """全 TF 陈旧（周末休市：tick 距最后 K 线 > freshness_mult×bar 周期）
    → data_quality=insufficient → CAUTION（已知市场状态，不降级不拒单）。"""
    m15 = _df()
    ctx = _ctx(m15=m15)
    ctx.tick["time"] = str(pd.Timestamp(m15.index[-1]) + pd.Timedelta(hours=10))  # M15 40 根
    d = await _engine(m15=m15).evaluate(_snapshot(), ctx)
    assert {c["name"]: c["choice"] for c in d.checks}["data_quality"] == "insufficient"
    assert d.verdict == "CAUTION"


@pytest.mark.asyncio
async def test_broker_clock_offset_ignored():
    """同源时钟：tick 与 K 线同出 bridge —— 两者整体偏移（模拟 broker 时钟
    与墙钟大幅不同）不影响陈旧度判断；只比相对差，绝不 datetime.now()。"""
    shift = pd.Timedelta(days=25000)
    m15 = _df(seed=7)
    h1 = _df(rows=120, base=2000.0, per_bar=0.3, vol=1.0, seed=11)
    m15.index = m15.index + shift  # 整条 K 线序列平移到 2094 年（tick 同源平移）
    h1.index = h1.index + shift
    ctx = _ctx(m15=m15)
    ctx.tick["time"] = str(pd.Timestamp(m15.index[-1]) + pd.Timedelta(seconds=60))
    d = await _engine(m15=m15, h1=h1).evaluate(_snapshot(), ctx)
    assert {c["name"]: c["choice"] for c in d.checks}["data_quality"] == "sufficient"


@pytest.mark.asyncio
async def test_tick_without_time_skips_freshness():
    """tick 无时间字段（测试 mock/旧桥）→ 跳过陈旧度检查，不臆断。"""
    m15 = _df()
    ctx = _ctx(m15=m15)
    ctx.tick.pop("time")
    d = await _engine(m15=m15).evaluate(_snapshot(), ctx)
    assert {c["name"]: c["choice"] for c in d.checks}["data_quality"] == "sufficient"


# ─── 规则细节 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_spec_warn_only():
    """spec 拉不到（tick_value 缺失）→ exposure 降级 warn-only → CAUTION，
    不会 REJECTED（无法验证敞口 ≠ 敞口安全，但也绝不放行为 block）。"""
    m15 = _df()
    d = await _engine(m15=m15, spec=None).evaluate(_snapshot(), _ctx(m15=m15))
    assert d.verdict == "CAUTION"
    assert "敞口上限：警告" in d.risk_flags


@pytest.mark.asyncio
async def test_size_median_warn():
    """lot ≥ 3× 同品种近 10 笔中位 → warn。微手账户（中位 0.01）×2 不再误报。"""
    m15 = _df()
    deals = [{"symbol": "GOLD", "lot": 0.01, "time": "2026-01-06T10:00:00"} for _ in range(5)]
    d = await _engine(m15=m15, deals=deals).evaluate(_snapshot(), _ctx(lot=0.02, m15=m15))
    assert d.verdict == "APPROVED"  # 0.02 < 3×0.01=0.03 → 不触发
    d2 = await _engine(m15=m15, deals=deals).evaluate(_snapshot(), _ctx(lot=0.05, m15=m15))
    assert d2.verdict == "CAUTION"  # 0.05 ≥ 0.03 → warn


@pytest.mark.asyncio
async def test_unfamiliar_symbol():
    m15 = _df()
    deals = [{"symbol": "EURUSD", "lot": 0.1, "time": "2026-01-06T10:00:00"}]
    d = await _engine(m15=m15, deals=deals).evaluate(_snapshot(), _ctx(m15=m15))
    assert "陌生品种：警告" in d.risk_flags
    # 空历史 = 数据不足不打标（低频账户防常态误报）
    d2 = await _engine(m15=m15, deals=[]).evaluate(_snapshot(), _ctx(m15=m15))
    assert "陌生品种：警告" not in d2.risk_flags


@pytest.mark.asyncio
async def test_loss_chase_requires_emotion_flag():
    """账户级日亏 1.5% + 情绪类 flag → warn；无情绪 flag → 不触发（梯度：
    硬闸门 3% 才拒，1~3% 且带情绪信号才 warn）。"""
    m15 = _df()
    ctx = _ctx(m15=m15, account_daily_pnl=-150.0)
    d = await _engine(m15=m15).evaluate(_snapshot(), ctx)
    assert "亏损后追单：警告" not in d.risk_flags
    snap = _snapshot(rule_flags=[{"flag": "loss_streak", "severity": "warn", "detail": "3 losses"}])
    d2 = await _engine(m15=m15).evaluate(snap, ctx)
    assert "亏损后追单：警告" in d2.risk_flags


@pytest.mark.asyncio
async def test_sentiment_conflict():
    m15 = _df()
    d = await _engine(m15=m15).evaluate(
        _snapshot(sentiment={"label": "bearish", "score": -0.7}), _ctx(m15=m15))
    assert "情绪冲突：警告" in d.risk_flags


@pytest.mark.asyncio
async def test_no_sl_is_display_only():
    """无 SL → severity=info 只进 reasoning 展示，绝不驱动收敛（死规则隔离：
    SL=0 在硬闸门已被拒，这里永远不该产生 REJECTED/CAUTION）。"""
    m15 = _df()
    ctx = _ctx(sl=0.0, tp=2050.0, m15=m15)
    d = await _engine(m15=m15).evaluate(_snapshot(), ctx)
    assert d.verdict == "APPROVED"
    assert "无止损" in d.reasoning


# ─── 输出形状 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_output_shapes():
    m15 = _df()
    d = await _engine(m15=m15).evaluate(_snapshot(), _ctx(m15=m15))
    shape = d.to_review_llm_shape()
    assert set(shape) == {"verdict", "confidence", "risk_flags", "emotional_indicators", "reasoning"}
    assert shape["verdict"] in ("APPROVED", "CAUTION", "REJECTED")
    assert len(shape["reasoning"]) <= 500
    block = d.audit_block()
    assert set(block) == {"provider", "ts", "latency_ms", "checks", "converge", "degraded"}
    assert block["provider"] == "local_jev"
    for c in block["checks"]:
        assert set(c) >= {"name", "choice", "evidence"}
        assert len(c["evidence"]) <= 200
