from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_core.models import (
    ContractType,
    FeeSource,
    FundingOpportunity,
    FundingRate,
    Instrument,
    MarketType,
    MatchQuality,
    TradingFee,
)
from trading_core.opportunities import FundingArbitrageFinder
from trading_core.policies import FreshnessPolicy
from trading_core.matching import CompatibilityPolicy


AS_OF = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
HORIZON = timedelta(hours=24)
FRESHNESS = FreshnessPolicy(max_age=timedelta(minutes=5))
NO_COMPATIBILITY = CompatibilityPolicy()


def make_instrument(
    venue: str,
    *,
    quote: str = "USDT",
    settlement: str = "USDT",
    market_type: MarketType = MarketType.PERPETUAL,
    contract_type: ContractType = ContractType.LINEAR,
) -> Instrument:
    return Instrument(
        venue=venue,
        venue_symbol=f"BTC/{quote}:{settlement}",
        base="BTC",
        quote=quote,
        settlement=settlement,
        market_type=market_type,
        contract_type=contract_type,
    )


def make_funding(
    instrument: Instrument,
    rate: str,
    *,
    received_at: datetime = AS_OF - timedelta(seconds=1),
    next_funding_at: datetime | None = AS_OF + timedelta(hours=4),
    interval: timedelta = timedelta(hours=8),
) -> FundingRate:
    return FundingRate(
        instrument=instrument,
        rate=Decimal(rate),
        interval=interval,
        next_funding_at=next_funding_at,
        exchange_timestamp=received_at,
        received_at=received_at,
    )


def make_fee(
    instrument: Instrument, taker: str | None, source: FeeSource
) -> TradingFee:
    return TradingFee(
        venue=instrument.venue,
        maker_fee=None if taker is None else Decimal("0.0001"),
        taker_fee=None if taker is None else Decimal(taker),
        source=source,
        instrument=instrument,
    )


def find(
    funding_rates: tuple[FundingRate, ...],
    *,
    fees: dict[Instrument, TradingFee] | None = None,
    compatibility_policy: CompatibilityPolicy = NO_COMPATIBILITY,
) -> tuple[FundingOpportunity, ...]:
    return FundingArbitrageFinder().find(
        funding_rates,
        fees={} if fees is None else fees,
        as_of=AS_OF,
        comparison_horizon=HORIZON,
        freshness_policy=FRESHNESS,
        compatibility_policy=compatibility_policy,
    )


def test_funding_finder_selects_lower_rate_long_and_higher_rate_short() -> None:
    long_instrument = make_instrument("binance")
    short_instrument = make_instrument("hyperliquid")
    long_rate = make_funding(long_instrument, "-0.0004")
    short_rate = make_funding(
        short_instrument,
        "0.0008",
        next_funding_at=AS_OF + timedelta(hours=6),
    )
    fees = {
        long_instrument: make_fee(long_instrument, "0.0002", FeeSource.CONFIG),
        short_instrument: make_fee(short_instrument, "0.0003", FeeSource.API),
    }

    [opportunity] = find((short_rate, long_rate), fees=fees)

    assert opportunity.long_funding is long_rate
    assert opportunity.short_funding is short_rate
    assert opportunity.long_normalized_rate == Decimal("-0.0012")
    assert opportunity.short_normalized_rate == Decimal("0.0024")
    assert opportunity.gross_edge == Decimal("0.0036")
    assert opportunity.round_trip_fee_rate == Decimal("0.0010")
    assert opportunity.estimated_fee_adjusted_edge == Decimal("0.0026")
    assert opportunity.long_open_fee.source is FeeSource.CONFIG
    assert opportunity.short_open_fee.source is FeeSource.API
    assert opportunity.long_close_fee == opportunity.long_open_fee
    assert opportunity.short_close_fee == opportunity.short_open_fee
    assert opportunity.match_quality is MatchQuality.EXACT
    assert opportunity.long_time_until_next_funding == timedelta(hours=4)
    assert opportunity.short_time_until_next_funding == timedelta(hours=6)


def test_funding_finder_excludes_zero_and_non_positive_gross_edges() -> None:
    first = make_instrument("binance")
    second = make_instrument("hyperliquid")

    assert (
        find(
            (
                make_funding(first, "0.0001"),
                make_funding(second, "0.0001"),
            )
        )
        == ()
    )


