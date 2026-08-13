from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from decimal import Decimal

import pytest

from trading_core.exceptions import TradingCoreError
from trading_core.exchanges import ExchangeConfig
from trading_core.exchanges.ccxt import CCXTAdapter
from trading_core.models import (
    ContractType,
    FundingRate,
    Instrument,
    MarketType,
    OrderBook,
    Ticker,
)


pytestmark = pytest.mark.integration


LIVE_VENUES = (
    pytest.param(
        "binance",
        "BTC/USDT:USDT",
        "USDT",
        id="binance",
    ),
    pytest.param(
        "hyperliquid",
        "BTC/USDC:USDC",
        "USDC",
        id="hyperliquid",
    ),
)


def _skip_unless_live() -> None:
    if os.environ.get("TRADING_CORE_RUN_LIVE") != "1":
        pytest.skip("set TRADING_CORE_RUN_LIVE=1 to run public network smoke tests")


def _instrument(venue: str, symbol: str, settlement: str) -> Instrument:
    return Instrument(
        venue=venue,
        venue_symbol=symbol,
        base="BTC",
        quote=settlement,
        settlement=settlement,
        market_type=MarketType.PERPETUAL,
        contract_type=ContractType.LINEAR,
    )


def _adapter(venue: str) -> CCXTAdapter:
    return CCXTAdapter(
        venue,
        ExchangeConfig(venue=venue, timeout=timedelta(seconds=10)),
    )


def _close(adapter: CCXTAdapter) -> None:
    asyncio.run(adapter.close())


def _run_public(
    venue: str,
    symbol: str,
    settlement: str,
    operation: str,
) -> Ticker | OrderBook | FundingRate:
    _skip_unless_live()
    instrument = _instrument(venue, symbol, settlement)
    adapter: CCXTAdapter | None = None
    try:
        adapter = _adapter(venue)
        if operation == "ticker":
            return asyncio.run(adapter.fetch_ticker(instrument))
        elif operation == "order_book":
            return asyncio.run(adapter.fetch_order_book(instrument))
        else:
            return asyncio.run(adapter.fetch_funding_rate(instrument))
    except ModuleNotFoundError:
        pytest.fail("install the optional CCXT dependency to run live smoke tests")
    except TradingCoreError as error:
        pytest.fail(
            f"{venue} live {operation} retrieval failed: {type(error).__name__}"
        )
    finally:
        if adapter is not None:
            _close(adapter)


@pytest.mark.parametrize("venue,symbol,settlement", LIVE_VENUES)
def test_public_ticker_is_available_without_credentials(
    venue: str,
    symbol: str,
    settlement: str,
) -> None:
    value = _run_public(venue, symbol, settlement, "ticker")

    assert isinstance(value, Ticker)
    assert value.instrument.venue == venue
    assert value.bid > Decimal("0")
    assert value.ask > Decimal("0")


@pytest.mark.parametrize("venue,symbol,settlement", LIVE_VENUES)
def test_public_order_book_is_available_without_credentials(
    venue: str,
    symbol: str,
    settlement: str,
) -> None:
    value = _run_public(venue, symbol, settlement, "order_book")

    assert isinstance(value, OrderBook)
    assert value.instrument.venue == venue
    assert value.bids
    assert value.asks


@pytest.mark.parametrize("venue,symbol,settlement", LIVE_VENUES)
def test_public_funding_is_available_without_credentials(
    venue: str,
    symbol: str,
    settlement: str,
) -> None:
    value = _run_public(venue, symbol, settlement, "funding")

    assert isinstance(value, FundingRate)
    assert value.instrument.venue == venue
    assert value.interval > timedelta()
