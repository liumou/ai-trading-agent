"""order_preflight 共享硬闸门测试(Phase 2)。

preflight 是 AI/MCP 通道与手动通道共用的闸门唯一真相源。本测试锁定:
- strict_symbol=True(手动):symbol 不可解析/无 volume 配置 → fail-closed
- strict_symbol=False(AI):保持历史 fail-open 语义(无 manager 继续、
  无 volume 配置跳过归一)
- 行情任一拉取失败 → 拒单(没有数据就没有防线)
- guardrails 拒绝原样透传;micro cap 由 preflight 落实;live 需 llm_allow_live
- spread history key 按 symbol 分(跨品种污染修复)
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.bot.manager as manager_mod
from app.config import SYMBOL_PROFILES, settings
from app.services.order_preflight import preflight_order


@pytest.fixture(autouse=True)
def _profiles():
    snapshot = {k: dict(v) for k, v in SYMBOL_PROFILES.items()}
    yield
    SYMBOL_PROFILES.clear()
    SYMBOL_PROFILES.update(snapshot)


@pytest.fixture
def ok_connector():
    c = AsyncMock()
    c.get_account.return_value = {"success": True, "data": {"balance": 10000.0, "profit": 0.0}}
    c.get_positions.return_value = {"success": True, "data": []}
    c.get_tick.return_value = {"success": True, "data": {"bid": 2000.0, "ask": 2000.5}}
    return c


@pytest.fixture
def ok_guardrails():
    g = MagicMock()
    g.validate_order = AsyncMock(return_value=SimpleNamespace(allowed=True, reason="ok"))
    g.get_persisted_rollout_mode = AsyncMock(return_value="live")
    g.record_order_opened = AsyncMock()
    return g


def _allow_manager(monkeypatch, key="GOLD"):
    mgr = MagicMock()
    mgr.resolve_symbol.return_value = key
    mgr.engines = {key: MagicMock()}
    monkeypatch.setattr(manager_mod, "get_global_manager", lambda: mgr)
    return mgr


def _deny_manager(monkeypatch):
    monkeypatch.setattr(manager_mod, "get_global_manager", lambda: None)


# ─── strict(手动通道)语义 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_strict_rejects_when_no_manager(ok_connector, redis_client, ok_guardrails, monkeypatch):
    _deny_manager(monkeypatch)
    pf = await preflight_order(ok_connector, redis_client, ok_guardrails,
                               "GOLD", "BUY", 0.1, 1900.0, 2100.0, strict_symbol=True)
    assert pf.ok is False and pf.kind == "symbol"
    ok_connector.get_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_strict_rejects_unresolvable_symbol(ok_connector, redis_client, ok_guardrails, monkeypatch):
    mgr = MagicMock()
    mgr.resolve_symbol.return_value = None
    mgr.engines = {}
    monkeypatch.setattr(manager_mod, "get_global_manager", lambda: mgr)
    pf = await preflight_order(ok_connector, redis_client, ok_guardrails,
                               "JUNK", "BUY", 0.1, 1900.0, 2100.0, strict_symbol=True)
    assert pf.ok is False and pf.kind == "symbol"


@pytest.mark.asyncio
async def test_strict_rejects_missing_volume_config(ok_connector, redis_client, ok_guardrails, monkeypatch):
    """无 volume 配置 = 防线不存在 = 拒绝(手动 fail-closed)。"""
    _allow_manager(monkeypatch)
    SYMBOL_PROFILES["GOLD"] = {"pip_value": 1.0}  # 无 volume_min
    pf = await preflight_order(ok_connector, redis_client, ok_guardrails,
                               "GOLD", "BUY", 0.1, 1900.0, 2100.0, strict_symbol=True)
    assert pf.ok is False and pf.kind == "lot"


# ─── 非 strict(AI 通道)语义保持 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_strict_continues_without_manager(ok_connector, redis_client, ok_guardrails, monkeypatch):
    _deny_manager(monkeypatch)
    SYMBOL_PROFILES["GOLD"] = {"pip_value": 1.0, "volume_min": 0.01, "volume_step": 0.01}
    pf = await preflight_order(ok_connector, redis_client, ok_guardrails,
                               "GOLD", "BUY", 0.1, 1900.0, 2100.0, strict_symbol=False)
    assert pf.ok is True


@pytest.mark.asyncio
async def test_non_strict_skips_volume_guard_without_config(ok_connector, redis_client, ok_guardrails, monkeypatch):
    """存量行为:无 volume 配置不阻断下单(引擎补齐前的兼容)。"""
    _allow_manager(monkeypatch)
    SYMBOL_PROFILES["GOLD"] = {"pip_value": 1.0}
    pf = await preflight_order(ok_connector, redis_client, ok_guardrails,
                               "GOLD", "BUY", 0.03, 1900.0, 2100.0, strict_symbol=False)
    assert pf.ok is True and pf.ctx.lot == pytest.approx(0.03)


# ─── 行情/闸门/rollout ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_data_fetch_failure_rejects(ok_connector, redis_client, ok_guardrails, monkeypatch):
    _allow_manager(monkeypatch)
    SYMBOL_PROFILES["GOLD"] = {"pip_value": 1.0, "volume_min": 0.01, "volume_step": 0.01}
    ok_connector.get_tick.return_value = {"success": False, "error": "bridge down"}
    pf = await preflight_order(ok_connector, redis_client, ok_guardrails,
                               "GOLD", "BUY", 0.1, 1900.0, 2100.0, strict_symbol=True)
    assert pf.ok is False and pf.kind == "data_fetch"
    ok_guardrails.validate_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_guardrail_rejection_passthrough(ok_connector, redis_client, ok_guardrails, monkeypatch):
    _allow_manager(monkeypatch)
    SYMBOL_PROFILES["GOLD"] = {"pip_value": 1.0, "volume_min": 0.01, "volume_step": 0.01}
    ok_guardrails.validate_order.return_value = SimpleNamespace(allowed=False, reason="daily loss limit")
    pf = await preflight_order(ok_connector, redis_client, ok_guardrails,
                               "GOLD", "BUY", 0.1, 1900.0, 2100.0, strict_symbol=True)
    assert pf.ok is False and pf.kind == "guardrail"
    assert pf.reason == "daily loss limit"


@pytest.mark.asyncio
async def test_lot_floored_to_grid(ok_connector, redis_client, ok_guardrails, monkeypatch):
    _allow_manager(monkeypatch)
    SYMBOL_PROFILES["GOLD"] = {"pip_value": 1.0, "volume_min": 0.01, "volume_step": 0.1}
    pf = await preflight_order(ok_connector, redis_client, ok_guardrails,
                               "GOLD", "BUY", 0.55, 1900.0, 2100.0, strict_symbol=True)
    assert pf.ok is True
    assert pf.ctx.lot == pytest.approx(0.5)
    assert pf.ctx.base_lot == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_micro_mode_caps_lot(ok_connector, redis_client, ok_guardrails, monkeypatch):
    _allow_manager(monkeypatch)
    SYMBOL_PROFILES["GOLD"] = {"pip_value": 1.0, "volume_min": 0.01, "volume_step": 0.01}
    ok_guardrails.get_persisted_rollout_mode = AsyncMock(return_value="micro")
    pf = await preflight_order(ok_connector, redis_client, ok_guardrails,
                               "GOLD", "BUY", 0.5, 1900.0, 2100.0, strict_symbol=True)
    assert pf.ok is True
    assert pf.ctx.lot == pytest.approx(0.01)  # MICRO_MAX_LOT
    assert pf.ctx.base_lot == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_live_requires_llm_allow_live(ok_connector, redis_client, ok_guardrails, monkeypatch):
    _allow_manager(monkeypatch)
    SYMBOL_PROFILES["GOLD"] = {"pip_value": 1.0, "volume_min": 0.01, "volume_step": 0.01}
    monkeypatch.setattr(settings, "llm_allow_live", False)
    pf = await preflight_order(ok_connector, redis_client, ok_guardrails,
                               "GOLD", "BUY", 0.1, 1900.0, 2100.0, strict_symbol=True)
    assert pf.ok is False and pf.kind == "live_auth"


@pytest.mark.asyncio
async def test_spread_history_key_per_symbol(ok_connector, redis_client, ok_guardrails, monkeypatch):
    """评审 M-2:spread history 必须按 symbol 分 key(全局 key 跨品种污染)。"""
    _allow_manager(monkeypatch)
    SYMBOL_PROFILES["GOLD"] = {"pip_value": 1.0, "volume_min": 0.01, "volume_step": 0.01}
    await preflight_order(ok_connector, redis_client, ok_guardrails,
                          "GOLD", "BUY", 0.1, 1900.0, 2100.0, strict_symbol=True)
    assert await redis_client.llen("guardrails:spread_history:GOLD") == 1
    assert await redis_client.exists("guardrails:spread_history") == 0


@pytest.mark.asyncio
async def test_guardrail_positions_normalized_to_canonical(ok_connector, redis_client, ok_guardrails, monkeypatch):
    """C4 回归:传给 guardrails.validate_order 的持仓 symbol 必须归一化为
    canonical 引擎键 —— 否则 broker 别名(如 GOLD_)与 canonical 比较永不匹配,
    MAX_CONCURRENT_PER_SYMBOL 静默失效。"""
    _allow_manager(monkeypatch)
    SYMBOL_PROFILES["GOLD"] = {"pip_value": 1.0, "volume_min": 0.01, "volume_step": 0.01}
    # DB 加载后别名条目(生产 load_profiles_from_db 会注册):GOLD_ → canonical GOLD
    SYMBOL_PROFILES["GOLD_"] = {"canonical": "GOLD"}
    # broker 返回的持仓带别名 GOLD_
    ok_connector.get_positions.return_value = {
        "success": True,
        "data": [{"ticket": 1, "symbol": "GOLD_", "type": "BUY", "volume": 0.1}],
    }
    captured = {}

    async def _capture(**kwargs):
        captured["positions"] = kwargs.get("current_positions")
        return SimpleNamespace(allowed=True, reason="ok")

    ok_guardrails.validate_order = _capture
    pf = await preflight_order(ok_connector, redis_client, ok_guardrails,
                               "GOLD", "BUY", 0.1, 1900.0, 2100.0, strict_symbol=True)
    assert pf.ok is True
    # 断言传给 validate_order 的持仓 symbol 已归一化为 GOLD(非 GOLD_)
    assert captured["positions"][0]["symbol"] == "GOLD"
    # ctx.positions 同样归一化(供 LLM snapshot 使用)
    assert pf.ctx.positions[0]["symbol"] == "GOLD"


@pytest.mark.asyncio
async def test_account_scoped_daily_pnl_key(ok_connector, redis_client, ok_guardrails, monkeypatch):
    """C1 回归:传入 account_login 时日亏读取必须走带账号前缀的 key
    (circuit:acc:{login}:daily_pnl:{symbol}),与引擎切换后重建的熔断器对齐
    —— 否则读到旧前缀的空 key,日亏闸门静默失效。"""
    _allow_manager(monkeypatch)
    SYMBOL_PROFILES["GOLD"] = {"pip_value": 1.0, "volume_min": 0.01, "volume_step": 0.01}
    # 引擎切换后写入的带账号前缀 key
    await redis_client.set("circuit:acc:123:daily_pnl:GOLD", "-500")
    # 旧前缀 key 故意不写(模拟切换后为空)
    captured = {}

    async def _capture(**kwargs):
        captured["daily_pnl"] = kwargs.get("daily_pnl")
        return SimpleNamespace(allowed=True, reason="ok")

    ok_guardrails.validate_order = _capture
    pf = await preflight_order(ok_connector, redis_client, ok_guardrails,
                               "GOLD", "BUY", 0.1, 1900.0, 2100.0, strict_symbol=True,
                               account_login="123")
    assert pf.ok is True
    # 读到带账号前缀的日亏(-500),而非旧前缀的空 key(0)
    assert captured["daily_pnl"] == -500
    assert pf.ctx.daily_pnl == -500


@pytest.mark.asyncio
async def test_default_account_login_aligns_with_bootstrap(ok_connector, redis_client, ok_guardrails, monkeypatch):
    """C1 引导期回归:account_login="0"(默认占位)时必须落到无前缀 key
    (circuit:daily_pnl:{symbol}),与引擎引导期无前缀构造对齐 ——
    否则 preflight 读 circuit:acc:0: 而引擎写 circuit:,日亏闸门静默失效。"""
    _allow_manager(monkeypatch)
    SYMBOL_PROFILES["GOLD"] = {"pip_value": 1.0, "volume_min": 0.01, "volume_step": 0.01}
    # 引擎引导期(未切换)写入的无前缀 key
    await redis_client.set("circuit:daily_pnl:GOLD", "-300")
    # circuit:acc:0: 前缀 key 故意不写(验证不读它)
    captured = {}

    async def _capture(**kwargs):
        captured["daily_pnl"] = kwargs.get("daily_pnl")
        return SimpleNamespace(allowed=True, reason="ok")

    ok_guardrails.validate_order = _capture
    pf = await preflight_order(ok_connector, redis_client, ok_guardrails,
                               "GOLD", "BUY", 0.1, 1900.0, 2100.0, strict_symbol=True,
                               account_login="0")
    assert pf.ok is True
    # 读到无前缀的日亏(-300),而非 circuit:acc:0: 的空 key(0)
    assert captured["daily_pnl"] == -300
    assert pf.ctx.daily_pnl == -300
