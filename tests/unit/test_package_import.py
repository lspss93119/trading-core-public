import importlib
import sys


def test_trading_core_import_contract_does_not_require_ccxt() -> None:
    trading_core = importlib.import_module("trading_core")

    assert trading_core.__version__ == "0.1.0"
    assert "ccxt" not in sys.modules
