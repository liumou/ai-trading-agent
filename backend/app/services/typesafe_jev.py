"""TypesafeJevProvider — 外部 JEV System One API（QuantDinger 协议镜像，Phase 3）。

POST `{base_url}/systemone`（Bearer API key），body = {model, state, questions}；
响应 answers{问题名: {choice, probabilities, confidence}}。逐问严格校验
（选项白名单 / 概率和≈1 / choice=argmax / confidence∈[0,1]），任一畸形 →
ProviderUnavailable → 网关沿链降级（local_jev → llm），绝不产出低质量判决。

决策收敛（QuantDinger 语义，映射到本地三元裁决）：
- risk_check / execution_quality = block，或 signal=conflict ∧ regime=adverse
  双高置信 → REJECTED
- risk/execution 置信度 < conf_floor(0.30) → ProviderUnavailable（近似噪声，
  降级链）；在 floor~min_confidence(0.55) 之间 → CAUTION（AI 倾向通过但把握
  不足 → 人工二次确认；free 模型常在此区间）
- caution 档检查（通过但带保留）→ CAUTION；全 clear 且置信达标 → APPROVED
- firewall 只收紧不放松：低置信永不产出 APPROVED

熔断：连续失败达阈值 → 冷却期内跳过本 provider（每单白等 8s 的代价不值得）。
密钥绝不进入 audit_block / reasoning / 日志。
"""

from __future__ import annotations

import asyncio
import time

import httpx
from loguru import logger

from app.config import settings
from app.services.systemone import OHLCV_COUNT, ProviderUnavailable, SystemOneDecision

# 与 QuantDinger ai_decision_filter 相同的 5 道类型化问题（人工手动单语境）
JEV_QUESTIONS = {
    "data_quality": {
        "type": "choice",
        "instructions": (
            "Assess whether the supplied point-in-time evidence is sufficient "
            "for a pre-trade decision on a manual order."
        ),
        "criteria": {
            "sufficient": "Market, signal, portfolio, and execution evidence are current and usable.",
            "partial": "Some evidence is missing or stale, but concrete risk checks remain possible.",
            "insufficient": "The state lacks enough current evidence for a directional or risk judgement.",
        },
    },
    "signal_alignment": {
        "type": "choice",
        "instructions": (
            "Compare the requested manual order direction and reason with the "
            "supplied market evidence. Missing evidence is insufficient, not conflict."
        ),
        "criteria": {
            "aligned": "Trend, momentum, and volatility evidence support the requested direction.",
            "mixed": "Evidence is usable but timeframes or indicators disagree without a strong contradiction.",
            "conflict": "Current evidence materially contradicts the requested direction.",
            "insufficient": "Not enough current market evidence to judge alignment.",
        },
    },
    "market_regime": {
        "type": "choice",
        "instructions": (
            "Judge whether the current market regime is suitable for this requested manual entry."
        ),
        "criteria": {
            "favorable": "Trend, momentum, volatility, and volume reasonably support this entry.",
            "neutral": "The regime is mixed or range-bound but does not materially oppose the entry.",
            "adverse": "The regime materially opposes the entry or shows unstable conditions.",
            "insufficient": "Market evidence is unavailable or too stale to judge the regime.",
        },
    },
    "risk_check": {
        "type": "choice",
        "instructions": (
            "Assess position sizing, leverage, existing exposure, drawdown, "
            "recent performance, and protection on this manual order."
        ),
        "criteria": {
            "clear": "The new exposure is proportionate and no material account or portfolio risk is visible.",
            "caution": "Risk is elevated but remains within limits and does not require blocking.",
            "block": "A concrete sizing, leverage, concentration, drawdown, loss-streak, or protection risk requires blocking.",
            "insufficient": "Account evidence is incomplete and no concrete blocking risk can be established.",
        },
    },
    "execution_quality": {
        "type": "choice",
        "instructions": (
            "Assess price freshness, reference-price deviation, order type, "
            "spread, protection, and execution constraints."
        ),
        "criteria": {
            "clear": "The order can be submitted with current data and no material execution concern.",
            "caution": "Execution conditions are imperfect but do not justify blocking.",
            "block": "Stale or contradictory pricing, invalid protection, or another concrete issue requires blocking.",
            "insufficient": "Execution evidence is incomplete and no concrete blocking issue can be established.",
        },
    },
}

JEV_CHECK_OPTIONS = {
    "data_quality": {"sufficient", "partial", "insufficient"},
    "signal_alignment": {"aligned", "mixed", "conflict", "insufficient"},
    "market_regime": {"favorable", "neutral", "adverse", "insufficient"},
    "risk_check": {"clear", "caution", "block", "insufficient"},
    "execution_quality": {"clear", "caution", "block", "insufficient"},
}

