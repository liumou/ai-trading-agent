"""回归测试 — MCP 工具回调 backend 的地址与鉴权必须正确。

背景（2026-09-14）：AI 分析报告里出现「数据源暂不可用 / 当日 sentiment 与 P&L
接口返回 502」，但行情数据（Close / ADX / RSI）是正常拿到的。

注意这与上一次修复的「品种别名 GOLD → GOLD_」是**两个不同的问题**：行情走的是
MCP → MT5BridgeConnector → 桥（HTTP 到 Windows 主机），别名修好后行情正常；而
sentiment / P&L / history / journal / memory 走的是 MCP → **backend 自身 HTTP
API**（tools.backend_url() + auth_headers()），这条链路有独立的两个 bug：

  1. 端口不一致：本地后端 start-backend.sh 监听 **8002**，而 tools.backend_url()
     默认 8000，且 PORT 从未 export 给 MCP 子进程 → connection refused。
  2. 鉴权头丢失：INTERNAL_API_TOKEN 由 backend lifespan mint 进 os.environ，但
     sdk_client 构造 MCP 子进程 env 时**只透传了 REDIS_URL / MT5_BRIDGE_URL /
     DATABASE_URL**，没带它 → auth 开启时 require_auth 直接 401。

本测试锁住这两条契约。全部离线，不联网也不需要真实后端（CI 是 ubuntu 无桥环境）。
"""

import pytest

from mcp_server import sdk_client
from mcp_server.tools import auth_headers, backend_url

@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """清空相关 env，保证每个用例从干净状态出发。"""
    for key in ("BACKEND_URL", "PORT", "INTERNAL_API_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    yield

def test_backend_url_defaults_to_8002():
    """无 env 时默认端口必须是 8002（与 start-backend.sh/.bat 一致），不是 8000。"""
    assert backend_url() == "http://localhost:8002"

def test_backend_url_honours_port_env(monkeypatch):
    """Railway 等平台通过 PORT 覆盖（子进程拿到 start-backend.sh export 的 PORT）。"""
    monkeypatch.setenv("PORT", "9000")
    assert backend_url() == "http://localhost:9000"

def test_backend_url_honours_explicit_override(monkeypatch):
    """显式 BACKEND_URL 优先级最高。"""
    monkeypatch.setenv("BACKEND_URL", "http://backend.internal:8080")
    assert backend_url() == "http://backend.internal:8080"

def test_auth_headers_empty_without_token():
    """无 token 时不发 Authorization 头（auth 关闭场景，require_auth 是 no-op）。"""
    assert auth_headers() == {}

def test_auth_headers_bearer_with_token(monkeypatch):
    """有 token 时必须带 Bearer 头，否则 require_auth 会 401。"""
    monkeypatch.setenv("INTERNAL_API_TOKEN", "jwt-abc123")
    assert auth_headers() == {"Authorization": "Bearer jwt-abc123"}

def _mcp_child_env() -> dict[str, str]:
    """取 sdk_client 为 MCP stdio 子进程构造的 env。"""
    cfg = sdk_client._get_mcp_server_config()
    return cfg[sdk_client.MCP_SERVER_NAME]["env"]

def test_mcp_child_env_carries_backend_url_and_token(monkeypatch):
    """核心契约：MCP 子进程必须同时拿到后端地址与内部 token。

    缺任一项，sentiment / P&L 类工具就会打不通，AI 报告显示「数据源暂不可用」。
    """
    monkeypatch.setenv("BACKEND_URL", "http://localhost:8002")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "jwt-xyz")

    env = _mcp_child_env()

    assert env.get("BACKEND_URL") == "http://localhost:8002"
    assert env.get("INTERNAL_API_TOKEN") == "jwt-xyz"

def test_mcp_child_env_carries_port(monkeypatch):
    """PORT 也要透传 —— 未设 BACKEND_URL 时子进程靠它拼出正确端口。"""
    monkeypatch.setenv("PORT", "8002")
    assert _mcp_child_env().get("PORT") == "8002"

def test_mcp_child_env_omits_empty_values(monkeypatch):
    """空值不得透传——空串会覆盖子进程从 .env 读到的真实值（历史坑，勿回退）。"""
    monkeypatch.setenv("BACKEND_URL", "")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "")
    monkeypatch.setenv("PORT", "")

    env = _mcp_child_env()

    assert "BACKEND_URL" not in env
    assert "INTERNAL_API_TOKEN" not in env
    assert "PORT" not in env

def test_mcp_child_env_keeps_bridge_and_db(monkeypatch):
    """补新键的同时不能挤掉原有的桥/DB 配置（防回归）。"""
    monkeypatch.setenv("REDIS_URL", "redis://example:6379/2")
    monkeypatch.setenv("MT5_BRIDGE_URL", "http://192.168.3.47:8001")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@host/db")

    env = _mcp_child_env()

    assert env["REDIS_URL"] == "redis://example:6379/2"
    assert env["MT5_BRIDGE_URL"] == "http://192.168.3.47:8001"
    assert env["DATABASE_URL"] == "postgresql+asyncpg://u:p@host/db"

