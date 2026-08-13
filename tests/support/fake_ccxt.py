"""Deterministic CCXT-shaped clients for optional-adapter unit tests."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast


class RequestTimeout(Exception):
    """Fake of ccxt.RequestTimeout without importing the optional package."""


class RateLimitExceeded(Exception):
    """Fake of ccxt.RateLimitExceeded without importing the optional package."""


class ExchangeNotAvailable(Exception):
    """Fake of ccxt.ExchangeNotAvailable without importing the optional package."""


class AuthenticationError(Exception):
    """Fake of ccxt.AuthenticationError without importing the optional package."""


class BadResponse(Exception):
    """Fake of ccxt.BadResponse without importing the optional package."""


BINANCE_MARKET: dict[str, object] = {
    "id": "BTCUSDT",
    "symbol": "BTC/USDT:USDT",
    "base": "BTC",
    "quote": "USDT",
    "settle": "USDT",
    "spot": False,
    "swap": True,
    "contract": True,
    "active": True,
    "linear": True,
    "inverse": False,
    "contractSize": "0.001",
    "info": {"backend": "binance"},
}

HYPERLIQUID_MARKET: dict[str, object] = {
    "id": "BTC",
    "symbol": "BTC/USDC:USDC",
    "base": "BTC",
    "quote": "USDC",
    "settle": "USDC",
    "spot": False,
    "swap": True,
    "contract": True,
    "active": True,
    "linear": True,
    "inverse": False,
    "contractSize": "1",
    "info": {"backend": "hyperliquid"},
}


def _ticker_payload(
    *,
    symbol: str,
    price: str,
    bid: str | None = None,
    ask: str | None = None,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "timestamp": 1_786_446_000_000,
        # Real unified futures tickers may omit top-of-book fields. The adapter
        # must obtain executable bid/ask prices from the order-book endpoint.
        "bid": bid,
        "ask": ask,
        "last": str(int(price) + 1),
        "markPrice": f"{price}.5",
        "indexPrice": f"{price}.25",
        "high": "do-not-export",
        "info": {"raw_secret": "do-not-export"},
    }


BINANCE_TICKER = _ticker_payload(symbol="BTC/USDT:USDT", price="100000")
HYPERLIQUID_TICKER = _ticker_payload(
    symbol="BTC/USDC:USDC",
    price="100100",
    bid="100099",
    ask="100104",
)

BINANCE_ORDER_BOOK: dict[str, object] = {
    "symbol": "BTC/USDT:USDT",
    "timestamp": 1_786_446_000_100,
    "bids": [["100000", "2"], ["100001", "1"]],
    "asks": [["100003", "4"], ["100002", "3"]],
    "nonce": 42,
    "info": {"raw_secret": "do-not-export"},
}

HYPERLIQUID_ORDER_BOOK: dict[str, object] = {
    "symbol": "BTC/USDC:USDC",
    "timestamp": 1_786_446_000_200,
    "bids": [["100100", "2"], ["100101", "1"]],
    "asks": [["100103", "4"], ["100102", "3"]],
    "nonce": 84,
    "info": {"raw_secret": "do-not-export"},
}

BINANCE_TOP_OF_BOOK: dict[str, object] = {
    "symbol": "BTC/USDT:USDT",
    "bid": "100000",
    "bidVolume": "2",
    "ask": "100003",
    "askVolume": "4",
    "info": {"raw_secret": "do-not-export"},
}

HYPERLIQUID_TOP_OF_BOOK: dict[str, object] = {
    "symbol": "BTC/USDC:USDC",
    "bid": "100100",
    "bidVolume": "2",
    "ask": "100103",
    "askVolume": "4",
    "info": {"raw_secret": "do-not-export"},
}

BINANCE_FUNDING: dict[str, object] = {
    "symbol": "BTC/USDT:USDT",
    "timestamp": 1_786_446_000_300,
    "fundingRate": "0.0001",
    "fundingTimestamp": 1_786_460_400_000,
    "interval": " \t ",
    "markPrice": "100000.5",
    "info": {"raw_secret": "do-not-export"},
}

BINANCE_FUNDING_INTERVAL: dict[str, object] = {
    "symbol": "BTC/USDT:USDT",
    "interval": "8h",
    "info": {"raw_secret": "do-not-export"},
}

HYPERLIQUID_FUNDING: dict[str, object] = {
    "symbol": "BTC/USDC:USDC",
    "timestamp": 1_786_446_000_400,
    "fundingRate": "-0.00005",
    "nextFundingTimestamp": 1_786_449_600_000,
    "interval": "1h",
    "markPrice": "100100.5",
    "info": {"raw_secret": "do-not-export"},
}


@dataclass(slots=True)
class FakeCCXTClient:
    """Small public-read client seam mirroring the inspected CCXT async API."""

    exchange_id: str
    options: Mapping[str, object]
    has: Mapping[str, object]
    markets: Mapping[str, Mapping[str, object]]
    ticker: Mapping[str, object]
    order_book: object
    funding_rate: Mapping[str, object]
    funding_rates: dict[str, object]
    funding_interval: Mapping[str, object] | None
    top_of_books: dict[str, object] = field(default_factory=dict)
    funding_intervals: dict[str, object] = field(default_factory=dict)
    errors: Mapping[str, BaseException] = field(default_factory=dict)
    delays: Mapping[str, float] = field(default_factory=dict)
    calls: list[tuple[str, str | None]] = field(default_factory=list)
    bulk_funding_symbols: list[tuple[str, ...]] = field(default_factory=list)
    bulk_top_of_book_symbols: list[tuple[str, ...]] = field(default_factory=list)
    bulk_top_of_book_response: object | None = None
    sandbox_modes: list[bool] = field(default_factory=list)

    async def load_markets(self) -> Mapping[str, Mapping[str, object]]:
        self.calls.append(("load_markets", None))
        await self._before("load_markets")
        return self.markets

    async def fetch_ticker(self, symbol: str) -> Mapping[str, object]:
        self.calls.append(("fetch_ticker", symbol))
        await self._before("fetch_ticker")
        return self.ticker

    async def fetch_order_book(self, symbol: str) -> object:
        self.calls.append(("fetch_order_book", symbol))
        await self._before("fetch_order_book")
        return self.order_book

    async def fetch_funding_rate(self, symbol: str) -> Mapping[str, object]:
        self.calls.append(("fetch_funding_rate", symbol))
        await self._before("fetch_funding_rate")
        return self.funding_rate

    async def fetch_bids_asks(self, symbols: Sequence[str]) -> Mapping[str, object]:
        self.calls.append(("fetch_bids_asks", None))
        requested = tuple(symbols)
        self.bulk_top_of_book_symbols.append(requested)
        await self._before("fetch_bids_asks")
        if self.bulk_top_of_book_response is not None:
            return cast(Mapping[str, object], self.bulk_top_of_book_response)
        return {
            symbol: self.top_of_books[symbol]
            for symbol in requested
            if symbol in self.top_of_books
        }

    async def fetch_funding_rates(self, symbols: Sequence[str]) -> Mapping[str, object]:
        self.calls.append(("fetch_funding_rates", None))
        requested = tuple(symbols)
        self.bulk_funding_symbols.append(requested)
        await self._before("fetch_funding_rates")
        return {
            symbol: self.funding_rates[symbol]
            for symbol in requested
            if symbol in self.funding_rates
        }

    async def fetch_funding_interval(self, symbol: str) -> Mapping[str, object]:
        self.calls.append(("fetch_funding_interval", symbol))
        await self._before("fetch_funding_interval")
        if self.funding_interval is None:
            raise BadResponse("funding interval is unavailable")
        return self.funding_interval

    async def fetch_funding_intervals(
        self, symbols: Sequence[str]
    ) -> Mapping[str, object]:
        self.calls.append(("fetch_funding_intervals", None))
        requested = tuple(symbols)
        await self._before("fetch_funding_intervals")
        return {
            symbol: self.funding_intervals[symbol]
            for symbol in requested
            if symbol in self.funding_intervals
        }

    async def close(self) -> None:
        self.calls.append(("close", None))
        await self._before("close")

    def set_sandbox_mode(self, enabled: bool) -> None:
        self.sandbox_modes.append(enabled)

    async def _before(self, operation: str) -> None:
        delay = self.delays.get(operation, 0.0)
        if delay:
            await asyncio.sleep(delay)
        error = self.errors.get(operation)
        if error is not None:
            raise error


@dataclass(slots=True)
class FakeCCXTFactory:
    """Build fake clients while recording generic exchange configuration."""

    clients: dict[str, FakeCCXTClient] = field(default_factory=dict)
    calls: list[tuple[str, Mapping[str, object]]] = field(default_factory=list)

    def __call__(
        self, exchange_id: str, options: Mapping[str, object]
    ) -> FakeCCXTClient:
        self.calls.append((exchange_id, dict(options)))
        client = self.clients.get(exchange_id)
        if client is not None:
            return client
        client = make_fake_client(exchange_id, options=options)
        self.clients[exchange_id] = client
        return client


def make_fake_client(
    exchange_id: str,
    *,
    options: Mapping[str, object] | None = None,
    errors: Mapping[str, BaseException] | None = None,
    delays: Mapping[str, float] | None = None,
    has: Mapping[str, object] | None = None,
) -> FakeCCXTClient:
    """Return a deterministic Binance or Hyperliquid client."""
    if exchange_id == "binance":
        market = BINANCE_MARKET
        ticker = BINANCE_TICKER
        order_book = BINANCE_ORDER_BOOK
        top_of_book = BINANCE_TOP_OF_BOOK
        funding = BINANCE_FUNDING
        funding_interval: Mapping[str, object] | None = BINANCE_FUNDING_INTERVAL
        default_has: Mapping[str, object] = {
            "fetchTicker": True,
            "fetchOrderBook": True,
            "fetchFundingRate": True,
            "fetchFundingRates": True,
            "fetchFundingInterval": "emulated",
            "fetchFundingIntervals": True,
            "fetchBidsAsks": True,
        }
    elif exchange_id == "hyperliquid":
        market = HYPERLIQUID_MARKET
        ticker = HYPERLIQUID_TICKER
        order_book = HYPERLIQUID_ORDER_BOOK
        top_of_book = HYPERLIQUID_TOP_OF_BOOK
        funding = HYPERLIQUID_FUNDING
        funding_interval = None
        default_has = {
            "fetchTicker": "emulated",
            "fetchOrderBook": True,
            "fetchFundingRate": False,
            "fetchFundingRates": True,
            "fetchFundingInterval": None,
            "fetchFundingIntervals": None,
            "fetchBidsAsks": False,
        }
    else:
        raise ValueError("unsupported fake exchange id")

    return FakeCCXTClient(
        exchange_id=exchange_id,
        options={} if options is None else dict(options),
        has=default_has if has is None else dict(has),
        markets={str(market["symbol"]): market},
        ticker=ticker,
        order_book=order_book,
        funding_rate=funding,
        funding_rates={str(funding["symbol"]): {**funding, "interval": "8h"}},
        funding_interval=funding_interval,
        top_of_books={str(top_of_book["symbol"]): top_of_book},
        funding_intervals=(
            {}
            if funding_interval is None
            else {str(funding["symbol"]): {**funding, "interval": "8h"}}
        ),
        errors={} if errors is None else dict(errors),
        delays={} if delays is None else dict(delays),
    )


def replace_payload(payload: Mapping[str, object], **changes: Any) -> dict[str, object]:
    """Copy one complete fake payload and apply focused malformed-data changes."""
    result = dict(payload)
    result.update(changes)
    return result
