from __future__ import annotations

import asyncio
import builtins
import importlib
import inspect
import sys
from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from datetime import timedelta
from decimal import Decimal
from typing import cast

import pytest

from tests.support.fake_ccxt import (
    AuthenticationError as CCXTAuthenticationError,
    BadResponse,
    BINANCE_FUNDING,
    BINANCE_MARKET,
    BINANCE_ORDER_BOOK,
    BINANCE_TOP_OF_BOOK,
    BINANCE_TICKER,
    ExchangeNotAvailable,
    FakeCCXTClient,
    FakeCCXTFactory,
    RateLimitExceeded,
    RequestTimeout,
    make_fake_client,
    replace_payload,
)
from trading_core.collectors import CollectionResult
from trading_core.exceptions import (
    AuthenticationError,
    ExchangeRateLimited,
    ExchangeError,
    ExchangeTimeout,
    ExchangeUnavailable,
    InvalidExchangeData,
    UnsupportedCapability,
)
from trading_core.exchanges import Capability, ExchangeConfig
from trading_core.exchanges.ccxt import CCXTAdapter
from trading_core.models import (
    ContractType,
    FundingRate,
    Instrument,
    MarketType,
    OrderBook,
    Ticker,
    TopOfBook,
)
from trading_core.normalization import RawAmountUnit
from trading_core.normalization.ccxt import normalize_ccxt_instrument


def instrument_for(exchange_id: str) -> Instrument:
    client = make_fake_client(exchange_id)
    market = next(iter(client.markets.values()))
    return normalize_ccxt_instrument(market, venue=exchange_id)


def configure_bulk_binance_client(client: FakeCCXTClient) -> tuple[Instrument, ...]:
    """Configure three canonical markets and their unified bulk responses."""
    markets: dict[str, dict[str, object]] = {}
    funding_rates: dict[str, object] = {}
    top_of_books: dict[str, object] = {}
    specifications = (
        ("BTC", "0.0001", 1_786_460_400_000, "100000", "2", "100003", "4"),
        ("ETH", "-0.00005", 1_786_460_401_000, "2000", "3", "2001", "5"),
        ("SOL", "0.0002", 1_786_460_402_000, "150", "6", "151", "7"),
    )
    for (
        base,
        rate,
        next_timestamp,
        bid,
        bid_volume,
        ask,
        ask_volume,
    ) in specifications:
        symbol = f"{base}/USDT:USDT"
        market = replace_payload(
            BINANCE_MARKET,
            id=f"{base}USDT",
            symbol=symbol,
            base=base,
        )
        markets[symbol] = market
        funding_rates[symbol] = replace_payload(
            BINANCE_FUNDING,
            symbol=symbol,
            fundingRate=rate,
            interval="8h",
            nextFundingTimestamp=next_timestamp,
        )
        top_of_books[symbol] = replace_payload(
            BINANCE_TOP_OF_BOOK,
            symbol=symbol,
            bid=bid,
            bidVolume=bid_volume,
            ask=ask,
            askVolume=ask_volume,
        )
    client.markets = markets
    client.funding_rates = funding_rates
    client.top_of_books = top_of_books
    return tuple(
        normalize_ccxt_instrument(market, venue="binance")
        for market in markets.values()
    )


def configure_binance_bulk_shape_without_intervals(
    client: FakeCCXTClient,
) -> tuple[Instrument, ...]:
    """Use Binance's bulk rate shape with interval metadata in its own response."""
    instruments = configure_bulk_binance_client(client)
    interval_payloads: dict[str, object] = {}
    for symbol, payload in tuple(client.funding_rates.items()):
        rate_payload = dict(cast(Mapping[str, object], payload))
        rate_payload.pop("interval", None)
        client.funding_rates[symbol] = rate_payload
        interval_payloads[symbol] = replace_payload(
            BINANCE_FUNDING,
            symbol=symbol,
            interval="8h",
        )
    client.funding_intervals = interval_payloads
    return instruments


def test_ccxt_namespace_import_is_lazy_and_default_construction_is_optional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sys.modules.pop("ccxt", None)
    sys.modules.pop("ccxt.async_support", None)
    sys.modules.pop("trading_core.exchanges.ccxt.adapter", None)
    sys.modules.pop("trading_core.exchanges.ccxt", None)
    real_import = builtins.__import__

    def block_ccxt_import(
        name: str,
        globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "ccxt" or name.startswith("ccxt."):
            raise ModuleNotFoundError("blocked optional ccxt import")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", block_ccxt_import)

    module = importlib.import_module("trading_core.exchanges.ccxt")
    injected = module.CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(),
    )

    assert injected.venue == "binance"
    assert "ccxt" not in sys.modules
    with pytest.raises(ModuleNotFoundError, match="optional"):
        module.CCXTAdapter("binance", ExchangeConfig(venue="binance"))


