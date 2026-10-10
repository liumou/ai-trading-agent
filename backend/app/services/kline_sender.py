"""
飞书 K 线图定时发送服务 — 组合引擎快照、行情数据、K 线图渲染、图片上传与卡片发送。

调度入口：``send_all()`` 由 scheduler 的 cron job（每 15 分钟）调用。

关键逻辑（用户确认的需求语义）：
1. **夜窗判定**：Asia/Shanghai 00:00–08:00 不发送，其余时段发送。
2. **只发启用且有行情的品种**：遍历引擎快照，``is_market_open(symbol)`` 为真才处理。
3. **每品种两张图**：M15 读 M15 K 线 + MA5/10/20/55，H1 读 H1 K 线 + MA5/10/20/55。
4. **失败隔离**：单品种失败仅记日志，不影响其他品种（对齐 ``_price_alert_job`` 模式）。
"""

import asyncio
import inspect
from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger

# K 线图周期（M15 + H1），各读对应周期的 OHLCV
KLINE_TIMEFRAMES = ("M15", "H1")

# 每周期取最近 100 根（足够覆盖 MA55）
KLINE_COUNT = 100

# 夜窗：Asia/Shanghai 00:00–08:00 不发送
NIGHT_START_HOUR = 0  # 含
NIGHT_END_HOUR = 8  # 不含（08:00 恢复发送）

# 并发限流：防止 N 品种同时打 2N 个 bridge 请求 + 2N 次上传，挤压交易主循环。
# price_alert_service 刻意不直连 bridge；本功能需要实时 K 线，只能直连，用信号量兜住。
CONCURRENCY_LIMIT = 3


def is_night_window(now: datetime | None = None) -> bool:
    """判定当前（Asia/Shanghai）是否处于夜窗。

    now 缺省用当前时刻；传入测试用 now（须带 tz 或视为 Shanghai 墙钟）。
    """
    if now is None:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
    elif now.tzinfo is None:
        # 无 tz 视为已是 Shanghai 墙钟时间（测试便利）
        now = now.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    else:
        now = now.astimezone(ZoneInfo("Asia/Shanghai"))
    hour = now.hour
    return NIGHT_START_HOUR <= hour < NIGHT_END_HOUR


class KlineSender:
    """飞书 K 线图发送服务。

    构造参数:
        feishu_notifier: FeishuNotifier（发卡片）
        uploader: FeishuImageUploader（上传图片 → image_key）
        engines_provider: 可调用对象，返回 dict[symbol → BotEngine]；
                          scheduler 传入其 ``_engines_snapshot``（保持数据源一致）
        market_open: 可调用对象，判定品种市场是否开市；缺省用 scheduler.is_market_open
    """

    def __init__(
        self,
        feishu_notifier,
        uploader,
        engines_provider,
        market_open=None,
        night_check=None,
    ):
        self.feishu_notifier = feishu_notifier
        self.uploader = uploader
        self.engines_provider = engines_provider
        self.market_open = market_open or self._default_market_open
        # 夜窗判定可注入（测试隔离真实时间；生产默认用实时 Asia/Shanghai 墙钟）
        self.night_check = night_check or is_night_window
        # 并发信号量：单轮最多 CONCURRENCY_LIMIT 个品种并行处理
        self._semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
        # 上传器未配置时只告警一次（对齐 price_alert_service 的一次性告警，防每 15 分钟刷屏）
        self._warned_uploader_disabled = False

    @staticmethod
    async def _default_market_open(symbol: str) -> bool:
        from app.bot.scheduler import is_market_open

        return is_market_open(symbol)

    async def send_all(self) -> None:
        """遍历启用且有行情的品种，为每个品种发送 M15 + H1 两张 K 线图。

        夜窗直接跳过；飞书未启用或上传器未配置时记录一次告警后跳过。
        """
        if not self.feishu_notifier.enabled:
            logger.debug("Kline sender skipped: feishu notifier disabled")
            return
        if not self.uploader.enabled:
            # 配置缺失是常见故障：不打一次用户只会看到"没图"无从排查。
            # 只打一次，避免每 15 分钟刷屏；配置恢复后自动重置。
            if not self._warned_uploader_disabled:
                logger.warning(
                    "Kline sender skipped: FEISHU_APP_ID / FEISHU_APP_SECRET not set — "
                    "K线图无法上传（需要飞书开放平台应用）"
                )
                self._warned_uploader_disabled = True
            return
        self._warned_uploader_disabled = False

        if self.night_check():
            logger.debug("Kline sender skipped: Asia/Shanghai night window (00:00–08:00)")
            return

        engines = self.engines_provider()
        if not engines:
            logger.debug("Kline sender skipped: no active engines")
            return

        async def _run_symbol(symbol, engine) -> None:
            async with self._semaphore:
                await self._send_symbol(symbol, engine)

        results = await asyncio.gather(
            *[_run_symbol(symbol, engine) for symbol, engine in engines.items()],
            return_exceptions=True,
        )
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                symbol = list(engines.keys())[idx]
                logger.warning(f"Kline sender failed [{symbol}]: {result!r}")

    async def _send_symbol(self, symbol: str, engine) -> None:
        """为单个品种发送 M15 + H1 两张图。内部失败隔离（try/except 全部兜住）。"""
        try:
            result = self.market_open(symbol)
            if asyncio.iscoroutine(result) or inspect.isawaitable(result):
                result = await result
            if not result:
                logger.debug(f"Kline sender skipped [{symbol}]: market closed")
                return
            for timeframe in KLINE_TIMEFRAMES:
                try:
                    await self._send_one(symbol, engine, timeframe)
                except Exception as e:
                    logger.warning(f"Kline sender [{symbol} {timeframe}] failed: {e!r}")
        except Exception as e:
            logger.warning(f"Kline sender [{symbol}] failed: {e!r}")

    async def _send_one(self, symbol: str, engine, timeframe: str) -> None:
        """单张图完整链路：取 OHLCV → 画图 → 上传 → 发卡片。"""
        df = await engine.market_data.get_ohlcv(symbol, timeframe, KLINE_COUNT)
        if df is None or df.empty:
            logger.debug(f"Kline sender skipped [{symbol} {timeframe}]: no data")
            return

        # 渲染 PNG（symbol_profile 可能为 None，安全取 display_name）
        from app.notifications.kline_chart import build_kline_png

        display_name = (engine.symbol_profile or {}).get("display_name")
        png_bytes = build_kline_png(symbol, timeframe, df, display_name=display_name)
        if not png_bytes:
            return

        # 上传图片 → image_key
        image_key = await self.uploader.upload_png_bytes(png_bytes)
        if not image_key:
            logger.warning(f"Kline sender [{symbol} {timeframe}]: image upload failed")
            return

        # 发卡片
        ok = await self.feishu_notifier.send_kline_card(symbol, timeframe, image_key)
        if ok:
            logger.info(f"Kline sender sent [{symbol} {timeframe}]")
        else:
            logger.warning(f"Kline sender [{symbol} {timeframe}]: card send failed")
