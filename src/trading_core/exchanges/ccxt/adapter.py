"""Optional generic CCXT adapter for public async market snapshots."""

from __future__ import annotations

import asyncio
import importlib
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar, cast

from trading_core.collectors.results import CollectionError, CollectionResult
from trading_core.exceptions import (
    AuthenticationError as CoreAuthenticationError,
)
from trading_core.exceptions import (
    ExchangeRateLimited,
    ExchangeTimeout,
    ExchangeUnavailable,
    InvalidExchangeData,
    TradingCoreError,
    UnsupportedCapability,
)
from trading_core.exchanges.interfaces import Capability, ExchangeConfig
from trading_core.models import FundingRate, Instrument, OrderBook, Ticker, TopOfBook
from trading_core.normalization.ccxt import (
    CCXTMarketMetadata,
    capabilities_from_ccxt,
    funding_interval_missing_from_ccxt,
    funding_interval_supported_from_ccxt,
    normalize_ccxt_bulk_top_of_book,
    normalize_ccxt_funding_rate,
    normalize_ccxt_market,
    normalize_ccxt_order_book,
    normalize_ccxt_ticker,
    ticker_order_book_required_from_ccxt,
    _normalize_ccxt_instruments,
)


_Result = TypeVar("_Result")


class _AsyncCCXTClient(Protocol):
    has: object

    async def load_markets(self) -> object:
        """Load unified CCXT market metadata."""

    async def fetch_ticker(self, symbol: str) -> object:
        """Fetch one unified ticker."""

    async def fetch_order_book(self, symbol: str) -> object:
        """Fetch one unified order book."""

    async def fetch_funding_rate(self, symbol: str) -> object:
        """Fetch one unified funding rate."""

    async def fetch_bids_asks(self, symbols: Sequence[str]) -> object:
        """Fetch unified best bid/ask tickers for multiple symbols."""

    async def fetch_funding_rates(self, symbols: Sequence[str]) -> object:
        """Fetch unified funding rates for multiple symbols."""

    async def fetch_funding_intervals(self, symbols: Sequence[str]) -> object:
        """Fetch unified funding intervals for multiple symbols."""

    async def fetch_funding_interval(self, symbol: str) -> object:
        """Fetch one unified funding interval when the rate omits it."""

    async def close(self) -> None:
        """Close the underlying async client."""

    def set_sandbox_mode(self, enabled: bool) -> None:
        """Select sandbox endpoints before the first request."""


