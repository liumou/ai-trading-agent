"""回归测试 — MT5 成交的 symbol（券商名）与 API 参数（规范名）必须归一后比较。

背景：MT5 返回的 deal.symbol 是券商名（GOLD_），而 API 路由收到的 symbol 参数
是规范名（GOLD）。此前直接 `deal.get("symbol") != symbol` 比较，导致：
即便 MT5 桥已经正确返回了 GOLD_ 的成交，也会在聚合时被判为"不匹配"而整体
丢弃 —— 表现为"明明有成交，daily-pnl / 绩效统计却是 0"。

修复：比较前用 get_canonical_symbol() 把券商名归一为规范名（规范名恒等返回）。
本测试锁定该契约，防止有人把它改回裸比较。
"""

import pytest

from app.api.routes.analytics import _deal_matches_symbol as analytics_matches
from app.api.routes.history import _deal_matches_symbol as history_matches
from app.config import SYMBOL_PROFILES


@pytest.fixture(autouse=True)
def restore_profiles():
    snapshot = dict(SYMBOL_PROFILES)
    yield
    SYMBOL_PROFILES.clear()
    SYMBOL_PROFILES.update(snapshot)


@pytest.fixture
def gold_alias():
    """GOLD_（券商名）与 GOLD（规范名）互为一映射（DB 加载后的形态）。"""
    SYMBOL_PROFILES["GOLD"] = {"broker_alias": "GOLD_"}
    SYMBOL_PROFILES["GOLD_"] = {"broker_alias": "GOLD_", "canonical": "GOLD"}


# 两个路由各有一份 helper（避免路由模块互相依赖），分别验证行为一致
@pytest.mark.parametrize("matches", [history_matches, analytics_matches])
def test_broker_symbol_deal_matches_canonical_query(matches, gold_alias):
    """券商名成交（GOLD_）必须匹配规范名查询（GOLD）—— 这正是原先被丢弃的场景。"""
    assert matches({"symbol": "GOLD_"}, "GOLD") is True


@pytest.mark.parametrize("matches", [history_matches, analytics_matches])
def test_canonical_symbol_deal_still_matches(matches, gold_alias):
    """已经是规范名的成交仍要匹配（恒等返回）。"""
    assert matches({"symbol": "GOLD"}, "GOLD") is True


@pytest.mark.parametrize("matches", [history_matches, analytics_matches])
def test_other_symbol_does_not_match(matches, gold_alias):
    """其它品种的成交不能被算进 GOLD 的统计。"""
    assert matches({"symbol": "EURUSD"}, "GOLD") is False


@pytest.mark.parametrize("matches", [history_matches, analytics_matches])
def test_none_symbol_query_matches_everything(matches, gold_alias):
    """不带 symbol 参数（全账户查询）时不过滤。"""
    assert matches({"symbol": "GOLD_"}, None) is True
    assert matches({"symbol": "EURUSD"}, None) is True


@pytest.mark.parametrize("matches", [history_matches, analytics_matches])
def test_missing_deal_symbol_is_rejected_when_filtering(matches, gold_alias):
    """成交缺 symbol 字段时，带过滤条件不应误判为匹配。"""
    assert matches({}, "GOLD") is False


@pytest.mark.parametrize("matches", [history_matches, analytics_matches])
def test_unknown_symbol_falls_back_to_identity(matches):
    """无 profile 的未知符号走恒等路径，保持既有行为。"""
    SYMBOL_PROFILES.pop("EURUSD", None)
    assert matches({"symbol": "EURUSD"}, "EURUSD") is True
    assert matches({"symbol": "EURUSD"}, "GOLD") is False
