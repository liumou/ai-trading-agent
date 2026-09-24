"""
行情提醒巡检引擎 — 周期性判定价格阈值提醒规则，满足条件后发送飞书卡片。

设计要点（评审修订后）：
- **不直接调 MT5 Bridge**：从 scheduler 写入的 Redis price cache（`price:cache:{symbol}`）读 tick，
  避免双倍 bridge 请求，也避免提醒故障拖累交易主循环。
- **严格连续满足**：价格必须连续满足条件 duration_seconds 秒才触发；中途回落即重置计时。
- **数据不可用处理**：tick 缺失 / Redis 异常 → 视为"未知"，重置 first_trigger（保守，避免跨休市/断连误触发），不清 sent_count。
- **原子计数防并发双发**：用 `UPDATE ... SET sent_count=sent_count+1 WHERE id=? AND sent_count < max_notifications`。
- **达到上限自动停用**：sent_count >= max_notifications → is_active=False，需用户手动重开（重开时重置计数）。
- **单条规则失败隔离**：每条规则 try/except 包裹，一条故障不影响其他规则。
- 注：单进程 AsyncIOScheduler 部署前提；多 worker 需额外 Redis 分布式锁（本项目 Railway 单 worker）。
"""

import json
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select, update

from app.db.models import PriceAlert
from app.db.session import async_session as _default_async_session

# Redis price cache 前缀（scheduler._fetch_tick 写入）
PRICE_CACHE_PREFIX = "price:cache:"

# first_trigger 状态 key 前缀（存首次满足条件的 unix 秒）
FIRST_TRIGGER_PREFIX = "price_alert:first_trigger:"

# first_trigger 的 TTL：至少 2 倍持续时长，最低 300 秒，防孤儿 key 残留
def _trigger_ttl(duration_seconds: int) -> int:
    return max(duration_seconds * 2, 300)


