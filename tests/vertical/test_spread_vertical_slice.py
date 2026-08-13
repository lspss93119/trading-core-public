from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal
from typing import cast

import pytest
import trading_core.exchanges.ccxt.adapter as ccxt_adapter_module
from tests.support.fake_ccxt import (
    FakeCCXTClient,
    FakeCCXTFactory,
    make_fake_client,
    replace_payload,
)
from trading_core.collectors import CollectionResult, TickerCollector
from trading_core.exchanges import ExchangeConfig, ProviderRegistry, TickerProvider
from trading_core.exchanges.ccxt import CCXTAdapter
from trading_core.models import (
    FeeSource,
    Instrument,
    MatchQuality,
    SpreadOpportunity,
    Ticker,
    TradingFee,
)
from trading_core.normalization.ccxt import normalize_ccxt_instrument
from trading_core.opportunities import CrossExchangeSpreadFinder
from trading_core.policies import FreshnessPolicy


AS_OF = datetime(2026, 8, 11, 11, 0, tzinfo=UTC)
COMPLETED_AT = AS_OF + timedelta(seconds=1)
STALE_AT = AS_OF - timedelta(minutes=10)


class FrozenDateTime:
    value = AS_OF

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return cls.value


class AdvancingClock:
    def __init__(self) -> None:
        self._calls = 0

    def __call__(self) -> datetime:
        value = AS_OF if self._calls == 0 else COMPLETED_AT
        self._calls += 1
        return value


def _hyperliquid_matching_client() -> FakeCCXTClient:
    client = make_fake_client("hyperliquid")
    symbol = "BTC/USDT:USDT"
    market = dict(next(iter(client.markets.values())))
    market.update({"symbol": symbol, "quote": "USDT", "settle": "USDT"})
    client.markets = {symbol: market}
    client.ticker = replace_payload(client.ticker, symbol=symbol)
    client.order_book = replace_payload(
        cast(Mapping[str, object], client.order_book), symbol=symbol
    )
    return client


def _adapters(
    *,
    matching_quote: bool,
) -> tuple[CCXTAdapter, CCXTAdapter, FakeCCXTClient, FakeCCXTClient]:
    binance_client = make_fake_client("binance")
    hyperliquid_client = (
        _hyperliquid_matching_client()
        if matching_quote
        else make_fake_client("hyperliquid")
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


def _instruments(
    binance_client: FakeCCXTClient,
    hyperliquid_client: FakeCCXTClient,
) -> tuple[Instrument, Instrument]:
    return (
        normalize_ccxt_instrument(
            next(iter(binance_client.markets.values())), venue="binance"
        ),
        normalize_ccxt_instrument(
            next(iter(hyperliquid_client.markets.values())), venue="hyperliquid"
        ),
    )


def _collect_tickers(
    binance: TickerProvider,
    hyperliquid: TickerProvider,
    instruments: tuple[Instrument, Instrument],
) -> CollectionResult[Ticker]:
    registry = ProviderRegistry()
    registry.register(binance)
    registry.register(hyperliquid)
    collector = TickerCollector(timeout=timedelta(seconds=1), clock=AdvancingClock())
    return asyncio.run(
        collector.collect(
            (
                (
                    cast(TickerProvider, registry.require("binance")),
                    instruments[0],
                ),
                (
                    cast(TickerProvider, registry.require("hyperliquid")),
                    instruments[1],
                ),
            )
        )
    )


def _fees(
    binance: Instrument,
    hyperliquid: Instrument,
) -> dict[Instrument, TradingFee]:
    return {
        binance: TradingFee(
            venue="binance",
            maker_fee=None,
            taker_fee=Decimal("0.001"),
            source=FeeSource.CONFIG,
            instrument=binance,
        ),
        hyperliquid: TradingFee(
            venue="hyperliquid",
            maker_fee=None,
            taker_fee=Decimal("0.001"),
            source=FeeSource.API,
            instrument=hyperliquid,
        ),
    }


def _find(
    collection: CollectionResult[Ticker],
    fees: dict[Instrument, TradingFee],
) -> tuple[SpreadOpportunity, ...]:
    return CrossExchangeSpreadFinder().find(
        collection.data,
        fees=fees,
        as_of=AS_OF,
        freshness_policy=FreshnessPolicy(max_age=timedelta(minutes=5)),
    )


def test_spread_vertical_slice_uses_real_ccxt_normalization_and_finder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ccxt_adapter_module, "datetime", FrozenDateTime)
    FrozenDateTime.value = AS_OF
    binance, hyperliquid, binance_client, hyperliquid_client = _adapters(
        matching_quote=True
    )
    instruments = _instruments(binance_client, hyperliquid_client)

    collection = _collect_tickers(
        cast(TickerProvider, binance),
        cast(TickerProvider, hyperliquid),
        instruments,
    )
    opportunities = _find(collection, _fees(*instruments))

    assert collection.complete is True
    assert len(collection.data) == 2
    [opportunity] = opportunities
    assert opportunity.buy_instrument.venue == "binance"
    assert opportunity.sell_instrument.venue == "hyperliquid"
    assert opportunity.buy_ask == Decimal("100002")
    assert opportunity.sell_bid == Decimal("100101")
    assert opportunity.gross_spread == (
        Decimal("100101") - Decimal("100002")
    ) / Decimal("100002")
    assert opportunity.estimated_net_spread == opportunity.gross_spread - Decimal(
        "0.002"
    )
    assert opportunity.buy_fee.source is FeeSource.CONFIG
    assert opportunity.sell_fee.source is FeeSource.API
    assert opportunity.match_quality is MatchQuality.EXACT
    assert opportunity.buy_received_at == AS_OF
    assert opportunity.sell_received_at == AS_OF
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


def test_spread_vertical_slice_retains_stale_data_but_finder_excludes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ccxt_adapter_module, "datetime", FrozenDateTime)
    FrozenDateTime.value = STALE_AT
    binance, hyperliquid, binance_client, hyperliquid_client = _adapters(
        matching_quote=True
    )
    instruments = _instruments(binance_client, hyperliquid_client)

    collection = _collect_tickers(
        cast(TickerProvider, binance),
        cast(TickerProvider, hyperliquid),
        instruments,
    )

    assert len(collection.data) == 2
    assert collection.errors == ()
    assert _find(collection, _fees(*instruments)) == ()


def test_spread_vertical_slice_rejects_usdt_vs_usdc_without_fx_conversion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ccxt_adapter_module, "datetime", FrozenDateTime)
    FrozenDateTime.value = AS_OF
    binance, hyperliquid, binance_client, hyperliquid_client = _adapters(
        matching_quote=False
    )
    instruments = _instruments(binance_client, hyperliquid_client)

    collection = _collect_tickers(
        cast(TickerProvider, binance),
        cast(TickerProvider, hyperliquid),
        instruments,
    )

    assert collection.complete is True
    assert _find(collection, _fees(*instruments)) == ()