@pytest.mark.parametrize("exchange_id", ["binance", "hyperliquid"])
def test_generic_factory_receives_explicit_exchange_config(exchange_id: str) -> None:
    factory = FakeCCXTFactory()
    secret = "credential-value-that-must-not-render"
    config = ExchangeConfig(
        venue=exchange_id,
        credentials={"apiKey": secret, "secret": "second-secret"},
        timeout=timedelta(milliseconds=1250),
        sandbox=True,
    )

    adapter = CCXTAdapter(exchange_id, config, client_factory=factory)

    assert type(adapter) is CCXTAdapter
    assert factory.calls == [
        (
            exchange_id,
            {
                "apiKey": secret,
                "secret": "second-secret",
                "timeout": 1250,
                "enableRateLimit": True,
            },
        )
    ]
    assert factory.clients[exchange_id].sandbox_modes == [True]
    assert secret not in repr(adapter)


@pytest.mark.parametrize("exchange_id", ["binance", "hyperliquid"])
def test_capabilities_follow_current_ccxt_metadata_without_venue_subclasses(
    exchange_id: str,
) -> None:
    client = make_fake_client(exchange_id)
    factory = FakeCCXTFactory(clients={exchange_id: client})

    adapter = CCXTAdapter(
        exchange_id,
        ExchangeConfig(venue=exchange_id),
        client_factory=factory,
    )

    expected = {
        Capability.TICKER_SNAPSHOT,
        Capability.ORDER_BOOK_SNAPSHOT,
        Capability.FUNDING_SNAPSHOT,
        Capability.BULK_FUNDING,
        Capability.INSTRUMENT_CATALOG,
    }
    if exchange_id == "binance":
        expected.add(Capability.BULK_TOP_OF_BOOK)
    assert adapter.capabilities == frozenset(expected)
    if exchange_id == "hyperliquid":
        assert client.has["fetchFundingRate"] is False
        assert client.has["fetchFundingRates"] is True


def test_bulk_funding_capability_requires_ccxt_bulk_metadata() -> None:
    client = make_fake_client("binance")
    client.has = {**client.has, "fetchFundingRates": False}
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    assert Capability.FUNDING_SNAPSHOT in adapter.capabilities
    assert Capability.BULK_FUNDING not in adapter.capabilities

    with pytest.raises(UnsupportedCapability):
        asyncio.run(adapter.fetch_funding_rates((instrument_for("binance"),)))

    assert client.calls == []


def test_fetch_funding_rates_normalizes_one_ccxt_bulk_response() -> None:
    client = make_fake_client("binance")
    instruments = configure_bulk_binance_client(client)
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    result = asyncio.run(adapter.fetch_funding_rates(instruments))

    assert isinstance(result, CollectionResult)
    assert tuple(item.instrument for item in result.data) == instruments
    assert tuple(item.rate for item in result.data) == (
        Decimal("0.0001"),
        Decimal("-0.00005"),
        Decimal("0.0002"),
    )
    assert result.data[0].next_funding_at is not None
    assert result.data[0].next_funding_at.timestamp() == 1_786_460_400
    assert result.data[1].exchange_timestamp is not None
    assert result.errors == ()
    assert result.requested_count == 3
    assert result.successful_count == 3
    assert result.failed_count == 0
    assert client.calls == [
        ("load_markets", None),
        ("fetch_funding_rates", None),
    ]
    assert client.bulk_funding_symbols == [
        tuple(instrument.venue_symbol for instrument in instruments)
    ]


def test_fetch_funding_rates_uses_bulk_interval_metadata_when_rate_omits_interval() -> (
    None
):
    client = make_fake_client("binance")
    instruments = configure_binance_bulk_shape_without_intervals(client)
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    result = asyncio.run(adapter.fetch_funding_rates(instruments))

    assert tuple(item.instrument for item in result.data) == instruments
    assert tuple(item.rate for item in result.data) == (
        Decimal("0.0001"),
        Decimal("-0.00005"),
        Decimal("0.0002"),
    )
    assert tuple(item.interval for item in result.data) == (
        timedelta(hours=8),
        timedelta(hours=8),
        timedelta(hours=8),
    )
    assert result.errors == ()
    assert client.calls == [
        ("load_markets", None),
        ("fetch_funding_rates", None),
        ("fetch_funding_intervals", None),
    ]


