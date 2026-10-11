"""复盘账号归属与 /history review-only 补行测试。

覆盖切换账号后复盘记录丢失的根因：
1. _resolve_owned_account —— 客户端账号须登记在 mt5_accounts 才放行；'0' 遗留
   桶放行；未登记/脏值降级到当前活跃账号；DB 异常时降级不 fail-open。
2. _review_only_rows —— 订单数据不可查（bridge 只回当前账号）时补出
   review_only 行，保证复盘徽章在切换账号后仍可见。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.routes.history import _review_only_rows
from app.api.routes.trade_reviews import (
    _clear_registered_cache,
    _resolve_owned_account,
)
from app.db import session as app_session
from app.db.models import MT5Account
from app.db.session import get_db


@pytest.fixture(autouse=True)
def _clear_registered_cache_fixture():
    """清空已登记账号 TTL 缓存，保证测试间隔离。

    _is_registered 是模块级缓存，前一个测试写入的登录号会让后续测试直接
    命中缓存、跳过查库 —— 例如 test_db_outage 依赖"查库失败降级"，若登录号
    已被缓存命中就不查库、返回已登记账号，降级断言落空。
    """
    _clear_registered_cache()
    yield
    _clear_registered_cache()


def _request(current: str = "0") -> MagicMock:
    """构造带 app.state.manager.current_account_login 的请求桩。"""
    req = MagicMock()
    req.app.state.manager.current_account_login = current
    return req


def _patch_db(monkeypatch, db_engine) -> None:
    """把 app.db.session.async_session 指向测试 SQLite engine。"""
    factory = async_sessionmaker(
        bind=db_engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(app_session, "async_session", factory)


def _session_factory(db_engine):
    return async_sessionmaker(
        bind=db_engine, class_=AsyncSession, expire_on_commit=False
    )


async def _add_accounts(db_session, logins: list[int]) -> None:
    from sqlalchemy import insert

    from app.db.models import MT5Account

    for login in logins:
        await db_session.execute(insert(MT5Account).values(
            login=login, password_encrypted=b"x", password_nonce=b"y",
        ))
    await db_session.commit()


async def _register_in(s, logins: list[int]) -> None:
    """在同一事务会话里登记 mt5_accounts（用于 review-only 补行测试）。"""
    from sqlalchemy import insert

    from app.db.models import MT5Account

    for login in logins:
        await s.execute(insert(MT5Account).values(
            login=login, password_encrypted=b"x", password_nonce=b"y",
        ))


class TestAccountOwnership:
    async def test_empty_client_falls_back_to_current(self):
        """空客户端账号 → 用当前活跃账号，无需查库。"""
        out = await _resolve_owned_account(_request("336773771"), "")
        assert out == "336773771"

    async def test_zero_legacy_bucket_always_allowed(self, db_engine, monkeypatch):
        """'0' 遗留桶（单账号期未标记账号的存量 trades）无需登记即放行。"""
        _patch_db(monkeypatch, db_engine)
        assert await _resolve_owned_account(_request("336773771"), "0") == "0"

    async def test_registered_account_across_accounts_allowed(
        self, db_engine, monkeypatch, db_session
    ):
        """修复核心：已登记账号（即使已切换走）放行，切换后仍能回看原账号复盘。"""
        _patch_db(monkeypatch, db_engine)
        await _add_accounts(db_session, [110351809, 336773771])
        # 当前活跃账号是 336773771，但请求 110351809（已切换走的账号）
        assert await _resolve_owned_account(
            _request("336773771"), "110351809"
        ) == "110351809"

    async def test_unregistered_account_degrades_to_current(
        self, db_engine, monkeypatch, db_session
    ):
        """未登记账号（他人账号）必须降级到当前账号，不能靠猜登录号越权。"""
        _patch_db(monkeypatch, db_engine)
        await _add_accounts(db_session, [336773771])
        assert await _resolve_owned_account(
            _request("336773771"), "99999999"
        ) == "336773771"

    @pytest.mark.parametrize(
        "dirty", ["abc", "1; DROP TABLE trades", "1,2", "-1", "1e3"]
    )
    async def test_dirty_client_degrades(
        self, db_engine, monkeypatch, db_session, dirty
    ):
        """脏值/注入尝试一律降级，不放行。"""
        _patch_db(monkeypatch, db_engine)
        await _add_accounts(db_session, [336773771])
        assert await _resolve_owned_account(
            _request("336773771"), dirty
        ) == "336773771"

    async def test_db_outage_degrades_not_fail_open(self, monkeypatch):
        """DB 异常时降级到当前账号，绝不 fail-open 成信任客户端。"""

        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(app_session, "async_session", _boom)
        assert await _resolve_owned_account(
            _request("336773771"), "110351809"
        ) == "336773771"

    async def test_leading_zeros_normalized(
        self, db_engine, monkeypatch, db_session
    ):
        """前导零必须归一化为规范登录号，不能原样入库。

        "00110351809" 与 "110351809" 是同一个 MT5 登录号（mt5_accounts.login 是
        BigInteger，成员检查走 int() 已等价放行）。但本函数的返回值会原样写进
        trade_reviews.account_login；若不归一化，"00110351809" 就成了账号串的真实
        存储值，而 /history 的 owned 过滤器用规范的 str(login) 永远匹配不上
        → 该复盘永久不可见，按规范登录号也查不回来。
        """
        _patch_db(monkeypatch, db_engine)
        await _add_accounts(db_session, [110351809])
        assert await _resolve_owned_account(
            _request("336773771"), "00110351809"
        ) == "110351809"
        # 全零 → 归一化后就是 '0' 遗留桶，命中桶短路而非降级
        assert await _resolve_owned_account(
            _request("336773771"), "00000"
        ) == "0"

    async def test_soft_deleted_account_still_allows_review(
        self, db_engine, monkeypatch, db_session
    ):
        """查询侧放行软删账号 —— 与启动恢复侧（排除软删）是有意区分。

        复盘挂在 MT5 登录号而非 mt5_accounts.id 上。用户"删除后重建同一券商
        账号"会得到新 id；若查询侧过滤 is_deleted，重建后所有历史复盘立刻不可见
        （正是本次修复要解决的症状）。启动恢复侧不能放行软删，因为那会把用户
        标记为"不再使用"的账号重新激活为交易账号。
        """
        from sqlalchemy import insert

        _patch_db(monkeypatch, db_engine)
        await db_session.execute(insert(MT5Account).values(
            login=55555555, password_encrypted=b"x", password_nonce=b"y",
            is_active=False, is_deleted=True,
        ))
        await db_session.commit()
        assert await _resolve_owned_account(
            _request("336773771"), "55555555"
        ) == "55555555"

    async def test_no_manager_state_still_allows_registered(
        self, db_engine, monkeypatch, db_session
    ):
        """app.state 无 manager 时 current 落到 '0'；已登记账号仍放行。"""
        _patch_db(monkeypatch, db_engine)
        await _add_accounts(db_session, [110351809])
        req = MagicMock()
        req.app.state.manager = None
        assert await _resolve_owned_account(req, "110351809") == "110351809"

    async def test_registered_cache_skips_second_db_lookup(
        self, db_engine, monkeypatch, db_session
    ):
        """首次查库命中后，TTL 内同一登录号不再查库（缓存生效）。

        前端一次 load 会对同一账号发多个 by-ticket 查询；缓存把查库压到每个
        登录号一次，避免通过 8+12 连接池并发空转。
        """
        _patch_db(monkeypatch, db_engine)
        await _add_accounts(db_session, [110351809])

        def _boom(*a, **k):
            raise AssertionError("second lookup must be served from cache")

        assert await _resolve_owned_account(_request("336773771"), "110351809") == "110351809"
        # 再次解析同一账号 → 应命中缓存，不应查库
        monkeypatch.setattr(app_session, "async_session", _boom)
        assert await _resolve_owned_account(_request("336773771"), "110351809") == "110351809"


class TestBatchAccountMap:
    """批量端点按行归属：account_map 的键（已 strip）必须与查找键口径一致。"""

    def _app(self, db_session, monkeypatch):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.api.routes import trade_reviews as tr_routes
        from app.api.routes.trade_reviews import router as tr_router

        app = FastAPI()
        app.include_router(tr_router)

        async def override_db():
            yield db_session

        app.dependency_overrides[get_db] = override_db
        app.state.redis = None
        # 必须是真实对象：MagicMock 未设属性时 getattr 返回另一个 Mock，
        # str(Mock()) 恒为真值 → 降级路径会拿到垃圾账号串而非 "336773771"。
        app.state.manager = SimpleNamespace(current_account_login="336773771")

        captured: list[dict] = []

        async def fake_trigger(**kwargs):
            captured.append(kwargs)
            return {"id": 1, "ticket": kwargs.get("ticket"),
                    "account_login": kwargs.get("account_login"), "status": "completed"}

        monkeypatch.setattr(tr_routes, "trigger_review", fake_trigger)
        return app, captured, ASGITransport(app=app), AsyncClient

    async def test_batch_strips_account_before_lookup(
        self, db_engine, monkeypatch, db_session
    ):
        """带首尾空白的账号必须归一化后再查 account_map。

        口径不一致会让它查不到映射 → 归属校验被静默跳过，复盘以 " 110351809 "
        这类垃圾字符串入账，之后再也查不回来。
        """
        _patch_db(monkeypatch, db_engine)
        await _add_accounts(db_session, [110351809, 336773771])

        app, captured, transport, AsyncClient = self._app(db_session, monkeypatch)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/trade-reviews/batch", json={
                "items": [
                    {"ticket": 1, "account_login": " 110351809 "},  # 带空格
                    {"ticket": 2, "account_login": "110351809"},    # 正常
                    {"ticket": 3, "account_login": "99999999"},     # 未登记 → 降级
                ]
            })
        assert r.status_code == 202
        assert r.json()["created"] == 3
        assert [e["account_login"] for e in captured] == [
            "110351809", "110351809", "336773771",
        ]

    async def test_batch_blank_account_rejected(
        self, db_engine, monkeypatch, db_session
    ):
        """纯空白账号必须显式拒绝，不能静默落进 '0' 遗留桶。

        空白账号真值判断为 True 通过前置校验，strip 后变空串；若不拒绝会以
        "" 入账 → 被当 '0'，成为故障诊断困难的静默错配。
        """
        _patch_db(monkeypatch, db_engine)
        await _add_accounts(db_session, [336773771])

        app, captured, transport, AsyncClient = self._app(db_session, monkeypatch)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/trade-reviews/batch", json={
                "items": [
                    {"ticket": 5, "account_login": "   "},
                    {"ticket": 6, "account_login": "\t"},
                ]
            })
        assert r.status_code == 202
        assert r.json()["created"] == 0
        assert len(r.json()["failed"]) == 2
        assert all("blank" in e["error"] for e in r.json()["failed"])
        # 不得有任何复盘以空串账号入账
        assert captured == []

    async def test_batch_bot_trade_uses_db_account(
        self, db_engine, monkeypatch, db_session
    ):
        """bot 单（trade_id）归属由 DB 解析，不受客户端账号/校验影响。"""
        from sqlalchemy import insert

        from app.db.models import MT5Account, Trade

        _patch_db(monkeypatch, db_engine)
        await _add_accounts(db_session, [336773771])

        app, captured, transport, AsyncClient = self._app(db_session, monkeypatch)
        async with db_session.begin():
            await db_session.execute(insert(MT5Account).values(
                login=222000000, password_encrypted=b"x", password_nonce=b"y",
            ))
            await db_session.execute(insert(Trade).values(
                ticket=777, account_login="222000000", symbol="GOLD", type="BUY",
                lot=0.01, open_price=2000.0, close_price=None, sl=1950.0, tp=2050.0,
                open_time=datetime(2026, 10, 1), strategy_name="ema",
            ))
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/trade-reviews/batch", json={
                "items": [{"trade_id": 1, "account_login": "99999999"}]
            })
        assert r.status_code == 202
        # 客户端传的未登记账号被忽略，按 trades 表里的真实账号入账
        assert [e["account_login"] for e in captured] == ["222000000"]


class TestReviewOnlyRows:
    async def test_emits_review_only_rows_for_missing_trades(
        self, db_engine, monkeypatch
    ):
        """有复盘、无订单数据 → 补出 review_only 行（价格列占位）。"""
        factory = _session_factory(db_engine)
        monkeypatch.setattr(app_session, "async_session", factory)

        from app.db.models import TradeReview

        base = datetime(2026, 10, 1, 10, 0)
        async with factory() as s, s.begin():
            await _register_in(s, [110351809])
            s.add(TradeReview(
                ticket=111, account_login="110351809", symbol="GOLD",
                status="completed", open_time=base,
                review={"summary": "x"},
            ))

        # 已有同 (ticket, account_login) 的 bot 行 → 不重复补出
        existing = [{
            "ticket": 111, "account_login": "110351809", "source": "bot",
            "open_time": base.isoformat(),
        }]
        assert await _review_only_rows(base - timedelta(days=30), existing) == []

        # 该订单数据不可查 → 补出 review_only 行
        rows = await _review_only_rows(base - timedelta(days=30), [])
        assert len(rows) == 1
        r = rows[0]
        assert r["source"] == "review_only"
        assert r["ticket"] == 111
        assert r["account_login"] == "110351809"
        assert r["symbol"] == "GOLD"
        assert r["profit"] is None
        assert r["close_time"] is None
        assert r["strategy_name"] == "review_only"
        assert r["id"] is None
        assert r["open_time"] == base.isoformat()

    async def test_skips_duplicate_account_bucket(self, db_engine, monkeypatch):
        """同一 ticket 但账号不同 → 视为不同订单，仍需补行。"""
        factory = _session_factory(db_engine)
        monkeypatch.setattr(app_session, "async_session", factory)

        from app.db.models import TradeReview

        base = datetime(2026, 10, 1, 10, 0)
        async with factory() as s, s.begin():
            await _register_in(s, [110351809])
            s.add(TradeReview(
                ticket=112, account_login="110351809", symbol="GOLD",
                status="completed", open_time=base, review={"summary": "x"},
            ))

        # 已有同 ticket 但账号为 '0' 的行 → 110351809 的复盘仍需补出
        existing = [{
            "ticket": 112, "account_login": "0", "source": "bot",
            "open_time": base.isoformat(),
        }]
        rows = await _review_only_rows(base - timedelta(days=30), existing)
        assert len(rows) == 1
        assert rows[0]["account_login"] == "110351809"

    async def test_skips_unregistered_account(self, db_engine, monkeypatch):
        """未登记账号的复盘不补行 —— "跨账号回看"不等于"任意账号可见"。

        复盘只能为用户自己登记的 mt5_accounts 创建（POST 端点校验归属），
        所以扫描必须收敛到 {已登记账号} ∪ {'0'}；否则历史里任何账号的复盘
        都会被全量扫进 /history。
        """
        factory = _session_factory(db_engine)
        monkeypatch.setattr(app_session, "async_session", factory)

        from app.db.models import TradeReview

        base = datetime(2026, 10, 1, 10, 0)
        async with factory() as s, s.begin():
            # 只登记 336773771；下面两条复盘分属已登记 / 未登记账号
            await _register_in(s, [336773771])
            s.add(TradeReview(
                ticket=401, account_login="336773771", symbol="GOLD",
                status="completed", open_time=base, review={"summary": "x"},
            ))
            s.add(TradeReview(
                ticket=402, account_login="99999999", symbol="GOLD",
                status="completed", open_time=base, review={"summary": "x"},
            ))
            # '0' 遗留桶：未登记但必须保留（单账号期存量数据）
            s.add(TradeReview(
                ticket=403, account_login="0", symbol="",
                status="completed", open_time=base, review={"summary": "x"},
            ))

        rows = await _review_only_rows(base - timedelta(days=30), [])
        assert sorted(r["ticket"] for r in rows) == [401, 403]

    async def test_respects_days_cutoff(self, db_engine, monkeypatch):
        """超出 days 窗口的复盘记录不补行。"""
        factory = _session_factory(db_engine)
        monkeypatch.setattr(app_session, "async_session", factory)

        from app.db.models import TradeReview

        old = datetime(2026, 1, 1)
        async with factory() as s, s.begin():
            s.add(TradeReview(
                ticket=222, account_login="0", symbol="",
                status="completed", open_time=old, review={"summary": "old"},
            ))

        assert await _review_only_rows(datetime(2026, 10, 1) - timedelta(days=30), []) == []
        # 窗口覆盖该日期 → 可命中（排除查询本身失效的可能）
        rows = await _review_only_rows(old - timedelta(days=1), [])
        assert len(rows) == 1
        assert rows[0]["ticket"] == 222

    async def test_honors_symbol_filter(self, db_engine, monkeypatch):
        """按品种筛选时，空品种/其他品种的复盘行不混入结果。"""
        factory = _session_factory(db_engine)
        monkeypatch.setattr(app_session, "async_session", factory)

        from app.db.models import TradeReview

        base = datetime(2026, 10, 1, 10, 0)
        async with factory() as s, s.begin():
            s.add(TradeReview(
                ticket=301, account_login="0", symbol="GOLD",
                status="completed", open_time=base, review={"summary": "x"},
            ))
            # 手动单复盘：symbol 为空
            s.add(TradeReview(
                ticket=302, account_login="0", symbol="",
                status="completed", open_time=base, review={"summary": "x"},
            ))

        # 复盘记录存的是规范名（来自 trades.symbol），与 /trades 归一后的
        # symbol 参数直接可比；命中 GOLD，排除空品种与其他品种。
        rows = await _review_only_rows(base - timedelta(days=30), [], symbol="GOLD")
        assert [r["ticket"] for r in rows] == [301]
        assert await _review_only_rows(base - timedelta(days=30), [], symbol="BTCUSD") == []
        assert await _review_only_rows(base - timedelta(days=30), [], symbol="OILCash") == []
        # 不带品种筛选 → 空品种的手动单复盘行仍补出
        assert sorted(r["ticket"] for r in await _review_only_rows(base - timedelta(days=30), [])) == [301, 302]

    async def test_honors_trade_type_filter(self, db_engine, monkeypatch):
        """按方向筛选时整类不出现 —— review_only 行不知开/平仓方向。"""
        factory = _session_factory(db_engine)
        monkeypatch.setattr(app_session, "async_session", factory)

        from app.db.models import TradeReview

        base = datetime(2026, 10, 1, 10, 0)
        async with factory() as s, s.begin():
            s.add(TradeReview(
                ticket=310, account_login="0", symbol="GOLD",
                status="completed", open_time=base, review={"summary": "x"},
            ))

        assert await _review_only_rows(base - timedelta(days=30), [], trade_type="BUY") == []
        # 不带类型筛选仍补出
        assert len(await _review_only_rows(base - timedelta(days=30), [])) == 1

    async def test_honors_strategy_filter(self, db_engine, monkeypatch):
        """按策略筛选时整类不出现 —— review_only 行的策略名是占位符而非真实策略。

        这是回归用例：调用方原先无条件补出占位行，所以 /trades?strategy=ema
        的结果里会混进 strategy_name="review_only" 的行，它们的策略归属无从判断。
        """
        factory = _session_factory(db_engine)
        monkeypatch.setattr(app_session, "async_session", factory)

        from app.db.models import TradeReview

        base = datetime(2026, 10, 1, 10, 0)
        async with factory() as s, s.begin():
            s.add(TradeReview(
                ticket=320, account_login="0", symbol="GOLD",
                status="completed", open_time=base, review={"summary": "x"},
            ))

        assert await _review_only_rows(base - timedelta(days=30), [], strategy="ema") == []
        # 不带策略筛选仍补出
        assert len(await _review_only_rows(base - timedelta(days=30), [])) == 1

    async def test_db_failure_returns_empty(self, monkeypatch):
        """DB 异常时安静返回空列表，不影响主查询。"""

        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(app_session, "async_session", _boom)
        assert await _review_only_rows(datetime(2026, 10, 1), []) == []
