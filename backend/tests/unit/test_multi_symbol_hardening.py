"""Unit tests for multi-symbol hardening (batch 1 + 3).

Covers:
- get_active_symbols fallback path (no BotManager → SYMBOL_PROFILES keys)
- agent_config raises ValueError when job_input lacks 'symbol'
- prompt_registry sentiment default contains `{symbol}` placeholder, not literal GOLD
"""

import pytest

from app.config import SYMBOL_PROFILES, get_active_symbols


@pytest.fixture(autouse=True)
def restore_profiles():
    snapshot = dict(SYMBOL_PROFILES)
    yield
    SYMBOL_PROFILES.clear()
    SYMBOL_PROFILES.update(snapshot)


def test_get_active_symbols_falls_back_to_profiles_when_no_manager():
    SYMBOL_PROFILES.clear()
    SYMBOL_PROFILES["GOLD"] = {"pip_value": 1}
    SYMBOL_PROFILES["EURUSD"] = {"pip_value": 10}
    # alias entries must be excluded
    SYMBOL_PROFILES["GOLDm#"] = {"pip_value": 1, "canonical": "GOLD"}

    active = get_active_symbols()

    assert "GOLD" in active
    assert "EURUSD" in active
    assert "GOLDm#" not in active


def test_get_active_symbols_returns_list_even_when_empty():
    SYMBOL_PROFILES.clear()
    assert get_active_symbols() == []


def test_agent_config_candle_analysis_requires_symbol():
    from mcp_server.agent_config import _build_user_message

    with pytest.raises(ValueError, match="symbol"):
        _build_user_message("candle_analysis", {})

    with pytest.raises(ValueError, match="symbol"):
        _build_user_message("candle_analysis", None)


def test_agent_config_manual_analysis_requires_symbol():
    from mcp_server.agent_config import _build_user_message

    with pytest.raises(ValueError, match="symbol"):
        _build_user_message("manual_analysis", {"timeframe": "M15"})


def test_agent_config_accepts_valid_symbol():
    from mcp_server.agent_config import _build_user_message

    msg = _build_user_message("candle_analysis", {"symbol": "EURUSD", "timeframe": "M15"})
    assert "EURUSD" in msg
    assert "M15" in msg


def test_agent_config_weekly_review_no_symbol_required():
    from mcp_server.agent_config import _build_user_message

    # weekly_review shouldn't require symbol
    msg = _build_user_message("weekly_review", None)
    assert "weekly trading review" in msg


def test_prompt_registry_sentiment_default_is_symbol_agnostic():
    from mcp_server.agents import prompt_registry

    # Force reload to pick up new default
    prompt_registry._defaults_loaded = False
    prompt_registry._DEFAULTS.clear()

    default = prompt_registry.get_default_prompt("sentiment")

    # Old behavior baked "GOLD" literal; new behavior uses {symbol} placeholder
    assert "{symbol}" in default
    # Must not contain the literal hardcoded "GOLD " in instructional text
    # (the word "GOLD" may still appear in the asset-class framework examples)
    # Key invariant: the target instrument slot is a placeholder.
    assert "headlines for the instrument **{symbol}**" in default


# ─── 别名解析：DB broker_alias 是唯一来源（PR3）───────────────────────────────


class TestAliasResolution:
    @pytest.fixture(autouse=True)
    def _restore(self, restore_profiles):
        yield

    def _load_alias_profile(self):
        """模拟 load_profiles_from_db() 为一行注册的内容：
        symbol=GOLD、broker_alias=GOLDmicro。"""
        SYMBOL_PROFILES.clear()
        SYMBOL_PROFILES["GOLD"] = {"pip_value": 1.0, "broker_alias": "GOLDmicro"}
        SYMBOL_PROFILES["GOLDmicro"] = {"pip_value": 1.0, "canonical": "GOLD"}

    def test_get_canonical_symbol_via_alias_profile(self):
        self._load_alias_profile()
        from app.config import get_canonical_symbol

        assert get_canonical_symbol("GOLDmicro") == "GOLD"
        assert get_canonical_symbol("GOLD") == "GOLD"
        assert get_canonical_symbol("UNKNOWN") == "UNKNOWN"

    def test_manager_resolve_symbol_direct_and_alias(self):
        self._load_alias_profile()
        from unittest.mock import MagicMock

        from app.bot.manager import BotManager

        mgr = BotManager.__new__(BotManager)
        engine = MagicMock()
        mgr.engines = {"GOLD": engine}

        assert mgr.resolve_symbol("GOLD") == "GOLD"
        assert mgr.resolve_symbol("GOLDmicro") == "GOLD"  # 别名条目 → 规范名
        assert mgr.resolve_symbol("NOPE") is None

    def test_resolve_canonical_symbol_uses_global_manager(self):
        self._load_alias_profile()
        from unittest.mock import MagicMock

        from app.bot.manager import get_global_manager, set_global_manager
        from app.config import resolve_canonical_symbol

        mgr = MagicMock()
        mgr.engines = {"GOLD": MagicMock()}
        mgr.resolve_symbol.return_value = "GOLD"
        set_global_manager(mgr)
        try:
            assert resolve_canonical_symbol("GOLDmicro") == "GOLD"
        finally:
            set_global_manager(None)
        assert get_global_manager() is None

    def test_resolve_canonical_symbol_falls_back_to_profiles(self):
        self._load_alias_profile()
        from app.config import resolve_canonical_symbol

        # 未注册 manager → 走基于 profile 的别名解析
        assert resolve_canonical_symbol("GOLDmicro") == "GOLD"
        assert resolve_canonical_symbol("GOLD") == "GOLD"

    def test_no_static_alias_entries_after_pr3(self):
        """静态 SYMBOL_ALIASES 注册已移除：全新静态 profile 集合
        不得再包含 micro 别名条目。"""
        from app.config import _STATIC_SYMBOL_PROFILES

        assert not any("canonical" in p for p in _STATIC_SYMBOL_PROFILES.values())
        assert "GOLDmicro" not in _STATIC_SYMBOL_PROFILES
