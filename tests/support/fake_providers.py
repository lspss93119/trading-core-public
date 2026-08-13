"""Deterministic normalized providers for collector unit tests."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

from trading_core.exceptions import (
    ExchangeUnavailable,
    TradingCoreError,
    UnsupportedCapability,
)
from trading_core.collectors import CollectionResult
from trading_core.exchanges import Capability
from trading_core.models import (
    ContractType,
    FundingRate,
    Instrument,
    MarketType,
    OrderBook,
    OrderBookLevel,
    Ticker,
    TopOfBook,
)


@dataclass(slots=True)
class _SnapshotProvider:
    venue: str
    capabilities: frozenset[Capability]
    result: object
    error: BaseException | None = None
    delay_seconds: float = 0.0
    release: asyncio.Event | None = None
    started: asyncio.Event = field(default_factory=asyncio.Event)
    calls: list[Instrument] = field(default_factory=list)

    async def _fetch(self, instrument: Instrument) -> object:
        self.calls.append(instrument)
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.error is not None:
            raise self.error
        return self.result


@dataclass(slots=True)
class FakeFundingProvider(_SnapshotProvider):
    async def fetch_funding_rate(self, instrument: Instrument) -> FundingRate:
        return cast(FundingRate, await self._fetch(instrument))


@dataclass(slots=True)
class FakeTickerProvider(_SnapshotProvider):
    async def fetch_ticker(self, instrument: Instrument) -> Ticker:
        return cast(Ticker, await self._fetch(instrument))


@dataclass(slots=True)
class FakeOrderBookProvider(_SnapshotProvider):
    async def fetch_order_book(self, instrument: Instrument) -> OrderBook:
        return cast(OrderBook, await self._fetch(instrument))


@dataclass(slots=True)
class MockNativeAdapter:
    """Deterministic provider fixture with no dependency on CCXT or raw payloads."""

    venue: str
    capabilities: frozenset[Capability] = field(
        default_factory=lambda: frozenset(
            {
                Capability.TICKER_SNAPSHOT,
                Capability.ORDER_BOOK_SNAPSHOT,
                Capability.FUNDING_SNAPSHOT,
                Capability.BULK_FUNDING,
                Capability.BULK_TOP_OF_BOOK,
                Capability.INSTRUMENT_CATALOG,
            }
        )
    )
    errors: dict[str, BaseException] = field(default_factory=dict)
    calls: list[tuple[str, Instrument]] = field(default_factory=list)
    closed: bool = False

    _received_at = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)

    def _check(
        self, capability: Capability, operation: str, instrument: Instrument
    ) -> None:
        self.calls.append((operation, instrument))
        if capability not in self.capabilities:
            raise UnsupportedCapability(self.venue, operation)
        error = self.errors.get(operation)
        if error is not None:
            if isinstance(error, TradingCoreError):
                raise error
            raise ExchangeUnavailable(self.venue, operation, cause=error) from error

    async def fetch_ticker(self, instrument: Instrument) -> Ticker:
        self._check(Capability.TICKER_SNAPSHOT, "fetch_ticker", instrument)
        return Ticker(
            instrument=instrument,
            bid=Decimal("100"),
            ask=Decimal("101"),
            last=Decimal("100.5"),
            mark=Decimal("100.25"),
            index=Decimal("100.125"),
            exchange_timestamp=self._received_at,
            received_at=self._received_at,
        )

    async def fetch_order_book(self, instrument: Instrument) -> OrderBook:
        self._check(Capability.ORDER_BOOK_SNAPSHOT, "fetch_order_book", instrument)
        return OrderBook(
            instrument=instrument,
            bids=(OrderBookLevel(price=Decimal("100"), amount=Decimal("1")),),
            asks=(OrderBookLevel(price=Decimal("101"), amount=Decimal("1")),),
            exchange_timestamp=self._received_at,
            received_at=self._received_at,
        )

    async def fetch_funding_rate(self, instrument: Instrument) -> FundingRate:
        self._check(Capability.FUNDING_SNAPSHOT, "fetch_funding_rate", instrument)
        return FundingRate(
            instrument=instrument,
            rate=Decimal("0.0001"),
            interval=timedelta(hours=8),
            next_funding_at=self._received_at + timedelta(hours=8),
            exchange_timestamp=self._received_at,
            received_at=self._received_at,
        )

    async def fetch_funding_rates(
        self, instruments: Sequence[Instrument]
    ) -> CollectionResult[FundingRate]:
        """Return one canonical funding outcome per requested instrument."""
        operation = "fetch_funding_rates"
        if Capability.BULK_FUNDING not in self.capabilities:
            raise UnsupportedCapability(self.venue, operation)
        data = tuple(
            FundingRate(
                instrument=instrument,
                rate=Decimal("0.0001"),
                interval=timedelta(hours=8),
                next_funding_at=self._received_at + timedelta(hours=8),
                exchange_timestamp=self._received_at,
                received_at=self._received_at,
            )
            for instrument in instruments
        )
        return CollectionResult(
            data=data,
            errors=(),
            started_at=self._received_at,
            completed_at=self._received_at,
            requests_made=bool(instruments),
        )

    async def fetch_top_of_books(
        self, instruments: Sequence[Instrument]
    ) -> CollectionResult[TopOfBook]:
        """Return canonical best bid/ask values without depending on CCXT."""
        operation = "fetch_top_of_books"
        if Capability.BULK_TOP_OF_BOOK not in self.capabilities:
            raise UnsupportedCapability(self.venue, operation)
        data = tuple(
            TopOfBook(
                instrument=instrument,
                bid_price=Decimal("100"),
                bid_amount=Decimal("1"),
                ask_price=Decimal("101"),
                ask_amount=Decimal("1"),
                received_at=self._received_at,
            )
            for instrument in instruments
        )
        return CollectionResult(
            data=data,
            errors=(),
            started_at=self._received_at,
            completed_at=self._received_at,
            requests_made=bool(instruments),
        )

    async def list_instruments(self) -> tuple[Instrument, ...]:
        """Return one canonical instrument without depending on CCXT."""
        operation = "list_instruments"
        if Capability.INSTRUMENT_CATALOG not in self.capabilities:
            raise UnsupportedCapability(self.venue, operation)
        error = self.errors.get(operation)
        if error is not None:
            if isinstance(error, TradingCoreError):
                raise error
            raise ExchangeUnavailable(self.venue, operation, cause=error) from error
        return (
            Instrument(
                venue=self.venue,
                venue_symbol="BTC/USDT:USDT",
                base="BTC",
                quote="USDT",
                settlement="USDT",
                market_type=MarketType.PERPETUAL,
                contract_type=ContractType.LINEAR,
            ),
        )

    async def close(self) -> None:
        self.closed = True
