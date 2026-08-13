from __future__ import annotations

import asyncio
import builtins
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol, cast

import pytest

from tests.contracts.fixtures import make_contract_instrument
from tests.support.fake_ccxt import (
    FakeCCXTFactory,
    make_fake_client,
)
from tests.support.fake_providers import MockNativeAdapter
from trading_core.collectors import CollectionResult
from trading_core.exceptions import (
    TradingCoreError,
    UnsupportedCapability,
)
from trading_core.exchanges import (
    BulkFundingProvider,
    BulkTopOfBookProvider,
    Capability,
    ExchangeConfig,
    FundingProvider,
    InstrumentCatalogProvider,
    OrderBookProvider,
    TickerProvider,
)
from trading_core.exchanges.ccxt import CCXTAdapter
from trading_core.models import FundingRate, Instrument, OrderBook, Ticker, TopOfBook
from trading_core.normalization.ccxt import normalize_ccxt_instrument


class _SnapshotProvider(
    TickerProvider,
    OrderBookProvider,
    FundingProvider,
    BulkFundingProvider,
    BulkTopOfBookProvider,
    InstrumentCatalogProvider,
    Protocol,
):
    async def close(self) -> None:
        """Close the provider's owned backend when applicable."""


class _ProviderCaseFactory(Protocol):
    def __call__(
        self,
        *,
        disabled: Capability | None = None,
        error: BaseException | None = None,
    ) -> tuple[_SnapshotProvider, Instrument]:
        """Build a provider and the instrument it serves."""


@dataclass(frozen=True, slots=True)
class _ProviderCase:
    name: str
    build: _ProviderCaseFactory


def _build_ccxt_provider(
    *,
    disabled: Capability | None = None,
    error: BaseException | None = None,
) -> tuple[_SnapshotProvider, Instrument]:
    client = make_fake_client(
        "binance",
        errors={} if error is None else {"fetch_ticker": error},
    )
    has = dict(client.has)
    if disabled is Capability.TICKER_SNAPSHOT:
        has["fetchTicker"] = False
    elif disabled is Capability.ORDER_BOOK_SNAPSHOT:
        has["fetchOrderBook"] = False
    elif disabled is Capability.FUNDING_SNAPSHOT:
        has["fetchFundingRate"] = False
        has["fetchFundingRates"] = False
    elif disabled is Capability.BULK_FUNDING:
        has["fetchFundingRates"] = False
    elif disabled is Capability.BULK_TOP_OF_BOOK:
        has["fetchBidsAsks"] = False
    client.has = has
    factory = FakeCCXTFactory(clients={"binance": client})
    provider = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance", timeout=timedelta(seconds=1)),
        client_factory=factory,
    )
    market = next(iter(client.markets.values()))
    return cast(_SnapshotProvider, provider), normalize_ccxt_instrument(
        market, venue="binance"
    )


def _build_native_provider(
    *,
    disabled: Capability | None = None,
    error: BaseException | None = None,
) -> tuple[_SnapshotProvider, Instrument]:
    capabilities = frozenset(
        {
            Capability.TICKER_SNAPSHOT,
            Capability.ORDER_BOOK_SNAPSHOT,
            Capability.FUNDING_SNAPSHOT,
            Capability.BULK_FUNDING,
            Capability.BULK_TOP_OF_BOOK,
            Capability.INSTRUMENT_CATALOG,
        }
    )
    if disabled is not None:
        capabilities -= {disabled}
    return (
        cast(
            _SnapshotProvider,
            MockNativeAdapter(
                venue="mock-native",
                capabilities=capabilities,
                errors={} if error is None else {"fetch_ticker": error},
            ),
        ),
        make_contract_instrument("mock-native"),
    )


@pytest.fixture(params=("ccxt", "native"), ids=("ccxt", "native"))
def provider_case(request: pytest.FixtureRequest) -> _ProviderCase:
    build = _build_ccxt_provider if request.param == "ccxt" else _build_native_provider
    return _ProviderCase(name=request.param, build=build)


@pytest.mark.parametrize(
    ("capability", "method_name", "model_type"),
    (
        (Capability.TICKER_SNAPSHOT, "fetch_ticker", Ticker),
        (Capability.ORDER_BOOK_SNAPSHOT, "fetch_order_book", OrderBook),
        (Capability.FUNDING_SNAPSHOT, "fetch_funding_rate", FundingRate),
        (Capability.INSTRUMENT_CATALOG, "list_instruments", tuple),
    ),
)
def test_provider_contract_returns_canonical_models_and_aware_timestamps(
    provider_case: _ProviderCase,
    capability: Capability,
    method_name: str,
    model_type: type[object],
) -> None:
    provider, instrument = provider_case.build()
    assert capability in provider.capabilities
    assert isinstance(provider.venue, str) and provider.venue

    model: Ticker | OrderBook | FundingRate | tuple[Instrument, ...]
    if method_name == "fetch_ticker":
        model = asyncio.run(provider.fetch_ticker(instrument))
    elif method_name == "fetch_order_book":
        model = asyncio.run(provider.fetch_order_book(instrument))
    elif method_name == "fetch_funding_rate":
        model = asyncio.run(provider.fetch_funding_rate(instrument))
    else:
        model = asyncio.run(provider.list_instruments())

    assert isinstance(model, model_type)
    if isinstance(model, tuple):
        assert model
        assert all(isinstance(value, Instrument) for value in model)
    else:
        assert model.instrument == instrument
        assert model.received_at.tzinfo is not None
        assert model.received_at.utcoffset() is not None
        assert (
            model.exchange_timestamp is None
            or model.exchange_timestamp.utcoffset() is not None
        )
        if isinstance(model, FundingRate):
            assert (
                model.next_funding_at is None
                or model.next_funding_at.utcoffset() is not None
            )


