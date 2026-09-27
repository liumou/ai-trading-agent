"""品种配置账号隔离测试（symbol_configs.account_login）。

MT5 切换账号后，品种管理数据必须与当前活跃账号绑定：
- list/get 只返回当前活跃账号的配置；
- create 归属到当前活跃账号，同 canonical 名在不同账号下可共存；
- 别名冲突检查只在账号内生效；
- broker-catalog 缓存 key 按登录号隔离；
- DB 已同步但无启用品种时，reload_engines 不再回退静态 settings.symbols。
"""

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock

from app.api.routes import symbols as symbols_routes
from app.db.models import MT5Account, SymbolConfig
from app.db.session import get_db
from app.services import symbol_config_service as svc


async def _seed_account(session, login: int, *, active: bool) -> MT5Account:
    acct = MT5Account(
        login=login,
        password_encrypted=b"x",
        password_nonce=b"y",
        server="XMGlobal-MT5",
        is_active=active,
    )
    session.add(acct)
    await session.commit()
    return acct


async def _seed_symbol(
    session,
    symbol: str,
    account_login: str,
    *,
    broker_alias: str | None = None,
    is_enabled: bool = False,
) -> SymbolConfig:
    cfg = SymbolConfig(
        symbol=symbol,
        account_login=account_login,
        display_name=symbol,
        broker_alias=broker_alias,
        pip_value=0.01,
        default_lot=0.1,
        max_lot=1.0,
        ml_tp_pips=30,
        ml_sl_pips=20,
        is_enabled=is_enabled,
    )
    session.add(cfg)
    await session.commit()
    return cfg


@pytest_asyncio.fixture
async def two_accounts(db_session):
    """账号 111 活跃，账号 222 非活跃 —— 模拟切换后的状态。"""
    await _seed_account(db_session, 111, active=True)
    await _seed_account(db_session, 222, active=False)
    return db_session


# ─── 服务层：账号解析与查询隔离 ────────────────────────────────────────────────


class TestAccountResolution:
    @pytest.mark.asyncio
    async def test_returns_active_login(self, two_accounts):
        assert await svc.get_current_account_login(two_accounts) == "111"

    @pytest.mark.asyncio
    async def test_returns_zero_without_active_account(self, db_session):
        assert await svc.get_current_account_login(db_session) == "0"

    @pytest.mark.asyncio
    async def test_ignores_deleted_active_account(self, db_session):
        acct = await _seed_account(db_session, 333, active=True)
        acct.is_deleted = True
        await db_session.commit()
        assert await svc.get_current_account_login(db_session) == "0"


class TestScopedQueries:
    @pytest.mark.asyncio
    async def test_list_scoped_to_active_account(self, two_accounts):
        await _seed_symbol(two_accounts, "GOLD", "111")
        await _seed_symbol(two_accounts, "XAUUSD", "222")
        symbols = [c.symbol for c in await svc.list_configs(two_accounts)]
        assert symbols == ["GOLD"]

    @pytest.mark.asyncio
    async def test_get_scoped_to_active_account(self, two_accounts):
        await _seed_symbol(two_accounts, "GOLD", "222")
        assert await svc.get_config(two_accounts, "GOLD") is None
        assert await svc.get_config(two_accounts, "GOLD", account_login="222") is not None

    @pytest.mark.asyncio
    async def test_same_symbol_allowed_on_two_accounts(self, two_accounts):
        """同 canonical 名在不同账号下各有一行 —— 唯一约束是 (account_login, symbol)。"""
        await _seed_symbol(two_accounts, "GOLD", "111", broker_alias="GOLD_")
        await _seed_symbol(two_accounts, "GOLD", "222", broker_alias="GOLD")
        rows = (await two_accounts.execute(
            SymbolConfig.__table__.select().where(SymbolConfig.symbol == "GOLD")
        )).fetchall()
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_list_filters_disabled_when_requested(self, two_accounts):
        """include_disabled=False 只返回启用品种（line 81 分支）。"""
        await _seed_symbol(two_accounts, "GOLD", "111", is_enabled=True)
        await _seed_symbol(two_accounts, "SILVER", "111", is_enabled=False)
        enabled = [c.symbol for c in await svc.list_configs(two_accounts, include_disabled=False)]
        assert enabled == ["GOLD"]

    @pytest.mark.asyncio
    async def test_list_disabled_filter_scoped_to_account(self, two_accounts):
        """include_disabled=False 与账号过滤叠加：非活跃账号 222 的启用品种不串入。"""
        await _seed_symbol(two_accounts, "GOLD", "111", is_enabled=True)
        await _seed_symbol(two_accounts, "SILVER", "222", is_enabled=True)  # 222 非活跃
        enabled = [c.symbol for c in await svc.list_configs(two_accounts, include_disabled=False)]
        assert enabled == ["GOLD"]

    @pytest.mark.asyncio
    async def test_list_explicit_account_login_bypasses_active_resolution(self, two_accounts):
        """显式传 account_login 时不解析活跃账号，直接按指定账号过滤。"""
        await _seed_symbol(two_accounts, "GOLD", "111", is_enabled=True)
        await _seed_symbol(two_accounts, "XAUUSD", "222", is_enabled=True)
        # 显式指定非活跃账号 222 —— 不得被"只查活跃账号"的默认行为拦截
        symbols = [c.symbol for c in await svc.list_configs(two_accounts, account_login="222")]
        assert symbols == ["XAUUSD"]
        # 对照：默认（不传）仍解析活跃账号 111
        default_symbols = [c.symbol for c in await svc.list_configs(two_accounts)]
        assert default_symbols == ["GOLD"]


