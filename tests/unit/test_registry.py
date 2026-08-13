import asyncio
import copy
import gc
import pickle
from dataclasses import MISSING, asdict, astuple, fields, replace
from datetime import timedelta
from decimal import Decimal
from types import MappingProxyType

import pytest

from tests.contracts.fixtures import FundingTickerProvider
from tests.support.fake_providers import MockNativeAdapter
from trading_core.exchanges import Capability, ExchangeConfig, ProviderRegistry
from trading_core.models import (
    ContractType,
    FeeSource,
    Instrument,
    MarketType,
    TradingFee,
)


def make_instrument() -> Instrument:
    return Instrument(
        venue="binance",
        venue_symbol="BTC/USDT:USDT",
        base="BTC",
        quote="USDT",
        settlement="USDT",
        market_type=MarketType.PERPETUAL,
        contract_type=ContractType.LINEAR,
    )


def test_registry_accepts_a_provider_with_only_funding_and_ticker_snapshots() -> None:
    provider = FundingTickerProvider(venue="binance")
    registry = ProviderRegistry()

    registry.register(provider)

    assert registry.resolve("binance") is provider
    assert registry.supports("binance", Capability.FUNDING_SNAPSHOT)
    assert not registry.supports("binance", Capability.BULK_FUNDING)
    assert registry.supports("binance", Capability.TICKER_SNAPSHOT)
    assert not registry.supports("binance", Capability.ORDER_BOOK_SNAPSHOT)
    assert (
        asyncio.run(provider.fetch_ticker(make_instrument())).instrument
        == make_instrument()
    )
    assert (
        asyncio.run(provider.fetch_funding_rate(make_instrument())).instrument
        == make_instrument()
    )


def test_registry_detects_instrument_catalog_capability() -> None:
    provider = MockNativeAdapter(venue="mock-native")
    registry = ProviderRegistry()

    registry.register(provider)

    assert registry.supports("mock-native", Capability.INSTRUMENT_CATALOG)
    assert registry.supports("mock-native", Capability.BULK_FUNDING)
    assert registry.supports("mock-native", Capability.BULK_TOP_OF_BOOK)
    assert asyncio.run(provider.list_instruments())[0].venue == "mock-native"


def test_registry_preserves_explicit_registration_order() -> None:
    binance = FundingTickerProvider(venue="binance")
    hyperliquid = FundingTickerProvider(venue="hyperliquid")
    registry = ProviderRegistry()

    registry.register(binance)
    registry.register(hyperliquid)

    assert registry.providers() == (binance, hyperliquid)


def test_registry_rejects_a_duplicate_venue() -> None:
    registry = ProviderRegistry()
    registry.register(FundingTickerProvider(venue="binance"))

    with pytest.raises(ValueError, match="binance"):
        registry.register(FundingTickerProvider(venue="binance"))


def test_registry_returns_none_and_false_for_an_unknown_venue() -> None:
    registry = ProviderRegistry()

    assert registry.resolve("unknown") is None
    assert not registry.supports("unknown", Capability.TICKER_SNAPSHOT)


@pytest.mark.parametrize(
    "venue",
    [
        "binance",
        "binance?api_key=credential-value&token=query-token-value",
        "config={'api_secret': 'config-secret-value'}",
    ],
)
def test_registry_require_uses_a_fixed_message_without_missing_venue_context(
    venue: str,
) -> None:
    registry = ProviderRegistry()

    with pytest.raises(KeyError) as error:
        registry.require(venue)

    assert error.value.args == ("provider is not registered for venue",)
    assert str(error.value) == "'provider is not registered for venue'"
    assert venue not in str(error.value)
    assert all(venue not in str(argument) for argument in error.value.args)


def test_exchange_config_hides_credentials_from_repr_and_uses_safe_defaults() -> None:
    config = ExchangeConfig(
        venue="binance",
        credentials={"api_key": "secret-api-key", "api_secret": "secret-api-secret"},
    )

    assert "secret-api-key" not in repr(config)
    assert "secret-api-secret" not in repr(config)
    assert "credentials" not in repr(config)
    assert config.timeout == timedelta(seconds=10)
    assert config.sandbox is False
    assert config.fee_overrides == {}


def test_exchange_config_copies_mapping_inputs_before_callers_can_mutate_them() -> None:
    instrument = make_instrument()
    fee = TradingFee(
        venue="binance",
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0004"),
        source=FeeSource.CONFIG,
        instrument=instrument,
    )
    credentials = {"api_key": "test-credential-value"}
    fee_overrides = {instrument: fee}

    config = ExchangeConfig(
        venue="binance",
        credentials=credentials,
        fee_overrides=fee_overrides,
    )
    credentials["api_key"] = "mutated-credential-value"
    fee_overrides.clear()

    assert config.credentials == {"api_key": "test-credential-value"}
    assert config.fee_overrides == {instrument: fee}


def test_exchange_config_exposes_fee_overrides_through_a_read_only_mapping() -> None:
    instrument = make_instrument()
    fee = TradingFee(
        venue="binance",
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0004"),
        source=FeeSource.CONFIG,
        instrument=instrument,
    )
    config = ExchangeConfig(
        venue="binance",
        credentials={"api_key": "test-credential-value"},
        fee_overrides={instrument: fee},
    )

    assert config.fee_overrides[instrument] is fee
    assert config.credentials is not None
    assert config.credentials["api_key"] == "test-credential-value"
    with pytest.raises(TypeError):
        config.fee_overrides[instrument] = fee  # type: ignore[index]
    with pytest.raises(TypeError):
        config.credentials["api_key"] = "changed"  # type: ignore[index]


