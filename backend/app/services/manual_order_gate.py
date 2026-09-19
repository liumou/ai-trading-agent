"""ManualOrderGate — the risk firewall for user-initiated manual orders.

Firewall invariants (v2 plan, tri-critic reviewed):
1. Hard gates (preflight) run first; their verdict can only be tightened,
   never loosened, by the LLM review layer.
2. REJECTED is final — no override path exists.
3. LLM unavailable / timeout / malformed verdict → fail-closed (order NOT
   executed; user may re-request review).
4. Rollout gates identical to the AI channel: shadow/paper never touch the
   real account, micro caps lot, live requires llm_allow_live.
5. Switching gate is FAIL-CLOSED here (manual = real money; unlike the MCP
   broker's best-effort check).
6. Confirm (CAUTION) is bound to the persisted review row — parameters can
   never be swapped between review and execution; confirm re-runs the hard
   gates (state may have drifted) and expires after CONFIRM_TTL_S.

Flow: submit → per-account lock {switching gate → preflight → emotion rules
(block-level rules reject inline)} → OrderAudit(PENDING_REVIEW) → async LLM
review → APPROVED: re-verify + execute / CAUTION: PENDING_CONFIRM /
REJECTED: blocked (+ AI_AGENT_ERROR when it's an infrastructure failure).
"""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import select

from app.ai.prompts import ORDER_REVIEW_SYSTEM_PROMPT, build_order_review_user_prompt
from app.constants import (
    MANUAL_MAGIC_NUMBER,
    MANUAL_SL_MAX_ENTRY_DIST_MULT,
    MANUAL_SL_WIDEN_DAILY_LIMIT,
)
from app.db.models import BotEvent, BotEventType, OrderAudit
from app.services.order_preflight import PreflightContext, _sanitize_comment, preflight_order

CONFIRM_TTL_S = 120
LLM_REVIEW_TIMEOUT_S = 25
REVENGE_WINDOW_MIN = 15
MARTINGALE_LOT_MULT = 2.0

