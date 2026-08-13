import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.support.fake_providers import (
    FakeFundingProvider,
    FakeOrderBookProvider,
    FakeTickerProvider,
)
from trading_core.collectors import (
    CollectionError,
    CollectionResult,
    FundingCollector,
    OrderBookCollector,
    TickerCollector,
)
from trading_core.exceptions import (
    ExchangeTimeout,
    ExchangeUnavailable,
    InvalidExchangeData,
    UnsupportedCapability,
)
from trading_core.exchanges import Capability
from trading_core.models import (
    ContractType,
    FundingRate,
    Instrument,
    MarketType,
    OrderBook,
    OrderBookLevel,
    Ticker,
)


STARTED_AT = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 8, 11, 12, 0, 1, tzinfo=UTC)


def make_instrument(venue: str, base: str = "BTC") -> Instrument:
    return Instrument(
        venue=venue,
        venue_symbol=f"{base}/USDT:USDT",
        base=base,
        quote="USDT",
        settlement="USDT",
        market_type=MarketType.PERPETUAL,
        contract_type=ContractType.LINEAR,
    )


def make_ticker(
    instrument: Instrument, *, received_at: datetime = STARTED_AT
) -> Ticker:
    return Ticker(
        instrument=instrument,
        bid=Decimal("100"),
        ask=Decimal("101"),
        last=Decimal("100.5"),
        mark=None,
        index=None,
        exchange_timestamp=received_at,
        received_at=received_at,
    )


def make_funding(instrument: Instrument) -> FundingRate:
    return FundingRate(
        instrument=instrument,
        rate=Decimal("0.0001"),
        interval=timedelta(hours=8),
        next_funding_at=None,
        exchange_timestamp=STARTED_AT,
        received_at=STARTED_AT,
    )


def make_order_book(instrument: Instrument) -> OrderBook:
    return OrderBook(
        instrument=instrument,
        bids=(OrderBookLevel(price=Decimal("100"), amount=Decimal("1")),),
        asks=(OrderBookLevel(price=Decimal("101"), amount=Decimal("1")),),
        exchange_timestamp=STARTED_AT,
        received_at=STARTED_AT,
    )


class AdvancingClock:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> datetime:
        value = STARTED_AT if self.calls == 0 else COMPLETED_AT
        self.calls += 1
        return value


clock = AdvancingClock()


def test_empty_collection_is_complete_without_requests() -> None:
    collector = TickerCollector(timeout=timedelta(seconds=1), clock=clock)
    clock.calls = 0

    result = asyncio.run(collector.collect(()))

    assert result.data == ()
    assert result.errors == ()
    assert result.requests_made is False
    assert result.complete is True
    assert result.partial is False
    assert result.failed is False
    assert result.started_at == STARTED_AT
    assert result.completed_at == COMPLETED_AT


def test_collection_result_rejects_requested_collection_without_outcome() -> None:
    with pytest.raises(ValueError, match="requests_made"):
        CollectionResult[Ticker](
            data=(),
            errors=(),
            started_at=STARTED_AT,
            completed_at=COMPLETED_AT,
            requests_made=True,
        )


@pytest.mark.parametrize("field_name", ["data", "errors"])
def test_collection_result_rejects_non_tuple_collections(field_name: str) -> None:
    ticker = make_ticker(make_instrument("binance"))
    error = CollectionError(
        venue="binance",
        operation="fetch_ticker",
        instrument=ticker.instrument,
        error=InvalidExchangeData("binance", "fetch_ticker"),
    )
    data: tuple[Ticker, ...] | list[Ticker] = (ticker,)
    errors: tuple[CollectionError, ...] | list[CollectionError] = (error,)
    if field_name == "data":
        data = [ticker]
    else:
        errors = [error]

    with pytest.raises(TypeError, match=field_name):
        CollectionResult[Ticker](
            data=data,  # type: ignore[arg-type]
            errors=errors,  # type: ignore[arg-type]
            started_at=STARTED_AT,
            completed_at=COMPLETED_AT,
            requests_made=True,
        )


def test_collection_result_preserves_exact_tuple_inputs() -> None:
    ticker = make_ticker(make_instrument("binance"))
    error = CollectionError(
        venue="binance",
        operation="fetch_ticker",
        instrument=ticker.instrument,
        error=InvalidExchangeData("binance", "fetch_ticker"),
    )

    result = CollectionResult(
        data=(ticker,),
        errors=(error,),
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
        requests_made=True,
    )

    assert type(result.data) is tuple
    assert type(result.errors) is tuple
    assert result.data == (ticker,)
    assert result.errors == (error,)