def test_exchange_config_excludes_sensitive_mappings_from_common_serialization() -> (
    None
):
    instrument = make_instrument()
    fee = TradingFee(
        venue="binance",
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0004"),
        source=FeeSource.CONFIG,
        instrument=instrument,
    )
    config = ExchangeConfig(
        venue="binance",
        credentials={"api_key": "test-credential-value"},
        fee_overrides={instrument: fee},
    )

    serialized = asdict(config)

    assert "test-credential-value" not in repr(config)
    assert "test-credential-value" not in str(config)
    assert "test-credential-value" not in repr(serialized)
    assert {field.name for field in fields(config)} == {
        "venue",
        "credentials",
        "timeout",
        "sandbox",
        "fee_overrides",
    }
    assert serialized["credentials"] != config.credentials
    assert serialized["fee_overrides"] != config.fee_overrides


def test_exchange_config_sensitive_mappings_participate_in_equality_hash_and_matching() -> (
    None
):
    instrument = make_instrument()
    fee = TradingFee(
        venue="binance",
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0004"),
        source=FeeSource.CONFIG,
        instrument=instrument,
    )
    config = ExchangeConfig(
        venue="binance",
        credentials={"api_key": "test-credential-value"},
        fee_overrides={instrument: fee},
    )
    equivalent = ExchangeConfig(
        venue="binance",
        credentials={"api_key": "test-credential-value"},
        fee_overrides={instrument: fee},
    )
    different_credentials = ExchangeConfig(
        venue="binance",
        credentials={"api_key": "different-credential-value"},
        fee_overrides={instrument: fee},
    )

    match config:
        case ExchangeConfig("binance", credentials, _, _, fee_overrides):
            assert credentials == {"api_key": "test-credential-value"}
            assert fee_overrides == {instrument: fee}
        case _:
            pytest.fail("ExchangeConfig did not expose its semantic fields")

    assert config == equivalent
    assert hash(config) == hash(equivalent)
    assert config != different_credentials


def test_exchange_config_copy_operations_and_replace_preserve_sensitive_state() -> None:
    instrument = make_instrument()
    fee = TradingFee(
        venue="binance",
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0004"),
        source=FeeSource.CONFIG,
        instrument=instrument,
    )
    config = ExchangeConfig(
        venue="binance",
        credentials={"api_key": "test-credential-value"},
        fee_overrides={instrument: fee},
    )

    shallow_copy = copy.copy(config)
    deep_copy = copy.deepcopy(config)
    updated = replace(config, timeout=timedelta(seconds=20))

    for cloned in (shallow_copy, deep_copy, updated):
        assert cloned.credentials == {"api_key": "test-credential-value"}
        assert cloned.fee_overrides == {instrument: fee}
        assert "test-credential-value" not in repr(cloned)
    assert updated.timeout == timedelta(seconds=20)


def test_exchange_config_ordinary_tuple_serialization_and_pickle_do_not_leak_credentials() -> (
    None
):
    config = ExchangeConfig(
        venue="binance",
        credentials={"api_key": "test-credential-value"},
    )

    tuple_data = astuple(config)

    assert "test-credential-value" not in repr(tuple_data)
    with pytest.raises(TypeError, match="credentials") as error:
        pickle.dumps(config)
    assert "test-credential-value" not in str(error.value)
    assert all(
        "test-credential-value" not in str(argument) for argument in error.value.args
    )


def test_exchange_config_mapping_storage_has_no_reachable_mutable_mapping() -> None:
    instrument = make_instrument()
    fee = TradingFee(
        venue="binance",
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0004"),
        source=FeeSource.CONFIG,
        instrument=instrument,
    )
    config = ExchangeConfig(
        venue="binance",
        credentials={"api_key": "test-credential-value"},
        fee_overrides={instrument: fee},
    )
    original_hash = hash(config)

    assert config.credentials is not None
    for mapping in (config.credentials, config.fee_overrides):
        pending: list[object] = [mapping]
        visited: set[int] = set()
        has_mutable_mapping = False
        while pending:
            candidate = pending.pop()
            if id(candidate) in visited:
                continue
            visited.add(id(candidate))
            if isinstance(candidate, dict):
                has_mutable_mapping = True
                break
            pending.extend(
                referent
                for referent in gc.get_referents(candidate)
                if isinstance(referent, (tuple, MappingProxyType))
            )
        assert not has_mutable_mapping

    assert config.credentials == {"api_key": "test-credential-value"}
    assert config.fee_overrides == {instrument: fee}
    assert hash(config) == original_hash

    for mapping in (config.credentials, config.fee_overrides):
        with pytest.raises(TypeError) as error:
            pickle.dumps(mapping)
        assert "test-credential-value" not in str(error.value)
        assert all(
            "test-credential-value" not in str(argument)
            for argument in error.value.args
        )


def test_exchange_config_credentials_field_metadata_matches_constructor_default() -> (
    None
):
    credentials_field = next(
        field for field in fields(ExchangeConfig) if field.name == "credentials"
    )

    assert credentials_field.default is None
    assert credentials_field.default_factory is MISSING
