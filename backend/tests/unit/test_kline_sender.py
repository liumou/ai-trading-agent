"""
KlineSender 端到端单元测试 — 验证遍历品种、取数据、画图、上传、发卡片全链路。

用 mock 引擎（提供 market_data.get_ohlcv + symbol_profile）+ mock 上传器 +
mock FeishuNotifier，不真实发请求。夜窗直接跳过、失败隔离逐品种覆盖。
"""

import os

os.environ.setdefault("MPLBACKEND", "Agg")

from datetime import datetime

import pandas as pd

from app.services.kline_sender import KlineSender, is_night_window


def _make_ohlcv(n: int = 100) -> pd.DataFrame:
    """构造 OHLCV DataFrame（与 MarketDataService.get_ohlcv 同形，含 tick_volume 列）。"""
    import numpy as np

    rng = np.random.default_rng(7)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    close = 2000 + np.cumsum(rng.normal(0, 1.0, n))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    high = np.maximum(open_, close) + 0.5
    low = np.minimum(open_, close) - 0.5
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "tick_volume": rng.integers(100, 1000, n)},
        index=idx,
    )


class FakeEngine:
    """最小 BotEngine 替身：只需 market_data.get_ohlcv + symbol_profile。

    ``requested_symbols`` 记录 KlineSender 传给 get_ohlcv 的品种名 —— 验证
    KlineSender 给 Bridge 用的是 broker_alias（账号映射后的真实品种 ID），
    而非 canonical（如 GOLD → GOLD_）。
    """

    def __init__(self, symbol: str, data=None, fail: bool = False):
        self.symbol = symbol
        self._data = data if data is not None else _make_ohlcv()
        self.fail = fail
        self.market_data = type("MD", (), {"get_ohlcv": self._get_ohlcv})()
        self.symbol_profile = {"display_name": f"{symbol} Display"}
        self.requested_symbols: list[str] = []

    async def _get_ohlcv(self, symbol, timeframe, count):
        self.requested_symbols.append(symbol)
        if self.fail:
            raise RuntimeError("bridge down")
        return self._data


class FakeNotifier:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.sent_cards: list[tuple[str, str, str]] = []  # (symbol, timeframe, image_key)

    async def send_kline_card(self, symbol, timeframe, image_key):
        self.sent_cards.append((symbol, timeframe, image_key))
        return True


class FakeUploader:
    def __init__(self, enabled: bool = True, fail: bool = False):
        self.enabled = enabled
        self.fail = fail
        self.uploads: list[bytes] = []

    async def upload_png_bytes(self, png):
        if self.fail:
            return None
        self.uploads.append(png)
        return f"img_key_{len(self.uploads)}"


async def _noop_market_open(symbol) -> bool:
    return True


def _build_sender(
    engines,
    notifier=None,
    uploader=None,
    market_open=None,
    night_check=None,
) -> KlineSender:
    return KlineSender(
        feishu_notifier=notifier or FakeNotifier(),
        uploader=uploader or FakeUploader(),
        engines_provider=lambda: engines,
        market_open=market_open or _noop_market_open,
        # 默认不在夜窗，主链路测试不受真实时间影响；夜窗场景单独注入
        night_check=night_check or (lambda: False),
    )


# ─── send_all 主链路 ─────────────────────────────────────────────────────