@pytest.mark.parametrize("has_data", [True, False])
def test_collection_result_rejects_outcomes_without_requests(has_data: bool) -> None:
    ticker = make_ticker(make_instrument("binance"))
    error = CollectionError(
        venue="binance",
        operation="fetch_ticker",
        instrument=ticker.instrument,
        error=InvalidExchangeData("binance", "fetch_ticker"),
    )

    with pytest.raises(ValueError, match="requests_made=False"):
        CollectionResult(
            data=(ticker,) if has_data else (),
            errors=() if has_data else (error,),
            started_at=STARTED_AT,
            completed_at=COMPLETED_AT,
            requests_made=False,
        )


def test_collection_result_requires_timezone_aware_timing() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        CollectionResult[Ticker](
            data=(),
            errors=(),
            started_at=STARTED_AT.replace(tzinfo=None),
            completed_at=COMPLETED_AT,
            requests_made=False,
        )


def test_ticker_collection_returns_normalized_data_in_request_order() -> None:
    first_instrument = make_instrument("binance", "BTC")
    second_instrument = make_instrument("hyperliquid", "ETH")
    first = FakeTickerProvider(
        venue="binance",
        capabilities=frozenset({Capability.TICKER_SNAPSHOT}),
        result=make_ticker(first_instrument),
        delay_seconds=0.01,
    )
    second = FakeTickerProvider(
        venue="hyperliquid",
        capabilities=frozenset({Capability.TICKER_SNAPSHOT}),
        result=make_ticker(second_instrument),
    )
    collector = TickerCollector(timeout=timedelta(seconds=1), clock=clock)
    clock.calls = 0

    result = asyncio.run(
        collector.collect(((first, first_instrument), (second, second_instrument)))
    )

    assert result.data == (
        make_ticker(first_instrument),
        make_ticker(second_instrument),
    )
    assert result.errors == ()
    assert result.requests_made is True
    assert result.complete is True
    assert result.partial is False
    assert result.failed is False
    assert result.started_at == STARTED_AT
    assert result.completed_at == COMPLETED_AT
    assert all(isinstance(item, Ticker) for item in result.data)


def test_ticker_collection_starts_independent_provider_calls_concurrently() -> None:
    first_instrument = make_instrument("binance")
    second_instrument = make_instrument("hyperliquid")
    release = asyncio.Event()
    first = FakeTickerProvider(
        venue="binance",
        capabilities=frozenset({Capability.TICKER_SNAPSHOT}),
        result=make_ticker(first_instrument),
        release=release,
    )
    second = FakeTickerProvider(
        venue="hyperliquid",
        capabilities=frozenset({Capability.TICKER_SNAPSHOT}),
        result=make_ticker(second_instrument),
    )
    collector = TickerCollector(timeout=timedelta(seconds=1), clock=clock)

    async def collect_after_both_have_started() -> CollectionResult[Ticker]:
        task = asyncio.create_task(
            collector.collect(((first, first_instrument), (second, second_instrument)))
        )
        await asyncio.wait_for(first.started.wait(), timeout=0.1)
        await asyncio.wait_for(second.started.wait(), timeout=0.1)
        release.set()
        return await task

    clock.calls = 0
    result = asyncio.run(collect_after_both_have_started())

    assert result.complete is True
    assert first.calls == [first_instrument]
    assert second.calls == [second_instrument]


def test_ticker_timeout_is_isolated_as_partial_result() -> None:
    first_instrument = make_instrument("binance")
    second_instrument = make_instrument("hyperliquid")
    timed_out = FakeTickerProvider(
        venue="binance",
        capabilities=frozenset({Capability.TICKER_SNAPSHOT}),
        result=make_ticker(first_instrument),
        delay_seconds=0.05,
    )
    successful = FakeTickerProvider(
        venue="hyperliquid",
        capabilities=frozenset({Capability.TICKER_SNAPSHOT}),
        result=make_ticker(second_instrument),
    )
    collector = TickerCollector(timeout=timedelta(milliseconds=1), clock=clock)
    clock.calls = 0

    result = asyncio.run(
        collector.collect(
            ((timed_out, first_instrument), (successful, second_instrument))
        )
    )

    assert result.data == (make_ticker(second_instrument),)
    assert len(result.errors) == 1
    assert result.errors[0].venue == "binance"
    assert result.errors[0].operation == "fetch_ticker"
    assert result.errors[0].instrument == first_instrument
    assert isinstance(result.errors[0].error, ExchangeTimeout)
    assert result.partial is True
    assert result.complete is False
    assert result.failed is False