class PriceAlertService:
    """行情提醒巡检引擎。"""

    def __init__(self, feishu_notifier, redis_client, session_factory=None):
        self.feishu_notifier = feishu_notifier
        self.redis = redis_client
        # 可注入 session factory（测试用 SQLite，生产默认全局 async_session）
        self._session_factory = session_factory or _default_async_session
        # 发送中的提醒 id 集合，防重入（同一提醒并发触发时只发一次）
        self._sending: set[int] = set()

    # ─── 巡检主入口 ──────────────────────────────────────────────────────────

    async def check_all(self) -> None:
        """加载全部活跃提醒，逐条判定。单条失败不影响其他规则。"""
        if not self.feishu_notifier.enabled:
            return
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(PriceAlert).where(PriceAlert.is_active.is_(True))
                )
                alerts = list(result.scalars().all())
        except Exception as e:
            logger.error(f"Price alert check: failed to load alerts: {e}")
            return

        for alert in alerts:
            try:
                await self._check_one(alert)
            except Exception as e:
                logger.error(f"Price alert check failed [alert {alert.id}]: {e}")

    # ─── 单条规则判定 ────────────────────────────────────────────────────────

    async def _check_one(self, alert: PriceAlert) -> None:
        """判定单条提醒：读价格 → 条件比较 → 维护持续状态 → 触发发送。"""
        tick = await self._get_price_from_cache(alert.symbol)
        if tick is None:
            # 数据不可用（市场休市 / Bridge 断连 / cache 过期）：保守重置计时，不清已发送计数
            await self._reset_first_trigger(alert.id)
            return

        bid = tick.get("bid")
        if bid is None:
            await self._reset_first_trigger(alert.id)
            return

        satisfied = self._is_satisfied(alert.condition, bid, alert.trigger_price)
        if not satisfied:
            await self._reset_first_trigger(alert.id)
            return

        # 满足条件：记录/推进首次满足时间
        first_ts = await self._get_first_trigger(alert.id)
        now_ts = int(datetime.now(timezone.utc).timestamp())
        if first_ts is None:
            first_ts = now_ts
            await self._set_first_trigger(alert.id, first_ts, alert.duration_seconds)

        elapsed = now_ts - first_ts
        if elapsed < alert.duration_seconds:
            return  # 持续时长未到，继续等待

        # 持续时长已满足 → 触发发送（异步，不阻塞巡检循环）
        await self._dispatch_send(alert, bid)

    # ─── 条件比较 ────────────────────────────────────────────────────────────

    @staticmethod
    def _is_satisfied(condition: str, bid: float, trigger_price: float) -> bool:
        """判定价格是否满足条件。严格比较（> / <），贴合用户"大于/小于"语义。

        价格恰好等于阈值视为不满足，避免在阈值附近震荡时反复重置/触发。
        """
        if condition == "above":
            return bid > trigger_price
        if condition == "below":
            return bid < trigger_price
        return False

    # ─── 触发发送（含原子计数 + 达上限停用）──────────────────────────────────

    async def _dispatch_send(self, alert: PriceAlert, current_price: float) -> None:
        """触发发送：原子计数占位 → 发送飞书卡片 → 达上限停用。

        用 `_sending` 集合防重入（同一提醒未完成发送前不重复调度）。
        """
        alert_id = alert.id
        if alert_id in self._sending:
            return
        self._sending.add(alert_id)
        try:
            # 原子计数占位：sent_count < max_notifications 才 +1，防并发双发
            claimed = await self._claim_notification(alert_id, alert.max_notifications)
            if not claimed:
                # 已达上限（并发下可能被其他 tick 占用）：确保停用
                await self._deactivate_if_saturated(alert_id, alert.max_notifications)
                return

            # 发送飞书卡片（异步 task，不阻塞巡检循环）
            ok = await self.feishu_notifier.send_price_alert_card(alert, current_price)
            if ok:
                await self._mark_sent(alert_id)
            # 发送后检查是否达上限 → 停用
            await self._deactivate_if_saturated(alert_id, alert.max_notifications)
        finally:
            self._sending.discard(alert_id)

    async def _claim_notification(self, alert_id: int, max_notifications: int) -> bool:
        """原子占位一次发送名额。返回是否占用成功。"""
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    update(PriceAlert)
                    .where(PriceAlert.id == alert_id, PriceAlert.sent_count < max_notifications)
                    .values(sent_count=PriceAlert.sent_count + 1)
                )
                await session.commit()
                return result.rowcount > 0
        except Exception as e:
            logger.error(f"Price alert claim failed [alert {alert_id}]: {e}")
            return False

    async def _mark_sent(self, alert_id: int) -> None:
        """记录最近发送时间。"""
        try:
            async with self._session_factory() as session:
                await session.execute(
                    update(PriceAlert)
                    .where(PriceAlert.id == alert_id)
                    .values(last_sent_at=datetime.now(timezone.utc).replace(tzinfo=None))
                )
                await session.commit()
        except Exception as e:
            logger.error(f"Price alert mark_sent failed [alert {alert_id}]: {e}")

    async def _deactivate_if_saturated(self, alert_id: int, max_notifications: int) -> None:
        """若 sent_count >= max_notifications 则停用规则并清理状态。"""
        try:
            async with self._session_factory() as session:
                alert = await session.get(PriceAlert, alert_id)
                if alert is not None and alert.sent_count >= alert.max_notifications:
                    alert.is_active = False
                    await session.commit()
                    await self._clear_first_trigger(alert_id)
                    logger.info(f"Price alert {alert_id} reached max notifications, deactivated")
        except Exception as e:
            logger.error(f"Price alert deactivate failed [alert {alert_id}]: {e}")

    # ─── Redis 状态管理 ──────────────────────────────────────────────────────

    async def _get_price_from_cache(self, symbol: str) -> dict | None:
        """从 Redis price cache 读最近 tick。返回 None 表示无数据（不可用）。"""
        try:
            raw = await self.redis.get(f"{PRICE_CACHE_PREFIX}{symbol}")
            if not raw:
                return None
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception as e:
            logger.error(f"Price alert cache read failed [{symbol}]: {e}")
            return None

    async def _get_first_trigger(self, alert_id: int) -> int | None:
        """读取首次满足条件的 unix 秒。无则返回 None。"""
        try:
            raw = await self.redis.get(f"{FIRST_TRIGGER_PREFIX}{alert_id}")
            return int(raw) if raw else None
        except Exception as e:
            logger.error(f"Price alert first_trigger read failed [alert {alert_id}]: {e}")
            return None

    async def _set_first_trigger(self, alert_id: int, ts: int, duration_seconds: int) -> None:
        """记录首次满足时间，带 TTL 防孤儿 key。"""
        try:
            await self.redis.setex(
                f"{FIRST_TRIGGER_PREFIX}{alert_id}", _trigger_ttl(duration_seconds), str(ts)
            )
        except Exception as e:
            logger.error(f"Price alert first_trigger set failed [alert {alert_id}]: {e}")

    async def _reset_first_trigger(self, alert_id: int) -> None:
        """条件不满足或数据不可用时，清除首次满足时间（重新计时）。"""
        await self._clear_first_trigger(alert_id)

    async def _clear_first_trigger(self, alert_id: int) -> None:
        """删除首次满足时间 key。"""
        try:
            await self.redis.delete(f"{FIRST_TRIGGER_PREFIX}{alert_id}")
        except Exception as e:
            logger.error(f"Price alert first_trigger clear failed [alert {alert_id}]: {e}")

    # ─── 外部清理（供 API 删除规则时调用）────────────────────────────────────

    async def cleanup_rule(self, alert_id: int) -> None:
        """删除规则时清理 Redis 状态（防孤儿 key）。"""
        await self._clear_first_trigger(alert_id)
        self._sending.discard(alert_id)