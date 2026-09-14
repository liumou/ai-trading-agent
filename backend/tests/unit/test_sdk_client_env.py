"""回归测试 — MCP stdio 子进程的 env 构造。

MCP server 是独立进程，需要 DATABASE_URL 才能加载 symbol_configs 里的券商别名
（GOLD → GOLD_）。缺了它，行情工具会以规范名打桥，AI 分析报 "No tick/OHLCV data"。

同时锁定一个易错点：env 里传空字符串会覆盖子进程从 .env 读到的真实值
（pydantic-settings 把空字符串当作显式设置），所以**只在有值时才透传**。
"""

import importlib
import os

import pytest


def _build_env(monkeypatch, database_url: str | None = None) -> dict:
    """按给定 DATABASE_URL 环境构造 MCP server env。"""
    monkeypatch.setenv("REDIS_URL", "redis://example:6379")
    monkeypatch.setenv("MT5_BRIDGE_URL", "http://bridge.example:8001")
    if database_url is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("DATABASE_URL", database_url)

    import mcp_server.sdk_client as sdk

    importlib.reload(sdk)
    return sdk._get_mcp_server_config()["trading-agent-tools"]["env"]


def test_database_url_is_propagated_when_set(monkeypatch):
    """有 DATABASE_URL 时必须注入子进程（否则别名加载不到）。"""
    env = _build_env(monkeypatch, "postgresql+asyncpg://u:p@host:5432/db")
    assert env["DATABASE_URL"] == "postgresql+asyncpg://u:p@host:5432/db"


def test_database_url_omitted_when_unset(monkeypatch):
    """没有 DATABASE_URL 时不应塞空字符串 —— 空串会覆盖子进程 .env 的真实值。"""
    env = _build_env(monkeypatch, None)
    assert "DATABASE_URL" not in env


def test_empty_database_url_is_not_injected(monkeypatch):
    """显式空值视为"未配置"，必须省略而非透传空串。"""
    env = _build_env(monkeypatch, "")
    assert "DATABASE_URL" not in env


def test_core_endpoints_always_present(monkeypatch):
    """桥与 Redis 地址始终注入，避免回归。"""
    env = _build_env(monkeypatch, None)
    assert env["MT5_BRIDGE_URL"] == "http://bridge.example:8001"
    assert env["REDIS_URL"] == "redis://example:6379"


@pytest.mark.parametrize("key", ["REDIS_URL", "MT5_BRIDGE_URL"])
def test_env_never_leaks_other_process_vars(monkeypatch, key):
    """env 是白名单构造，不应把宿主进程的无关变量泄漏进子进程。"""
    monkeypatch.setenv("SOME_SECRET", "should-not-appear")
    env = _build_env(monkeypatch, None)
    assert "SOME_SECRET" not in env
    assert key in env
    assert os.environ["SOME_SECRET"] == "should-not-appear"
