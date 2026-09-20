"""_infer_asset_class 的路径→类别映射测试。

背景：XM Derivatives 返回 "Derivatives\\SpotMetals_\\GOLD_"，类别词
（metal）位于第二段且带下划线噪声；旧实现只看首段导致推断为 forex，
与正确的 asset_class='metal' 交叉校验失败（400）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.api.routes.symbols import _infer_asset_class  # noqa: E402


def test_xm_derivatives_spot_metals_infers_metal():
    """实际触发线上报错的路径。"""
    assert _infer_asset_class("Derivatives\\SpotMetals_\\GOLD_") == "metal"


def test_classic_forex_path():
    assert _infer_asset_class("Forex\\Majors\\EURUSD") == "forex"


def test_crypto_path():
    assert _infer_asset_class("Crypto\\BTCUSD") == "crypto"


def test_energy_path():
    assert _infer_asset_class("Energies\\OILCash") == "energy"


def test_index_path():
    assert _infer_asset_class("Indices\\US30") == "index"


def test_stock_path():
    assert _infer_asset_class("Stocks\\AAPL") == "stock"


def test_empty_path_defaults_to_forex():
    assert _infer_asset_class("") == "forex"


def test_unknown_path_defaults_to_forex():
    assert _infer_asset_class("Derivatives\\Whatever\\XYZ") == "forex"