def test_fetch_funding_rates_reports_missing_items_without_silent_omission() -> None:
    client = make_fake_client("binance")
    instruments = configure_bulk_binance_client(client)
    del client.funding_rates[instruments[2].venue_symbol]
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    result = asyncio.run(adapter.fetch_funding_rates(instruments))

    assert tuple(item.instrument for item in result.data) == instruments[:2]
    assert len(result.errors) == 1
    assert result.errors[0].instrument == instruments[2]
    assert isinstance(result.errors[0].error, InvalidExchangeData)
    assert result.errors[0].operation == "fetch_funding_rates"
    assert result.partial is True
    assert result.requested_count == 3
    assert result.successful_count == 2
    assert result.failed_count == 1


def test_fetch_funding_rates_keeps_valid_items_when_one_item_is_malformed() -> None:
    client = make_fake_client("binance")
    instruments = configure_bulk_binance_client(client)
    client.funding_rates[instruments[1].venue_symbol] = replace_payload(
        cast(Mapping[str, object], client.funding_rates[instruments[1].venue_symbol]),
        fundingRate="not-a-decimal",
    )
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    result = asyncio.run(adapter.fetch_funding_rates(instruments))

    assert tuple(item.instrument for item in result.data) == (
        instruments[0],
        instruments[2],
    )
    assert len(result.errors) == 1
    assert result.errors[0].instrument == instruments[1]
    assert isinstance(result.errors[0].error, InvalidExchangeData)
    assert result.failed_count == 1


def test_fetch_funding_rates_maps_whole_backend_failures_to_stable_errors() -> None:
    client = make_fake_client(
        "binance",
        errors={"fetch_funding_rates": RequestTimeout("apiKey=bulk-secret")},
    )
    instruments = (instrument_for("binance"),)
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    with pytest.raises(ExchangeTimeout) as error:
        asyncio.run(adapter.fetch_funding_rates(instruments))

    assert error.value.cause is client.errors["fetch_funding_rates"]
    assert "bulk-secret" not in str(error.value)
    assert client.calls == [
        ("load_markets", None),
        ("fetch_funding_rates", None),
    ]


def test_fetch_funding_rates_empty_input_does_not_load_markets_or_call_backend() -> (
    None
):
    client = make_fake_client("binance")
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    result = asyncio.run(adapter.fetch_funding_rates(()))

    assert result.data == ()
    assert result.errors == ()
    assert result.requests_made is False
    assert result.requested_count == 0
    assert client.calls == []


def test_fetch_funding_rates_deduplicates_symbols_in_first_seen_order() -> None:
    client = make_fake_client("binance")
    instruments = configure_bulk_binance_client(client)
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    result = asyncio.run(
        adapter.fetch_funding_rates((instruments[0], instruments[1], instruments[0]))
    )

    assert tuple(item.instrument for item in result.data) == instruments[:2]
    assert result.requested_count == 2
    assert client.bulk_funding_symbols == [
        (instruments[0].venue_symbol, instruments[1].venue_symbol)
    ]


def test_fetch_funding_rates_rejects_an_instrument_from_another_venue() -> None:
    client = make_fake_client("binance")
    instrument = replace(instrument_for("binance"), venue="hyperliquid")
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    with pytest.raises(InvalidExchangeData) as error:
        asyncio.run(adapter.fetch_funding_rates((instrument,)))

    assert error.value.venue == "binance"
    assert error.value.operation == "fetch_funding_rates"
    assert client.calls == []


def test_fetch_top_of_books_normalizes_three_items_with_one_bulk_call() -> None:
    client = make_fake_client("binance")
    instruments = configure_bulk_binance_client(client)
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    result = asyncio.run(adapter.fetch_top_of_books(instruments))

    assert isinstance(result, CollectionResult)
    assert tuple(item.instrument for item in result.data) == instruments
    assert tuple(
        (item.bid_price, item.bid_amount, item.ask_price, item.ask_amount)
        for item in result.data
    ) == (
        (Decimal("100000"), Decimal("0.002"), Decimal("100003"), Decimal("0.004")),
        (Decimal("2000"), Decimal("0.003"), Decimal("2001"), Decimal("0.005")),
        (Decimal("150"), Decimal("0.006"), Decimal("151"), Decimal("0.007")),
    )
    assert all(isinstance(item, TopOfBook) for item in result.data)
    assert all(item.received_at.tzinfo is not None for item in result.data)
    assert result.errors == ()
    assert result.requested_count == 3
    assert result.successful_count == 3
    assert result.failed_count == 0
    assert client.calls == [
        ("load_markets", None),
        ("fetch_bids_asks", None),
    ]
    assert client.bulk_top_of_book_symbols == [
        tuple(instrument.venue_symbol for instrument in instruments)
    ]