def test_bulk_funding_contract_is_backend_independent(
    provider_case: _ProviderCase,
) -> None:
    provider, instrument = provider_case.build()

    assert Capability.BULK_FUNDING in provider.capabilities
    result = asyncio.run(provider.fetch_funding_rates((instrument,)))

    assert isinstance(result, CollectionResult)
    assert result.data[0].instrument == instrument
    assert result.errors == ()
    assert result.requested_count == 1
    assert result.successful_count == 1
    assert result.failed_count == 0


def test_bulk_top_of_book_contract_is_backend_independent(
    provider_case: _ProviderCase,
) -> None:
    provider, instrument = provider_case.build()

    assert Capability.BULK_TOP_OF_BOOK in provider.capabilities
    result = asyncio.run(provider.fetch_top_of_books((instrument,)))

    assert isinstance(result, CollectionResult)
    assert isinstance(result.data[0], TopOfBook)
    assert result.data[0].instrument == instrument
    assert result.errors == ()
    assert result.requested_count == 1
    assert result.successful_count == 1
    assert result.failed_count == 0


@pytest.mark.parametrize(
    "capability",
    (
        Capability.TICKER_SNAPSHOT,
        Capability.ORDER_BOOK_SNAPSHOT,
        Capability.FUNDING_SNAPSHOT,
    ),
)
def test_provider_contract_unsupported_capability_is_stable_error(
    provider_case: _ProviderCase,
    capability: Capability,
) -> None:
    provider, instrument = provider_case.build(disabled=capability)
    methods: Mapping[Capability, str] = {
        Capability.TICKER_SNAPSHOT: "fetch_ticker",
        Capability.ORDER_BOOK_SNAPSHOT: "fetch_order_book",
        Capability.FUNDING_SNAPSHOT: "fetch_funding_rate",
    }

    with pytest.raises(UnsupportedCapability) as error:
        asyncio.run(getattr(provider, methods[capability])(instrument))

    assert error.value.venue == provider.venue
    assert isinstance(error.value, TradingCoreError)


def test_bulk_funding_provider_unsupported_capability_is_stable_error(
    provider_case: _ProviderCase,
) -> None:
    provider, instrument = provider_case.build(disabled=Capability.BULK_FUNDING)

    with pytest.raises(UnsupportedCapability) as error:
        asyncio.run(provider.fetch_funding_rates((instrument,)))

    assert error.value.venue == provider.venue
    assert error.value.operation == "fetch_funding_rates"
    assert isinstance(error.value, TradingCoreError)


def test_bulk_top_of_book_provider_unsupported_capability_is_stable_error(
    provider_case: _ProviderCase,
) -> None:
    provider, instrument = provider_case.build(disabled=Capability.BULK_TOP_OF_BOOK)

    with pytest.raises(UnsupportedCapability) as error:
        asyncio.run(provider.fetch_top_of_books((instrument,)))

    assert error.value.venue == provider.venue
    assert error.value.operation == "fetch_top_of_books"
    assert isinstance(error.value, TradingCoreError)


def test_native_provider_catalog_capability_can_be_disabled() -> None:
    provider = MockNativeAdapter(
        venue="mock-native",
        capabilities=frozenset(
            {
                Capability.TICKER_SNAPSHOT,
                Capability.ORDER_BOOK_SNAPSHOT,
                Capability.FUNDING_SNAPSHOT,
            }
        ),
    )

    with pytest.raises(UnsupportedCapability) as error:
        asyncio.run(provider.list_instruments())

    assert error.value.venue == provider.venue
    assert error.value.operation == "list_instruments"


def test_provider_contract_maps_backend_errors_without_leaking_backend_types(
    provider_case: _ProviderCase,
) -> None:
    provider, instrument = provider_case.build(error=RuntimeError("raw backend detail"))

    with pytest.raises(TradingCoreError) as error:
        asyncio.run(provider.fetch_ticker(instrument))

    assert "raw backend detail" not in str(error.value)


def test_provider_contract_has_explicit_close_for_owned_clients(
    provider_case: _ProviderCase,
) -> None:
    provider, _ = provider_case.build()

    asyncio.run(provider.close())

    if isinstance(provider, MockNativeAdapter):
        assert getattr(provider, "closed") is True


def test_mock_native_contract_does_not_import_ccxt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = MockNativeAdapter(venue="mock-native")
    instrument = make_contract_instrument("mock-native")
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

    result = asyncio.run(provider.fetch_ticker(instrument))

    assert isinstance(result, Ticker)
