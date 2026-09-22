"""engine 开仓侧 laya 影子观测（Phase 4，只观测不改行为）。

与 Phase 3.2 的 ManualGate 影子不同（phase4-observation.md）：
- engine 路径没有 LLM 判定参照（架构 C1），观测对象是「laya vs TradeGate+确定性链」
  的分歧率，不是一致率。
- 全程零副作用：观测器不返回能否交易、不拦截、不推送；laya 故障/超时只留痕
  UNAVAILABLE。laya 异常绝不影响 `_check_trade_permission` 的结果（H-3 影子非干扰性）。
- `laya_gate_engine_shadow` 默认 True（用户 2026-09-22 批准打开，观测-only）；
  本阶段不存在 enforce（不建 gate 形态）。

分歧定义（收紧/放松口径，见 phase4-observation.md）：
- tighten（收紧分歧，重点）：现有链路放行，但 laya 判 REJECTED/ESCALATE →
  潜在「laya 会拦住但现有规则放行」案例，人工复核。
- loosen（放松分歧）：现有链路不放行，但 laya 判 APPROVED →
  潜在「laya 会放行但现有规则拦住」案例，人工复核。
- none：现有链路与 laya 方向一致（同放行或同不放行）。
- 其余（chain 弃权 / laya UNAVAILABLE / laya CAUTION / laya ESCALATE 且链不放行）
  各自归位，不参与收紧/放松计数。

存储：LayaEngineObservation 专表（见 db/models.py + alembic 迁移），
与 ManualShadowReview 并列，互不干扰。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional, Sequence

from loguru import logger

from app.config import settings

# 观测上下文等待上限：positions/daily_pnl 与 TradeGate 判定都在 inner 函数开头附近
# 产生，正常远低于该值；超限视为缺失（不阻塞、不等待关键路径）。
_CONTEXT_WAIT_S = 5.0
# 等待最终放行结果的兜底上限（finish 由 wrapper 的 finally 保证调用，这里防取消泄漏）
_ALLOWED_WAIT_S = 30.0

# 分歧类别（纯字符串常量，入库/报表共用）
DIV_NONE = "none"
DIV_TIGHTEN = "tighten"
DIV_LOOSEN = "loosen"
DIV_CHAIN_ABSTAIN = "chain_abstain"
DIV_LAYA_UNAVAILABLE = "laya_unavailable"
DIV_LAYA_ESCALATE = "laya_escalate"
DIV_CAUTION_ALLOW = "caution_on_allow"
DIV_CAUTION_DENY = "caution_on_deny"
DIV_LAYA_ABSENT = "laya_absent"  # 影子未启用（观测对象未被创建）

_LAYA_VERDICTS = ("APPROVED", "CAUTION", "REJECTED", "ESCALATE", "UNAVAILABLE")


# ─── 纯函数：市场摘要（确定性预计算，供 laya 6 问 state）────────────────────

def build_market_summary(df: Any) -> Dict[str, float | str]:
    """从 engine 已取的 OHLCV df 计算紧凑市场摘要（纯 pandas，无 I/O）。

    只做廉价确定性计算（价格/动量/波动/位置），不触发任何网络或 DB 往返。
    df 缺列或样本不足时返回空 dict——调用方按「证据不足」处理，不阻断任何路径。
    """
    if df is None or not hasattr(df, "columns") or "close" not in df.columns:
        return {}
    try:
        closes = df["close"].dropna()
        if len(closes) < 2:
            return {}
        values = closes.astype(float)
        last = float(values.iloc[-1])
        if last <= 0:
            return {}
        prev = float(values.iloc[-2])
        change_1 = (last - prev) / prev if prev > 0 else 0.0
        change_5 = 0.0
        if len(values) >= 6:
            base5 = float(values.iloc[-6])
            change_5 = (last - base5) / base5 if base5 > 0 else 0.0
        # 近 14 根收益率的波动估计
        vol_14 = 0.0
        if len(values) >= 3:
            rets = values.pct_change().dropna().tail(14)
            if len(rets) >= 2:
                vol_14 = float(rets.std())
        # 近 50 根高低区间内的位置（0-1）
        range_pos = 0.5
        window = values.tail(50)
        if len(window) >= 2:
            hi, lo = float(window.max()), float(window.min())
            if hi > lo:
                range_pos = (last - lo) / (hi - lo)
        summary: Dict[str, float | str] = {
            "last_close": round(last, 6),
            "change_1_pct": round(change_1 * 100.0, 4),
            "change_5_pct": round(change_5 * 100.0, 4),
            "vol_14": round(vol_14 * 100.0, 4),
            "range_position_50": round(range_pos, 4),
        }
        # 轻量均线比（存在性判断用简单均线，避免引入重型指标依赖）
        if len(values) >= 9:
            sma9 = float(values.tail(9).mean())
            if sma9 > 0:
                summary["price_vs_sma9"] = round(last / sma9, 4)
        if len(values) >= 21:
            sma21 = float(values.tail(21).mean())
            if sma21 > 0:
                summary["price_vs_sma21"] = round(last / sma21, 4)
        return summary
    except Exception as e:  # 纯观测：任何计算失败都降级为空摘要
        logger.debug(f"[laya-engine-obs] market summary failed: {e}")
        return {}


def _compact_position(p: Any) -> Dict[str, Any]:
    """持仓压缩为审计友好 dict。

    H1 修复：生产路径 `order_executor.get_open_positions()` 返回 `list[dict]`
    （内部即 `p.get("symbol")`），须兼容 dict；对象形态（测试/其他调用方）走 getattr。
    """
    if isinstance(p, dict):
        return {
            "symbol": p.get("symbol", ""),
            "type": p.get("type", ""),
            "volume": p.get("volume"),
            "profit": p.get("profit"),
        }
    try:
        return {
            "symbol": getattr(p, "symbol", ""),
            "type": getattr(p, "type", ""),
            "volume": getattr(p, "volume", None),
            "profit": getattr(p, "profit", None),
        }
    except Exception:
        return {}


def build_laya_engine_snapshot(
    *,
    symbol: str,
    timeframe: str,
    signal: int,
    signal_label: str,
    balance: float,
    df: Any,
    positions: Optional[Sequence[Any]],
    daily_pnl: Optional[float],
    recent_wr: Optional[float],
) -> Dict[str, Any]:
    """组装 engine 侧 laya 6 问推理 state（与 ManualGate 快照同构）。

    复用 laya_gate_review 的 6 问提示词；市场部分用确定性预计算摘要。
    positions 只取前 5 且压缩字段；recent_trades 用 recent_wr 摘要代替全量列表
    （engine 路径观测不触发额外 DB 查询，评审「观测零额外 I/O 成本」约束）。
    """
    market = build_market_summary(df)
    market.update({"symbol": symbol, "timeframe": timeframe})
    account: Dict[str, Any] = {
        "balance": float(balance or 0.0),
        "positions_count": len(list(positions or [])),
        "daily_pnl": float(daily_pnl) if daily_pnl is not None else None,
    }
    if recent_wr is not None:
        account["recent_win_rate"] = float(recent_wr)
    return {
        "order": {
            "signal": int(signal) if signal is not None else None,
            "signal_label": signal_label,
            "symbol": symbol,
            "timeframe": timeframe,
            "side": "BUY" if signal and signal > 0 else ("SELL" if signal and signal < 0 else "FLAT"),
        },
        "account": account,
        "positions": [_compact_position(p) for p in (positions or [])[:5]],
        "recent_trades": [],
        "rule_flags": [],
        "market": market,
    }


# ─── 纯函数：分歧分类 ─────────────────────────────────────────────────────

def classify_divergence(
    chain_can_trade: Optional[bool],
    laya_verdict: Optional[str],
    final_allowed: Optional[bool] = None,
) -> Dict[str, str]:
    """laya vs 现有链路的分歧分类（纯函数）。

    chain_can_trade：TradeGate 判定（None=弃权/未评估）。
    laya_verdict：laya 收敛器 verdict（None=观测未启用/未跑到）。
    final_allowed：`_check_trade_permission` 最终结果（含确定性风控链）。
    返回 {"gate": ..., "final": ...} 两个口径；final 以 final_allowed 为准，
    缺失时退化为 gate 口径。
    """
    gate_kind = _classify(chain_can_trade, laya_verdict)
    # L1：无最终判定（allowed=None，如 inner 异常）→ final 口径不记录，防误计分歧
    if final_allowed is None:
        return {"gate": gate_kind, "final": None}
    return {"gate": gate_kind, "final": _classify(final_allowed, laya_verdict)}


def _classify(chain: Optional[bool], laya: Optional[str]) -> str:
    if laya is None:
        return DIV_LAYA_ABSENT
    if chain is None:
        return DIV_CHAIN_ABSTAIN
    if laya == "UNAVAILABLE":
        return DIV_LAYA_UNAVAILABLE
    if laya == "ESCALATE":
        return DIV_TIGHTEN if chain else DIV_LAYA_ESCALATE
    if laya == "REJECTED":
        return DIV_TIGHTEN if chain else DIV_NONE
    if laya == "APPROVED":
        return DIV_NONE if chain else DIV_LOOSEN
    if laya == "CAUTION":
        return DIV_CAUTION_ALLOW if chain else DIV_CAUTION_DENY
    # M5：未知 verdict（畸形/未来新增值）→ 视为不可用，而非「影子未启用」
    return DIV_LAYA_UNAVAILABLE


# ─── 观测器（只记录，零副作用）────────────────────────────────────────────

class EngineLayaObservation:
    """engine 开仓侧 laya 影子观测器。

    生命周期（wrapper 驱动）：
      1. wrapper 创建后立即后台启动 `_run`（与 inner 的确定性检查并行）；
      2. inner 顶部 gather 出 positions/daily_pnl 后 `set_account(...)`；
      3. TradeGate predict 后 `set_chain(...)`；
      4. wrapper finally 调 `finish(allowed)`（同步，只 set event）；
      5. `_run` 收集齐上下文后跑 laya 6 问，等 final 结果，best-effort 落库。
    任何一步失败都不抛给调用方（影子非干扰性，验证 H-3）。
    """

    def __init__(
        self,
        *,
        symbol: str,
        timeframe: str,
        signal: int,
        signal_label: str,
        balance: float,
        df: Any,
        recent_wr: Optional[float],
    ) -> None:
        self._symbol = symbol
        self._timeframe = timeframe
        self._signal = signal
        self._signal_label = signal_label
        self._balance = balance
        self._df = df
        self._recent_wr = recent_wr
        self._positions: Optional[Sequence[Any]] = None
        self._daily_pnl: Optional[float] = None
        self._chain_can_trade: Optional[bool] = None
        self._chain_prob: Optional[float] = None
        self._allowed: Optional[bool] = None
        self._account_evt = asyncio.Event()
        self._chain_evt = asyncio.Event()
        self._allowed_evt = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    # ── 同步注入（inner 内一行调用，绝不 await）──
    def set_account(self, positions: Sequence[Any], daily_pnl: Optional[float]) -> None:
        if self._account_evt.is_set():
            return
        self._positions = positions
        self._daily_pnl = daily_pnl
        self._account_evt.set()

    def set_chain(self, can_trade: Optional[bool], prob: Optional[float]) -> None:
        if self._chain_evt.is_set():
            return
        self._chain_can_trade = can_trade
        self._chain_prob = prob
        self._chain_evt.set()

    def finish(self, allowed: Optional[bool]) -> None:
        if self._allowed_evt.is_set():
            return
        self._allowed = allowed
        self._allowed_evt.set()

    # ── 后台任务 ──
    async def _run(self) -> None:
        try:
            # 1) 等 account/chain 上下文（inner 开头就产生，正常远低于超时）
            await self._wait(self._account_evt, _CONTEXT_WAIT_S)
            await self._wait(self._chain_evt, _CONTEXT_WAIT_S)

            # 2) 与确定性检查并行跑 laya（观测不阻塞任何路径）
            snapshot = build_laya_engine_snapshot(
                symbol=self._symbol,
                timeframe=self._timeframe,
                signal=self._signal,
                signal_label=self._signal_label,
                balance=self._balance,
                df=self._df,
                positions=self._positions,
                daily_pnl=self._daily_pnl,
                recent_wr=self._recent_wr,
            )
            laya_review, latency_ms = await self._run_laya_review(snapshot)

            # 3) 等 wrapper finally 的最终放行结果，然后 best-effort 落库
            await self._wait(self._allowed_evt, _ALLOWED_WAIT_S)
            divergence = classify_divergence(
                self._chain_can_trade,
                laya_review.get("decision") if laya_review else None,
                self._allowed,
            )
            await self._persist(snapshot, laya_review, latency_ms, divergence)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - 观测零副作用
            logger.warning(f"[laya-engine-obs] observation failed (ignored): {e}")

    @staticmethod
    async def _wait(evt: asyncio.Event, timeout: float) -> None:
        try:
            await asyncio.wait_for(evt.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass  # 上下文缺失不阻塞、不报错

    async def _run_laya_review(
        self, snapshot: Dict[str, Any]
    ) -> tuple[Optional[Dict[str, Any]], int]:
        if not settings.laya_gate_engine_shadow:
            return None, 0
        started = time.perf_counter()
        try:
            from app.ai.laya_gate import laya_gate_review

            review = await asyncio.wait_for(
                laya_gate_review(snapshot, timeout=settings.laya_gate_predict_timeout_s),
                timeout=settings.laya_gate_predict_timeout_s + 5.0,
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            if review is None:
                return {"engine": "laya", "decision": "UNAVAILABLE", "latency_ms": latency_ms}, latency_ms
            review["latency_ms"] = latency_ms
            return review, latency_ms
        except asyncio.TimeoutError:
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.warning(f"[laya-engine-obs] laya review timed out ({settings.laya_gate_predict_timeout_s}s)")
            return {"engine": "laya", "decision": "UNAVAILABLE", "error": "timeout", "latency_ms": latency_ms}, latency_ms
        except Exception as e:  # noqa: BLE001
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.warning(f"[laya-engine-obs] laya review failed: {e}")
            return {"engine": "laya", "decision": "UNAVAILABLE", "error": str(e)[:200], "latency_ms": latency_ms}, latency_ms

    async def _persist(
        self,
        snapshot: Dict[str, Any],
        laya_review: Optional[Dict[str, Any]],
        latency_ms: int,
        divergence: Dict[str, str],
    ) -> None:
        """best-effort 落 LayaEngineObservation 专表；任何异常仅记日志。"""
        try:
            from app.db.models import LayaEngineObservation
            from app.db.session import async_session

            laya_verdict = (laya_review or {}).get("decision")
            async with async_session() as session:
                session.add(LayaEngineObservation(
                    symbol=self._symbol,
                    timeframe=self._timeframe,
                    signal_label=self._signal_label,
                    signal=self._signal,
                    balance=float(self._balance or 0.0),
                    chain_can_trade=self._chain_can_trade,
                    chain_prob=self._chain_prob,
                    allowed=self._allowed,
                    laya_verdict=laya_verdict,
                    laya_confidence=(laya_review or {}).get("confidence"),
                    laya_reasons=(laya_review or {}).get("reasons"),
                    laya_checks=(laya_review or {}).get("checks"),
                    laya_answers=(laya_review or {}).get("answers"),
                    divergence_gate=divergence.get("gate"),
                    divergence_final=divergence.get("final"),
                    laya_latency_ms=int(latency_ms),
                    state_snapshot=snapshot,
                ))
                await session.commit()
                logger.debug(f"[laya-engine-obs] persisted: {self._signal_label} laya={laya_verdict}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[laya-engine-obs] persist failed (ignored): {e}")


def start_engine_observation(
    *,
    symbol: str,
    timeframe: str,
    signal: int,
    signal_label: str,
    balance: float,
    df: Any,
    recent_wr: Optional[float] = None,
) -> Optional[EngineLayaObservation]:
    """engine wrapper 入口：未启用观测时返回 None（零开销）。

    仅由 `laya_gate_engine_shadow` 控制；laya 依赖/模型不可用时观测器会
    落 UNAVAILABLE 行（兜底率可观测），绝不影响交易路径。
    """
    # M8：laya 完全未启用时不建观测任务、不落 UNAVAILABLE 噪音行
    # （laya_enabled 与 shadow 都开才观测；故障留痕仍由 _run_laya_review 处理）
    if not (settings.laya_gate_engine_shadow and settings.laya_enabled):
        return None
    return EngineLayaObservation(
        symbol=symbol,
        timeframe=timeframe,
        signal=signal,
        signal_label=signal_label,
        balance=balance,
        df=df,
        recent_wr=recent_wr,
    )