def test_fetch_top_of_books_reports_missing_items_without_silent_omission() -> None:
    client = make_fake_client("binance")
    instruments = configure_bulk_binance_client(client)
    del client.top_of_books[instruments[2].venue_symbol]
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    result = asyncio.run(adapter.fetch_top_of_books(instruments))

    assert tuple(item.instrument for item in result.data) == instruments[:2]
    assert len(result.errors) == 1
    assert result.errors[0].instrument == instruments[2]
    assert isinstance(result.errors[0].error, InvalidExchangeData)
    assert result.errors[0].operation == "fetch_top_of_books"
    assert result.partial is True
    assert result.requested_count == 3
    assert result.successful_count == 2
    assert result.failed_count == 1


def test_fetch_top_of_books_keeps_valid_items_when_one_item_is_malformed() -> None:
    client = make_fake_client("binance")
    instruments = configure_bulk_binance_client(client)
    client.top_of_books[instruments[1].venue_symbol] = replace_payload(
        cast(Mapping[str, object], client.top_of_books[instruments[1].venue_symbol]),
        bidVolume="not-a-decimal",
    )
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    result = asyncio.run(adapter.fetch_top_of_books(instruments))

    assert tuple(item.instrument for item in result.data) == (
        instruments[0],
        instruments[2],
    )
    assert len(result.errors) == 1
    assert result.errors[0].instrument == instruments[1]
    assert isinstance(result.errors[0].error, InvalidExchangeData)
    assert result.failed_count == 1


def test_fetch_top_of_books_accepts_zero_amounts_but_rejects_invalid_numbers() -> None:
    client = make_fake_client("binance")
    instruments = configure_bulk_binance_client(client)
    client.top_of_books[instruments[0].venue_symbol] = replace_payload(
        cast(Mapping[str, object], client.top_of_books[instruments[0].venue_symbol]),
        bidVolume="0",
        askVolume="0",
    )
    client.top_of_books[instruments[1].venue_symbol] = replace_payload(
        cast(Mapping[str, object], client.top_of_books[instruments[1].venue_symbol]),
        ask="NaN",
    )
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    result = asyncio.run(adapter.fetch_top_of_books(instruments))

    assert result.data[0].bid_amount == Decimal("0")
    assert result.data[0].ask_amount == Decimal("0")
    assert [error.instrument for error in result.errors] == [instruments[1]]


def test_fetch_top_of_books_keeps_valid_items_when_contract_metadata_is_unusable() -> (
    None
):
    client = make_fake_client("binance")
    instruments = configure_bulk_binance_client(client)
    client.markets = dict(client.markets)
    client.markets[instruments[1].venue_symbol] = replace_payload(
        client.markets[instruments[1].venue_symbol],
        contractSize=None,
    )
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    result = asyncio.run(adapter.fetch_top_of_books(instruments))

    assert tuple(item.instrument for item in result.data) == (
        instruments[0],
        instruments[2],
    )
    assert [error.instrument for error in result.errors] == [instruments[1]]
    assert isinstance(result.errors[0].error, InvalidExchangeData)
    assert result.errors[0].error.operation == "normalize_ccxt_bulk_top_of_book"
    assert result.partial is True


def test_fetch_top_of_books_deduplicates_symbols_in_first_seen_order() -> None:
    client = make_fake_client("binance")
    instruments = configure_bulk_binance_client(client)
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    result = asyncio.run(
        adapter.fetch_top_of_books((instruments[0], instruments[1], instruments[0]))
    )

    assert tuple(item.instrument for item in result.data) == instruments[:2]
    assert result.requested_count == 2
    assert client.bulk_top_of_book_symbols == [
        (instruments[0].venue_symbol, instruments[1].venue_symbol)
    ]


def test_fetch_top_of_books_associates_payload_symbol_when_mapping_key_differs() -> (
    None
):
    client = make_fake_client("binance")
    instruments = configure_bulk_binance_client(client)
    client.bulk_top_of_book_response = {
        "BTCUSDT": replace_payload(
            cast(
                Mapping[str, object], client.top_of_books[instruments[0].venue_symbol]
            ),
            symbol=instruments[0].venue_symbol,
        )
    }
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    result = asyncio.run(adapter.fetch_top_of_books((instruments[0],)))

    assert result.errors == ()
    assert result.data[0].instrument == instruments[0]
    assert result.data[0].bid_price == Decimal("100000")


def test_fetch_top_of_books_empty_input_does_not_load_markets_or_call_backend() -> None:
    client = make_fake_client("binance")
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    result = asyncio.run(adapter.fetch_top_of_books(()))

    assert result.data == ()
    assert result.errors == ()
    assert result.requests_made is False
    assert result.requested_count == 0
    assert client.calls == []