_VERDICTS = {"APPROVED", "CAUTION", "REJECTED"}


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ManualOrderGate:
    def __init__(self, connector, redis, ai_client):
        self.connector = connector
        self.redis = redis
        self.ai_client = ai_client
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()

    def _lock_for(self, account_login: str) -> asyncio.Lock:
        # guardrails 的检查是 check-then-act 非原子（评审 H-3）：per-account
        # 锁串行化「硬闸门 → 审查落库」与「执行」两个临界区。单进程够用；
        # 多 worker 部署需升级 Redis 原子计数。
        if account_login not in self._locks:
            self._locks[account_login] = asyncio.Lock()
        return self._locks[account_login]

    # ─── switching gate（fail-closed）─────────────────────────────────────

    async def _switching_blocked(self) -> str | None:
        try:
            if await self.redis.get("switching:in_progress"):
                return "Account switch in progress — retry after switch completes"
        except Exception as e:  # noqa: BLE001
            logger.error(f"ManualGate switching gate unavailable: {e!r}")
            return "Risk gate temporarily unavailable (redis error) — order blocked"
        return None

    # ─── submit（下单 / 新挂单 / 改挂单，统一入口）────────────────────────

    async def submit_order(
        self,
        *,
        symbol: str,
        order_kind: str,  # market | pending
        order_type: str,
        lot: float,
        sl: float = 0.0,
        tp: float = 0.0,
        price: float | None = None,
        comment: str = "",
        account_login: str = "0",
        modify_ticket: int | None = None,
    ) -> dict:
        """Hard gates + emotion rules synchronously; LLM review async.

        Returns {"status": "REJECTED", reason, ...} for denials (HTTP 200) or
        {"status": "PENDING_REVIEW", "review_id", ...} (HTTP 202).
        """
        # 改挂单 = 全流水线重审（评审 C-2：远价挂单过审后改价逼近市价 = 
        # 事实上市价单绕过审查）。审查针对变更后的订单参数。
        if order_kind == "pending" and modify_ticket is not None:
            owned = await self._verify_order_ticket(modify_ticket)
            if not owned:
                return {"status": "REJECTED", "reason": f"Pending order {modify_ticket} not found on current account"}

        direction = "BUY" if order_type.startswith("BUY") else "SELL"
        audit_id = await self._create_audit(
            symbol=symbol, order_type=direction, lot=lot, sl=sl, tp=tp,
            order_kind=order_kind, order_price=price, account_login=account_login,
            expected_price=price or 0.0,
            review={"modify_ticket": modify_ticket, "raw_order_type": order_type, "comment": comment[:60]},
        )

        async with self._lock_for(account_login):
            blocked = await self._switching_blocked()
            if blocked:
                return await self._reject_inline(audit_id, blocked, kind="switching")

            pf = await preflight_order(
                self.connector, self.redis, None,
                symbol=symbol, order_type=order_type, lot=lot, sl=sl, tp=tp,
                strict_symbol=True,
                direction=direction,
                entry_price=price if order_kind == "pending" else None,
                account_login=account_login,
            )
            if not pf.ok:
                return await self._reject_inline(audit_id, pf.reason, kind=pf.kind)

            # ─── 情绪化交易规则（block 级直接拒，不烧 LLM token）──────────
            snapshot = await self._build_snapshot(pf.ctx, audit_id)
            block_flag = next((f for f in snapshot["rule_flags"] if f.get("severity") == "block"), None)
            if block_flag:
                return await self._reject_inline(
                    audit_id, f"Emotional-trading rule triggered: {block_flag['flag']}", kind="emotion",
                    rule_flags=snapshot["rule_flags"],
                )

        # 硬闸门通过 → 异步 LLM 审查（锁外；执行前重验硬状态）
        await self._update_audit(audit_id, review={
            "raw_order_type": order_type, "modify_ticket": modify_ticket,
            "comment": comment[:60], "rule_flags": snapshot["rule_flags"],
        })
        task = asyncio.create_task(self._review_and_maybe_execute(audit_id, snapshot))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

        return {
            "status": "PENDING_REVIEW",
            "review_id": audit_id,
            "rule_flags": snapshot["rule_flags"],
        }

    # ─── LLM 审查 + 执行 ──────────────────────────────────────────────────

    async def _review_and_maybe_execute(self, audit_id: int, snapshot: dict):
        try:
            await self._review(audit_id, snapshot)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"ManualGate review task [{audit_id}] crashed")
            await self._reject_inline(
                audit_id, f"AI review failed: {e!s} — order blocked (fail-closed)",
                kind="llm_unavailable", retryable=True,
            )
            await self._ai_error_event(audit_id, f"LLM review crashed: {e!s:.200}")

    async def _review(self, audit_id: int, snapshot: dict):
        # 超时只约束 LLM 调用本身 —— 不能包住执行段，否则「订单已成交但
        # 审计行被取消」会造成不可回滚的孤行。
        try:
            raw = await asyncio.wait_for(
                self.ai_client.complete_json_async(
                    ORDER_REVIEW_SYSTEM_PROMPT, build_order_review_user_prompt(snapshot),
                    max_tokens=300, agent_id="manual_order_review",
                ),
                timeout=LLM_REVIEW_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            await self._reject_inline(
                audit_id, f"AI review timed out after {LLM_REVIEW_TIMEOUT_S}s — order blocked (fail-closed)",
                kind="llm_unavailable", retryable=True,
            )
            await self._ai_error_event(audit_id, "LLM review timeout")
            return
        verdict_data = self._normalize_verdict(raw)
        if verdict_data is None:
            await self._reject_inline(
                audit_id, "AI review unavailable (LLM failure or malformed output) — order blocked (fail-closed)",
                kind="llm_unavailable", retryable=True,
            )
            await self._ai_error_event(audit_id, "LLM verdict missing/malformed")
            return

        verdict = verdict_data["verdict"]
        audit = await self._load_audit(audit_id)
        if audit is None:
            return
        stored = dict(audit.review or {})
        stored["llm"] = verdict_data
        await self._update_audit(audit_id, review=stored)

        if verdict == "REJECTED":
            reason = verdict_data.get("reasoning") or "AI review rejected the order"
            await self._reject_inline(audit_id, reason, kind="ai_rejected",
                                      rule_flags=stored.get("rule_flags"), llm=verdict_data)
            return
        if verdict == "CAUTION":
            expires = _utcnow() + timedelta(seconds=CONFIRM_TTL_S)
            stored["confirm_expires_at"] = expires.isoformat()
            await self._update_audit(audit_id, status="PENDING_CONFIRM", review=stored)
            await self._push({
                "type": "manual_review", "review_id": audit_id, "status": "PENDING_CONFIRM",
                "verdict": "CAUTION", "reasoning": verdict_data.get("reasoning", ""),
            })
            return

        # APPROVED → 执行前重验硬状态（评审 H-1：审查期间状态可能漂移）
        await self._execute_approved(audit_id, stored)

    def _normalize_verdict(self, raw: dict | None) -> dict | None:
        """verdict 白名单校验：缺失/大小写/同义词一律视为畸形（fail-closed）。"""
        if not isinstance(raw, dict):
            return None
        verdict = str(raw.get("verdict", "")).strip().upper()
        if verdict not in _VERDICTS:
            return None
        try:
            confidence = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        return {
            "verdict": verdict,
            "confidence": max(0.0, min(confidence, 1.0)),
            "risk_flags": [str(f)[:60] for f in (raw.get("risk_flags") or [])][:10],
            "emotional_indicators": [str(f)[:120] for f in (raw.get("emotional_indicators") or [])][:10],
            "reasoning": str(raw.get("reasoning", ""))[:500],
        }

    # ─── confirm（CAUTION 二次确认；review_id 绑定参数）───────────────────

    async def confirm_and_execute(self, review_id: int, account_login: str = "0") -> dict:
        audit = await self._load_audit(review_id)
        if audit is None:
            return {"status": "REJECTED", "reason": "Review not found"}
        if audit.status != "PENDING_CONFIRM":
            return {"status": audit.status, "reason": f"Review is not awaiting confirmation (status={audit.status})"}
        review = dict(audit.review or {})
        expires_at = review.get("confirm_expires_at")
        if expires_at:
            try:
                if _utcnow() > datetime.fromisoformat(expires_at):
                    await self._update_audit(review_id, status="EXPIRED")
                    return {"status": "EXPIRED", "reason": "Confirmation window (120s) elapsed — resubmit the order"}
            except ValueError:
                pass

        # 参数以持久化行为准（杜绝「确认 A 单执行 B 单」）；重跑硬闸门。
        audit_account = str(audit.account_login or account_login or "0")
        async with self._lock_for(account_login):
            blocked = await self._switching_blocked()
            if blocked:
                return await self._reject_inline(review_id, blocked, kind="switching")
            pf = await preflight_order(
                self.connector, self.redis, None,
                symbol=audit.symbol, order_type=review.get("raw_order_type") or audit.order_type,
                lot=audit.requested_lot, sl=audit.requested_sl, tp=audit.requested_tp,
                strict_symbol=True,
                entry_price=audit.order_price if audit.order_kind == "pending" else None,
                account_login=audit_account,
            )
            if not pf.ok:
                return await self._reject_inline(review_id, f"State changed since review: {pf.reason}", kind=pf.kind)
            snapshot = await self._build_snapshot(pf.ctx, review_id, rule_flags=review.get("rule_flags"))
            await self._execute_approved(review_id, review, ctx=pf.ctx)

        return {"status": "EXECUTED", "review_id": review_id}

    async def _execute_approved(self, audit_id: int, review: dict, ctx: PreflightContext | None = None):
        audit = await self._load_audit(audit_id)
        if audit is None:
            return
        if ctx is None:
            # _review 路径：执行前重验硬状态（审查期间可能漂移）
            pf = await preflight_order(
                self.connector, self.redis, None,
                symbol=audit.symbol, order_type=review.get("raw_order_type") or audit.order_type,
                lot=audit.requested_lot, sl=audit.requested_sl, tp=audit.requested_tp,
                strict_symbol=True,
                entry_price=audit.order_price if audit.order_kind == "pending" else None,
                account_login=str(audit.account_login or "0"),
            )
            if not pf.ok:
                await self._reject_inline(audit_id, f"State changed since review: {pf.reason}", kind=pf.kind)
                return
            ctx = pf.ctx

        blocked = await self._switching_blocked()
        if blocked:
            await self._reject_inline(audit_id, blocked, kind="switching")
            return

        # ROLLOUT 门禁（评审 C-2）：手动通道与 AI 通道同语义 —— shadow/paper
        # 绝不让真实单碰到真实账号。preflight 本身不拒（AI 通道需要拦截
        # 消息而非拒单），此处是执行前最后一道闸。
        if ctx.rollout_mode in ("shadow", "paper"):
            await self._reject_inline(
                audit_id,
                f"Rollout mode '{ctx.rollout_mode}' blocks manual real orders — enable live or paper first",
                kind="rollout",
            )
            return

        raw_type = review.get("raw_order_type") or audit.order_type
        modify_ticket = review.get("modify_ticket")
        comment = _sanitize_comment(str(review.get("comment", "")), prefix="M")
        start = time.monotonic()
        if modify_ticket:
            # 改挂单：price/sl/tp 以审查通过的持久化行为准。
            # 执行前重验 ticket 归属（评审 I-4）：提交时验证过，但审查期间
            # 该单可能被撤/被改账号 —— 不重验会在他人撤销后改错对象。
            if not await self._verify_order_ticket(int(modify_ticket)):
                await self._reject_inline(
                    audit_id,
                    f"Pending order {modify_ticket} no longer exists on current account — resubmit",
                    kind="ticket_ownership",
                )
                return
            result = await self.connector.modify_order(
                int(modify_ticket),
                price=audit.order_price,
                sl=audit.requested_sl,
                tp=audit.requested_tp,
            )
        elif raw_type in ("BUY", "SELL"):
            result = await self.connector.place_order(
                symbol=ctx.broker_symbol, order_type=raw_type, lot=ctx.lot,
                sl=ctx.sl, tp=ctx.tp, comment=comment, magic=MANUAL_MAGIC_NUMBER,
            )
        else:
            # 新挂单（BUY_LIMIT/SELL_LIMIT/BUY_STOP/SELL_STOP）
            result = await self.connector.place_pending_order(
                symbol=ctx.symbol, order_type=raw_type, lot=ctx.lot,
                price=float(audit.order_price or 0), sl=ctx.sl, tp=ctx.tp,
                comment=comment, magic=MANUAL_MAGIC_NUMBER,
            )
        latency_ms = int((time.monotonic() - start) * 1000)

        if not result.get("success"):
            error = result.get("error", "Order execution failed")
            await self._update_audit(audit_id, status="FAILED", error_message=error, latency_ms=latency_ms)
            await self._log_event(BotEventType.ORDER_FAILED, f"[Manual] order failed [{ctx.symbol}]: {error}")
            await self._push({"type": "manual_review", "review_id": audit_id, "status": "FAILED", "reason": error})
            return

        data = result.get("data") or {}
        ticket = data.get("ticket")
        fill_price = data.get("price")
        await self._update_audit(audit_id, status="EXECUTED", ticket=ticket, fill_price=fill_price,
                                 latency_ms=latency_ms)
        # 频率/间隔计数（评审 M-2：不调用则手动通道对频率限制免疫）
        try:
            await ctx.guardrails.record_order_opened()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ManualGate record_order_opened failed: {e!r}")
        if modify_ticket:
            await self._log_event(BotEventType.SETTINGS_CHANGED,
                                  f"[Manual] pending order #{modify_ticket} modified → price={fill_price or ctx.sl}")
            await self._push({"type": "manual_review", "review_id": audit_id, "status": "EXECUTED",
                              "action": "modify", "ticket": modify_ticket})
        else:
            await self._log_event(
                BotEventType.TRADE_OPENED,
                f"[Manual] {review.get('raw_order_type') or ctx.order_type} {ctx.lot} {ctx.symbol} "
                f"@ {fill_price or ctx.entry_ref:.2f} SL={ctx.sl} TP={ctx.tp}",
            )
            await self._push({"type": "manual_review", "review_id": audit_id, "status": "EXECUTED",
                              "ticket": ticket, "price": fill_price, "symbol": ctx.symbol, "lot": ctx.lot})

    # ─── 撤单 / 改持仓 SL/TP ─────────────────────────────────────────────

    async def cancel_pending_order(self, ticket: int) -> dict:
        """撤单：风险只减不增 —— 仅 switching 门禁 + ticket 归属校验。"""
        blocked = await self._switching_blocked()
        if blocked:
            return {"cancelled": False, "rejected": True, "reason": blocked}
        if not await self._verify_order_ticket(ticket):
            return {"cancelled": False, "error": f"Pending order {ticket} not found on current account"}
        result = await self.connector.cancel_order(ticket)
        if not result.get("success"):
            return {"cancelled": False, "error": result.get("error", "Cancel failed")}
        await self._log_event(BotEventType.SETTINGS_CHANGED, f"[Manual] pending order #{ticket} cancelled")
        await self._push({"type": "manual_order", "action": "cancel", "ticket": ticket})
        return {"cancelled": True, "ticket": ticket}

    async def modify_position_sltp(self, ticket: int, sl: float | None, tp: float | None,
                                   account_login: str = "0") -> dict:
        """改持仓 SL/TP：以 entry 为固定基数的漂移预算（评审 C-4）。

        现 broker.modify_position 以「当前 SL」为基数（×2 逐轮 = 几何漂移），
        且 SL=0 时整个检查被跳过、TP 无校验。此处：
        - SL=0 → 设任何合法 SL 都放行（风险只减不增），并把该距离记为锚点；
        - 拉宽：new_dist ≤ MANUAL_SL_MAX_ENTRY_DIST_MULT × 锚点距离（锚点
          首次修改时写入 Redis，TTL 7 天 —— 固定基数，杜绝几何漂移）；
        - 每张仓每日拉宽次数 ≤ MANUAL_SL_WIDEN_DAILY_LIMIT；
        - 收紧快速放行；SL/TP 方向合法性必校验。
        """
        blocked = await self._switching_blocked()
        if blocked:
            return {"modified": False, "rejected": True, "reason": blocked}

        positions_res = await self.connector.get_positions()
        if not positions_res.get("success"):
            return {"modified": False, "error": positions_res.get("error", "Failed to fetch positions")}
        pos = next((p for p in positions_res.get("data", []) if p.get("ticket") == ticket), None)
        if pos is None:
            return {"modified": False, "error": f"Position {ticket} not found on current account"}

        ptype = str(pos.get("type", "")).upper()
        is_buy = ptype.startswith("BUY")
        entry = float(pos.get("open_price") or 0)
        current_sl = float(pos.get("sl") or 0)
        current_tp = float(pos.get("tp") or 0)
        new_sl = current_sl if sl is None else float(sl)
        new_tp = current_tp if tp is None else float(tp)

        # 方向合法性（SL/TP 必须在持仓的正确一侧）
        if is_buy:
            if new_sl and new_sl >= entry:
                return {"modified": False, "error": f"SL {new_sl} must be below entry {entry} for BUY"}
            if new_tp and new_tp <= entry:
                return {"modified": False, "error": f"TP {new_tp} must be above entry {entry} for BUY"}
        else:
            if new_sl and new_sl <= entry:
                return {"modified": False, "error": f"SL {new_sl} must be above entry {entry} for SELL"}
            if new_tp and new_tp >= entry:
                return {"modified": False, "error": f"TP {new_tp} must be below entry {entry} for SELL"}

        # 删除已有止损 = 无限拉宽（评审 C-3）：风险只减不增，已有 SL 时
        # new_sl=0 必须硬拦截 —— 任何有限倍数都无法约束「无限距离」。
        if current_sl != 0 and new_sl == 0:
            return {
                "modified": False,
                "rejected": True,
                "reason": "Removing an existing stop-loss is not allowed — set a new SL instead",
            }

        widening = False
        if new_sl != current_sl and entry:
            new_dist = abs(entry - new_sl)
            anchor_key = f"manual:sl_anchor:{ticket}"
            day_key = f"manual:sl_widens:{_utcnow().date().isoformat()}:{ticket}"
            anchor_raw = await self.redis.get(anchor_key)
            if current_sl == 0:
                # 无止损仓：设 SL = 风险收紧，放行；新距离成为锚点
                await self.redis.set(anchor_key, str(new_dist), ex=7 * 86400)
            else:
                anchor = float(anchor_raw) if anchor_raw else abs(entry - current_sl)
                if anchor_raw is None:
                    await self.redis.set(anchor_key, str(anchor), ex=7 * 86400)
                if new_dist > anchor * MANUAL_SL_MAX_ENTRY_DIST_MULT:
                    return {
                        "modified": False,
                        "rejected": True,
                        "reason": (
                            f"SL distance {new_dist:.5f} exceeds {MANUAL_SL_MAX_ENTRY_DIST_MULT}x "
                            f"anchored risk distance {anchor:.5f} (drift budget)"
                        ),
                    }
                if new_dist > abs(entry - current_sl):
                    widens = int(await self.redis.get(day_key) or 0)
                    if widens >= MANUAL_SL_WIDEN_DAILY_LIMIT:
                        return {
                            "modified": False,
                            "rejected": True,
                            "reason": f"Daily SL-widen budget exhausted ({MANUAL_SL_WIDEN_DAILY_LIMIT}/day)",
                        }
                    widening = True

        result = await self.connector.modify_position(ticket, sl=new_sl, tp=new_tp)
        if not result.get("success"):
            return {"modified": False, "error": result.get("error", "Modification failed")}
        if widening:
            day_key = f"manual:sl_widens:{_utcnow().date().isoformat()}:{ticket}"
            await self.redis.incr(day_key)
            await self.redis.expire(day_key, 86400)
        await self._log_event(BotEventType.SETTINGS_CHANGED,
                              f"[Manual] position #{ticket} SL/TP → {new_sl}/{new_tp}")
        await self._push({"type": "manual_order", "action": "modify_sltp", "ticket": ticket,
                          "sl": new_sl, "tp": new_tp})
        return {"modified": True, "ticket": ticket, "sl": new_sl, "tp": new_tp}

    # ─── 查询 ─────────────────────────────────────────────────────────────

    async def get_review(self, review_id: int) -> dict | None:
        audit = await self._load_audit(review_id)
        if audit is None:
            return None
        return _audit_to_dict(audit)

    async def list_reviews(self, account_login: str | None = None, limit: int = 50) -> list[dict]:
        from app.db.session import async_session

        async with async_session() as session:
            stmt = select(OrderAudit).where(OrderAudit.source == "manual").order_by(OrderAudit.id.desc()).limit(limit)
            if account_login:
                stmt = stmt.where(OrderAudit.account_login == account_login)
            rows = (await session.execute(stmt)).scalars().all()
            return [_audit_to_dict(r) for r in rows]

    async def list_pending_orders(self) -> list[dict]:
        res = await self.connector.get_orders()
        if not res.get("success"):
            return []
        return res.get("data", [])

    # ─── 内部 ─────────────────────────────────────────────────────────────

    async def _verify_order_ticket(self, ticket: int) -> bool:
        """跨账号 ticket 可重复（H4）：改/撤前必须确认单在当前账号名下。"""
        res = await self.connector.get_orders()
        if not res.get("success"):
            return False
        return any(o.get("ticket") == ticket for o in res.get("data", []))

    async def _build_snapshot(self, ctx: PreflightContext, audit_id: int,
                              rule_flags: list | None = None) -> dict:
        deals = await self._recent_deals(ctx.symbol)
        sentiment = await self._latest_sentiment(ctx.symbol)
        if rule_flags is None:
            rule_flags = await self._emotion_flags(deals, ctx)
        return {
            "order": {
                "review_id": audit_id, "symbol": ctx.symbol, "type": ctx.order_type,
                "lot": ctx.lot, "sl": ctx.sl, "tp": ctx.tp,
            },
            "account": {
                "balance": ctx.account.get("balance"), "equity": ctx.account.get("equity"),
                "floating_profit": ctx.account.get("profit"), "realized_daily_pnl": ctx.daily_pnl,
            },
            "positions": [
                {"symbol": p.get("symbol"), "type": p.get("type"), "lot": p.get("lot"),
                 "profit": p.get("profit")}
                for p in ctx.positions
            ],
            "recent_trades": deals[:10],
            "rule_flags": rule_flags,
            "market": {
                "bid": ctx.tick.get("bid"), "ask": ctx.tick.get("ask"),
                "spread": ctx.spread, "avg_spread": ctx.avg_spread, "sentiment": sentiment,
            },
        }

    async def _recent_deals(self, symbol: str) -> list[dict]:
        """/history 天然只含当前登录账号（账号隔离自动满足）；trades 表只记
        引擎成交，对手动通道有盲区 —— 统一以成交历史为准。"""
        res = await self.connector.get_history(days=1, symbol=symbol)
        if not res.get("success"):
            return []
        deals = res.get("data", []) or []
        return sorted(deals, key=lambda d: d.get("time", ""), reverse=True)

    async def _latest_sentiment(self, symbol: str) -> dict | None:
        try:
            raw = await self.redis.get(f"sentiment:latest:{symbol}")
            if raw:
                data = json.loads(raw)
                return {"label": data.get("sentiment"), "score": data.get("score")}
        except Exception:  # noqa: BLE001
            pass
        return None

    async def _emotion_flags(self, deals: list[dict], ctx: PreflightContext) -> list[dict]:
        """情绪化交易规则层（评审：BiasGuard 是死代码、trade_accountability
        是事后分类器 —— 规则直接基于成交历史 + guardrails 计数自建）。"""
        flags: list[dict] = []
        now = _utcnow()

        if not ctx.sl:
            flags.append({"flag": "no_stop_loss", "severity": "warn",
                          "detail": "Order has no stop loss"})

        if deals:
            last = deals[0]
            last_profit = float(last.get("profit") or 0)
            last_lot = float(last.get("lot") or 0)
            try:
                last_time = datetime.fromisoformat(str(last.get("time")).replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                last_time = None
            minutes_since = (now - last_time).total_seconds() / 60 if last_time else None

            if last_profit < 0 and minutes_since is not None and minutes_since <= REVENGE_WINDOW_MIN:
                if ctx.lot >= last_lot * MARTINGALE_LOT_MULT:
                    flags.append({
                        "flag": "martingale_after_loss", "severity": "block",
                        "detail": (
                            f"Lot {ctx.lot} ≥ {MARTINGALE_LOT_MULT}x the lot of a loss "
                            f"({last_lot}) closed {minutes_since:.0f}min ago — revenge/martingale pattern"
                        ),
                    })
                else:
                    flags.append({
                        "flag": "revenge_trade_window", "severity": "warn",
                        "detail": (
                            f"Re-entering {minutes_since:.0f}min after a "
                            f"{last_profit:.2f} loss on {last.get('symbol')}"
                        ),
                    })

        # 连亏计数（guardrails:trade_results:{date}，引擎/AI/手动平仓都会写）
        try:
            results = await self.redis.lrange(f"guardrails:trade_results:{now.date().isoformat()}", 0, -1)
            streak = 0
            for r in reversed(results):
                if (r.decode() if isinstance(r, bytes) else str(r)) == "0":
                    streak += 1
                else:
                    break
            if streak >= 3:
                flags.append({"flag": "loss_streak", "severity": "warn",
                              "detail": f"{streak} consecutive losses today"})
        except Exception:  # noqa: BLE001
            pass

        try:
            trades_this_hour = int(await self.redis.get(
                f"guardrails:trades:{now.date().isoformat()}T{now.hour:02d}"
            ) or 0)
            if trades_this_hour >= 4:
                flags.append({"flag": "near_frequency_limit", "severity": "warn",
                              "detail": f"{trades_this_hour} trades in the last hour (limit 5)"})
        except Exception:  # noqa: BLE001
            pass

        return flags

    # ─── 审计与事件 ───────────────────────────────────────────────────────

    async def _create_audit(self, *, symbol: str, order_type: str, lot: float, sl: float, tp: float,
                            order_kind: str, order_price: float | None, account_login: str,
                            expected_price: float, review: dict | None = None) -> int:
        from app.db.session import async_session

        async with async_session() as session:
            audit = OrderAudit(
                symbol=symbol, order_type=order_type, requested_lot=lot,
                requested_sl=sl, requested_tp=tp, expected_price=expected_price,
                status="PENDING_REVIEW", signal_source="manual", source="manual",
                account_login=account_login or "0", order_kind=order_kind,
                order_price=order_price, review=review,
            )
            session.add(audit)
            await session.commit()
            return audit.id

    async def _load_audit(self, audit_id: int) -> OrderAudit | None:
        from app.db.session import async_session

        async with async_session() as session:
            return (await session.execute(select(OrderAudit).where(OrderAudit.id == audit_id))).scalar_one_or_none()

    async def _update_audit(self, audit_id: int, **fields):
        from app.db.session import async_session

        async with async_session() as session:
            audit = (await session.execute(select(OrderAudit).where(OrderAudit.id == audit_id))).scalar_one_or_none()
            if audit is None:
                return
            for k, v in fields.items():
                setattr(audit, k, v)
            await session.commit()

    async def _reject_inline(self, audit_id: int, reason: str, *, kind: str,
                             rule_flags: list | None = None, llm: dict | None = None,
                             retryable: bool = False) -> dict:
        # 加载并合并（评审 I-2）：审计行可能已存有 LLM verdict/rule_flags，
        # 整体覆盖会丢失先前审查阶段的证据 —— 合并保留完整审查轨迹。
        audit = await self._load_audit(audit_id)
        review = dict((audit.review or {}) if audit else {})
        review.update({"reject_kind": kind, "retryable": retryable})
        if rule_flags is not None:
            review["rule_flags"] = rule_flags
        if llm:
            review["llm"] = llm
        await self._update_audit(audit_id, status="REJECTED", error_message=reason[:500], review=review)
        await self._log_event(BotEventType.TRADE_BLOCKED, f"[Manual] order blocked ({kind}): {reason[:200]}")
        await self._push({"type": "manual_review", "review_id": audit_id, "status": "REJECTED",
                          "reason": reason, "kind": kind})
        return {"status": "REJECTED", "review_id": audit_id, "reason": reason,
                "kind": kind, "retryable": retryable}

    async def _ai_error_event(self, audit_id: int, detail: str):
        # 基础设施故障不伪装成分析结论：AI_AGENT_ERROR 而非 AI_ANALYSIS
        await self._log_event(BotEventType.AI_AGENT_ERROR,
                              f"[Manual] review infrastructure failure for review #{audit_id}: {detail}")

    async def _log_event(self, event_type: BotEventType, message: str):
        from app.db.session import async_session

        try:
            async with async_session() as session:
                session.add(BotEvent(event_type=event_type, message=message))
                await session.commit()
        except Exception as e:  # noqa: BLE001
            logger.error(f"ManualGate event log failed: {e!r}")

    async def _push(self, payload: dict):
        try:
            await self.redis.publish("bot_event", json.dumps(payload, default=str))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ManualGate WS push failed: {e!r}")


def _audit_to_dict(a: OrderAudit) -> dict:
    return {
        "id": a.id, "symbol": a.symbol, "order_type": a.order_type,
        "requested_lot": a.requested_lot, "requested_sl": a.requested_sl,
        "requested_tp": a.requested_tp, "expected_price": a.expected_price,
        "fill_price": a.fill_price, "ticket": a.ticket, "status": a.status,
        "error_message": a.error_message, "account_login": a.account_login,
        "order_kind": a.order_kind, "order_price": a.order_price,
        "review": a.review, "created_at": a.created_at.isoformat() if a.created_at else None,
    }