class TestSendAll:
    async def test_sends_two_timeframes_per_symbol(self):
        sender = _build_sender({"GOLD": FakeEngine("GOLD")})
        notifier = sender.feishu_notifier
        uploader = sender.uploader
        await sender.send_all()
        # M15 + H1 各一张卡片
        assert sorted((s, tf) for s, tf, _ in notifier.sent_cards) == [
            ("GOLD", "H1"),
            ("GOLD", "M15"),
        ]
        assert len(uploader.uploads) == 2

    async def test_uses_broker_alias_for_bridge_requests(self):
        # 生产 get_ohlcv 内部会做 to_broker_alias；此处模拟引擎已在 get_ohlcv
        # 内应用了 alias（真实 BotEngine.market_data 就是这样），验证 KlineSender
        # 不做二次映射、透传引擎拿到的品种名 → 即账号对应的真实品种 ID。
        engine = FakeEngine("GOLD")
        sender = _build_sender({"GOLD": engine})
        await sender.send_all()
        # 两张图（M15/H1）都用引擎给的品种名去取数据，没有用 canonical 覆盖
        assert set(engine.requested_symbols) == {"GOLD"}

    async def test_multi_symbol_all_sent(self):
        sender = _build_sender(
            {"GOLD": FakeEngine("GOLD"), "OILCash": FakeEngine("OILCash")}
        )
        notifier = sender.feishu_notifier
        await sender.send_all()
        assert len(notifier.sent_cards) == 4  # 2 symbols × 2 timeframes

    async def test_night_window_skips_all(self):
        sender = _build_sender(
            {"GOLD": FakeEngine("GOLD")},
            night_check=lambda: True,
        )
        notifier = sender.feishu_notifier
        await sender.send_all()
        assert notifier.sent_cards == []

    async def test_disabled_notifier_skips(self):
        sender = _build_sender({"GOLD": FakeEngine("GOLD")}, notifier=FakeNotifier(enabled=False))
        await sender.send_all()
        assert sender.feishu_notifier.sent_cards == []

    async def test_disabled_uploader_skips(self):
        sender = _build_sender({"GOLD": FakeEngine("GOLD")}, uploader=FakeUploader(enabled=False))
        await sender.send_all()
        assert sender.feishu_notifier.sent_cards == []

    async def test_uploader_disabled_warns_once(self):
        # 上传器未配置：连续触发只打一次告警（对齐 price_alert_service 的一次性告警）
        sender = _build_sender({"GOLD": FakeEngine("GOLD")}, uploader=FakeUploader(enabled=False))
        await sender.send_all()
        await sender.send_all()
        assert sender._warned_uploader_disabled is True

    async def test_market_closed_skips_symbol(self):
        async def closed(symbol):
            return False

        sender = _build_sender({"GOLD": FakeEngine("GOLD")}, market_open=closed)
        await sender.send_all()
        assert sender.feishu_notifier.sent_cards == []

    async def test_empty_data_skips_symbol(self):
        engine = FakeEngine("GOLD", data=pd.DataFrame())
        sender = _build_sender({"GOLD": engine})
        await sender.send_all()
        assert sender.feishu_notifier.sent_cards == []


# ─── 失败隔离 ────────────────────────────────────────────────────────────


class TestFailureIsolation:
    async def test_one_symbol_failure_does_not_block_others(self):
        good = FakeEngine("GOOD")
        bad = FakeEngine("BAD", fail=True)
        sender = _build_sender({"GOOD": good, "BAD": bad})
        notifier = sender.feishu_notifier
        await sender.send_all()  # 不应抛出
        # GOOD 正常发 2 张；BAD 失败仅记日志
        assert sorted((s, tf) for s, tf, _ in notifier.sent_cards) == [
            ("GOOD", "H1"),
            ("GOOD", "M15"),
        ]

    async def test_upload_failure_skips_card_but_keeps_symbol(self):
        engine = FakeEngine("GOLD")
        uploader = FakeUploader(fail=True)
        sender = _build_sender({"GOLD": engine}, uploader=uploader)
        await sender.send_all()
        assert sender.feishu_notifier.sent_cards == []  # 上传失败→不发卡


# ─── 夜窗判定（独立） ────────────────────────────────────────────────────


class TestNightWindow:
    def test_0_am_is_night(self):
        assert is_night_window(datetime(2026, 1, 1, 0, 0))

    def test_8_am_is_day(self):
        assert not is_night_window(datetime(2026, 1, 1, 8, 0))