def test_fetch_top_of_books_rejects_an_instrument_from_another_venue() -> None:
    client = make_fake_client("binance")
    instrument = replace(instrument_for("binance"), venue="hyperliquid")
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    with pytest.raises(InvalidExchangeData) as error:
        asyncio.run(adapter.fetch_top_of_books((instrument,)))

    assert error.value.venue == "binance"
    assert error.value.operation == "fetch_top_of_books"
    assert client.calls == []


def test_fetch_top_of_books_maps_whole_backend_failures_to_stable_errors() -> None:
    client = make_fake_client(
        "binance",
        errors={"fetch_bids_asks": RequestTimeout("apiKey=bulk-secret")},
    )
    instrument = instrument_for("binance")
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    with pytest.raises(ExchangeTimeout) as error:
        asyncio.run(adapter.fetch_top_of_books((instrument,)))

    assert error.value.cause is client.errors["fetch_bids_asks"]
    assert "bulk-secret" not in str(error.value)
    assert client.calls == [
        ("load_markets", None),
        ("fetch_bids_asks", None),
    ]


def test_hyperliquid_does_not_advertise_unsupported_bulk_top_of_book() -> None:
    client = make_fake_client("hyperliquid")
    adapter = CCXTAdapter(
        "hyperliquid",
        ExchangeConfig(venue="hyperliquid"),
        client_factory=FakeCCXTFactory(clients={"hyperliquid": client}),
    )

    assert Capability.BULK_TOP_OF_BOOK not in adapter.capabilities
    with pytest.raises(UnsupportedCapability) as error:
        asyncio.run(adapter.fetch_top_of_books((instrument_for("hyperliquid"),)))

    assert error.value.operation == "fetch_top_of_books"
    assert client.calls == []


def test_list_instruments_returns_canonical_catalog_and_reuses_market_cache() -> None:
    client = make_fake_client("binance")
    spot_market = replace_payload(
        BINANCE_MARKET,
        id="BTCUSDT-SPOT",
        symbol="BTC/USDT",
        spot=True,
        swap=False,
        contract=False,
        settle=None,
        linear=False,
        inverse=False,
        contractSize=None,
    )
    future_market = replace_payload(
        BINANCE_MARKET,
        id="BTCUSDT-FUTURE",
        symbol="BTC/USDT:USDT-20261231",
        spot=False,
        swap=False,
        contract=True,
        future=True,
        linear=True,
        inverse=False,
    )
    client.markets = {
        "BTC/USDT:USDT": BINANCE_MARKET,
        "BTC/USDT": spot_market,
        "BTC/USDT:USDT-20261231": future_market,
    }
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    async def list_twice() -> tuple[tuple[Instrument, ...], tuple[Instrument, ...]]:
        return await adapter.list_instruments(), await adapter.list_instruments()

    first, second = asyncio.run(list_twice())

    expected = (
        Instrument(
            venue="binance",
            venue_symbol="BTC/USDT:USDT",
            base="BTC",
            quote="USDT",
            settlement="USDT",
            market_type=MarketType.PERPETUAL,
            contract_type=ContractType.LINEAR,
        ),
        Instrument(
            venue="binance",
            venue_symbol="BTC/USDT",
            base="BTC",
            quote="USDT",
            settlement="USDT",
            market_type=MarketType.SPOT,
            contract_type=ContractType.NONE,
        ),
    )
    assert first == expected
    assert second == expected
    assert all(isinstance(instrument, Instrument) for instrument in first)
    assert all("backend" not in repr(instrument) for instrument in first)
    assert client.calls == [("load_markets", None)]


def test_list_instruments_preserves_explicitly_inactive_markets() -> None:
    client = make_fake_client("binance")
    inactive_market = replace_payload(
        BINANCE_MARKET,
        id="OLDCOINUSDT",
        symbol="OLDCOIN/USDT:USDT",
        base="OLDCOIN",
        active=False,
    )
    client.markets = {
        "BTC/USDT:USDT": BINANCE_MARKET,
        "OLDCOIN/USDT:USDT": inactive_market,
    }
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    instruments = asyncio.run(adapter.list_instruments())

    assert tuple((item.base, item.is_active) for item in instruments) == (
        ("BTC", True),
        ("OLDCOIN", False),
    )