def test_funding_finder_requires_perpetual_compatible_instruments() -> None:
    perp = make_instrument("binance")
    spot = make_instrument("hyperliquid", market_type=MarketType.SPOT)
    usdt = make_instrument("bybit")
    usdc = make_instrument("okx", quote="USDC", settlement="USDC")

    assert find((make_funding(perp, "-0.0001"), make_funding(spot, "0.0002"))) == ()
    assert find((make_funding(usdt, "-0.0001"), make_funding(usdc, "0.0002"))) == ()

    compatible_policy = CompatibilityPolicy(
        allowed_quote_settlement_pairs=frozenset(
            {frozenset({("USDT", "USDT"), ("USDC", "USDC")})}
        )
    )
    [opportunity] = find(
        (make_funding(usdt, "-0.0001"), make_funding(usdc, "0.0002")),
        compatibility_policy=compatible_policy,
    )
    assert opportunity.match_quality is MatchQuality.COMPATIBLE


def test_funding_finder_rejects_pairs_from_the_same_venue() -> None:
    first = make_instrument("binance", quote="USDT")
    second = make_instrument("binance", quote="USDC", settlement="USDC")
    policy = CompatibilityPolicy(
        allowed_quote_settlement_pairs=frozenset(
            {frozenset({("USDT", "USDT"), ("USDC", "USDC")})}
        )
    )

    assert (
        find(
            (make_funding(first, "-0.0001"), make_funding(second, "0.0002")),
            compatibility_policy=policy,
        )
        == ()
    )


def test_funding_finder_excludes_stale_observations_using_explicit_as_of() -> None:
    first = make_instrument("binance")
    second = make_instrument("hyperliquid")
    stale = make_funding(
        first,
        "-0.0001",
        received_at=AS_OF - timedelta(minutes=6),
    )

    assert find((stale, make_funding(second, "0.0002"))) == ()


def test_unknown_fee_keeps_gross_edge_but_withholds_fee_adjusted_edge() -> None:
    first = make_instrument("binance")
    second = make_instrument("hyperliquid")

    [opportunity] = find(
        (make_funding(first, "-0.0001"), make_funding(second, "0.0002")),
        fees={first: make_fee(first, None, FeeSource.UNKNOWN)},
    )

    assert opportunity.gross_edge == Decimal("0.0009")
    assert opportunity.estimated_fee_adjusted_edge is None
    assert opportunity.round_trip_fee_rate is None
    assert opportunity.long_open_fee.source is FeeSource.UNKNOWN
    assert opportunity.short_open_fee.source is FeeSource.UNKNOWN


def test_funding_finder_order_is_deterministic_and_independent_of_input_order() -> None:
    first = make_instrument("a-venue")
    second = make_instrument("b-venue")
    third = make_instrument("c-venue")
    rates = (
        make_funding(first, "-0.0004"),
        make_funding(second, "0.0008"),
        make_funding(third, "0.0005"),
    )

    forward = find(rates)
    reverse = find(tuple(reversed(rates)))

    assert forward == reverse
    assert [item.gross_edge for item in forward] == [
        Decimal("0.0036"),
        Decimal("0.0027"),
        Decimal("0.0009"),
    ]
    assert [
        (item.long_funding.instrument.venue, item.short_funding.instrument.venue)
        for item in forward
    ] == [("a-venue", "b-venue"), ("a-venue", "c-venue"), ("c-venue", "b-venue")]


def test_funding_finder_is_pure_and_has_no_provider_or_backend_dependency() -> None:
    first = make_instrument("binance")
    second = make_instrument("hyperliquid")
    rates = (make_funding(first, "-0.0001"), make_funding(second, "0.0002"))

    first_result = find(rates)
    second_result = find(rates)

    assert first_result == second_result
    assert first_result[0].as_of == AS_OF
    assert "ccxt" not in FundingArbitrageFinder.__module__
    assert not hasattr(FundingArbitrageFinder, "fetch_ticker")
    assert not hasattr(FundingArbitrageFinder, "fetch_funding_rate")
