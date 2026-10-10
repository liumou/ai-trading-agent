"""Tests for canonical → broker alias resolution at the MT5 boundary."""

import pytest

from app.config import SYMBOL_PROFILES
from app.mt5.symbol_resolver import to_broker_alias


@pytest.fixture(autouse=True)
def restore_profiles():
    snapshot = dict(SYMBOL_PROFILES)
    yield
    SYMBOL_PROFILES.clear()
    SYMBOL_PROFILES.update(snapshot)


def test_returns_broker_alias_when_set():
    SYMBOL_PROFILES["GOLD"] = {"broker_alias": "GOLDm#"}
    assert to_broker_alias("GOLD") == "GOLDm#"


def test_returns_symbol_when_alias_empty():
    SYMBOL_PROFILES["EURUSD"] = {"broker_alias": ""}
    assert to_broker_alias("EURUSD") == "EURUSD"


def test_returns_symbol_when_alias_none():
    SYMBOL_PROFILES["EURUSD"] = {"broker_alias": None}
    assert to_broker_alias("EURUSD") == "EURUSD"


def test_returns_symbol_when_no_alias_key():
    SYMBOL_PROFILES["EURUSD"] = {"pip_value": 10}
    assert to_broker_alias("EURUSD") == "EURUSD"


def test_returns_symbol_when_profile_missing():
    SYMBOL_PROFILES.pop("UNKNOWN", None)
    assert to_broker_alias("UNKNOWN") == "UNKNOWN"


def test_idempotent_on_alias_input():
    SYMBOL_PROFILES["GOLDm#"] = {"broker_alias": "GOLDm#"}
    assert to_broker_alias("GOLDm#") == "GOLDm#"


def test_alias_follows_account_switch():
    # 不同 MT5 账号（不同券商）对同一 canonical 品种 GOLD 的 broker_alias 可能不同。
    # load_profiles_into_memory() 在账号切换后重建 SYMBOL_PROFILES，to_broker_alias
    # 必须跟随当前账号（profile 里 broker_alias 最新值），不能残留旧账号别名。
    # 账号 A：GOLD → GOLD_
    SYMBOL_PROFILES["GOLD"] = {"broker_alias": "GOLD_"}
    assert to_broker_alias("GOLD") == "GOLD_"
    # 账号切换后：GOLD → XAUUSD（另一券商）
    SYMBOL_PROFILES["GOLD"] = {"broker_alias": "XAUUSD"}
    assert to_broker_alias("GOLD") == "XAUUSD"
    # 无 alias（静态默认 / 未同步）→ 原样返回 canonical，绝不猜别名
    SYMBOL_PROFILES["GOLD"] = {"pip_value": 1.0}
    assert to_broker_alias("GOLD") == "GOLD"


def test_empty_input_returns_empty():
    assert to_broker_alias("") == ""


def test_none_input_returns_none():
    assert to_broker_alias(None) is None
