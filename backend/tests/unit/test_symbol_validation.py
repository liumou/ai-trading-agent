"""券商品种校验（services/symbol_validation.py）单元测试。"""

import pytest

from app.services.symbol_validation import (
    BrokerSymbolCheck,
    check_broker_symbol,
    pip_value_suggestion,
)


class TestPipValueSuggestion:
    """规则表必须与静态 SYMBOL_PROFILES 的约定一致。"""

    def test_gold_metal_d2_matches_static(self):
        # XAUUSD：digits=2, point=0.01 → 静态 GOLD pip_value=1.0
        assert pip_value_suggestion("metal", 2, 0.01) == pytest.approx(1.0)

    def test_silver_metal_d3(self):
        # XAGUSD：digits=3, point=0.001 → 约定 pip 0.01
        assert pip_value_suggestion("metal", 3, 0.001) == pytest.approx(0.01)

    def test_btc_crypto_d2_matches_static(self):
        # BTCUSD：digits=2, point=0.01 → 静态 BTCUSD pip_value=1.0
        assert pip_value_suggestion("crypto", 2, 0.01) == pytest.approx(1.0)

    def test_eurusd_forex_d5_matches_static(self):
        assert pip_value_suggestion("forex", 5, 0.00001) == pytest.approx(0.0001)

    def test_usdjpy_forex_d3_matches_static(self):
        # 静态 USDJPY profile：1 pip = 0.01 价格单位
        assert pip_value_suggestion("forex", 3, 0.001) == pytest.approx(0.01)

    def test_index_uses_point(self):
        assert pip_value_suggestion("index", 1, 0.1) == pytest.approx(0.1)
        assert pip_value_suggestion("stock", 2, 0.01) == pytest.approx(0.01)

    def test_energy_d2(self):
        assert pip_value_suggestion("energy", 2, 0.01) == pytest.approx(1.0)


class TestCheckBrokerSymbol:
    @pytest.mark.asyncio
    async def test_none_connector_unreachable(self):
        check = await check_broker_symbol(None, "GOLD")
        assert check.kind == "unreachable"
        assert not check.broker_answered

    @pytest.mark.asyncio
    async def test_ok_returns_spec(self):
        class Connector:
            async def get_symbol_spec(self, symbol):
                return {
                    "success": True,
                    "data": {"symbol": symbol, "digits": 2, "trade_mode": 4},
                }

        check = await check_broker_symbol(Connector(), "GOLD")
        assert check.ok
        assert check.spec["digits"] == 2

    @pytest.mark.asyncio
    async def test_not_found_is_definitive(self):
        class Connector:
            async def get_symbol_spec(self, symbol):
                return {"success": False, "data": None, "error": f"Symbol {symbol} not found"}

        check = await check_broker_symbol(Connector(), "NOPE")
        assert check.kind == "not_found"
        assert check.broker_answered  # 确定性答复 → strict 模式可置 PAUSED

    @pytest.mark.asyncio
    async def test_not_tradable_trade_mode(self):
        class Connector:
            async def get_symbol_spec(self, symbol):
                return {
                    "success": True,
                    "data": {"symbol": symbol, "digits": 2, "trade_mode": 3},
                }

        check = await check_broker_symbol(Connector(), "GOLD")
        assert check.kind == "not_tradable"
        assert check.broker_answered

    @pytest.mark.asyncio
    async def test_missing_trade_mode_is_ok_old_bridge(self):
        class Connector:
            async def get_symbol_spec(self, symbol):
                return {"success": True, "data": {"symbol": symbol, "digits": 2}}

        check = await check_broker_symbol(Connector(), "GOLD")
        assert check.ok

    @pytest.mark.asyncio
    async def test_mt5_not_connected_is_unreachable(self):
        """'MT5 not connected' 属于基础设施故障，不是品种被下架。"""

        class Connector:
            async def get_symbol_spec(self, symbol):
                return {"success": False, "data": None, "error": "MT5 not connected"}

        check = await check_broker_symbol(Connector(), "GOLD")
        assert check.kind == "unreachable"
        assert not check.broker_answered

    @pytest.mark.asyncio
    async def test_timeout_is_unreachable(self):
        import asyncio

        class Connector:
            async def get_symbol_spec(self, symbol):
                await asyncio.sleep(10)

        check = await check_broker_symbol(Connector(), "GOLD", timeout=0.05)
        assert check.kind == "unreachable"

    @pytest.mark.asyncio
    async def test_malformed_success_payload(self):
        class Connector:
            async def get_symbol_spec(self, symbol):
                return {"success": True, "data": None}

        check = await check_broker_symbol(Connector(), "GOLD")
        assert check.kind == "unexpected"

    def test_broker_symbol_check_ok_property(self):
        assert BrokerSymbolCheck("ok", spec={}).ok
        assert not BrokerSymbolCheck("not_found").ok