def test_list_instruments_fails_closed_for_malformed_representable_market() -> None:
    client = make_fake_client("binance")
    malformed = replace_payload(
        BINANCE_MARKET,
        id="ETHUSDT",
        symbol="ETH/USDT:USDT",
        base="ETH",
        settle=None,
    )
    client.markets = {
        "BTC/USDT:USDT": BINANCE_MARKET,
        "ETH/USDT:USDT": malformed,
    }
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    with pytest.raises(InvalidExchangeData) as error:
        asyncio.run(adapter.list_instruments())

    assert error.value.venue == "binance"
    assert error.value.operation == "normalize_ccxt_instruments"
    assert client.calls == [("load_markets", None)]


def test_list_instruments_maps_market_loading_errors_without_raw_details() -> None:
    backend_error = BadResponse("apiKey=market-secret")
    client = make_fake_client("binance", errors={"load_markets": backend_error})
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    with pytest.raises(InvalidExchangeData) as error:
        asyncio.run(adapter.list_instruments())

    assert error.value.operation == "list_instruments"
    assert error.value.cause is backend_error
    assert "market-secret" not in str(error.value)


def test_snapshot_methods_forward_venue_symbol_normalize_and_load_markets_once() -> (
    None
):
    client = make_fake_client("binance")
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance", timeout=timedelta(seconds=1)),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )
    instrument = instrument_for("binance")

    async def fetch_all() -> tuple[Ticker, OrderBook, FundingRate]:
        return (
            await adapter.fetch_ticker(instrument),
            await adapter.fetch_order_book(instrument),
            await adapter.fetch_funding_rate(instrument),
        )

    ticker, order_book, funding = asyncio.run(fetch_all())

    assert isinstance(ticker, Ticker)
    assert isinstance(order_book, OrderBook)
    assert isinstance(funding, FundingRate)
    assert ticker.instrument == instrument
    assert order_book.instrument == instrument
    assert funding.instrument == instrument
    assert client.calls == [
        ("load_markets", None),
        ("fetch_ticker", "BTC/USDT:USDT"),
        ("fetch_order_book", "BTC/USDT:USDT"),
        ("fetch_order_book", "BTC/USDT:USDT"),
        ("fetch_funding_rate", "BTC/USDT:USDT"),
        ("fetch_funding_interval", "BTC/USDT:USDT"),
    ]
    assert all(
        value.received_at.tzinfo is not None for value in (ticker, order_book, funding)
    )


def test_hyperliquid_uses_the_same_generic_snapshot_path() -> None:
    client = make_fake_client("hyperliquid")
    adapter = CCXTAdapter(
        "hyperliquid",
        ExchangeConfig(venue="hyperliquid"),
        client_factory=FakeCCXTFactory(clients={"hyperliquid": client}),
    )
    instrument = instrument_for("hyperliquid")

    async def fetch_all() -> tuple[Ticker, OrderBook, FundingRate]:
        return await asyncio.gather(
            adapter.fetch_ticker(instrument),
            adapter.fetch_order_book(instrument),
            adapter.fetch_funding_rate(instrument),
        )

    ticker, order_book, funding = asyncio.run(fetch_all())

    assert ticker.bid == Decimal("100101")
    assert ticker.ask == Decimal("100102")
    assert order_book.bids[0].amount == Decimal("1")
    assert funding.interval == timedelta(hours=1)
    assert funding.rate == Decimal("-0.00005")
    assert [call for call in client.calls if call[0] == "load_markets"] == [
        ("load_markets", None)
    ]
    assert [call for call in client.calls if call[0] == "fetch_order_book"] == [
        ("fetch_order_book", "BTC/USDC:USDC"),
        ("fetch_order_book", "BTC/USDC:USDC"),
    ]


def test_emulated_ticker_with_null_order_book_fails_closed() -> None:
    client = make_fake_client("hyperliquid")
    client.order_book = None
    adapter = CCXTAdapter(
        "hyperliquid",
        ExchangeConfig(venue="hyperliquid"),
        client_factory=FakeCCXTFactory(clients={"hyperliquid": client}),
    )

    with pytest.raises(InvalidExchangeData) as error:
        asyncio.run(adapter.fetch_ticker(instrument_for("hyperliquid")))

    assert error.value.operation == "normalize_ccxt_ticker"
    assert client.calls == [
        ("load_markets", None),
        ("fetch_ticker", "BTC/USDC:USDC"),
        ("fetch_order_book", "BTC/USDC:USDC"),
    ]


def test_complete_non_emulated_ticker_uses_direct_prices_without_fallback() -> None:
    client = make_fake_client("binance")
    client.ticker = replace_payload(
        BINANCE_TICKER,
        bid="99999",
        ask="100003",
    )
    client.order_book = None
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    ticker = asyncio.run(adapter.fetch_ticker(instrument_for("binance")))

    assert ticker.bid == Decimal("99999")
    assert ticker.ask == Decimal("100003")
    assert client.calls == [
        ("load_markets", None),
        ("fetch_ticker", "BTC/USDT:USDT"),
    ]


