"""Concurrent, partial-failure snapshot collectors."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta
from typing import Generic, TypeVar

from trading_core.exceptions import (
    ExchangeTimeout,
    ExchangeUnavailable,
    InvalidExchangeData,
    TradingCoreError,
    UnsupportedCapability,
)
from trading_core.exchanges import (
    Capability,
    FundingProvider,
    OrderBookProvider,
    Provider,
    TickerProvider,
)
from trading_core.models import FundingRate, Instrument, OrderBook, Ticker

from .results import CollectionError, CollectionResult


T = TypeVar("T")
P = TypeVar("P", bound=Provider)


class _SnapshotCollector(Generic[T, P]):
    """Small shared orchestration for the three typed snapshot collectors."""

    def __init__(self, *, timeout: timedelta, clock: Callable[[], datetime]) -> None:
        if not isinstance(timeout, timedelta) or timeout <= timedelta():
            raise ValueError("timeout must be positive")
        self._timeout = timeout
        self._clock = clock

    async def _collect(
        self,
        requests: Sequence[tuple[P, Instrument]],
        *,
        capability: Capability,
        operation: str,
        fetch: Callable[[P, Instrument], Awaitable[T]],
        model_type: type[T],
    ) -> CollectionResult[T]:
        started_at = self._clock()
        outcomes = await asyncio.gather(
            *(
                self._collect_one(
                    provider,
                    instrument,
                    capability=capability,
                    operation=operation,
                    fetch=fetch,
                    model_type=model_type,
                )
                for provider, instrument in requests
            )
        )
        completed_at = self._clock()
        return CollectionResult(
            data=tuple(data for data, error in outcomes if data is not None),
            errors=tuple(error for data, error in outcomes if error is not None),
            started_at=started_at,
            completed_at=completed_at,
            requests_made=bool(requests),
        )

    async def _collect_one(
        self,
        provider: P,
        instrument: Instrument,
        *,
        capability: Capability,
        operation: str,
        fetch: Callable[[P, Instrument], Awaitable[T]],
        model_type: type[T],
    ) -> tuple[T | None, CollectionError | None]:
        venue = provider.venue
        if capability not in provider.capabilities:
            return None, self._error(
                venue,
                operation,
                instrument,
                UnsupportedCapability(venue, operation),
            )
        try:
            model = await asyncio.wait_for(
                fetch(provider, instrument), timeout=self._timeout.total_seconds()
            )
        except asyncio.TimeoutError as error:
            return None, self._error(
                venue,
                operation,
                instrument,
                ExchangeTimeout(venue, operation, cause=error),
            )
        except asyncio.CancelledError as error:
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                raise
            return None, self._error(
                venue,
                operation,
                instrument,
                ExchangeUnavailable(venue, operation, cause=error),
            )
        except TradingCoreError as error:
            return None, self._error(venue, operation, instrument, error)
        except Exception as error:
            return None, self._error(
                venue,
                operation,
                instrument,
                ExchangeUnavailable(venue, operation, cause=error),
            )
        if not isinstance(model, model_type):
            return None, self._error(
                venue,
                operation,
                instrument,
                InvalidExchangeData(venue, operation),
            )
        return model, None

    @staticmethod
    def _error(
        venue: str,
        operation: str,
        instrument: Instrument,
        error: TradingCoreError,
    ) -> CollectionError:
        return CollectionError(
            venue=venue,
            operation=operation,
            instrument=instrument,
            error=error,
        )


class FundingCollector(_SnapshotCollector[FundingRate, FundingProvider]):
    """Collect normalized funding-rate snapshots from independent providers."""

    async def collect(
        self,
        requests: Sequence[tuple[FundingProvider, Instrument]],
    ) -> CollectionResult[FundingRate]:
        return await self._collect(
            requests,
            capability=Capability.FUNDING_SNAPSHOT,
            operation="fetch_funding_rate",
            fetch=lambda provider, instrument: provider.fetch_funding_rate(instrument),
            model_type=FundingRate,
        )


class TickerCollector(_SnapshotCollector[Ticker, TickerProvider]):
    """Collect normalized ticker snapshots from independent providers."""

    async def collect(
        self,
        requests: Sequence[tuple[TickerProvider, Instrument]],
    ) -> CollectionResult[Ticker]:
        return await self._collect(
            requests,
            capability=Capability.TICKER_SNAPSHOT,
            operation="fetch_ticker",
            fetch=lambda provider, instrument: provider.fetch_ticker(instrument),
            model_type=Ticker,
        )


class OrderBookCollector(_SnapshotCollector[OrderBook, OrderBookProvider]):
    """Collect normalized order-book snapshots from independent providers."""

    async def collect(
        self,
        requests: Sequence[tuple[OrderBookProvider, Instrument]],
    ) -> CollectionResult[OrderBook]:
        return await self._collect(
            requests,
            capability=Capability.ORDER_BOOK_SNAPSHOT,
            operation="fetch_order_book",
            fetch=lambda provider, instrument: provider.fetch_order_book(instrument),
            model_type=OrderBook,
        )
