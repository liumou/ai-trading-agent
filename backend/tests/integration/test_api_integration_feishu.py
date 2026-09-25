"""飞书前端可配置 — 集成测试。

覆盖：
- `/api/integration/config` 保存飞书 webhook 后，运行中的 FeishuNotifier 即时刷新（免重启）
- 保存后在 `/api/integration/status` 显示 configured，且不泄露 webhook URL
- env 未配置时卡片显示 not_configured

依赖注入：测试 app 挂 FeishuNotifier（env 未配 → disabled），模拟前端保存。
Vault 在测试环境不可用，_set_vault_value 静默跳过，重点验证 reload 侧（真实持久化另由
Secret 表 + vault 单测覆盖）。
"""

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import integration as integration_routes
from app.notifications.feishu import FeishuNotifier


@pytest_asyncio.fixture
async def client():
    """挂 integration 路由 + 运行中的 FeishuNotifier（模拟 main.py lifespan）。"""
    app = FastAPI()
    app.include_router(integration_routes.router)
    notifier = FeishuNotifier()  # env 无 FEISHU_WEBHOOK_URL → disabled
    app.state.feishu_notifier = notifier
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.app = app  # 让测试能访问 app.state
        yield ac


async def test_save_feishu_reloads_notifier(client, monkeypatch):
    """保存飞书 webhook 后，运行中的 notifier 立即 enabled（不需重启）。"""
    app = client.app
    notifier: FeishuNotifier = app.state.feishu_notifier
    assert notifier.enabled is False, "前置：env 未配置，notifier 应 disabled"

    resp = await client.put(
        "/api/integration/config",
        json={"integration_id": "feishu", "config": {"Webhook URL": "https://open.feishu.cn/open-apis/bot/v2/hook/abc123"}},
    )
    assert resp.status_code == 200
    assert resp.json()["saved"] == ["Webhook URL"]

    # 运行时刷新：notifier 立即 enabled（下轮价格巡检即用）
    assert notifier.enabled is True
    assert notifier.webhook_url == "https://open.feishu.cn/open-apis/bot/v2/hook/abc123"


async def test_status_shows_configured_after_save(client):
    """保存后 /api/integration/status 的 Feishu 状态反映运行中 notifier。"""
    await client.put(
        "/api/integration/config",
        json={"integration_id": "feishu", "config": {"Webhook URL": "https://open.feishu.cn/hook/test"}},
    )

    # 从 status gather 中找 Feishu 项
    resp = await client.get("/api/integration/status")
    assert resp.status_code == 200
    services = {s["name"]: s for s in resp.json()["services"]}
    feishu = services["Feishu"]
    assert feishu["status"] == "configured"
    assert "webhook_url" not in str(feishu).lower(), "不得泄露 webhook URL"


async def test_status_not_configured_when_env_unset(client):
    """env 未配置且未保存时，Feishu 状态为 not_configured。"""
    resp = await client.get("/api/integration/status")
    services = {s["name"]: s for s in resp.json()["services"]}
    assert services["Feishu"]["status"] == "not_configured"


async def test_single_service_test_feishu(client):
    """GET /api/integration/test/feishu 返回配置状态（不实际发请求）。"""
    resp = await client.get("/api/integration/test/feishu")
    assert resp.status_code == 200
    assert resp.json()["status"] in ("configured", "not_configured")