class CCXTAdapter:
    """Backend-independent provider backed by one generic CCXT async client."""

    def __init__(
        self,
        exchange_id: str,
        config: ExchangeConfig,
        *,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not isinstance(exchange_id, str) or not exchange_id.strip():
            raise ValueError("exchange_id must be a non-empty string")
        if not isinstance(config, ExchangeConfig):
            raise TypeError("config must be an ExchangeConfig")
        timeout_seconds = config.timeout.total_seconds()
        if timeout_seconds <= 0:
            raise ValueError("config.timeout must be positive")

        self._config = config
        self._timeout_seconds = timeout_seconds
        self._markets: object | None = None
        self._markets_lock = asyncio.Lock()

        options: dict[str, object] = {}
        if config.credentials is not None:
            options.update(config.credentials)
        options.update(
            {
                "timeout": math.ceil(timeout_seconds * 1000),
                "enableRateLimit": True,
            }
        )
        factory = _default_client_factory if client_factory is None else client_factory
        self._client = cast(_AsyncCCXTClient, factory(exchange_id, options))
        if config.sandbox:
            self._client.set_sandbox_mode(True)
        self._capabilities = capabilities_from_ccxt(self._client.has)
        if callable(getattr(self._client, "load_markets", None)):
            self._capabilities = self._capabilities | {Capability.INSTRUMENT_CATALOG}

    @property
    def venue(self) -> str:
        """Return the stable venue name used in provider contracts."""
        return self._config.venue

    @property
    def capabilities(self) -> frozenset[Capability]:
        """Return capabilities derived from the inspected CCXT metadata shape."""
        return self._capabilities

    async def list_instruments(self) -> tuple[Instrument, ...]:
        """Load and normalize the venue's currently discoverable instruments."""
        operation = "list_instruments"
        self._require_capability(Capability.INSTRUMENT_CATALOG, operation)
        markets = await self._load_markets_once(operation=operation)
        return _normalize_ccxt_instruments(markets, venue=self.venue)

    async def fetch_ticker(self, instrument: Instrument) -> Ticker:
        """Fetch and normalize one ticker snapshot."""
        operation = "fetch_ticker"
        self._require_capability(Capability.TICKER_SNAPSHOT, operation)
        market = await self._market_for(instrument, operation=operation)
        raw_ticker = await self._await_backend(
            self._client.fetch_ticker(instrument.venue_symbol),
            operation=operation,
        )
        fallback_required = ticker_order_book_required_from_ccxt(
            self._client.has, raw_ticker
        )
        raw_order_book: object | None = None
        if fallback_required:
            raw_order_book = await self._await_backend(
                self._client.fetch_order_book(instrument.venue_symbol),
                operation=operation,
            )
        return normalize_ccxt_ticker(
            raw_ticker,
            raw_order_book=raw_order_book,
            require_top_of_book=fallback_required,
            market=market,
            received_at=datetime.now(UTC),
        )

    async def fetch_order_book(self, instrument: Instrument) -> OrderBook:
        """Fetch and normalize one order-book snapshot."""
        operation = "fetch_order_book"
        self._require_capability(Capability.ORDER_BOOK_SNAPSHOT, operation)
        market = await self._market_for(instrument, operation=operation)
        raw_order_book = await self._await_backend(
            self._client.fetch_order_book(instrument.venue_symbol),
            operation=operation,
        )
        return normalize_ccxt_order_book(
            raw_order_book,
            market=market,
            received_at=datetime.now(UTC),
        )

    async def fetch_funding_rate(self, instrument: Instrument) -> FundingRate:
        """Fetch and normalize one observed funding-rate snapshot."""
        operation = "fetch_funding_rate"
        self._require_capability(Capability.FUNDING_SNAPSHOT, operation)
        await self._market_for(instrument, operation=operation)
        raw_funding_rate = await self._await_backend(
            self._client.fetch_funding_rate(instrument.venue_symbol),
            operation=operation,
        )
        raw_funding_interval: object | None = None
        if funding_interval_missing_from_ccxt(raw_funding_rate) and (
            funding_interval_supported_from_ccxt(self._client.has)
        ):
            raw_funding_interval = await self._await_backend(
                self._client.fetch_funding_interval(instrument.venue_symbol),
                operation=operation,
            )
        return normalize_ccxt_funding_rate(
            raw_funding_rate,
            raw_funding_interval=raw_funding_interval,
            instrument=instrument,
            received_at=datetime.now(UTC),
        )

    async def fetch_top_of_books(
        self, instruments: Sequence[Instrument]
    ) -> CollectionResult[TopOfBook]:
        """Fetch and normalize top-of-book snapshots with one bulk backend call."""
        operation = "fetch_top_of_books"
        self._require_capability(Capability.BULK_TOP_OF_BOOK, operation)
        started_at = datetime.now(UTC)
        requested = _deduplicate_instruments(
            instruments,
            venue=self.venue,
            operation=operation,
        )
        if not requested:
            completed_at = datetime.now(UTC)
            return CollectionResult(
                data=(),
                errors=(),
                started_at=started_at,
                completed_at=completed_at,
                requests_made=False,
            )

        for instrument in requested:
            await self._market_for(instrument, operation=operation)

        raw_top_of_books = await self._await_backend(
            self._client.fetch_bids_asks(
                [instrument.venue_symbol for instrument in requested]
            ),
            operation=operation,
        )
        received_at = datetime.now(UTC)
        direct_items, symbol_items = _index_bulk_top_of_books(
            raw_top_of_books,
            venue=self.venue,
            operation=operation,
        )
        data: list[TopOfBook] = []
        errors: list[CollectionError] = []
        for instrument in requested:
            raw_top_of_book = direct_items.get(
                instrument.venue_symbol,
                symbol_items.get(instrument.venue_symbol, _MISSING),
            )
            if raw_top_of_book is _MISSING:
                errors.append(
                    CollectionError(
                        venue=self.venue,
                        operation=operation,
                        instrument=instrument,
                        error=InvalidExchangeData(self.venue, operation),
                    )
                )
                continue
            try:
                data.append(
                    normalize_ccxt_bulk_top_of_book(
                        raw_top_of_book,
                        instrument=instrument,
                        received_at=received_at,
                    )
                )
            except TradingCoreError as error:
                errors.append(
                    CollectionError(
                        venue=self.venue,
                        operation=operation,
                        instrument=instrument,
                        error=error,
                    )
                )
            except Exception as error:
                normalized_error = InvalidExchangeData(
                    self.venue,
                    operation,
                    cause=error,
                )
                errors.append(
                    CollectionError(
                        venue=self.venue,
                        operation=operation,
                        instrument=instrument,
                        error=normalized_error,
                    )
                )

        completed_at = datetime.now(UTC)
        return CollectionResult(
            data=tuple(data),
            errors=tuple(errors),
            started_at=started_at,
            completed_at=completed_at,
            requests_made=True,
        )

    async def fetch_funding_rates(
        self, instruments: Sequence[Instrument]
    ) -> CollectionResult[FundingRate]:
        """Fetch and normalize funding-rate snapshots with one bulk backend call."""
        operation = "fetch_funding_rates"
        self._require_capability(Capability.BULK_FUNDING, operation)
        started_at = datetime.now(UTC)
        requested = _deduplicate_instruments(
            instruments,
            venue=self.venue,
            operation=operation,
        )
        if not requested:
            completed_at = datetime.now(UTC)
            return CollectionResult(
                data=(),
                errors=(),
                started_at=started_at,
                completed_at=completed_at,
                requests_made=False,
            )

        for instrument in requested:
            await self._market_for(instrument, operation=operation)

        raw_funding_rates = await self._await_backend(
            self._client.fetch_funding_rates(
                [instrument.venue_symbol for instrument in requested]
            ),
            operation=operation,
        )
        received_at = datetime.now(UTC)
        direct_items, symbol_items = _index_bulk_funding_rates(
            raw_funding_rates,
            venue=self.venue,
            operation=operation,
        )
        interval_items, interval_errors = await self._bulk_funding_interval_items(
            requested,
            direct_items=direct_items,
            symbol_items=symbol_items,
            operation=operation,
        )
        data: list[FundingRate] = []
        errors: list[CollectionError] = []
        for instrument in requested:
            raw_funding_rate = direct_items.get(
                instrument.venue_symbol,
                symbol_items.get(instrument.venue_symbol, _MISSING),
            )
            if raw_funding_rate is _MISSING:
                errors.append(
                    CollectionError(
                        venue=self.venue,
                        operation=operation,
                        instrument=instrument,
                        error=InvalidExchangeData(self.venue, operation),
                    )
                )
                continue
            interval_error = interval_errors.get(instrument.venue_symbol)
            if interval_error is not None:
                errors.append(
                    CollectionError(
                        venue=self.venue,
                        operation=operation,
                        instrument=instrument,
                        error=interval_error,
                    )
                )
                continue
            try:
                data.append(
                    normalize_ccxt_funding_rate(
                        raw_funding_rate,
                        raw_funding_interval=interval_items.get(
                            instrument.venue_symbol
                        ),
                        instrument=instrument,
                        received_at=received_at,
                    )
                )
            except TradingCoreError as error:
                errors.append(
                    CollectionError(
                        venue=self.venue,
                        operation=operation,
                        instrument=instrument,
                        error=error,
                    )
                )
            except Exception as error:
                normalized_error = InvalidExchangeData(
                    self.venue,
                    operation,
                    cause=error,
                )
                errors.append(
                    CollectionError(
                        venue=self.venue,
                        operation=operation,
                        instrument=instrument,
                        error=normalized_error,
                    )
                )

        completed_at = datetime.now(UTC)
        return CollectionResult(
            data=tuple(data),
            errors=tuple(errors),
            started_at=started_at,
            completed_at=completed_at,
            requests_made=True,
        )

    async def _bulk_funding_interval_items(
        self,
        instruments: Sequence[Instrument],
        *,
        direct_items: Mapping[str, object],
        symbol_items: Mapping[str, object],
        operation: str,
    ) -> tuple[dict[str, object], dict[str, TradingCoreError]]:
        missing_symbols: list[str] = []
        for instrument in instruments:
            raw_funding_rate = direct_items.get(
                instrument.venue_symbol,
                symbol_items.get(instrument.venue_symbol, _MISSING),
            )
            if raw_funding_rate is not _MISSING and funding_interval_missing_from_ccxt(
                raw_funding_rate
            ):
                missing_symbols.append(instrument.venue_symbol)
        missing_symbols = list(dict.fromkeys(missing_symbols))
        if not missing_symbols or not funding_interval_supported_from_ccxt(
            self._client.has
        ):
            return {}, {}

        interval_items: dict[str, object] = {}
        interval_errors: dict[str, TradingCoreError] = {}
        fetch_many = getattr(self._client, "fetch_funding_intervals", None)
        bulk_intervals_supported = isinstance(self._client.has, Mapping) and (
            self._client.has.get("fetchFundingIntervals") in (True, "emulated")
        )
        if bulk_intervals_supported and callable(fetch_many):
            try:
                raw_intervals = await self._await_backend(
                    cast(
                        Callable[[Sequence[str]], Awaitable[object]],
                        fetch_many,
                    )(missing_symbols),
                    operation=operation,
                )
                direct_intervals, symbol_intervals = _index_bulk_funding_rates(
                    raw_intervals,
                    venue=self.venue,
                    operation=operation,
                )
                for symbol in missing_symbols:
                    interval = direct_intervals.get(
                        symbol,
                        symbol_intervals.get(symbol, _MISSING),
                    )
                    if interval is _MISSING:
                        interval_errors[symbol] = InvalidExchangeData(
                            self.venue,
                            operation,
                        )
                    else:
                        interval_items[symbol] = interval
                return interval_items, interval_errors
            except TradingCoreError as error:
                return {}, {symbol: error for symbol in missing_symbols}

        for symbol in missing_symbols:
            try:
                interval_items[symbol] = await self._await_backend(
                    self._client.fetch_funding_interval(symbol),
                    operation=operation,
                )
            except TradingCoreError as error:
                interval_errors[symbol] = error
        return interval_items, interval_errors

    async def close(self) -> None:
        """Explicitly close the optional async backend client."""
        await self._await_backend(self._client.close(), operation="close")

    async def _market_for(
        self, instrument: Instrument, *, operation: str
    ) -> CCXTMarketMetadata:
        markets = await self._load_markets_once(operation=operation)
        return normalize_ccxt_market(
            markets,
            instrument=instrument,
            venue=self.venue,
        )

    async def _load_markets_once(self, *, operation: str) -> object:
        if self._markets is not None:
            return self._markets
        async with self._markets_lock:
            if self._markets is None:
                self._markets = await self._await_backend(
                    self._client.load_markets(), operation=operation
                )
        return self._markets

    async def _await_backend(
        self,
        awaitable: Awaitable[_Result],
        *,
        operation: str,
    ) -> _Result:
        try:
            return await asyncio.wait_for(
                awaitable,
                timeout=self._timeout_seconds,
            )
        except TradingCoreError:
            raise
        except Exception as error:
            mapped = _map_backend_error(
                error,
                venue=self.venue,
                operation=operation,
            )
            raise mapped from error

    def _require_capability(self, capability: Capability, operation: str) -> None:
        if capability not in self.capabilities:
            raise UnsupportedCapability(self.venue, operation)


def _default_client_factory(
    exchange_id: str,
    options: Mapping[str, object],
) -> _AsyncCCXTClient:
    try:
        ccxt_async = importlib.import_module("ccxt.async_support")
    except ModuleNotFoundError as error:
        raise ModuleNotFoundError(
            "CCXT is an optional backend; install trading-core[ccxt] to use it"
        ) from error

    exchange_class = getattr(ccxt_async, exchange_id, None)
    if not callable(exchange_class):
        raise ValueError("requested CCXT exchange id is unavailable")
    return cast(_AsyncCCXTClient, exchange_class(dict(options)))


def _map_backend_error(
    error: Exception,
    *,
    venue: str,
    operation: str,
) -> TradingCoreError:
    names = {error_type.__name__ for error_type in type(error).__mro__}
    if names & {"TimeoutError", "RequestTimeout", "OperationTimedOut"}:
        return ExchangeTimeout(venue, operation, cause=error)
    if names & {"RateLimitExceeded", "DDoSProtection"}:
        return ExchangeRateLimited(venue, operation, cause=error)
    if names & {"AuthenticationError", "PermissionDenied", "AccountSuspended"}:
        return CoreAuthenticationError(venue, operation, cause=error)
    if names & {"NotSupported"}:
        return UnsupportedCapability(venue, operation, cause=error)
    if names & {"BadResponse", "NullResponse", "BadRequest", "BadSymbol"}:
        return InvalidExchangeData(venue, operation, cause=error)
    if names & {
        "ExchangeNotAvailable",
        "NetworkError",
        "OnMaintenance",
        "ExchangeError",
    }:
        return ExchangeUnavailable(venue, operation, cause=error)
    return ExchangeUnavailable(venue, operation, cause=error)


_MISSING = object()


def _deduplicate_instruments(
    instruments: Sequence[Instrument], *, venue: str, operation: str
) -> tuple[Instrument, ...]:
    """Validate and retain unique canonical instruments in first-seen order."""
    unique: list[Instrument] = []
    seen: set[Instrument] = set()
    for instrument in instruments:
        if not isinstance(instrument, Instrument) or instrument.venue != venue:
            raise InvalidExchangeData(venue, operation)
        if instrument in seen:
            continue
        seen.add(instrument)
        unique.append(instrument)
    return tuple(unique)


def _index_bulk_funding_rates(
    raw_funding_rates: object, *, venue: str, operation: str
) -> tuple[dict[str, object], dict[str, object]]:
    """Index a unified CCXT mapping without exposing its raw values."""
    if not isinstance(raw_funding_rates, Mapping):
        raise InvalidExchangeData(venue, operation)

    direct_items: dict[str, object] = {}
    symbol_items: dict[str, object] = {}
    for raw_symbol, raw_funding_rate in raw_funding_rates.items():
        if isinstance(raw_symbol, str):
            direct_items.setdefault(raw_symbol, raw_funding_rate)
        if isinstance(raw_funding_rate, Mapping):
            payload_symbol = raw_funding_rate.get("symbol")
            if isinstance(payload_symbol, str):
                symbol_items.setdefault(payload_symbol, raw_funding_rate)
    return direct_items, symbol_items


def _index_bulk_top_of_books(
    raw_top_of_books: object, *, venue: str, operation: str
) -> tuple[dict[str, object], dict[str, object]]:
    """Index a unified CCXT best bid/ask mapping by key and payload symbol."""
    if not isinstance(raw_top_of_books, Mapping):
        raise InvalidExchangeData(venue, operation)

    direct_items: dict[str, object] = {}
    symbol_items: dict[str, object] = {}
    for raw_symbol, raw_top_of_book in raw_top_of_books.items():
        if isinstance(raw_symbol, str):
            direct_items.setdefault(raw_symbol, raw_top_of_book)
        if isinstance(raw_top_of_book, Mapping):
            payload_symbol = raw_top_of_book.get("symbol")
            if isinstance(payload_symbol, str):
                symbol_items.setdefault(payload_symbol, raw_top_of_book)
    return direct_items, symbol_items


__all__ = ["CCXTAdapter"]
