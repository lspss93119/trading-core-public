from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal
from typing import cast

import trading_core.exchanges.ccxt.adapter as ccxt_adapter_module
import pytest
from tests.support.fake_ccxt import (
    FakeCCXTClient,
    FakeCCXTFactory,
    RateLimitExceeded,
    make_fake_client,
)
from trading_core.collectors import CollectionResult, FundingCollector
from trading_core.exchanges import (
    ExchangeConfig,
    FundingProvider,
    ProviderRegistry,
)
from trading_core.exchanges.ccxt import CCXTAdapter
from trading_core.matching import CompatibilityPolicy
from trading_core.models import (
    FeeSource,
    FundingRate,
    Instrument,
    MatchQuality,
    TradingFee,
)
from trading_core.normalization.ccxt import normalize_ccxt_instrument
from trading_core.opportunities import FundingArbitrageFinder
from trading_core.policies import FreshnessPolicy


AS_OF = datetime(2026, 8, 11, 11, 0, tzinfo=UTC)
COMPLETED_AT = AS_OF + timedelta(seconds=1)
HORIZON = timedelta(hours=24)
MATCHING_POLICY = CompatibilityPolicy(
    allowed_quote_settlement_pairs=frozenset(
        {frozenset({("USDT", "USDT"), ("USDC", "USDC")})}
    )
)


class FrozenDateTime:
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return AS_OF


class AdvancingClock:
    def __init__(self) -> None:
        self._calls = 0

    def __call__(self) -> datetime:
        value = AS_OF if self._calls == 0 else COMPLETED_AT
        self._calls += 1
        return value


def _instruments() -> tuple[Instrument, Instrument]:
    binance_client = make_fake_client("binance")
    hyperliquid_client = make_fake_client("hyperliquid")
    return (
        normalize_ccxt_instrument(
            next(iter(binance_client.markets.values())), venue="binance"
        ),
        normalize_ccxt_instrument(
            next(iter(hyperliquid_client.markets.values())), venue="hyperliquid"
        ),
    )


def _fees(
    binance: Instrument,
    hyperliquid: Instrument,
) -> dict[Instrument, TradingFee]:
    return {
        binance: TradingFee(
            venue="binance",
            maker_fee=None,
            taker_fee=Decimal("0.0003"),
            source=FeeSource.CONFIG,
            instrument=binance,
        ),
        hyperliquid: TradingFee(
            venue="hyperliquid",
            maker_fee=None,
            taker_fee=Decimal("0.0002"),
            source=FeeSource.API,
            instrument=hyperliquid,
        ),
    }


def _adapters(
    *,
    hyperliquid_error: BaseException | None = None,
) -> tuple[CCXTAdapter, CCXTAdapter, FakeCCXTClient, FakeCCXTClient]:
    binance_client = make_fake_client("binance")
    hyperliquid_client = make_fake_client(
        "hyperliquid",
        errors=(
            {}
            if hyperliquid_error is None
            else {"fetch_funding_rate": hyperliquid_error}
        ),
    )
    binance = CCXTAdapter(
        "binance",
        ExchangeConfig(venue="binance", timeout=timedelta(seconds=1)),
        client_factory=FakeCCXTFactory(clients={"binance": binance_client}),
    )
    hyperliquid = CCXTAdapter(
        "hyperliquid",
        ExchangeConfig(venue="hyperliquid", timeout=timedelta(seconds=1)),
        client_factory=FakeCCXTFactory(clients={"hyperliquid": hyperliquid_client}),
    )
    return binance, hyperliquid, binance_client, hyperliquid_client