def test_ticker_fallback_rejects_order_book_without_symbol() -> None:
    client = make_fake_client("binance")
    raw_order_book = dict(BINANCE_ORDER_BOOK)
    del raw_order_book["symbol"]
    client.order_book = raw_order_book
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    with pytest.raises(InvalidExchangeData) as error:
        asyncio.run(adapter.fetch_ticker(instrument_for("binance")))

    assert error.value.operation == "normalize_ccxt_ticker"


@pytest.mark.parametrize(
    "order_book_changes",
    [
        pytest.param(
            {"bids": [["100000", "2"], ["100001", "0"]]},
            id="zero-best-bid-amount",
        ),
        pytest.param(
            {"asks": [["100003", "4"], ["100002", None]]},
            id="none-best-ask-amount",
        ),
        pytest.param(
            {"bids": [["100000", "2"], ["100001", "not-numeric"]]},
            id="invalid-best-bid-amount",
        ),
        pytest.param(
            {"asks": [["100003", "4"], ["100002", "-1"]]},
            id="negative-best-ask-amount",
        ),
    ],
)
def test_ticker_fallback_rejects_invalid_best_level_amounts(
    order_book_changes: dict[str, object],
) -> None:
    client = make_fake_client("binance")
    client.order_book = replace_payload(BINANCE_ORDER_BOOK, **order_book_changes)
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    with pytest.raises(InvalidExchangeData) as error:
        asyncio.run(adapter.fetch_ticker(instrument_for("binance")))

    assert error.value.operation == "normalize_ccxt_ticker"


def test_ticker_capability_requires_order_book_fallback_support() -> None:
    client = make_fake_client(
        "binance",
        has={
            "fetchTicker": True,
            "fetchOrderBook": False,
            "fetchFundingRate": False,
            "fetchFundingRates": False,
        },
    )
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    assert Capability.TICKER_SNAPSHOT not in adapter.capabilities
    with pytest.raises(UnsupportedCapability):
        asyncio.run(adapter.fetch_ticker(instrument_for("binance")))
    assert client.calls == []


@pytest.mark.parametrize(
    ("market_changes", "venue_symbol", "contract_type"),
    [
        pytest.param(
            {"contractSize": None},
            "BTC/USDT:USDT",
            ContractType.LINEAR,
            id="linear-missing-contract-size",
        ),
        pytest.param(
            {
                "linear": False,
                "inverse": True,
                "contractSize": "100",
            },
            "BTC/USDT:USDT",
            ContractType.INVERSE,
            id="inverse",
        ),
        pytest.param(
            {
                "symbol": "BTC/USDT",
                "settle": None,
                "spot": True,
                "swap": False,
                "contract": False,
                "linear": False,
                "inverse": False,
                "contractSize": None,
            },
            "BTC/USDT",
            ContractType.NONE,
            id="spot",
        ),
    ],
)
def test_ticker_capability_runtime_uses_price_only_fallback_for_market_type(
    market_changes: dict[str, object],
    venue_symbol: str,
    contract_type: ContractType,
) -> None:
    market = replace_payload(BINANCE_MARKET, **market_changes)
    client = make_fake_client("binance")
    client.markets = {venue_symbol: market}
    client.ticker = replace_payload(BINANCE_TICKER, symbol=venue_symbol)
    client.order_book = replace_payload(BINANCE_ORDER_BOOK, symbol=venue_symbol)
    instrument = normalize_ccxt_instrument(market, venue="binance")
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    ticker = asyncio.run(adapter.fetch_ticker(instrument))

    assert Capability.TICKER_SNAPSHOT in adapter.capabilities
    assert ticker.instrument is instrument
    assert ticker.instrument.contract_type is contract_type
    assert ticker.bid == Decimal("100001")
    assert ticker.ask == Decimal("100002")
    assert client.calls == [
        ("load_markets", None),
        ("fetch_ticker", venue_symbol),
        ("fetch_order_book", venue_symbol),
    ]


def test_binance_blank_rate_interval_uses_current_ccxt_interval_api() -> None:
    client = make_fake_client("binance")
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    funding = asyncio.run(adapter.fetch_funding_rate(instrument_for("binance")))

    assert funding.interval == timedelta(hours=8)
    assert client.calls == [
        ("load_markets", None),
        ("fetch_funding_rate", "BTC/USDT:USDT"),
        ("fetch_funding_interval", "BTC/USDT:USDT"),
    ]


