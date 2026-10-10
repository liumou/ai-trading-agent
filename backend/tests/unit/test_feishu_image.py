"""
飞书图片上传 + K 线卡片发送单元测试。

- FeishuImageUploader：用 httpx.MockTransport 拦截 open.feishu.cn 请求，
  验证 token 获取/缓存/失效重取、图片上传、失败不抛出。
- FeishuNotifier.send_kline_card：验证 payload 结构（含 img 元素 + image_key）。
"""

import httpx

from app.notifications.feishu import FeishuNotifier
from app.notifications.feishu_image import FeishuImageUploader

APP_ID = "cli_test_app"
APP_SECRET = "test_secret"


class _MockHandler:
    """httpx.MockTransport 的 handler：按路径返回 token / 上传 / 失败。"""

    def __init__(self):
        self.token_calls = 0
        self.upload_calls = 0
        self.fail_upload = False
        self.fail_token = False
        self.token_delay = 0.0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
            self.token_calls += 1
            if self.fail_token:
                return httpx.Response(200, json={"code": 99991663, "msg": "invalid secret"})
            if self.token_delay:
                import time

                time.sleep(self.token_delay)
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "ok",
                    "tenant_access_token": "t-test-token",
                    "expire": 7200,
                },
            )
        if request.url.path.endswith("/im/v1/images"):
            self.upload_calls += 1
            if self.fail_upload:
                return httpx.Response(200, json={"code": 99991668, "msg": "token expired"})
            return httpx.Response(200, json={"code": 0, "msg": "ok", "data": {"image_key": "img_v2_test_key"}})
        return httpx.Response(404, json={"code": 404, "msg": "not found"})


def _make_uploader(handler: _MockHandler) -> FeishuImageUploader:
    return FeishuImageUploader(
        app_id=APP_ID,
        app_secret=APP_SECRET,
        transport=httpx.MockTransport(handler),
    )


# ─── token 获取 ──────────────────────────────────────────────────────────


class TestGetToken:
    async def test_token_obtained_and_cached(self):
        handler = _MockHandler()
        uploader = _make_uploader(handler)
        token = await uploader.get_tenant_access_token()
        assert token == "t-test-token"
        assert handler.token_calls == 1
        # 缓存命中，不再请求
        token2 = await uploader.get_tenant_access_token()
        assert token2 == "t-test-token"
        assert handler.token_calls == 1

    async def test_disabled_returns_none(self):
        uploader = FeishuImageUploader(app_id="", app_secret="")
        assert await uploader.get_tenant_access_token() is None

    async def test_token_failure_returns_none(self):
        handler = _MockHandler()
        handler.fail_token = True
        uploader = _make_uploader(handler)
        assert await uploader.get_tenant_access_token() is None
        # 失败不清缓存（无缓存），下次可重试
        assert uploader._token is None

    async def test_concurrent_requests_share_one_token_refresh(self):
        # 缓存过期 + 并发多品种时，只应有一个协程真正发 token 请求（锁串行化）
        import asyncio

        handler = _MockHandler()
        handler.token_delay = 0.05  # 模拟网络延迟，让并发真正竞争锁
        uploader = _make_uploader(handler)
        tokens = await asyncio.gather(*[uploader.get_tenant_access_token() for _ in range(10)])
        assert tokens == ["t-test-token"] * 10
        assert handler.token_calls == 1


# ─── 图片上传 ────────────────────────────────────────────────────────────


class TestUploadImage:
    async def test_upload_returns_image_key(self):
        handler = _MockHandler()
        uploader = _make_uploader(handler)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        key = await uploader.upload_image(png)
        assert key == "img_v2_test_key"
        assert handler.upload_calls == 1
        assert handler.token_calls == 1  # 上传时自动取 token

    async def test_empty_bytes_returns_none(self):
        uploader = FeishuImageUploader(app_id=APP_ID, app_secret=APP_SECRET)
        assert await uploader.upload_image(b"") is None

    async def test_upload_failure_returns_none_and_refreshes_token(self):
        handler = _MockHandler()
        uploader = _make_uploader(handler)
        # 先取一个有效 token
        assert await uploader.get_tenant_access_token() == "t-test-token"
        # 上传失败（token 过期码）→ 返回 None 且清空缓存
        handler.fail_upload = True
        assert await uploader.upload_image(b"png-data") is None
        assert uploader._token is None

    async def test_upload_png_bytes_alias(self):
        handler = _MockHandler()
        uploader = _make_uploader(handler)
        key = await uploader.upload_png_bytes(b"\x89PNG\x00")
        assert key == "img_v2_test_key"


# ─── K 线卡片 payload ────────────────────────────────────────────────────


class TestSendKlineCard:
    def test_build_kline_card_contains_img(self):
        notifier = FeishuNotifier(webhook_url="https://example.com/webhook")
        card = notifier._build_kline_card("GOLD", "M15", "img_v2_test_key")
        assert card["msg_type"] == "interactive"
        elements = card["card"]["elements"]
        img = elements[0]
        assert img["tag"] == "img"
        assert img["img_key"] == "img_v2_test_key"
        # display_name = SYMBOL_PROFILES["GOLD"].display_name = "Gold (XAUUSD)"
        # alt 必须是对象结构（webhook 卡片要求），不是纯字符串（实测 200621）
        assert img["alt"] == {"tag": "plain_text", "content": "Gold (XAUUSD) M15 K线"}
        # 标题含品种 + 周期
        header = card["card"]["header"]["title"]["content"]
        assert "Gold" in header
        assert "M15" in header

    async def test_send_kline_card_empty_key_returns_false(self):
        notifier = FeishuNotifier(webhook_url="https://example.com/webhook")
        assert await notifier.send_kline_card("GOLD", "M15", "") is False