# ─── API：create 归属与冲突检查 ────────────────────────────────────────────────


def _build_app(db_session, connector=None, redis_client=None) -> FastAPI:
    app = FastAPI()
    app.include_router(symbols_routes.router)

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.state.connector = connector
    app.state.redis = redis_client
    return app


def _broker_spec_ok():
    return {
        "success": True,
        "data": {
            "symbol": "EURUSDm",
            "digits": 5,
            "point": 0.00001,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "trade_contract_size": 100000.0,
            "trade_tick_size": 0.00001,
            "trade_tick_value": 1.0,
            "visible": True,
        },
    }


@pytest_asyncio.fixture
async def api_client(two_accounts):
    connector = AsyncMock()
    connector.get_symbol_spec.return_value = _broker_spec_ok()
    connector.list_symbols.return_value = {
        "success": True,
        "data": {
            "items": [
                {
                    "symbol": "GOLD",
                    "path": "Metals",
                    "description": "Gold",
                    "digits": 2,
                    "point": 0.01,
                    "trade_contract_size": 100.0,
                    "volume_min": 0.01,
                    "volume_max": 100.0,
                    "volume_step": 0.01,
                }
            ]
        },
    }
    app = _build_app(two_accounts, connector=connector)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestCreateScoping:
    @pytest.mark.asyncio
    async def test_create_assigns_active_account(self, api_client, two_accounts):
        resp = await api_client.post(
            "/api/symbols",
            json={
                "symbol": "EURUSD",
                "broker_alias": "EURUSDm",
                "display_name": "Euro/Dollar",
                "pip_value": 0.0001,
                "price_decimals": 5,
                "default_lot": 0.1,
                "max_lot": 2.0,
                "ml_tp_pips": 30,
                "ml_sl_pips": 20,
            },
        )
        assert resp.status_code == 200, resp.text
        cfg = await svc.get_config(two_accounts, "EURUSD")
        assert cfg is not None
        assert cfg.account_login == "111"

    @pytest.mark.asyncio
    async def test_alias_collision_only_within_account(self, api_client, two_accounts):
        """222 账号已占用别名 EURUSDm —— 活跃账号 111 创建同名别名不应误判冲突。"""
        await _seed_symbol(two_accounts, "OTHER", "222", broker_alias="EURUSDm")
        resp = await api_client.post(
            "/api/symbols",
            json={
                "symbol": "EURUSD",
                "broker_alias": "EURUSDm",
                "display_name": "Euro/Dollar",
                "pip_value": 0.0001,
                "price_decimals": 5,
                "default_lot": 0.1,
                "max_lot": 2.0,
                "ml_tp_pips": 30,
                "ml_sl_pips": 20,
            },
        )
        assert resp.status_code == 200, resp.text

    @pytest.mark.asyncio
    async def test_alias_collision_detected_within_account(self, api_client, two_accounts):
        """账号内已有品种的符号名恰为新行的别名（EURUSDm）→ 409。"""
        await _seed_symbol(two_accounts, "EURUSDm", "111")
        resp = await api_client.post(
            "/api/symbols",
            json={
                "symbol": "EURUSD",
                "broker_alias": "EURUSDm",
                "display_name": "Euro/Dollar",
                "pip_value": 0.0001,
                "price_decimals": 5,
                "default_lot": 0.1,
                "max_lot": 2.0,
                "ml_tp_pips": 30,
                "ml_sl_pips": 20,
            },
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_duplicate_rejected_within_account(self, api_client):
        payload = {
            "symbol": "EURUSD",
            "broker_alias": "EURUSDm",
            "display_name": "Euro/Dollar",
            "pip_value": 0.0001,
            "price_decimals": 5,
            "default_lot": 0.1,
            "max_lot": 2.0,
            "ml_tp_pips": 30,
            "ml_sl_pips": 20,
        }
        resp = await api_client.post("/api/symbols", json=payload)
        assert resp.status_code == 200
        resp2 = await api_client.post("/api/symbols", json=payload)
        assert resp2.status_code == 409


# ─── broker-catalog：缓存 key 按登录号隔离 ───────────────────────────────────


class TestCatalogCacheKey:
    @pytest.mark.asyncio
    async def test_cache_key_contains_active_login(self, two_accounts, redis_client):
        connector = AsyncMock()
        connector.list_symbols.return_value = {
            "success": True,
            "data": {"items": []},
        }
        app = _build_app(two_accounts, connector=connector, redis_client=redis_client)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/symbols/broker-catalog")
        assert resp.status_code == 200, resp.text
        keys = await redis_client.keys("xm:catalog:v2:*")
        assert keys and all(k.decode().endswith(":111") for k in keys)

    @pytest.mark.asyncio
    async def test_db_degraded_bypasses_cache(self, two_accounts, redis_client, monkeypatch):
        """DB 挂时（SQLAlchemyError）退化到 '0' 但仍返回目录，且**不写缓存**。

        评审问题 6：收窄异常只捕 DB 层错误；退化目录直接 _fetch 绕过缓存，
        避免把退化结果写进缓存污染后续请求。
        """
        from sqlalchemy.exc import SQLAlchemyError

        connector = AsyncMock()
        connector.list_symbols.return_value = {
            "success": True,
            "data": {"items": []},
        }
        # mock get_current_account_login 抛 DB 层异常（broker_catalog 经 svc. 调用）
        async def _boom(*args, **kwargs):
            raise SQLAlchemyError("db down")

        monkeypatch.setattr(svc, "get_current_account_login", _boom)
        app = _build_app(two_accounts, connector=connector, redis_client=redis_client)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/symbols/broker-catalog")
        assert resp.status_code == 200, resp.text
        # 退化路径不写缓存 —— 无任何 xm:catalog:v2:* key
        keys = await redis_client.keys("xm:catalog:v2:*")
        assert keys == []


# ─── reload_engines：静态兜底仅在 DB 未同步时启用 ────────────────────────────


@pytest.fixture
def _restore_symbol_profiles():
    """SYMBOL_PROFILES 是进程级共享 dict（manager 持同一对象引用），就地清空后
    必须恢复，否则污染同进程后续测试（test_api_symbols 的静态兜底用例等）。"""
    import app.config as cfg

    saved = dict(cfg.SYMBOL_PROFILES)
    yield
    cfg.SYMBOL_PROFILES.clear()
    cfg.SYMBOL_PROFILES.update(saved)


@pytest.mark.usefixtures("_restore_symbol_profiles")
class TestReloadEnginesFallback:
    def _make_manager(self):
        from app.bot.manager import BotManager

        return BotManager(connector=AsyncMock(), db_session=AsyncMock(), redis_client=AsyncMock())

    @pytest.mark.asyncio
    async def test_no_fallback_when_db_synced(self, monkeypatch):
        import app.config as cfg

        mgr = self._make_manager()
        mgr.engines.clear()  # 构造器会按静态兜底预建引擎，测试只关心 reload 行为
        # 模拟 apply_db_symbol_profiles({}) 后的同步状态（in-place，manager 共享同一 dict）
        cfg.SYMBOL_PROFILES.clear()
        monkeypatch.setattr(cfg, "SYMBOL_PROFILES_DB_SYNCED", True)
        summary = await mgr.reload_engines()
        assert summary["active"] == []
        assert mgr.engines == {}

    @pytest.mark.asyncio
    async def test_static_fallback_when_db_never_synced(self, monkeypatch):
        import app.config as cfg

        cfg.SYMBOL_PROFILES.clear()
        monkeypatch.setattr(cfg, "SYMBOL_PROFILES_DB_SYNCED", False)
        mgr = self._make_manager()
        mgr.engines.clear()
        mgr._build_engine = AsyncMock(side_effect=lambda s, p: AsyncMock(symbol=s))
        summary = await mgr.reload_engines()
        assert sorted(summary["active"]) == sorted(cfg.settings.symbol_list)
