"""启动时活跃账号恢复测试（manager.current_account_login 从 mt5_accounts 持久态重建）。

修复背景：manager.current_account_login 是内存态，进程重启归零为 "0"，而
mt5_accounts.is_active 是 DB 持久态。两套"当前账号"定义不一致会让重启后的
历史复盘/持仓归属全部落到 '0' 桶（复盘徽章丢失的复现路径）。

重点验证除幂等性外的安全性质：查询失败必须回滚共享长生命周期 db_session，
否则会话停留在失败事务里（asyncpg InFailedSQLTransactionError），启动后半段
服务全部打不开事务。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from sqlalchemy import insert

from app.db.models import MT5Account
from app.main import _restore_active_account


class _FakeManager:
    """记录 set_current_account 调用；不需要真实引擎。"""

    def __init__(self) -> None:
        self.current_account_login = "0"
        self.calls: list[str] = []

    async def set_current_account(self, account_login: str) -> None:
        self.current_account_login = account_login
        self.calls.append(account_login)


async def _add_account(
    db_session, login: int, *, is_active: bool = True, is_deleted: bool = False,
) -> None:
    await db_session.execute(insert(MT5Account).values(
        login=login, password_encrypted=b"x", password_nonce=b"y",
        is_active=is_active, is_deleted=is_deleted,
    ))
    await db_session.commit()


async def test_restores_active_account_from_db(db_session):
    """DB 有唯一 is_active 账号 → 恢复到 manager，且只调用一次。"""
    await _add_account(db_session, 110351809)
    mgr = _FakeManager()

    await _restore_active_account(mgr, db_session)

    assert mgr.current_account_login == "110351809"
    assert mgr.calls == ["110351809"]


async def test_no_active_account_leaves_default(db_session):
    """无活跃账号 → 保持 "0" 默认，不调用 set_current_account。"""
    await _add_account(db_session, 99999999, is_active=False)
    mgr = _FakeManager()

    await _restore_active_account(mgr, db_session)

    assert mgr.current_account_login == "0"
    assert mgr.calls == []


async def test_excludes_deleted_accounts(db_session):
    """is_deleted 账号即使 is_active 也不得恢复。"""
    await _add_account(db_session, 88888888, is_active=True, is_deleted=True)
    await _add_account(db_session, 77777777, is_active=True, is_deleted=False)
    mgr = _FakeManager()

    await _restore_active_account(mgr, db_session)

    assert mgr.current_account_login == "77777777"


async def test_deleted_active_account_not_reactivated(db_session):
    """软删账号即使 is_active 也不得被恢复为交易账号。

    这是启动侧与查询侧的有意区分：查询侧（复盘回看）要放行软删账号，
    因为复盘挂在 MT5 登录号而非 mt5_accounts.id 上；但启动恢复侧不能放行，
    否则用户标记为"不再使用"的账号会被重启自动重新激活为可交易账号。
    """
    await _add_account(db_session, 10000001, is_active=True, is_deleted=True)
    await _add_account(db_session, 10000002, is_active=True, is_deleted=False)
    mgr = _FakeManager()

    await _restore_active_account(mgr, db_session)

    assert mgr.current_account_login == "10000002"


async def test_deleted_account_only_falls_back_to_default(db_session):
    """唯一活跃账号已被软删 → 不恢复，保持 '0' 默认（不激活废弃账号）。"""
    await _add_account(db_session, 10000003, is_active=True, is_deleted=True)
    mgr = _FakeManager()

    await _restore_active_account(mgr, db_session)

    assert mgr.current_account_login == "0"
    assert mgr.calls == []


async def test_empty_table_is_noop(db_session):
    """mt5_accounts 为空（首次部署/本地开发）→ 静默保持默认。"""
    mgr = _FakeManager()
    await _restore_active_account(mgr, db_session)
    assert mgr.current_account_login == "0"
    assert mgr.calls == []


async def test_success_path_also_rolls_back(db_session):
    """成功路径也回滚 —— SELECT 会 autobegin 事务，不清理会一直开着。

    只读操作，回滚不撤销任何写入，只是让共享连接回到 idle 态。
    """
    await _add_account(db_session, 42424242)
    mgr = _FakeManager()

    await _restore_active_account(mgr, db_session)

    assert mgr.current_account_login == "42424242"
    # 回滚后共享 session 不应停留在活动事务中（否则后续服务复用同一事务）
    assert db_session.in_transaction() is False


async def test_db_failure_rolls_back_shared_session():
    """查询失败必须回滚共享 db_session——否则后续服务全部打不开事务。"""
    db = MagicMock()

    async def _boom(*a, **k):
        raise RuntimeError("db down")

    async def _rollback() -> None:
        db.rollback_called = True

    db.execute = _boom
    db.rollback = _rollback
    mgr = _FakeManager()

    # 不得抛异常，不得调用 set_current_account
    await _restore_active_account(mgr, db)

    assert db.rollback_called is True
    assert mgr.calls == []
    assert mgr.current_account_login == "0"


async def test_manager_failure_also_rolls_back(db_session):
    """恢复过程在 set_current_account 阶段失败 → 同样回滚，保持 "0"。"""
    await _add_account(db_session, 66666666)

    class _BrokenManager(_FakeManager):
        async def set_current_account(self, account_login: str) -> None:
            raise RuntimeError("engine sync failed")

    mgr = _BrokenManager()
    await _restore_active_account(mgr, db_session)

    # 未成功应用 → 不应用部分结果
    assert mgr.calls == []
    assert mgr.current_account_login == "0"
