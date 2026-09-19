"""MT5 Bridge 单测（Phase 3 / M3）。

关键点：`MetaTrader5` 仅 Windows 可 import，本机（Linux/macOS/CI）必须
在 import `main` 前注入 mock 模块。conftest.py 负责 `sys.modules` 注入。
这些测试验证切换端点与健康语义的逻辑，**不验证真实 MT5 SDK 行为**
（真实行为由 Phase 0 spike 在 VPS 上验证）。

运行（从 mt5_bridge/ 目录）：
    python -m pytest tests/ -v
"""

import types

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, mt5_mock):
    """构造 Bridge TestClient，注入 mt5 mock 的终端/账号状态。"""
    # 默认：终端存活 + 已登录账号 12345
    mt5_mock.terminal_info.return_value = object()
    acct = types.SimpleNamespace(
        login=12345, server="Broker-Server", balance=10000.0,
        equity=10100.0, margin=100.0, margin_free=10000.0,
        profit=100.0, currency="USD",
    )
    mt5_mock.account_info.return_value = acct
    mt5_mock.last_error.return_value = (0, "no error")

    from main import app

    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("terminal_ok,account_ok,expected_status", [
    (True, True, "ok"),
    (True, False, "degraded"),   # C2: 终端存活但未登录 → degraded
    (False, False, "disconnected"),
])
def test_health_logged_in_semantics(client, monkeypatch, mt5_mock, terminal_ok, account_ok, expected_status):
    """C2 修复：/health 的 status 必须反映账号登录状态，而非仅终端存活。"""
    from main import app

    mt5_mock.terminal_info.return_value = object() if terminal_ok else None
    if account_ok:
        mt5_mock.account_info.return_value = types.SimpleNamespace(login=1, server="S")
    else:
        mt5_mock.account_info.return_value = None

    with TestClient(app) as c:
        resp = c.get("/health", headers={"X-Bridge-Key": "test-key"})
    body = resp.json()
    assert resp.status_code == 200
    assert body["status"] == expected_status
    assert body["logged_in"] == account_ok
    if account_ok:
        assert body["mt5"]["login"] is not None
    else:
        assert body["mt5"] is None


def test_switch_account_success(client, mt5_mock):
    """切换成功：返回新账号快照 + previous。"""
    from main import app

    def fake_login(login, password="", server=None):
        mt5_mock.account_info.return_value = types.SimpleNamespace(
            login=login, server=server or "Broker-Server",
            balance=5000.0, equity=5100.0, currency="USD",
        )
        return True

    mt5_mock.login.side_effect = fake_login

    with TestClient(app) as c:
        resp = c.post(
            "/account/switch",
            json={"login": 99999, "password": "secret", "server": "Broker-Server"},
            headers={"X-Bridge-Key": "test-key"},
        )
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["switched"] is True
    assert body["data"]["account"]["login"] == 99999
    assert body["data"]["previous"]["login"] == 12345


def test_switch_account_failure_reports_current(client, mt5_mock):
    """切换失败：返回错误 + previous + current（不回滚，由后端决定）。"""
    from main import app

    mt5_mock.login.return_value = False
    mt5_mock.last_error.return_value = (1, "invalid login")

    with TestClient(app) as c:
        resp = c.post(
            "/account/switch",
            json={"login": 99999, "password": "wrong", "server": "Broker-Server"},
            headers={"X-Bridge-Key": "test-key"},
        )
    body = resp.json()
    assert body["success"] is False
    assert "Account switch failed" in body["error"]
    # current 应仍在原账号（login 失败通常原子保持原账号）
    assert body["data"]["current"]["login"] == 12345
    assert body["data"]["previous"]["login"] == 12345


def test_switch_account_requires_auth(client):
    """M2：/account/switch 必须强制 verify_api_key。"""
    from main import app

    with TestClient(app) as c:
        resp = c.post("/account/switch", json={"login": 1, "password": "x"})
    assert resp.status_code in (401, 422)  # 401 无 key / 422 缺 header


def test_switch_account_missing_fields(client, mt5_mock):
    """M2：缺 login/password 应 422。"""
    from main import app

    with TestClient(app) as c:
        resp = c.post(
            "/account/switch",
            json={"login": 12345},  # 缺 password
            headers={"X-Bridge-Key": "test-key"},
        )
    assert resp.status_code == 422