def test_ticker_collection_maps_invalid_data_and_unknown_provider_exception_safely() -> (
    None
):
    first_instrument = make_instrument("binance")
    second_instrument = make_instrument("hyperliquid")
    invalid = FakeTickerProvider(
        venue="binance",
        capabilities=frozenset({Capability.TICKER_SNAPSHOT}),
        result={"raw": "dictionary"},
    )
    secret = "api_key=do-not-leak"
    unavailable = FakeTickerProvider(
        venue="hyperliquid",
        capabilities=frozenset({Capability.TICKER_SNAPSHOT}),
        result=make_ticker(second_instrument),
        error=RuntimeError(secret),
    )
    collector = TickerCollector(timeout=timedelta(seconds=1), clock=clock)
    clock.calls = 0

    result = asyncio.run(
        collector.collect(
            ((invalid, first_instrument), (unavailable, second_instrument))
        )
    )

    assert result.data == ()
    assert [type(item.error) for item in result.errors] == [
        InvalidExchangeData,
        ExchangeUnavailable,
    ]
    assert result.failed is True
    assert all(secret not in str(item.error) for item in result.errors)
    unavailable_error = result.errors[1].error
    assert isinstance(unavailable_error, ExchangeUnavailable)
    assert unavailable_error.cause is unavailable.error


def test_ticker_collection_preserves_stable_error_and_maps_cancellation() -> None:
    first_instrument = make_instrument("binance")
    second_instrument = make_instrument("hyperliquid")
    stable_error = InvalidExchangeData("binance", "normalized_ticker")
    invalid = FakeTickerProvider(
        venue="binance",
        capabilities=frozenset({Capability.TICKER_SNAPSHOT}),
        result=make_ticker(first_instrument),
        error=stable_error,
    )
    cancelled = FakeTickerProvider(
        venue="hyperliquid",
        capabilities=frozenset({Capability.TICKER_SNAPSHOT}),
        result=make_ticker(second_instrument),
        error=asyncio.CancelledError(),
    )
    collector = TickerCollector(timeout=timedelta(seconds=1), clock=clock)
    clock.calls = 0

    result = asyncio.run(
        collector.collect(((invalid, first_instrument), (cancelled, second_instrument)))
    )

    assert result.errors[0].error is stable_error
    cancellation_error = result.errors[1].error
    assert isinstance(cancellation_error, ExchangeUnavailable)
    assert cancellation_error.cause is cancelled.error
    assert result.failed is True


def test_unsupported_capability_does_not_invoke_provider() -> None:
    instrument = make_instrument("binance")
    provider = FakeTickerProvider(
        venue="binance",
        capabilities=frozenset(),
        result=make_ticker(instrument),
    )
    collector = TickerCollector(timeout=timedelta(seconds=1), clock=clock)
    clock.calls = 0

    result = asyncio.run(collector.collect(((provider, instrument),)))

    assert result.data == ()
    assert result.errors[0].instrument == instrument
    assert isinstance(result.errors[0].error, UnsupportedCapability)
    assert provider.calls == []
    assert result.failed is True


def test_funding_and_order_book_collectors_retain_stale_normalized_models() -> None:
    funding_instrument = make_instrument("binance")
    order_book_instrument = make_instrument("hyperliquid")
    stale_time = STARTED_AT - timedelta(days=7)
    stale_funding = make_funding(funding_instrument)
    stale_order_book = OrderBook(
        instrument=order_book_instrument,
        bids=(OrderBookLevel(price=Decimal("100"), amount=Decimal("1")),),
        asks=(OrderBookLevel(price=Decimal("101"), amount=Decimal("1")),),
        exchange_timestamp=stale_time,
        received_at=stale_time,
    )
    funding_provider = FakeFundingProvider(
        venue="binance",
        capabilities=frozenset({Capability.FUNDING_SNAPSHOT}),
        result=stale_funding,
    )
    order_book_provider = FakeOrderBookProvider(
        venue="hyperliquid",
        capabilities=frozenset({Capability.ORDER_BOOK_SNAPSHOT}),
        result=stale_order_book,
    )
    funding_collector = FundingCollector(timeout=timedelta(seconds=1), clock=clock)
    order_book_collector = OrderBookCollector(timeout=timedelta(seconds=1), clock=clock)
    clock.calls = 0

    funding_result = asyncio.run(
        funding_collector.collect(((funding_provider, funding_instrument),))
    )
    clock.calls = 0
    order_book_result = asyncio.run(
        order_book_collector.collect(((order_book_provider, order_book_instrument),))
    )

    assert funding_result.data == (stale_funding,)
    assert order_book_result.data == (stale_order_book,)
    assert funding_result.complete is True
    assert order_book_result.complete is True
