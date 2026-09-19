"""MT5 账号切换服务（Phase 5 核心）。

编排「安全暂停 → Bridge 切换 → 刷新规格 → 显式恢复」全流程：
- H2：串行化锁 + Redis `switching:in_progress` 门禁 + 在途 drain
- H1：显式 `manager.start()` + active_count 断言（切换成功但引擎不恢复 = 静默停）
- H3：切换后 `manager.set_current_account()` 同步引擎 account_login（风控隔离）
- H4：对账账号限定（reconcile 由 engine 读取 account_login）
- LOW2：审计在切换**开始**时写入（失败也留痕）
"""

import asyncio
import time
from datetime import datetime

from loguru import logger
from sqlalchemy import select

from app.audit import log_audit
from app.db.models import MT5Account
from app.vault import VaultUnavailableError, vault

# Redis 门禁 key：下单入口（broker.place_order / engine._size_and_place_order）切换期间拒绝
SWITCHING_FLAG_KEY = "switching:in_progress"
SWITCHING_FLAG_TTL = 120  # 秒；切换失败也要自动过期，避免卡死


class AccountSwitchError(Exception):
    """账号切换失败（含回滚语义）。"""


class AccountSwitchService:
    def __init__(self, manager, connector, db_session, redis_client, notifier=None):
        self._manager = manager
        self._connector = connector
        self._db = db_session
        self._redis = redis_client
        self._notifier = notifier
        self._lock = asyncio.Lock()  # H2: 串行化切换，阻止并发切换/竞态

    # ─── 辅助 ────────────────────────────────────────────────────────────

    async def _load_account(self, account_id: int) -> MT5Account:
        result = await self._db.execute(
            select(MT5Account).where(MT5Account.id == account_id, MT5Account.is_deleted.is_(False))
        )
        account = result.scalars().first()
        if account is None:
            raise AccountSwitchError(f"Account {account_id} not found")
        return account

    async def _decrypt_password(self, account: MT5Account) -> str:
        if not vault.is_available:
            raise AccountSwitchError("Vault master key not configured")
        try:
            return vault.decrypt(account.password_encrypted, account.password_nonce)
        except (VaultUnavailableError, Exception) as e:  # noqa: BLE001
            raise AccountSwitchError(f"Failed to decrypt password for account {account.login}") from e

    async def _set_switching_flag(self, on: bool) -> None:
        if on:
            await self._redis.set(SWITCHING_FLAG_KEY, "1", ex=SWITCHING_FLAG_TTL)
        else:
            await self._redis.delete(SWITCHING_FLAG_KEY)

    # ─── 核心切换 ────────────────────────────────────────────────────────

    async def switch(self, target_account_id: int, actor: str = "owner") -> dict:
        """切换到目标账号。全程持锁，失败不恢复引擎（H1 安全语义）。

        返回 dict（成功含新账号快照）或抛 AccountSwitchError。
        """
        start_ts = time.monotonic()
        async with self._lock:
            previous_login = self._manager.current_account_login

            # 0. 读目标账号 + 解密密码（切换开始就写审计，失败也留痕 — LOW2）
            target = await self._load_account(target_account_id)
            password = await self._decrypt_password(target)
            await log_audit(
                self._db,
                action="account.switch",
                actor=actor,
                resource=f"account:{target.id}",
                detail={
                    "from": previous_login,
                    "to": target.login,
                    "server": target.server,
                },
                success=True,  # 先记"开始"，失败会在 except 补一条失败记录
            )

            # 1. 置门禁 + 暂停引擎（H2）
            await self._set_switching_flag(True)
            try:
                await self._manager.stop()  # 全部引擎 STOPPED，scheduler 不再触发新下单
                await asyncio.sleep(1.0)  # drain：让已越过 state 检查的在途 HTTP 调用排空

                # 2. 调 Bridge /account/switch（Phase 3 端点）
                result = await self._connector.switch_account(
                    login=target.login,
                    password=password,
                    server=target.server or None,
                )
                if not result.get("success"):
                    detail = result.get("error", "unknown")
                    await log_audit(
                        self._db,
                        action="account.switch_failed",
                        actor=actor,
                        resource=f"account:{target.id}",
                        detail={
                            "from": previous_login,
                            "to": target.login,
                            "error": detail,
                            "elapsed_s": round(time.monotonic() - start_ts, 2),
                        },
                        success=False,
                    )
                    # 尝试回滚到原账号（若 Bridge 失败可能已落入半切换态）
                    if previous_login and previous_login != "0":
                        rollback = await self._try_rollback(previous_login, actor)
                        if rollback:
                            logger.warning(
                                f"Switch to {target.login} failed; rolled back to {previous_login}: {detail}"
                            )
                    raise AccountSwitchError(f"Bridge account switch failed: {detail}")

                new_login = str(target.login)
                # 3. 更新账号状态 + 同步引擎（H3/H4/M5）
                await self._manager.set_current_account(new_login)
                target.is_active = True
                target.last_switched_at = datetime.utcnow()
                # 原活跃账号取消标记
                old_active = await self._db.execute(
                    select(MT5Account).where(MT5Account.is_active.is_(True))
                )
                for other in old_active.scalars().all():
                    if other.id != target.id:
                        other.is_active = False
                await self._db.commit()

                # 4. 刷新规格（同券商基本一致，重跑 volume 回填）
                try:
                    from app.services.symbol_config_service import load_profiles_into_memory

                    await load_profiles_into_memory()
                    await self._validate_symbols()
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Symbol spec refresh after switch failed (continuing): {e}")

                # 5. H1：显式恢复引擎 + active_count 断言
                await self._manager.reload_engines()
                start_result = await self._manager.start()
                # get_status() 返回 {symbols: {...}, active_count: N, ...}，直接读聚合计数
                status = self._manager.get_status()
                active_count = int(status.get("active_count", 0) or 0)
                if active_count == 0:
                    # 引擎未恢复 = 静默停，需报告
                    await log_audit(
                        self._db,
                        action="account.switch_start_failed",
                        actor=actor,
                        resource=f"account:{target.id}",
                        detail={
                            "from": previous_login,
                            "to": new_login,
                            "elapsed_s": round(time.monotonic() - start_ts, 2),
                            "start_result": str(start_result),
                        },
                        success=False,
                    )
                    raise AccountSwitchError(
                        f"Account switched to {new_login} but engines did not resume (active_count=0)"
                    )

                # 6. 审计成功
                await log_audit(
                    self._db,
                    action="account.switched",
                    actor=actor,
                    resource=f"account:{target.id}",
                    detail={
                        "from": previous_login,
                        "to": new_login,
                        "elapsed_s": round(time.monotonic() - start_ts, 2),
                    },
                    success=True,
                )
            finally:
                # 门禁必须清理：成功/失败/任何异常出口都复位，避免切换窗口内下单被拒
                await self._set_switching_flag(False)

            logger.info(f"Account switched: {previous_login} -> {new_login} ({time.monotonic() - start_ts:.2f}s)")
            return {
                "switched": True,
                "from": previous_login,
                "to": new_login,
                "server": target.server,
                "active_engines": active_count,
            }

    async def _try_rollback(self, login: str, actor: str) -> bool:
        """尝试从 DB 找该账号并切回（失败不抛出，只记录）。"""
        try:
            result = await self._db.execute(select(MT5Account).where(MT5Account.login == int(login)))
            acct = result.scalars().first()
            if acct is None:
                return False
            password = await self._decrypt_password(acct)
            resp = await self._connector.switch_account(
                login=acct.login,
                password=password,
                server=acct.server or None,
            )
            if resp.get("success"):
                await self._manager.set_current_account(str(acct.login))
                return True
            return False
        except Exception as e:  # noqa: BLE001
            logger.error(f"Rollback to {login} failed: {e!r}")
            return False

    async def _validate_symbols(self) -> None:
        """切换后重跑品种校验 + volume 回填（同券商场景）。"""
        from app.config import SYMBOL_PROFILES
        from app.services.symbol_validation import verify_enabled_symbols

        symbols = list(self._manager.engines.keys())
        if not symbols:
            return
        broker_names = {
            s: (SYMBOL_PROFILES.get(s, {}).get("broker_alias") or s) for s in symbols
        }
        checks = await verify_enabled_symbols(self._connector, [broker_names[s] for s in symbols])
        for symbol in symbols:
            check = checks.get(broker_names[symbol])
            if check is not None and check.ok and check.spec:
                profile = SYMBOL_PROFILES.get(symbol)
                if profile is not None:
                    profile.update(
                        volume_min=check.spec.get("volume_min"),
                        volume_max=check.spec.get("volume_max"),
                        volume_step=check.spec.get("volume_step"),
                    )
                    engine = self._manager.get_engine(symbol)
                    if engine is not None:
                        engine.apply_profile(profile)
