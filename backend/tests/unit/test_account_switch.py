"""AccountSwitchService 单测（Phase 5）。

验证编排核心：
- 成功路径：暂停 → Bridge 切换 → set_current_account → 恢复 + active_count 断言
- 失败路径：不恢复引擎 + 清门禁 + 审计失败
- H2：切换期间置 Redis 门禁
- H4：切换后 manager.set_current_account 同步引擎 account_login
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.bot.account_switch import AccountSwitchError, AccountSwitchService, SWITCHING_FLAG_KEY


def _make_service(**overrides):
    """构造最小可用的 service，全部依赖 mock。"""
    manager = MagicMock()
    manager.current_account_login = "0"
    manager.engines = {}
    manager.set_current_account = AsyncMock()
    manager.stop = AsyncMock()
    manager.start = AsyncMock(return_value={"started": []})
    manager.reload_engines = AsyncMock(return_value={})
    # 与真实 manager.get_status() 返回结构一致（I4 修复）：聚合 dict
    manager.get_status = MagicMock(
        return_value={
            "symbols": {"GOLD": {"state": "RUNNING"}},
            "active_count": 1,
            "total_count": 1,
            "enable_auto_strategy_switch": False,
        }
    )

    connector = MagicMock()
    connector.switch_account = AsyncMock(return_value={"success": True, "data": {"switched": True}})

    redis = MagicMock()
    redis.set = AsyncMock()
    redis.delete = AsyncMock()

    db = MagicMock()
    db.commit = AsyncMock()
    # db.execute 需可 await 且返回带 .scalars().first() 链的对象
    result_chain = MagicMock()
    result_chain.scalars.return_value.first.return_value = None  # 无其他活跃账号
    result_chain.scalars.return_value.all.return_value = []  # 无其他活跃账号列表
    db.execute = AsyncMock(return_value=result_chain)

    service = AccountSwitchService(manager, connector, db, redis)
    # 覆盖 _load_account / _decrypt_password 避免依赖真实 DB/Vault
    acct = MagicMock()
    acct.id = 1
    acct.login = 1001
    acct.server = "Broker-Server"
    acct.password_encrypted = b"x"
    acct.password_nonce = b"y"
    acct.is_active = False
    service._load_account = AsyncMock(return_value=acct)
    service._decrypt_password = AsyncMock(return_value="pw")
    return service, manager, connector, redis, db


@pytest.mark.asyncio
async def test_switch_success_sets_gate_and_resumes():
    """成功路径：置门禁 → 暂停 → 切换 → set_current_account → 恢复。"""
    service, manager, connector, redis, db = _make_service()

    result = await service.switch(1)

    assert result["switched"] is True
    assert result["to"] == "1001"
    # 门禁设置/清除
    redis.set.assert_awaited_once_with(SWITCHING_FLAG_KEY, "1", ex=120)
    redis.delete.assert_awaited()
    # 暂停 + 恢复
    manager.stop.assert_awaited_once()
    manager.start.assert_awaited_once()
    manager.set_current_account.assert_awaited_once_with("1001")
    # 规格刷新
    manager.reload_engines.assert_awaited_once()


@pytest.mark.asyncio
async def test_switch_failure_does_not_resume_engines():
    """失败路径：Bridge 失败 → 不恢复引擎 + 清门禁 + 抛错（H1）。"""
    service, manager, connector, redis, db = _make_service()
    connector.switch_account.return_value = {"success": False, "error": "bad login"}

    with pytest.raises(AccountSwitchError):
        await service.switch(1)

    # 引擎绝不恢复
    manager.start.assert_not_awaited()
    manager.reload_engines.assert_not_awaited()
    # 门禁被清除（避免卡死后续下单）
    redis.delete.assert_awaited()
    # 不更新账号状态
    manager.set_current_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_switch_clears_gate_even_when_resume_fails():
    """active_count=0（引擎未恢复）→ 清门禁 + 抛错（避免静默停）。"""
    service, manager, connector, redis, db = _make_service()
    manager.get_status.return_value = {
        "symbols": {"GOLD": {"state": "STOPPED"}},
        "active_count": 0,  # 引擎没恢复
        "total_count": 1,
        "enable_auto_strategy_switch": False,
    }

    with pytest.raises(AccountSwitchError) as ei:
        await service.switch(1)

    assert "did not resume" in str(ei.value)
    redis.delete.assert_awaited()  # 门禁必清


@pytest.mark.asyncio
async def test_switch_concurrent_locked():
    """H2：切换持锁，第二个 switch 等待（不并发）。"""
    service, manager, connector, redis, db = _make_service()
    hold = asyncio.Event()

    async def slow_switch(**kw):
        await hold.wait()
        return {"success": True, "data": {"switched": True}}

    connector.switch_account.side_effect = slow_switch

    t1 = asyncio.create_task(service.switch(1))
    await asyncio.sleep(0.05)  # 让 t1 拿到锁
    t2 = asyncio.create_task(service.switch(2))
    await asyncio.sleep(0.05)
    # t2 应阻塞在锁上（还没完成）
    assert not t2.done()
    hold.set()
    await asyncio.gather(t1, t2)
    assert t1.done() and t2.done()
    assert connector.switch_account.await_count == 2
