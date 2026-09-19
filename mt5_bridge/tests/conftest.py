"""MT5 Bridge 测试夹具（M3）。

MetaTrader5 仅 Windows 可 import。为了让测试在 Linux/macOS/CI 上可运行，
在 import `main` 前向 `sys.modules` 注入一个 mock 的 `MetaTrader5` 模块。

真实 SDK 行为由 Phase 0 spike 在 VPS 上验证；这里只测 Bridge 的逻辑分支。
"""

import os
import sys
import types
from unittest.mock import MagicMock

import pytest

# 测试环境的 bridge key：main.py 的 verify_api_key 在 key 为空时直接 503，
# 会让依赖鉴权的 /account/switch 用例全挂（测试夹具缺失）。此处统一注入，
# 避免依赖外部 .env 或 shell 环境变量。
os.environ.setdefault("BRIDGE_API_KEY", "test-key")

# 构造 mock MetaTrader5 模块。关键：模块的每个属性都引用 _mt5_mock 的
# 子属性，这样测试通过 mt5_mock.<attr>.return_value 的修改会同时影响
# main.py 中 `mt5.<attr>` 看到的对象（二者是同一个 MagicMock 链）。
_mt5_mock = MagicMock(name="MetaTrader5")
_mt5_module = types.ModuleType("MetaTrader5")
_mt5_module.initialize = _mt5_mock.initialize
_mt5_module.login = _mt5_mock.login
_mt5_module.shutdown = _mt5_mock.shutdown
_mt5_module.terminal_info = _mt5_mock.terminal_info
_mt5_module.account_info = _mt5_mock.account_info
_mt5_module.last_error = _mt5_mock.last_error
_mt5_module.TIMEFRAME_M1 = 1
_mt5_module.TIMEFRAME_M5 = 5
_mt5_module.TIMEFRAME_M15 = 15
_mt5_module.TIMEFRAME_M30 = 30
_mt5_module.TIMEFRAME_H1 = 60
_mt5_module.TIMEFRAME_H4 = 240
_mt5_module.TIMEFRAME_D1 = 1440
_mt5_module.TIMEFRAME_W1 = 10080
_mt5_module.ORDER_TYPE_BUY = 0
_mt5_module.ORDER_TYPE_SELL = 1
_mt5_module.ORDER_TIME_GTC = 0
_mt5_module.ORDER_FILLING_IOC = 2
_mt5_module.TRADE_ACTION_DEAL = 1
_mt5_module.TRADE_ACTION_SLTP = 6
_mt5_module.TRADE_RETCODE_DONE = 10009


@pytest.fixture
def mt5_mock(monkeypatch):
    """返回可被测试修改行为的 mt5 mock 句柄。每次重置，避免跨测试泄漏。"""
    _mt5_mock.reset_mock()
    # 显式清 side_effect：MagicMock.reset_mock 在部分版本不重置 side_effect，
    # 残留的 fake_login 会让后续失败测试误判成功。
    _mt5_mock.initialize.side_effect = None
    _mt5_mock.login.side_effect = None
    _mt5_mock.shutdown.side_effect = None
    _mt5_mock.initialize.return_value = True
    _mt5_mock.login.return_value = True
    _mt5_mock.shutdown.return_value = True
    _mt5_mock.terminal_info.return_value = None
    _mt5_mock.account_info.return_value = None
    _mt5_mock.last_error.return_value = (0, "no error")
    yield _mt5_mock


def _install_mt5_mock():
    if "MetaTrader5" not in sys.modules:
        sys.modules["MetaTrader5"] = _mt5_module


# pytest 插件入口：在任何测试 import 前注入 mock MT5 模块
def pytest_configure(config):
    _install_mt5_mock()
