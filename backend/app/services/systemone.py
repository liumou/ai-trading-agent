"""SystemOne — 手动单的 System One 确定性决策引擎（JEV 式类型化检查）。

替代慢速 LLM 自由文本审查（90s）：固定输入 → 类型化答案 → 代码收敛器，
毫秒~秒级，全程可审计。设计对齐 QuantDinger 的 JEV 模式（5 道固定检查 +
确定性收敛），但两处刻意不同：
1. 本地确定性规则而非外部 AI API（provider 链里 typesafe_jev 才是外部 API）；
2. fail-closed 而非 fail-open —— 本地通道是真金白银，规则盲区走 LLM 兜底，
   全链失败拒单，绝不放行。

数据质量三档分诊（评审 P0：指标栈对空/陈旧数据静默返 normal/0，会把
「看不清」判成「没问题」——引擎侧必须显式校验）：
- 拉取异常/超时/双 TF 全空/全不足 → ProviderUnavailable → 网关降级链
- 单 TF 缺/陈旧/NaN/deal time 畸形 → data_quality=partial → 收敛 CAUTION
- 齐全 → sufficient

风控强度不松于原 LLM 提示词基线（prompts.ORDER_REVIEW_SYSTEM_PROMPT）：
REJECTED 仅来自规则 block 与 MTF 双 TF 逆势升级；其余风险一律 CAUTION 人在环。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
from loguru import logger

from app.config import get_canonical_symbol, settings
from app.strategy.indicators import adx as calc_adx
from app.strategy.indicators import atr as calc_atr
from app.strategy.indicators import ema as calc_ema
from app.strategy.mtf_filter import get_mtf_consensus, get_trend, get_trend_strength
from app.strategy.regime import detect_regime

OHLCV_COUNT = 120  # 请求 120 根：校验线 min_bars=60 的缓冲（broker 少回几根不误触发）
BAR_SECONDS = {"M15": 900, "H1": 3600}
SPIKE_EMA_PERIOD = 20
ATR_PERIOD = 14
SPIKE_HIST_MIN = 100  # z 分位历史不足 100 根：只允许 warn，不允许 block
CONSEC_DIRECTIONAL_BARS = 4  # spike block 需要的连续同向 K 线
MEDIAN_SAMPLE_MIN = 3  # 中位手数样本下限（<3 笔样本无意义，跳过）

# 各检查的「通过」选项；不在此列 → CAUTION（risk/execution 的 block → REJECTED）
_PASS_CHOICES = {
    "data_quality": {"sufficient"},
    "signal_alignment": {"aligned"},
    "market_regime": {"favorable", "neutral"},
    "risk_check": {"clear"},
    "execution_quality": {"clear"},
}


# 用户可见文案中文化（review 卡片/审计可读性；converge 与 checks 的机器键保持英文）
_CHECK_CN = {
    "data_quality": "数据质量", "signal_alignment": "信号一致性", "market_regime": "市场状态",
    "risk_check": "风险检查", "execution_quality": "执行质量",
}
_CHOICE_CN = {
    "sufficient": "充分", "partial": "部分可用", "insufficient": "不足",
    "aligned": "一致", "mixed": "分歧", "conflict": "冲突",
    "favorable": "有利", "neutral": "中性", "adverse": "不利",
    "clear": "通过", "caution": "谨慎", "block": "阻断",
}
_RULE_CN = {
    "spike_chase": "追高", "exposure_cap": "敞口上限", "size_near_limit": "手数接近上限",
    "loss_chase_combo": "亏损后追单", "rr_sanity": "止损/盈亏比",
    "unfamiliar_symbol": "陌生品种", "sentiment_conflict": "情绪冲突",
    "no_stop_loss": "无止损", "mtf_conflict": "多周期趋势冲突",
}
_SEV_CN = {"warn": "警告", "block": "阻断", "info": "提示"}


class ProviderUnavailable(Exception):
    """Provider 无法产出可信判断（数据获取失败/超时/全盲）——网关沿链降级。"""


@dataclass
class _Flag:
    """单条规则触发结果。strength = 实测值/阈值（>1 越界程度，用于 confidence）。"""

    rule: str
    severity: str  # warn | block | info
    detail: str
    strength: float = 1.0


@dataclass
class SystemOneDecision:
    verdict: str  # APPROVED | CAUTION | REJECTED
    confidence: float
    risk_flags: list[str]
    emotional_indicators: list[str]
    reasoning: str
    checks: list[dict] = field(default_factory=list)
    converge: dict = field(default_factory=dict)
    provider: str = "local_jev"
    degraded: bool = False
    latency_ms: int = 0

    def to_review_llm_shape(self) -> dict:
        """与 _normalize_verdict 输出同形的兼容映射（前端读 review.llm.reasoning）。"""
        return {
            "verdict": self.verdict,
            "confidence": self.confidence,
            "risk_flags": self.risk_flags,
            "emotional_indicators": self.emotional_indicators,
            "reasoning": self.reasoning,
        }

    def audit_block(self) -> dict:
        return {
            "provider": self.provider,
            "ts": datetime.utcnow().isoformat(),
            "latency_ms": self.latency_ms,
            "checks": self.checks,
            "converge": self.converge,
            "degraded": self.degraded,
        }


@dataclass
class _Inputs:
    direction: int
    symbol: str
    entry: float
    sl: float
    tp: float
    lot: float
    spread: float
    equity: float
    balance: float
    account: dict
    account_daily_pnl: float | None
    profile: dict
    positions: list
    rule_flags: list
    sentiment: dict | None
    spec: dict | None
    deals_all: list | None
    deals_symbol: list


class LocalRuleEngine:
    """local_jev provider：全部检查本地确定性计算，无外部依赖。"""

    provider = "local_jev"

    def __init__(self, market_data):
        # market_data: MarketDataService（get_ohlcv 返回校验后 DataFrame；
        # .connector 供 get_symbol_spec / get_history 复用）
        self.market_data = market_data

    # ─── 入口 ─────────────────────────────────────────────────────────────

    async def evaluate(self, snapshot: dict, ctx) -> SystemOneDecision:
        started = time.perf_counter()
        order = snapshot.get("order", {})
        direction = 1 if str(order.get("type", "")).upper().startswith("BUY") else -1

        m15, h1, spec, deals_all = await self._fetch_inputs(ctx)
        dq_choice, dq_issues, usable = self._assess_data_quality(m15, h1, ctx)

        inp = self._build_inputs(snapshot, ctx, direction, spec, deals_all)

        flags: list[_Flag] = []
        flags.extend(self._rule_exposure(inp))
        flags.extend(self._rule_size_near_limit(inp))
        loss_chase = self._rule_loss_chase(inp)
        if loss_chase:
            flags.append(loss_chase)
        flags.extend(self._rule_rr_sanity(inp, usable.get("M15")))
        unfamiliar = self._rule_unfamiliar(inp)
        if unfamiliar:
            flags.append(unfamiliar)
        sentiment = self._rule_sentiment(inp)
        if sentiment:
            flags.append(sentiment)
        no_sl = self._rule_no_sl_display(inp)
        if no_sl:
            flags.append(no_sl)
        spike = self._rule_spike_chase(inp, usable.get("M15"), direction)
        if spike:
            flags.append(spike)
        # 3c 方向纪律：同品种反向持仓 / 当日反手 → CAUTION（人在环确认）。
        # 硬拒（第 2 次反手）已在 discipline_gate 完成；此处是审查层软检查，
        # 让 CAUTION 确认流携带"方向切换"上下文（评审 2 R4 / 评审 7）。
        direction_flip = self._rule_direction_flip(inp, direction)
        if direction_flip:
            flags.append(direction_flip)

        alignment, align_evi, strong_against = self._signal_alignment(usable, direction)
        # conflict 升级：双 TF 同向逆向且各 ADX≥reject，或 conflict 叠加
        # spike/loss_chase warn（= 逆势 + 追高/情绪化组合，基线允许拒）。
        mtf_upgrade: _Flag | None = None
        if strong_against >= 2:
            mtf_upgrade = _Flag("mtf_conflict", "block",
                                f"两个周期均逆势且 ADX 强劲：{align_evi}", 1.0)
        elif alignment == "conflict" and any(
            f.rule in ("spike_chase", "loss_chase_combo") and f.severity == "warn" for f in flags
        ):
            mtf_upgrade = _Flag("mtf_conflict", "block",
                                f"逆势 + 追高/情绪化警告：{align_evi}", 1.0)

        regime_choice, regime_evi = self._market_regime(usable.get("M15"), direction)

        triggered = [f for f in flags if f.severity in ("warn", "block")]
        risk_flags = [f for f in triggered if f.rule in ("exposure_cap", "size_near_limit", "loss_chase_combo")]
        exec_flags = [f for f in triggered if f.rule in ("spike_chase", "rr_sanity")]

        checks, converge = self._converge(
            dq_choice, dq_issues, alignment, align_evi, regime_choice, regime_evi,
            risk_flags, exec_flags, mtf_upgrade,
        )
        confidence, reasoning, risk_flag_names = self._summarize(
            checks, flags, triggered, dq_issues, inp, usable,
        )

        return SystemOneDecision(
            verdict=converge["verdict"],
            confidence=confidence,
            risk_flags=risk_flag_names,
            emotional_indicators=[],  # 情绪模式已在 gate 情绪规则层硬判，此处不重复
            reasoning=reasoning,
            checks=checks,
            converge=converge,
            provider=self.provider,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    # ─── 数据获取与质量 ───────────────────────────────────────────────────

    async def _fetch_inputs(self, ctx) -> tuple[pd.DataFrame | None, pd.DataFrame | None, dict | None, list | None]:
        """M15/H1/spec/history 四路并发，整体 fetch_timeout 封顶。

        - 超时/异常 → ProviderUnavailable（基础设施故障 ≠ 风险违规）
        - 双 TF 全空 → ProviderUnavailable
        - spec/history 失败容忍（返回 None，对应规则降级 warn-only/跳过）
        """
        try:
            m15_df, h1_df, spec, deals = await asyncio.wait_for(
                asyncio.gather(
                    self.market_data.get_ohlcv(ctx.symbol, "M15", OHLCV_COUNT),
                    self.market_data.get_ohlcv(ctx.symbol, "H1", OHLCV_COUNT),
                    self._fetch_spec(ctx.symbol),
                    self._fetch_full_history(),
                ),
                timeout=settings.manual_review_fetch_timeout_s,
            )
        except Exception as e:  # noqa: BLE001 — 超时与异常同诊：provider 失败
            raise ProviderUnavailable(f"market data fetch failed: {e}") from e

        for df in (m15_df, h1_df):
            if df is not None and not df.empty:
                break
        else:
            raise ProviderUnavailable("ohlcv empty on all timeframes")

        return m15_df, h1_df, spec, deals if isinstance(deals, list) else None

    async def _fetch_spec(self, symbol: str) -> dict | None:
        try:
            res = await self.market_data.connector.get_symbol_spec(symbol)
            if res.get("success"):
                return res.get("data") or {}
        except Exception as e:  # noqa: BLE001
            logger.debug(f"SystemOne symbol spec fetch failed: {e!r}")
        return None

    async def _fetch_full_history(self) -> list | None:
        """全品种近 N 天成交（unfamiliar/中位手数）。失败容忍 → None。

        刻意不碰 gate 的 snapshot.recent_trades（那是同品种 1 天口径，
        LLM 兜底 prompt 依赖它 —— provider=llm 回滚纯度）。
        """
        try:
            res = await self.market_data.connector.get_history(
                days=settings.manual_review_familiar_days
            )
            if res.get("success"):
                return res.get("data", []) or []
        except Exception as e:  # noqa: BLE001
            logger.debug(f"SystemOne history fetch failed: {e!r}")
        return None

    def _assess_data_quality(
        self, m15: pd.DataFrame | None, h1: pd.DataFrame | None, ctx
    ) -> tuple[str, list[str], dict[str, pd.DataFrame]]:
        """逐 TF 显式校验：空/根数不足/陈旧 → 该 TF 不可用。

        全部不可用 → ProviderUnavailable（上游已拦双空，这里拦双不足）。
        任一不可用/陈旧 → partial。返回 (choice, issues, usable_TFs)。
        """
        min_bars = settings.manual_review_min_bars
        issues: list[str] = []
        usable: dict[str, pd.DataFrame] = {}
        for df, tf in ((m15, "M15"), (h1, "H1")):
            if df is None or df.empty:
                issues.append(f"{tf}：无数据")
                continue
            if len(df) < min_bars:
                issues.append(f"{tf}：{len(df)} 根 < 最低 {min_bars} 根")
                continue
            stale = self._staleness_bars(df, ctx, BAR_SECONDS[tf])
            if stale is not None and stale > settings.manual_review_freshness_mult:
                issues.append(f"{tf}：最后K线陈旧（{stale:.1f} 根）")
                continue
            usable[tf] = df
        if not usable:
            too_short = any(("无数据" in i) or ("最低" in i) for i in issues)
            if too_short:
                # 根数不足/无数据 = 规则全盲 ≈ 基础设施故障 → 降级 LLM 兜底
                raise ProviderUnavailable(f"no usable timeframe data: {'; '.join(issues)}")
            # 全 TF 陈旧（周末休市/断 feed 的已知市场状态）：不是基础设施故障
            # —— 规则弃权、data_quality=insufficient → CAUTION 人在环
            # （评审分诊表：陈旧可确认或重试，非违规）
            return "insufficient", issues, {}
        return ("partial" if issues else "sufficient"), issues, usable

    def _staleness_bars(self, df: pd.DataFrame, ctx, bar_seconds: int) -> float | None:
        """陈旧度（单位=bar 周期数）。同源时钟：tick 时间与 K 线时间都出自
        bridge（同一 fromtimestamp 换算），差值免疫时区/周末/DST。
        tick 无时间字段（测试 mock/旧桥）→ None = 无法评估，不臆断陈旧。"""
        last_bar = df.index[-1]
        tick_time_raw = (ctx.tick or {}).get("time")
        if not tick_time_raw:
            return None
        try:
            ref = pd.Timestamp(tick_time_raw)
            if ref.tzinfo is not None:
                ref = ref.tz_localize(None)
            lb = pd.Timestamp(last_bar)
            if lb.tzinfo is not None:
                lb = lb.tz_localize(None)
            return (ref - lb).total_seconds() / bar_seconds
        except (ValueError, TypeError):
            return None

    # ─── 输入打包 ─────────────────────────────────────────────────────────

    def _build_inputs(self, snapshot, ctx, direction, spec, deals_all) -> _Inputs:
        account = ctx.account or {}
        sentiment = (snapshot.get("market") or {}).get("sentiment")
        deals_symbol = [
            d for d in (deals_all or [])
            if get_canonical_symbol(str(d.get("symbol") or "")) == ctx.symbol
        ]
        deals_symbol.sort(key=lambda d: str(d.get("time") or ""), reverse=True)
        return _Inputs(
            direction=direction,
            symbol=ctx.symbol,
            entry=float(ctx.entry_ref or 0),
            sl=float(ctx.sl or 0),
            tp=float(ctx.tp or 0),
            lot=float(ctx.lot or 0),
            spread=float(ctx.spread or 0),
            equity=float(account.get("equity") or 0),
            balance=float(account.get("balance") or 0),
            account=account,
            account_daily_pnl=ctx.account_daily_pnl,
            profile=ctx.profile or {},
            positions=ctx.positions or [],
            rule_flags=snapshot.get("rule_flags") or [],
            sentiment=sentiment if isinstance(sentiment, dict) else None,
            spec=spec,
            deals_all=deals_all,
            deals_symbol=deals_symbol,
        )

    # ─── 规则（阈值全部 settings.manual_review_*，公式见计划参数表）────────

    def _rule_spike_chase(self, inp, m15, direction) -> _Flag | None:
        """z=|entry−EMA20|/ATR14 对近 200 根滚动分位。历史<100 根只 warn；
        block 需 >block 分位 且 连续≥4 根同向 K 线（防单根脉冲误杀）。"""
        if m15 is None:
            return None
        close = m15["close"]
        ema20 = calc_ema(close, SPIKE_EMA_PERIOD)
        atr14 = calc_atr(m15["high"], m15["low"], close, ATR_PERIOD)
        if pd.isna(ema20.iloc[-1]) or pd.isna(atr14.iloc[-1]) or atr14.iloc[-1] <= 0:
            return None
        z_now = abs(inp.entry - float(ema20.iloc[-1])) / float(atr14.iloc[-1])
        z_hist = ((close - ema20).abs() / atr14).dropna()
        pct = float((z_hist < z_now).mean()) if len(z_hist) else 0.0
        warn_p = settings.manual_review_spike_pct_warn
        block_p = settings.manual_review_spike_pct_block
        detail = f"入场偏离 z={z_now:.2f}（近 {len(z_hist)} 根分位 p{pct * 100:.0f}）"
        if (
            len(z_hist) >= SPIKE_HIST_MIN
            and pct > block_p
            and self._consecutive_directional_bars(m15, direction) >= CONSEC_DIRECTIONAL_BARS
        ):
            return _Flag("spike_chase", "block",
                         f"{detail}，且连续 {CONSEC_DIRECTIONAL_BARS} 根同向K线", pct)
        if pct > warn_p:
            return _Flag("spike_chase", "warn", detail, pct)
        return None

    def _consecutive_directional_bars(self, m15: pd.DataFrame, direction: int) -> int:
        body_dir = (m15["close"] > m15["open"]).astype(int).replace(0, -1)
        n = 0
        for v in reversed(body_dir.tolist()):
            if int(v) == direction:
                n += 1
            else:
                break
        return n

    def _rule_exposure(self, inp) -> list[_Flag]:
        """risk_pct = |entry−SL|/tick_size × tick_value × lot / equity。

        tick_value 由 bridge /symbol-spec 提供（账户货币计价）——规避
        quote≠USD 品种（USDJPY）notional 直除 equity 的汇率错误（评审 P0）。
        spec 缺失 → warn-only（无法验证敞口 ≠ 敞口安全）。margin_pct 为
        可选辅指标：bridge /account 暂无 leverage 字段，有则启用。
        """
        flags: list[_Flag] = []
        if inp.sl <= 0 or inp.entry <= 0 or inp.equity <= 0:
            return flags
        tick_value = (inp.spec or {}).get("trade_tick_value")
        tick_size = (inp.spec or {}).get("trade_tick_size")
        if not tick_value or not tick_size or tick_size <= 0:
            return [_Flag("exposure_cap", "warn",
                          "无法核实风险占比（缺 tick_value/tick_size）——请人工核对仓位", 1.0)]
        loss = abs(inp.entry - inp.sl) / tick_size * tick_value * inp.lot
        risk_pct = loss / inp.equity
        warn_p = settings.manual_review_risk_pct_warn
        block_p = settings.manual_review_risk_pct_block
        detail = f"风险占比 {risk_pct * 100:.1f}%（潜在亏损 {loss:.2f}）"
        if risk_pct > block_p:
            flags.append(_Flag("exposure_cap", "block", f"{detail}，超过阻断线 {block_p * 100:.0f}%", risk_pct / block_p))
        elif risk_pct > warn_p:
            flags.append(_Flag("exposure_cap", "warn", f"{detail}，超过警告线 {warn_p * 100:.0f}%", risk_pct / warn_p))

        leverage = inp.account.get("leverage")
        if leverage:
            try:
                cs = float((inp.spec or {}).get("trade_contract_size") or 0)
                margin_pct = (inp.entry * inp.lot * cs / float(leverage)) / inp.equity if cs else 0.0
                mw, mb = settings.manual_review_margin_pct_warn, settings.manual_review_margin_pct_block
                if inp.profile.get("asset_class") == "crypto":
                    # crypto 保证金率天然 5-10%（forex ~1%），同一 margin_pct
                    # 含义不同——阈值放宽一倍，避免常态误报
                    mw, mb = mw * 2, mb * 2
                detail = f"保证金占用 {margin_pct * 100:.1f}% of equity"
                if margin_pct > mb:
                    flags.append(_Flag("exposure_cap", "block", f"{detail} 超过阻断线 {mb * 100:.0f}%", margin_pct / mb))
                elif margin_pct > mw:
                    flags.append(_Flag("exposure_cap", "warn", f"{detail} 超过警告线 {mw * 100:.0f}%", margin_pct / mw))
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        return flags

    def _rule_size_near_limit(self, inp) -> list[_Flag]:
        flags: list[_Flag] = []
        # 全局上限与硬闸门一致（env GUARDRAILS_MAX_LOT_PER_TRADE 可覆盖）
        cap = float(getattr(settings, "guardrails_max_lot_per_trade", 1.0))
        sym_cap = inp.profile.get("max_lot")
        if sym_cap:
            cap = min(cap, float(sym_cap))
        min_lot = float(inp.profile.get("volume_min") or 0.01)
        warn_line = cap * settings.manual_review_size_cap_frac
        # 上限与最小可下单位几乎重合（如 cap=0.01、volume_min=0.01）时，
        # 「接近上限」警告无信息量（每单必触发）→ 跳过，避免 CAUTION 刷屏
        if warn_line > min_lot and inp.lot >= warn_line:
            flags.append(_Flag("size_near_limit", "warn",
                               f"手数 {inp.lot} ≥ 上限 {cap} 的 {settings.manual_review_size_cap_frac:.0%}", 1.0))
        lots = [float(d.get("lot") or 0) for d in inp.deals_symbol[:10] if d.get("lot")]
        if len(lots) >= MEDIAN_SAMPLE_MIN:
            med = sorted(lots)[len(lots) // 2]
            if med > 0 and inp.lot >= med * settings.manual_review_size_med_mult:
                flags.append(_Flag(
                    "size_near_limit", "warn",
                    f"手数 {inp.lot} ≥ 近 {len(lots)} 笔中位 {med} 的 {settings.manual_review_size_med_mult:.0f} 倍",
                    inp.lot / (med * settings.manual_review_size_med_mult),
                ))
        return flags

    def _rule_direction_flip(self, inp, direction) -> _Flag | None:
        """3c 方向纪律（评审 2 R4 / 评审 7）：同品种存在反向持仓，或当日已
        反手（flip key 计数 ≥2）→ CAUTION 级，强制人工二次确认。

        语义：硬拒（当日第 2 次反手）在 discipline_gate 已做；此处是审查层
        软检查，把"方向切换"上下文带进 CAUTION 确认流 —— 确认时前端需
        勾选 ≥2 项确认信号（3a）。crypto 反向持仓同样拦（跨品种对冲不受限）。
        """
        try:
            same_symbol = [
                p for p in (inp.positions or [])
                if get_canonical_symbol(str(p.get("symbol") or "")) == inp.symbol
            ]
            for p in same_symbol:
                p_type = str(p.get("type") or "").upper()
                pos_dir = 1 if p_type.startswith("BUY") else -1 if p_type.startswith("SELL") else 0
                if pos_dir != 0 and pos_dir != direction:
                    return _Flag(
                        "direction_flip", "warn",
                        f"开仓方向与同品种持仓相反（{p_type} {p.get('lot')}）——"
                        f"反向加仓/反手需人工确认（≥2 项确认信号）", 0.6,
                    )
        except Exception:  # noqa: BLE001
            pass
        return None

    def _rule_loss_chase(self, inp) -> _Flag | None:
        """账户级日亏 1%~3%（3% 由硬闸门拒）+ 情绪类 warn 信号的组合。"""
        adp = inp.account_daily_pnl
        if adp is None or inp.balance <= 0:
            return None
        threshold = -settings.manual_review_loss_warn_pct * inp.balance
        if adp > threshold:
            return None
        emotion_flags = {f.get("flag") for f in inp.rule_flags}
        combo = emotion_flags & {"revenge_trade_window", "loss_streak", "near_frequency_limit"}
        if not combo:
            return None
        return _Flag("loss_chase_combo", "warn",
                     f"账户当日亏损 {adp:.2f}（{adp / inp.balance * 100:.1f}%）且带情绪标记 {sorted(combo)}", 1.0)

    def _rule_rr_sanity(self, inp, m15) -> list[_Flag]:
        flags: list[_Flag] = []
        if inp.sl <= 0 or inp.entry <= 0:
            return flags  # 无 SL 单已被硬闸门拒绝，防御性跳过
        sl_dist = abs(inp.entry - inp.sl)
        atr14 = None
        if m15 is not None:
            v = calc_atr(m15["high"], m15["low"], m15["close"], ATR_PERIOD).iloc[-1]
            atr14 = None if pd.isna(v) else float(v)
        floor = max(3 * inp.spread if inp.spread > 0 else 0.0,
                    0.5 * atr14 if atr14 else 0.0)
        if floor > 0 and sl_dist < floor:
            flags.append(_Flag("rr_sanity", "warn",
                               f"止损距离 {sl_dist} < 下限 {floor:.5f}（点差/ATR）——止损过紧", 1.0))
        if inp.tp > 0 and sl_dist > 0:
            rr = abs(inp.tp - inp.entry) / sl_dist
            if rr < settings.manual_review_rr_min:
                flags.append(_Flag("rr_sanity", "warn",
                                   f"盈亏比 {rr:.2f} < {settings.manual_review_rr_min}——风险报酬倒挂",
                                   rr / settings.manual_review_rr_min))
        if atr14 and sl_dist > settings.manual_review_sl_max_atr * atr14:
            flags.append(_Flag("rr_sanity", "warn",
                               f"止损距离 {sl_dist} > {settings.manual_review_sl_max_atr}x ATR {atr14:.5f}——止损过宽", 1.0))
        return flags

    def _rule_unfamiliar(self, inp) -> _Flag | None:
        if not inp.deals_all:
            return None  # 全量历史不可得/为空：跳过不打标（数据缺失 ≠ 陌生，且
            # 低频账户在 14d 窗口内本来就没成交，打标只会常态误报）
        known = {get_canonical_symbol(str(d.get("symbol") or "")) for d in inp.deals_all}
        known |= {str(p.get("symbol") or "") for p in inp.positions}
        if inp.symbol in known:
            return None
        return _Flag("unfamiliar_symbol", "warn",
                     f"近 {settings.manual_review_familiar_days} 天首次交易 {inp.symbol}", 1.0)

    def _rule_sentiment(self, inp) -> _Flag | None:
        s = inp.sentiment
        if not s:
            return None
        try:
            score = float(s.get("score"))
        except (TypeError, ValueError):
            return None
        thr = settings.manual_review_sent_conflict
        if inp.direction == 1 and score <= -thr:
            return _Flag("sentiment_conflict", "warn", f"BUY 与情绪相悖（score {score}）", 1.0)
        if inp.direction == -1 and score >= thr:
            return _Flag("sentiment_conflict", "warn", f"SELL 与情绪相悖（score {score}）", 1.0)
        return None

    def _rule_no_sl_display(self, inp) -> _Flag | None:
        # 展示性标志：SL=0 已被 guardrails 硬拒，正常流到不了这里；
        # severity=info 不进收敛（死规则隔离，评审 P1）。
        if inp.sl > 0:
            return None
        return _Flag("no_stop_loss", "info", "订单无止损（硬闸门本应拒绝）", 0.0)

    def _signal_alignment(self, usable, direction) -> tuple[str, str, int]:
        """规则 9 mtf_conflict：consensus(M15,H1) 逆向 → conflict。

        conflict 单独 = CAUTION（= LLM 基线：counter to momentum 本就是
        CAUTION 档）；升级 REJECTED 的判断由 evaluate() 依据 strong_against
        与情绪/动量 warn 组合完成。返回 (choice, evidence, strong_against_count)。
        """
        trends: dict[str, int] = {}
        adx_values: dict[str, float] = {}
        for tf, df in usable.items():
            t = get_trend(df, tf)
            if t != 0:
                trends[tf] = t
            a = get_trend_strength(df)
            if a > 0:
                adx_values[tf] = a
        if not trends:
            # 无趋势周期 = 无方向矛盾（震荡市）→ 放行；震荡本身由
            # market_regime 的 neutral 承接，alignment 不重复惩罚
            # （否则横盘市每单都 CAUTION —— 评审 P0 的常态误报）。
            return "aligned", "no trending timeframe — no directional contradiction", 0

        consensus = get_mtf_consensus(trends, adx_values or None)
        if consensus == direction:
            choice = "aligned"
        elif consensus == -direction:
            choice = "conflict"
        else:
            choice = "mixed"
        evi = f"consensus={consensus} trends={trends} adx={ {k: round(v, 1) for k, v in adx_values.items()} }"
        strong_against = sum(
            1 for tf, t in trends.items()
            if t == -direction and adx_values.get(tf, 0) >= settings.manual_review_mtf_adx_reject
        )
        return choice, evi, strong_against

    def _market_regime(self, m15, direction) -> tuple[str, str]:
        if m15 is None or len(m15) < 16:
            return "insufficient", "no M15 data"
        atr14 = calc_atr(m15["high"], m15["low"], m15["close"], ATR_PERIOD).iloc[-1]
        adx_val = calc_adx(m15["high"], m15["low"], m15["close"], ATR_PERIOD)["adx"].iloc[-1]
        if pd.isna(atr14) or pd.isna(adx_val):
            return "insufficient", "ATR/ADX NaN (flat or insufficient bars)"
        price = float(m15["close"].iloc[-1])
        atr_pct = float(atr14) / price if price > 0 else 0.0
        regime = detect_regime(atr_pct, float(adx_val))
        trend = get_trend(m15, "M15")
        evi = f"regime={regime} atr%={atr_pct * 100:.2f} adx={float(adx_val):.1f} m15_trend={trend}"
        if regime.startswith("trending"):
            if trend == direction:
                return "favorable", evi
            if trend == -direction:
                return "adverse", evi
        return "neutral", evi

    # ─── 收敛与汇总 ───────────────────────────────────────────────────────

    def _converge(self, dq, dq_issues, alignment, align_evi, regime, regime_evi,
                  risk_flags, exec_flags, mtf_upgrade) -> tuple[list[dict], dict]:
        risk_block = any(f.severity == "block" for f in risk_flags)
        exec_block = any(f.severity == "block" for f in exec_flags)
        checks = [
            {"name": "data_quality", "choice": dq,
             "evidence": ("; ".join(dq_issues) or "各输入新鲜可用")[:200]},
            {"name": "signal_alignment", "choice": alignment, "evidence": align_evi[:200]},
            {"name": "market_regime", "choice": regime, "evidence": regime_evi[:200]},
            {"name": "risk_check",
             "choice": "block" if risk_block else ("caution" if risk_flags else "clear"),
             "evidence": ("；".join(f.detail for f in risk_flags) or "无仓位/敞口问题")[:200]},
            {"name": "execution_quality",
             "choice": "block" if exec_block else ("caution" if exec_flags else "clear"),
             "evidence": ("；".join(f.detail for f in exec_flags) or "无执行问题")[:200]},
        ]
        if risk_block or exec_block or (mtf_upgrade is not None and mtf_upgrade.severity == "block"):
            verdict = "REJECTED"
        elif any(c["choice"] not in _PASS_CHOICES[c["name"]] for c in checks):
            verdict = "CAUTION"
        else:
            verdict = "APPROVED"
        converge = {c["name"]: c["choice"] for c in checks}
        converge["verdict"] = verdict
        converge["upgrade"] = f"{mtf_upgrade.rule}: {mtf_upgrade.detail}" if mtf_upgrade else ""
        return checks, converge

    def _summarize(self, checks, all_flags, triggered, dq_issues, inp, usable) -> tuple[float, str, list[str]]:
        # 证据完备度：可用输入加权占比（M15 权重最高——全部规则的基准周期）
        parts = [
            ("M15" in usable, 0.35),
            ("H1" in usable, 0.20),
            (inp.deals_all is not None, 0.15),
            (bool(inp.spec), 0.20),
            (inp.sentiment is not None, 0.10),
        ]
        completeness = sum(w for ok, w in parts if ok)
        strengths = [min(f.strength, 2.0) for f in triggered] or [1.0]
        margin = min(max(strengths) / 2, 1.0)
        confidence = round(0.5 * completeness + 0.5 * margin, 3)

        seg = [f"{_CHECK_CN.get(c['name'], c['name'])}={_CHOICE_CN.get(c['choice'], c['choice'])}" for c in checks]
        if triggered:
            seg.append("标记：" + "；".join(
                f"{_RULE_CN.get(f.rule, f.rule)}（{_SEV_CN.get(f.severity, f.severity)}）：{f.detail}"
                for f in triggered))
        info_flags = [f for f in all_flags if f.severity == "info"]
        if info_flags:
            seg.append("提示：" + "；".join(f.detail for f in info_flags))
        if dq_issues:
            seg.append("数据：" + "；".join(dq_issues))
        reasoning = (" | ".join(seg))[:500]

        risk_flag_names = [
            f"{_RULE_CN.get(f.rule, f.rule)}：{_SEV_CN.get(f.severity, f.severity)}"
            for f in triggered
        ][:10]
        return confidence, reasoning, risk_flag_names
