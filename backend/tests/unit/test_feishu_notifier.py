"""FeishuNotifier._post 发送健壮性单元测试。

覆盖 _post 各分支：成功（code==0）、业务码非 0、非 JSON 响应体（Extra data
根因）、非 dict body、传输层异常。全部用 httpx.MockTransport 拦截，不真实发送。

背景（2026-10-12 生产事故）：webhook 返回 `true\n` 等非标准 JSON 时，
旧实现 `resp.json()` 抛 `Extra data: line 1 column 5 (char 4)`，被 except 吞成
一行无上下文 error，无法定位是 URL 配置错误还是网关拦截。
"""

import httpx
import pytest

from app.notifications.feishu import FeishuNotifier, _mask_host

POST_BODY = {"msg_type": "interactive", "card": {"elements": []}}

# webhook 正常返回（code==0）
_SUCCESS_RESPONSE = httpx.Response(200, json={"code": 0, "msg": "success"})


@pytest.fixture
def notifier():
    """构造 enabled 的 notifier（URL 不真实请求，仅用其状态与 host 脱敏）。"""
    return FeishuNotifier(webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/abc123")


def _patch_transport(monkeypatch, handler) -> None:
    """把 httpx.AsyncClient 替换为注入 MockTransport 的版本。

    handler 返回 httpx.Response 或抛 httpx.TransportError。这样 _post 的
    `httpx.AsyncClient()` 调用走 mock transport，无需改动产品签名。
    """
    _original_client = httpx.AsyncClient  # 替换前保存原始类，避免递归
    transport = httpx.MockTransport(handler)

    class _Client:
        def __init__(self, *args, **kwargs):
            # 透传 kwargs（含 _post 设的 timeout / headers），只强制覆盖 transport
            self._client = _original_client(*args, transport=transport, **kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            await self._client.aclose()
            return False

        async def post(self, url, json=None):
            return await self._client.post(url, json=json)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)


# ─── 成功分支 ──────────────────────────────────────────────────────────


async def test_post_success_code_zero(monkeypatch, notifier):
    """HTTP 200 + code==0 → 成功 True。"""
    _patch_transport(monkeypatch, lambda req: _SUCCESS_RESPONSE)
    assert await notifier._post(POST_BODY) is True


async def test_post_sends_curl_user_agent(monkeypatch, notifier):
    """请求必须带 curl UA —— 规避 Clash 类代理对 python-httpx 的 502 拦截。

    本机实测（2026-10-12）：默认 UA 返回 502 HTML，curl UA 返回 200。生产
    无代理不受影响；UA 是兼作「非爬虫」信号，防止中间网关误判。
    """
    from app.notifications.feishu import _WEBHOOK_USER_AGENT

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("User-Agent")
        seen["url"] = str(request.url)
        return _SUCCESS_RESPONSE

    _patch_transport(monkeypatch, handler)
    assert await notifier._post(POST_BODY) is True
    assert seen["ua"] == _WEBHOOK_USER_AGENT
    assert "/open-apis/bot/v2/hook/abc123" in seen["url"]


async def test_post_business_code_nonzero_fails(monkeypatch, notifier):
    """HTTP 200 但业务 code 非 0 → 失败 False（如 200621 parse card json err）。"""
    _patch_transport(
        monkeypatch,
        lambda req: httpx.Response(200, json={"code": 200621, "msg": "parse card json err"}),
    )
    assert await notifier._post(POST_BODY) is False


# ─── 响应体异常分支 ────────────────────────────────────────────────────


async def test_post_non_json_body_fails(monkeypatch, notifier):
    """响应体非标准 JSON（如 `true\n` / 网关错误页）→ 失败 False，不抛出。

    这是生产日志 `Extra data: line 1 column 5 (char 4)` 的来源：webhook 被
    反代/网关返回了健康检查页或错误页。
    """
    _patch_transport(
        monkeypatch,
        lambda req: httpx.Response(200, content=b"true\n", headers={"content-type": "text/html"}),
    )
    assert await notifier._post(POST_BODY) is False


async def test_post_non_dict_body_fails(monkeypatch, notifier):
    """JSON 解析成功但非 dict（如 `true` / `["a"]`）→ 防御性拒绝，不 AttributeError。"""
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=b"true"))
    assert await notifier._post(POST_BODY) is False


async def test_post_http_error_status_fails(monkeypatch, notifier):
    """HTTP 非 200 + 非 JSON 体 → 失败 False（网关拦截常见形态）。"""
    _patch_transport(
        monkeypatch,
        lambda req: httpx.Response(502, content=b"<html>Bad Gateway</html>"),
    )
    assert await notifier._post(POST_BODY) is False