def _collect_funding(
    binance: FundingProvider,
    hyperliquid: FundingProvider,
    instruments: tuple[Instrument, Instrument],
) -> CollectionResult[FundingRate]:
    registry = ProviderRegistry()
    registry.register(binance)
    registry.register(hyperliquid)
    collector = FundingCollector(timeout=timedelta(seconds=1), clock=AdvancingClock())
    return asyncio.run(
        collector.collect(
            (
                (
                    cast(FundingProvider, registry.require("binance")),
                    instruments[0],
                ),
                (
                    cast(FundingProvider, registry.require("hyperliquid")),
                    instruments[1],
                ),
            )
        )
    )


def test_funding_vertical_slice_uses_real_ccxt_normalization_and_finder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ccxt_adapter_module, "datetime", FrozenDateTime)
    instruments = _instruments()
    binance, hyperliquid, binance_client, hyperliquid_client = _adapters()

    collection = _collect_funding(
        cast(FundingProvider, binance),
        cast(FundingProvider, hyperliquid),
        instruments,
    )
    opportunities = FundingArbitrageFinder().find(
        collection.data,
        fees=_fees(*instruments),
        as_of=AS_OF,
        comparison_horizon=HORIZON,
        freshness_policy=FreshnessPolicy(max_age=timedelta(minutes=1)),
        compatibility_policy=MATCHING_POLICY,
    )

    assert collection.complete is True
    assert len(collection.data) == 2
    assert all(isinstance(item, FundingRate) for item in collection.data)
    [opportunity] = opportunities
    assert opportunity.long_funding.instrument.venue == "hyperliquid"
    assert opportunity.short_funding.instrument.venue == "binance"
    assert opportunity.long_funding.rate == Decimal("-0.00005")
    assert opportunity.short_funding.rate == Decimal("0.0001")
    assert opportunity.long_funding.interval == timedelta(hours=1)
    assert opportunity.short_funding.interval == timedelta(hours=8)
    assert opportunity.long_normalized_rate == Decimal("-0.0012")
    assert opportunity.short_normalized_rate == Decimal("0.0003")
    assert opportunity.gross_edge == Decimal("0.0015")
    assert opportunity.round_trip_fee_rate == Decimal("0.0010")
    assert opportunity.estimated_fee_adjusted_edge == Decimal("0.0005")
    assert opportunity.long_next_funding_at == AS_OF + timedelta(hours=1)
    assert opportunity.short_next_funding_at == AS_OF + timedelta(hours=4)
    assert opportunity.long_time_until_next_funding == timedelta(hours=1)
    assert opportunity.short_time_until_next_funding == timedelta(hours=4)
    assert opportunity.match_quality is MatchQuality.COMPATIBLE
    assert opportunity.long_open_fee.source is FeeSource.API
    assert opportunity.short_open_fee.source is FeeSource.CONFIG
    assert all(
        operation
        not in {
            "create_order",
            "cancel_order",
            "set_leverage",
            "transfer",
            "withdraw",
        }
        for operation, _symbol in binance_client.calls + hyperliquid_client.calls
    )


def test_funding_vertical_slice_preserves_partial_failure_and_does_not_claim_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ccxt_adapter_module, "datetime", FrozenDateTime)
    instruments = _instruments()
    binance, hyperliquid, _binance_client, hyperliquid_client = _adapters(
        hyperliquid_error=RateLimitExceeded("rate limited")
    )

    collection = _collect_funding(
        cast(FundingProvider, binance),
        cast(FundingProvider, hyperliquid),
        instruments,
    )
    opportunities = FundingArbitrageFinder().find(
        collection.data,
        fees=_fees(*instruments),
        as_of=AS_OF,
        comparison_horizon=HORIZON,
        freshness_policy=FreshnessPolicy(max_age=timedelta(minutes=1)),
        compatibility_policy=MATCHING_POLICY,
    )

    assert collection.partial is True
    assert collection.complete is False
    assert collection.failed is False
    assert len(collection.data) == 1
    assert len(collection.errors) == 1
    assert collection.errors[0].venue == "hyperliquid"
    assert opportunities == ()
    assert hyperliquid_client.calls[-1] == ("fetch_funding_rate", "BTC/USDC:USDC")
