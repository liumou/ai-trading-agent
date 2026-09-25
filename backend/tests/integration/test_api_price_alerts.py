"""行情提醒 API 集成测试 — CRUD + toggle 状态机 + symbol 校验。

使用独立 FastAPI app + override get_db（SQLite 内存）+ ASGITransport。
鉴权在 conftest 中通过 AUTH_PASSWORD_HASH="" 关闭。
"""

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import price_alerts as price_alerts_routes
from app.db.session import get_db


def _build_app(db_session, redis_client=None) -> FastAPI:
    app = FastAPI()
    app.include_router(price_alerts_routes.router)

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.state.redis = redis_client
    return app


@pytest_asyncio.fixture
async def client(db_session, redis_client):
    app = _build_app(db_session, redis_client)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _sample() -> dict:
    return {
        "symbol": "GOLD",
        "condition": "above",
        "trigger_price": 3350.0,
        "duration_seconds": 60,
        "max_notifications": 3,
    }


# ─── CRUD ─────────────────────────────────────────────────────────────────────


class TestCRUD:
    async def test_create_and_list(self, client):
        resp = await client.post("/api/price-alerts", json=_sample())
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["symbol"] == "GOLD"
        assert data["condition"] == "above"
        assert data["trigger_price"] == 3350.0
        assert data["sent_count"] == 0
        assert data["is_active"] is True
        assert data["id"] > 0

        # 列表
        resp = await client.get("/api/price-alerts")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_update_resets_counter(self, client):
        created = (await client.post("/api/price-alerts", json=_sample())).json()
        aid = created["id"]

        resp = await client.put(f"/api/price-alerts/{aid}", json={"trigger_price": 3400.0})
        assert resp.status_code == 200
        data = resp.json()
        assert data["trigger_price"] == 3400.0
        assert data["sent_count"] == 0

    async def test_delete(self, client):
        created = (await client.post("/api/price-alerts", json=_sample())).json()
        aid = created["id"]
        resp = await client.delete(f"/api/price-alerts/{aid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        # 列表应为空（已删除）
        resp = await client.get("/api/price-alerts")
        assert resp.status_code == 200
        assert len(resp.json()) == 0

    async def test_unknown_symbol_rejected(self, client):
        payload = _sample()
        payload["symbol"] = "NOT_A_SYMBOL"
        resp = await client.post("/api/price-alerts", json=payload)
        assert resp.status_code == 422


# ─── Toggle 状态机（达上限重开重置）─────────────────────────────────────────


class TestToggle:
    async def test_toggle_off_then_on_resets_counter(self, client, db_session):
        """停用 → 启用时重置 sent_count（修复达上限后重开静默失效）。"""
        created = (await client.post("/api/price-alerts", json=_sample())).json()
        aid = created["id"]

        # 直接改 sent_count 为已达上限，且停用
        from app.db.models import PriceAlert

        alert = await db_session.get(PriceAlert, aid)
        alert.sent_count = 3  # = max_notifications
        alert.is_active = False
        await db_session.commit()

        # 重新启用 → sent_count 应重置为 0
        resp = await client.post(f"/api/price-alerts/{aid}/toggle")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_active"] is True
        assert data["sent_count"] == 0

    async def test_toggle_on_off(self, client):
        created = (await client.post("/api/price-alerts", json=_sample())).json()
        aid = created["id"]

        # 关闭
        resp = await client.post(f"/api/price-alerts/{aid}/toggle")
        assert resp.json()["is_active"] is False
        # 再开
        resp = await client.post(f"/api/price-alerts/{aid}/toggle")
        assert resp.json()["is_active"] is True


# ─── 运行状态 ─────────────────────────────────────────────────────────────────


class FakeNotifier:
    """只暴露 /status 端点用到的 enabled 属性。"""

    def __init__(self, enabled: bool):
        self.enabled = enabled


def _app_with_notifier(db_session, redis_client, enabled: bool) -> FastAPI:
    """构建带指定 feishu 状态的 app。"""
    app = _build_app(db_session, redis_client)
    app.state.feishu_notifier = FakeNotifier(enabled=enabled)
    return app


@pytest_asyncio.fixture
async def app_with_notifier(db_session, redis_client):
    """挂载带已配置 feishu_notifier 的 app，用于 /status 的正向路径。"""
    app = _app_with_notifier(db_session, redis_client, enabled=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestStatus:
    async def test_status_when_feishu_enabled(self, app_with_notifier):
        """飞书已配置：报 enabled、统计活跃规则，且不泄露 webhook URL。"""
        created = (await app_with_notifier.post("/api/price-alerts", json=_sample())).json()
        assert created["is_active"] is True

        resp = await app_with_notifier.get("/api/price-alerts/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["feishu_enabled"] is True
        assert body["active_alerts"] == 1
        assert body["message"] is None
        # 机密不得出现在响应中
        assert "webhook_url" not in str(body)

    async def test_status_when_feishu_disabled(self, db_session, redis_client):
        """飞书未配置：明确告知未配置，并提示配置项名称。"""
        app = _app_with_notifier(db_session, redis_client, enabled=False)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/price-alerts/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["feishu_enabled"] is False
        assert body["config_key"] == "FEISHU_WEBHOOK_URL"
        assert body["active_alerts"] == 0
        assert body["message"] and "FEISHU_WEBHOOK_URL" in body["message"]

    async def test_status_no_notifier_configured(self, client):
        """启动异常（notifier 未注入）时降级为未配置，不得 500。"""
        resp = await client.get("/api/price-alerts/status")
        assert resp.status_code == 200
        assert resp.json()["feishu_enabled"] is False

    async def test_status_not_shadowed_by_id_route(self, app_with_notifier):
        """路由顺序：/status 必须先于 /{alert_id} 注册，否则会被路径参数抢走。

        回归测试：若顺序被破坏，GET /status 会匹配到 /{alert_id} 并报 422。
        """
        resp = await app_with_notifier.get("/api/price-alerts/status")
        assert resp.status_code == 200, "status 端点被 /{alert_id} 遮蔽"


# ─── 参数校验 ─────────────────────────────────────────────────────────────────


class TestValidation:
    async def test_invalid_condition(self, client):
        payload = _sample()
        payload["condition"] = "sideways"
        resp = await client.post("/api/price-alerts", json=payload)
        assert resp.status_code == 422

    async def test_nonpositive_trigger_price(self, client):
        payload = _sample()
        payload["trigger_price"] = -5
        resp = await client.post("/api/price-alerts", json=payload)
        assert resp.status_code == 422

    async def test_zero_max_notifications(self, client):
        payload = _sample()
        payload["max_notifications"] = 0
        resp = await client.post("/api/price-alerts", json=payload)
        assert resp.status_code == 422