def test_unsupported_capability_fails_before_client_or_market_calls() -> None:
    client = make_fake_client(
        "binance",
        has={
            "fetchTicker": False,
            "fetchOrderBook": True,
            "fetchFundingRate": False,
            "fetchFundingRates": False,
        },
    )
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    with pytest.raises(UnsupportedCapability) as error:
        asyncio.run(adapter.fetch_ticker(instrument_for("binance")))

    assert error.value.venue == "binance"
    assert error.value.operation == "fetch_ticker"
    assert client.calls == []


def test_market_metadata_must_match_the_exact_canonical_instrument() -> None:
    client = make_fake_client("binance")
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )
    wrong_symbol = replace(instrument_for("binance"), venue_symbol="BTC-USDT-unparsed")

    with pytest.raises(InvalidExchangeData):
        asyncio.run(adapter.fetch_ticker(wrong_symbol))

    assert client.calls == [("load_markets", None)]


def test_market_lookup_returns_only_typed_normalized_metadata() -> None:
    client = make_fake_client("binance")
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )
    instrument = instrument_for("binance")

    metadata = asyncio.run(adapter._market_for(instrument, operation="test"))

    assert inspect.signature(CCXTAdapter._market_for).return_annotation == (
        "CCXTMarketMetadata"
    )
    assert is_dataclass(metadata)
    assert not isinstance(metadata, Mapping)
    assert {field.name for field in fields(metadata)} == {
        "instrument",
        "amount_unit",
        "contract_metadata",
    }
    assert metadata.instrument is instrument
    assert metadata.amount_unit is RawAmountUnit.CONTRACT
    assert metadata.contract_metadata is not None
    assert metadata.contract_metadata.multiplier == Decimal("0.001")
    assert all(
        not isinstance(getattr(metadata, field.name), Mapping)
        for field in fields(metadata)
    )
    assert "backend" not in repr(metadata)


def test_adapter_enforces_exchange_config_timeout_around_client_awaits() -> None:
    client = make_fake_client("binance", delays={"fetch_ticker": 0.05})
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance", timeout=timedelta(milliseconds=1)),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    with pytest.raises(ExchangeTimeout) as error:
        asyncio.run(adapter.fetch_ticker(instrument_for("binance")))

    assert isinstance(error.value.cause, TimeoutError)


@pytest.mark.parametrize(
    ("backend_error", "stable_type"),
    [
        (RequestTimeout("apiKey=timeout-secret"), ExchangeTimeout),
        (RateLimitExceeded("apiKey=rate-secret"), ExchangeRateLimited),
        (ExchangeNotAvailable("apiKey=unavailable-secret"), ExchangeUnavailable),
        (CCXTAuthenticationError("apiKey=auth-secret"), AuthenticationError),
        (BadResponse("apiKey=payload-secret"), InvalidExchangeData),
    ],
)
def test_ccxt_failures_map_to_stable_secret_safe_errors(
    backend_error: BaseException,
    stable_type: type[ExchangeError],
) -> None:
    client = make_fake_client("binance", errors={"fetch_ticker": backend_error})
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    with pytest.raises(stable_type) as error:
        asyncio.run(adapter.fetch_ticker(instrument_for("binance")))

    assert error.value.cause is backend_error
    assert "secret" not in str(error.value)
    assert "apiKey" not in str(error.value)


def test_adapter_close_uses_the_async_client_boundary() -> None:
    client = make_fake_client("binance")
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )

    asyncio.run(adapter.close())

    assert client.calls == [("close", None)]


def test_public_adapter_results_and_annotations_never_expose_raw_backend_values() -> (
    None
):
    client = make_fake_client("binance")
    adapter = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance"),
        client_factory=FakeCCXTFactory(clients={"binance": client}),
    )
    instrument = instrument_for("binance")

    async def fetch_all() -> tuple[Ticker, OrderBook, FundingRate]:
        return (
            await adapter.fetch_ticker(instrument),
            await adapter.fetch_order_book(instrument),
            await adapter.fetch_funding_rate(instrument),
        )

    results = asyncio.run(fetch_all())
    return_annotations = {
        name: inspect.signature(getattr(CCXTAdapter, name)).return_annotation
        for name in ("fetch_ticker", "fetch_order_book", "fetch_funding_rate")
    }

    assert return_annotations == {
        "fetch_ticker": "Ticker",
        "fetch_order_book": "OrderBook",
        "fetch_funding_rate": "FundingRate",
    }
    assert all(is_dataclass(result) for result in results)
    assert all(
        not isinstance(getattr(result, field.name), dict)
        for result in results
        for field in fields(result)
    )
    assert all("do-not-export" not in repr(result) for result in results)
    assert not any(
        hasattr(adapter, name)
        for name in ("create_order", "cancel_order", "fetch_balance")
    )