def test_sentiment_tool_calls_backend_with_auth(monkeypatch):
    """端到端：get_sentiment 必须带上 Bearer 头请求后端正确地址。"""
    import httpx

    from mcp_server.tools import sentiment

    monkeypatch.setenv("BACKEND_URL", "http://localhost:8002")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "jwt-e2e")

    captured: dict = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"label": "neutral", "score": 0.0}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            captured["url"] = url
            captured["headers"] = headers
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _Client())

    import asyncio

    result = asyncio.run(sentiment.get_latest_sentiment())

    assert result["label"] == "neutral"
    assert captured["url"] == "http://localhost:8002/api/ai/sentiment"
    assert captured["headers"] == {"Authorization": "Bearer jwt-e2e"}

def test_sentiment_tool_reports_error_on_failure(monkeypatch):
    """后端不可达时返回结构化 error，而不是抛异常炸掉整个 agent 循环。"""
    import httpx

    from mcp_server.tools import sentiment

    monkeypatch.setenv("BACKEND_URL", "http://localhost:8002")

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            raise OSError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _Client())

    import asyncio

    result = asyncio.run(sentiment.get_latest_sentiment())

    assert "error" in result
    assert "connection refused" in result["error"]

def test_daily_pnl_tool_targets_backend(monkeypatch):
    """P&L 同样走 backend HTTP，地址与鉴权必须一致。"""
    import httpx

    from mcp_server.tools import history

    monkeypatch.setenv("BACKEND_URL", "http://localhost:8002")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "jwt-pnl")

    captured: dict = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"daily_pnl": 0.0}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            captured["url"] = url
            captured["headers"] = headers
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _Client())

    import asyncio

    result = asyncio.run(history.get_daily_pnl("GOLD"))

    assert result["daily_pnl"]["daily_pnl"] == 0.0
    assert captured["url"] == "http://localhost:8002/api/history/daily-pnl"
    assert captured["headers"] == {"Authorization": "Bearer jwt-pnl"}


# ─── analyze_recent_trades / get_trade_history 响应解析回归（2026-09-18）─────────
#
# 背景：后端 /api/history/trades 返回 {"trades": [...], "total": n}（包装对象），
# 而 analyze_recent_trades 曾把整个 body 当列表用，遍历 dict key（字符串）后调用
# .get() 抛 "'str' object has no attribute 'get'"，被 except 吞掉后报告出现
# 「数据缺口提示」。get_trade_history 则是把包装对象再嵌一层，产生畸形嵌套。
# 这两个用例锁住解析契约：包装对象 / 裸数组两种形状都必须正常工作。

_TRADE = {
    "id": 1,
    "ticket": 1001,
    "symbol": "GOLD_",
    "type": "SELL",
    "lot": 0.1,
    "open_price": 4360.0,
    "close_price": 4344.0,
    "profit": 16.0,
    "strategy_name": "mean_reversion",
}

def _mock_httpx_client(monkeypatch, trades_body, perf_body):
    """复用本文件的 mock 模式：替换 httpx.AsyncClient 为同步假客户端。"""
    import httpx

    responses = {"trades": trades_body, "performance": perf_body}

    class _Resp:
        status_code = 200
        def __init__(self, body):
            self._body = body
        def json(self):
            return self._body

    class _Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url, headers=None, params=None):
            key = "trades" if "history/trades" in url else "performance"
            return _Resp(responses[key])

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _Client())


def test_analyze_recent_trades_parses_wrapped_response(monkeypatch):
    """后端标准响应 {"trades": [...], "total": n} 必须被正确解包，不得抛 .get 错误。"""
    import asyncio

    from mcp_server.tools import learning

    _mock_httpx_client(monkeypatch, {"trades": [_TRADE], "total": 1}, {"total_trades": 1, "win_rate": 1.0, "total_profit": 16.0})

    result = asyncio.run(learning.analyze_recent_trades(days=7))

    assert result["trade_count"] == 1
    assert result["wins"] == 1
    assert result["win_rate"] == 1.0
    assert "error" not in result
    assert result["strategy_performance"]["mean_reversion"]["wins"] == 1


def test_analyze_recent_trades_tolerates_bare_list(monkeypatch):
    """若接口未来改成裸数组（向后兼容防御），也必须能正常解析。"""
    import asyncio

    from mcp_server.tools import learning

    _mock_httpx_client(monkeypatch, [_TRADE], {"total_trades": 1, "win_rate": 1.0, "total_profit": 16.0})

    result = asyncio.run(learning.analyze_recent_trades(days=7))

    assert result["trade_count"] == 1
    assert "error" not in result


def test_get_trade_history_unwraps_wrapped_response(monkeypatch):
    """get_trade_history 必须返回扁平 {"trades": [...]}，不得嵌套成 {"trades": {"trades": ...}}。"""
    import asyncio

    from mcp_server.tools import history

    _mock_httpx_client(monkeypatch, {"trades": [_TRADE], "total": 1}, {})

    result = asyncio.run(history.get_trade_history(days=7))

    assert result["trades"] == [_TRADE]
    assert result["total"] == 1


def test_get_trade_history_tolerates_bare_list(monkeypatch):
    """裸数组形状也兼容。"""
    import asyncio

    from mcp_server.tools import history

    _mock_httpx_client(monkeypatch, [_TRADE], {})

    result = asyncio.run(history.get_trade_history(days=7))

    assert result["trades"] == [_TRADE]
    assert result["total"] == 1