# 用户可见文案中文化（converge 与 checks 的机器键保持英文）
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
_REASON_CN = {
    "jev_entry_approved": "JEV 判定通过",
    "jev_entry_approved_with_caution": "JEV 判定通过（需谨慎）",
    "jev_entry_low_confidence": "JEV 置信度不足（需人工确认）",
    "jev_entry_rejected:risk_block": "JEV 判定拒绝：风险阻断",
    "jev_entry_rejected:execution_block": "JEV 判定拒绝：执行阻断",
    "jev_entry_rejected:signal_conflict": "JEV 判定拒绝：信号冲突",
}


def make_client(proxy: str | None, timeout_s: float) -> httpx.AsyncClient:
    """独立工厂：测试用 MockTransport 替换（monkeypatch 模块级函数即可）。"""
    return httpx.AsyncClient(timeout=timeout_s, proxy=proxy)


class TypesafeJevProvider:
    """typesafe_jev provider：外部 JEV 决策 API，输出与 local 同形的 SystemOneDecision。

    market_data（MarketDataService）可选：提供时在 state 里附上紧凑多周期
    行情证据（M15/H1 trend/ADX/ATR%）——JEV 的 signal_alignment / market_regime
    判断需要它，缺了会让执行质量检查低置信（实测 exec=0.32 < 0.55 而降级）。
    """

    provider = "typesafe_jev"

    def __init__(self, market_data=None):
        self._market_data = market_data
        self._failures = 0
        self._cooldown_until = 0.0

    def is_configured(self) -> bool:
        return bool((settings.manual_review_typesafe_api_key or "").strip())

    def _circuit_open(self) -> bool:
        return time.monotonic() < self._cooldown_until

    def _note_failure(self):
        self._failures += 1
        thr = settings.manual_review_typesafe_circuit_threshold
        if self._failures >= thr:
            self._cooldown_until = time.monotonic() + settings.manual_review_typesafe_circuit_cooldown_s
            logger.bind(event="jev_circuit_open", failures=self._failures).warning(
                f"JEV failed {self._failures}x consecutively — skipping provider for "
                f"{settings.manual_review_typesafe_circuit_cooldown_s}s (chain continues)")

    async def evaluate(self, snapshot: dict, ctx) -> SystemOneDecision:
        if not self.is_configured():
            raise ProviderUnavailable("typesafe_jev not configured (no API key)")
        if self._circuit_open():
            raise ProviderUnavailable("typesafe_jev circuit open (cooldown)")
        started = time.perf_counter()
        try:
            payload = await self._request(snapshot, ctx)
        except Exception as e:  # noqa: BLE001 — 网络/超时/HTTP 错误同诊：降级
            self._note_failure()
            raise ProviderUnavailable(f"jev request failed: {self._safe(e)}") from e
        try:
            results = self._validate(payload)
            verdict, confidence, reason, checks, converge = self._converge(results)
        except ValueError as e:
            self._note_failure()
            raise ProviderUnavailable(f"jev response invalid: {self._safe(e)}") from e
        self._failures = 0
        return SystemOneDecision(
            verdict=verdict,
            confidence=confidence,
            risk_flags=[f"JEV {_CHECK_CN.get(name, name)}={_CHOICE_CN.get(choice, choice)}"
                        for name, (choice, _, _) in results.items()][:10],
            emotional_indicators=[],
            reasoning=reason,
            checks=checks,
            converge=converge,
            provider=self.provider,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    # ─── HTTP ─────────────────────────────────────────────────────────────

    async def _request(self, snapshot: dict, ctx) -> dict:
        base = (settings.manual_review_typesafe_base_url or "").strip().rstrip("/")
        if not base:
            raise ValueError("typesafe_jev base_url not configured")
        url = base if base.endswith("/systemone") else f"{base}/systemone"
        proxy = (settings.manual_review_typesafe_proxy_url or "").strip() or None
        timeout_s = settings.manual_review_typesafe_timeout_s
        evidence = await self._market_evidence(ctx)
        async with make_client(proxy, timeout_s) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {settings.manual_review_typesafe_api_key.strip()}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": (settings.manual_review_typesafe_model or "jev-latest").strip(),
                    "state": self._state_payload(snapshot, ctx, evidence),
                    "questions": JEV_QUESTIONS,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("response is not a JSON object")
        return data

    async def _market_evidence(self, ctx) -> dict:
        """紧凑多周期行情证据（与 local 引擎同源的 OHLCV + 指标摘要）。

        拉取失败/未注入 market_data → 返回 {}（JEV 缺证据可低置信降级，
        不在此抛错 —— 数据问题归决策链处理）。"""
        if self._market_data is None:
            return {}
        try:
            m15, h1 = await asyncio.wait_for(
                asyncio.gather(
                    self._market_data.get_ohlcv(ctx.symbol, "M15", OHLCV_COUNT),
                    self._market_data.get_ohlcv(ctx.symbol, "H1", OHLCV_COUNT),
                ),
                timeout=settings.manual_review_fetch_timeout_s,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"JEV market evidence fetch failed: {e!r}")
            return {}

        def _tf(df, tf: str) -> dict:
            if df is None or df.empty or len(df) < 22:
                return {"tf": tf, "available": False}
            try:
                from app.strategy.indicators import adx as calc_adx
                from app.strategy.indicators import atr as calc_atr
                from app.strategy.indicators import ema as calc_ema
                from app.strategy.indicators import macd as calc_macd
                from app.strategy.indicators import rsi as calc_rsi
                from app.strategy.mtf_filter import get_trend

                close = df["close"]
                atr14 = calc_atr(df["high"], df["low"], close, 14).iloc[-1]
                adx_val = calc_adx(df["high"], df["low"], close, 14)["adx"].iloc[-1]
                price = float(close.iloc[-1])
                return {
                    "tf": tf, "available": True,
                    "trend": int(get_trend(df, tf) or 0),
                    "adx": round(float(adx_val), 1) if adx_val == adx_val else None,
                    "atr_pct": round(float(atr14) / price * 100, 3) if price > 0 else None,
                    "rsi": round(float(calc_rsi(close, 14).iloc[-1]), 1),
                    "macd_hist": round(float(calc_macd(close)["histogram"].iloc[-1]), 3),
                    "momentum_pct": round(
                        (float(price) / float(close.iloc[-min(20, len(close))]) - 1) * 100, 3),
                    "last_close": round(price, 2),
                    "bars": int(len(df)),
                }
            except Exception as e:  # noqa: BLE001
                logger.debug(f"JEV TF evidence failed ({tf}): {e!r}")
                return {"tf": tf, "available": False}

        m15_evi, h1_evi = _tf(m15, "M15"), _tf(h1, "H1")
        if not (m15_evi.get("available") or h1_evi.get("available")):
            return {}
        return {"m15": m15_evi, "h1": h1_evi}

    @staticmethod
    def _state_payload(snapshot: dict, ctx, evidence: dict | None = None) -> dict:
        order = snapshot.get("order", {}) or {}
        account = ctx.account or {}
        action = "buy" if str(order.get("type", "")).upper().startswith("BUY") else "sell"
        return {
            "source_type": "manual",
            "symbol": ctx.symbol,
            "action": action,
            "market_type": "cfd",
            "order_type": str(order.get("type", "")),
            "quantity": float(ctx.lot or 0),
            "reference_price": float(ctx.entry_ref or 0),
            "notional": float(ctx.entry_ref or 0) * float(ctx.lot or 0),
            "leverage": float(account.get("leverage") or 1.0),
            "strategy_type": "manual",
            "signal_reason": "manual order request",
            "context": {
                "sl": float(ctx.sl or 0),
                "tp": float(ctx.tp or 0),
                "spread": float(ctx.spread or 0),
                "account": {k: account.get(k) for k in ("balance", "equity", "profit", "margin")},
                "positions": [
                    {"symbol": p.get("symbol"), "type": p.get("type"), "lot": p.get("lot"),
                     "profit": p.get("profit")}
                    for p in (ctx.positions or [])
                ][:20],
                "recent_trades": (snapshot.get("recent_trades") or [])[:10],
                "rule_flags": (snapshot.get("rule_flags") or []),
                "market": snapshot.get("market", {}),
                "market_evidence": evidence or {},
            },
        }

    # ─── 严格校验（镜像 QuantDinger _validate_choice_answer）──────────────

    def _validate(self, payload: dict) -> dict[str, tuple[str, dict[str, float], float]]:
        answers = payload.get("answers") or payload.get("result") or payload.get("data") or {}
        results: dict[str, tuple[str, dict[str, float], float]] = {}
        for name, options in JEV_CHECK_OPTIONS.items():
            answer = self._answer(answers, name)
            choice, probabilities = self._validate_choice(answer, name, options)
            confidence = self._confidence(answer, probabilities, choice)
            results[name] = (choice, probabilities, confidence)
        return results

    @staticmethod
    def _answer(answers, key: str) -> dict:
        if isinstance(answers, dict):
            value = answers.get(key)
            if isinstance(value, dict):
                return value
            nested = answers.get("answers")
            if isinstance(nested, dict) and isinstance(nested.get(key), dict):
                return nested[key]
        return {}

    @staticmethod
    def _probabilities(answer: dict) -> dict:
        value = answer.get("probabilities") or answer.get("probability") or {}
        return dict(value) if isinstance(value, dict) else {}

    @classmethod
    def _validate_choice(cls, answer: dict, question: str, options: set) -> tuple[str, dict[str, float]]:
        choice = str(answer.get("choice") or answer.get("selected") or "").strip().lower()
        if choice not in options:
            raise ValueError(f"invalid choice for {question}")
        probs = cls._probabilities(answer)
        if set(probs) != options:
            raise ValueError(f"incomplete probabilities for {question}")
        parsed: dict[str, float] = {}
        for option, value in probs.items():
            try:
                p = float(value)
            except (TypeError, ValueError) as e:
                raise ValueError(f"invalid probability for {question}") from e
            if p < 0 or p > 1:
                raise ValueError(f"probability out of range for {question}")
            parsed[str(option)] = p
        if abs(sum(parsed.values()) - 1.0) > 0.001:
            raise ValueError(f"probabilities do not sum to one for {question}")
        if parsed.get(choice) != max(parsed.values()):
            raise ValueError(f"choice not the highest probability for {question}")
        return choice, parsed

    @classmethod
    def _confidence(cls, answer: dict, probabilities: dict, choice: str) -> float:
        raw = answer.get("confidence")
        if raw is not None:
            try:
                return max(0.0, min(float(raw), 1.0))
            except (TypeError, ValueError) as e:
                raise ValueError(f"invalid confidence for {choice}") from e
        return probabilities.get(choice, 0.0)

    # ─── 收敛（QuantDinger 规则 + 三元裁决映射）────────────────────────────

    def _converge(self, results) -> tuple[str, float, str, list[dict], dict]:
        min_conf = settings.manual_review_min_confidence
        floor = settings.manual_review_typesafe_conf_floor
        risk_choice, _, risk_conf = results["risk_check"]
        exec_choice, _, exec_conf = results["execution_quality"]
        if risk_conf is None or exec_conf is None:
            raise ValueError("risk/execution confidence missing")
        confidence = round(min(risk_conf, exec_conf), 3)
        # 低于地板 = 应答整体不可靠（近似噪声）→ 降级链；地板~min 之间 =
        # AI 倾向通过但把握不足 → CAUTION 人工确认（free 模型常在此区间）
        if confidence < floor:
            raise ValueError(
                f"confidence below floor (risk={risk_conf}, exec={exec_conf}, floor={floor})")
        signal_choice, _, signal_conf = results["signal_alignment"]
        regime_choice, _, regime_conf = results["market_regime"]
        directional_block = (
            signal_choice == "conflict" and regime_choice == "adverse"
            and float(signal_conf or 0) >= min_conf and float(regime_conf or 0) >= min_conf
        )
        allowed = risk_choice != "block" and exec_choice != "block" and not directional_block

        if not allowed:
            if risk_choice == "block":
                verdict, reason = "REJECTED", "jev_entry_rejected:risk_block"
            elif exec_choice == "block":
                verdict, reason = "REJECTED", "jev_entry_rejected:execution_block"
            else:
                verdict, reason = "REJECTED", "jev_entry_rejected:signal_conflict"
        elif risk_choice == "caution" or exec_choice == "caution":
            verdict, reason = "CAUTION", "jev_entry_approved_with_caution"
        elif confidence < min_conf:
            verdict, reason = "CAUTION", "jev_entry_low_confidence"
        else:
            verdict, reason = "APPROVED", "jev_entry_approved"

        checks = [
            {"name": name, "choice": choice, "confidence": conf,
             "evidence": f"JEV 类型化回答（置信 {conf:.2f}）"}
            for name, (choice, _, conf) in results.items()
        ]
        converge = {c["name"]: c["choice"] for c in checks}
        converge["verdict"] = verdict
        converge["upgrade"] = ""
        cn_reason = _REASON_CN.get(reason, reason)
        checks_cn = "；".join(
            f"{_CHECK_CN.get(c['name'], c['name'])}={_CHOICE_CN.get(c['choice'], c['choice'])}"
            f"（置信 {c['confidence']:.2f}）" for c in checks)
        return verdict, confidence, f"{cn_reason}｜检查：{checks_cn}", checks, converge

    @staticmethod
    def _safe(e: Exception) -> str:
        return " ".join(str(e or e.__class__.__name__).split())[:300]