# ─── 传输层异常分支 ────────────────────────────────────────────────────


async def test_post_transport_exception_fails(monkeypatch, notifier):
    """传输层异常（连接被拒/超时）→ 失败 False，不抛出。"""

    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _patch_transport(monkeypatch, _boom)
    assert await notifier._post(POST_BODY) is False


async def test_post_disabled_returns_false(monkeypatch):
    """未配置 webhook → 直接 False，不发请求。

    用 monkeypatch 强制 settings.feishu_webhook_url 为空（本机 .env 已配置，
    否则 `FeishuNotifier(webhook_url="")` 会回退到 env 值而 enabled）。
    """
    from app.notifications.feishu import settings as _settings

    monkeypatch.setattr(_settings, "feishu_webhook_url", "")
    n = FeishuNotifier(webhook_url="")
    assert n.enabled is False
    assert await n._post(POST_BODY) is False


# ─── 脱敏 ─────────────────────────────────────────────────────────────


def test_mask_host_redacts_path_and_scheme():
    """_mask_host 只保留 host，去掉路径（含 hook token，属机密）。"""
    assert _mask_host("https://open.feishu.cn/open-apis/bot/v2/hook/abc123") == "open.feishu.cn"
    assert _mask_host("") == "<invalid url>"
    assert _mask_host("not-a-valid-url") in ("not-a-valid-url", "<invalid url>")


# ─── webhook 格式校验 / 坏值回退（2026-10-13 加固） ─────────────────────


def test_is_valid_webhook_url_accepts_standard():
    """标准飞书自定义机器人路径 → 合法。"""
    from app.notifications.feishu import _is_valid_webhook_url

    good = "https://open.feishu.cn/open-apis/bot/v2/hook/abcd-1234-efgh-5678-ijklmnopqrst"
    assert _is_valid_webhook_url(good) is True


def test_is_valid_webhook_url_rejects_bad_path_and_host():
    """非标准路径（Vault 坏值形态）或错误 host → 非法，防止盖掉有效值。"""
    from app.notifications.feishu import _is_valid_webhook_url

    bad_path = "https://open.feishu.cn/xx/yyy"
    bad_host = "https://evil.example.com/open-apis/bot/v2/hook/abc"
    empty = ""
    assert _is_valid_webhook_url(bad_path) is False
    assert _is_valid_webhook_url(bad_host) is False
    assert _is_valid_webhook_url(empty) is False


def test_pick_webhook_falls_back_from_bad_vault_to_env(monkeypatch):
    """Vault 传入坏值 + env 有有效值 → 回退 env，不再被坏值盖住。"""
    from app.notifications.feishu import _pick_webhook_url

    bad_vault = "https://open.feishu.cn/xx/yyy"
    good_env = "https://open.feishu.cn/open-apis/bot/v2/hook/abcd-1234-efgh-5678-ijklmnopqrst"
    assert _pick_webhook_url(bad_vault, good_env) == good_env


def test_pick_webhook_all_invalid_disables(monkeypatch):
    """Vault/env 全非法 → 返回空串，通知器禁用（不再带坏 URL 空转）。"""
    from app.notifications.feishu import _pick_webhook_url

    bad_vault = "https://open.feishu.cn/xx/yyy"
    bad_host = "https://evil.example.com/open-apis/bot/v2/hook/abc"
    assert _pick_webhook_url(bad_vault, bad_host) == ""


def test_reload_empty_still_disables(monkeypatch):
    """reload_from('') 仍显式清空禁用（不回退旧值）——保存配置为空的意图。"""
    from app.notifications.feishu import settings as _settings

    monkeypatch.setattr(_settings, "feishu_webhook_url", "")
    n = FeishuNotifier(webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/abc123")
    assert n.enabled is True
    n.reload_from("")
    assert n.enabled is False
    assert n.webhook_url == ""


def test_reload_bad_value_keeps_old_good(monkeypatch):
    """reload_from 传入格式非法值 → 保留当前有效值，不打断运行中的 webhook。"""
    from app.notifications.feishu import settings as _settings

    monkeypatch.setattr(_settings, "feishu_webhook_url", "")
    good = "https://open.feishu.cn/open-apis/bot/v2/hook/abcd-1234-efgh-5678-ijklmnopqrst"
    n = FeishuNotifier(webhook_url=good)
    assert n.enabled is True
    n.reload_from("https://open.feishu.cn/xx/yyy")
    assert n.enabled is True
    assert n.webhook_url == good
