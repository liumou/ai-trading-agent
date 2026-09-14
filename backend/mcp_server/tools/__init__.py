"""Shared utilities for MCP tools."""

import os


def backend_url() -> str:
    """Get the backend API URL from env or default.

    默认 8002，与 start-backend.sh / start-backend.bat 的监听端口一致。
    此前默认 8000，而本地后端实际跑在 8002 —— MCP 工具（sentiment / P&L /
    history）回调 backend 时全部 connection refused，AI 报告就会出现
    “数据源暂不可用”。Railway 用 PORT 覆盖，不受影响。
    """
    port = os.environ.get("PORT", "8002")
    return os.environ.get("BACKEND_URL", f"http://localhost:{port}")


def auth_headers() -> dict[str, str]:
    """Return Authorization header for internal backend calls.

    Reads INTERNAL_API_TOKEN set by backend lifespan. Empty when auth disabled.
    """
    token = os.environ.get("INTERNAL_API_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def init_mcp_tools(redis_client) -> None:
    """Initialize all MCP tools that require Redis. Idempotent — safe to call once at startup."""
    from mcp_server.tools.broker import init_broker
    from mcp_server.tools.session import init_session
    from mcp_server.tools.strategy_switch import init_strategy_switch

    init_broker(redis_client)
    init_session(redis_client)
    init_strategy_switch(redis_client)
