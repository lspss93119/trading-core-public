from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading_core.models import (
    ContractType,
    FeeSource,
    FundingOpportunity,
    FundingRate,
    Instrument,
    MarketType,
    MatchQuality,
    OrderBook,
    OrderBookLevel,
    SpreadOpportunity,
    Ticker,
    TopOfBook,
    TradingFee,
)


AS_OF = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
RECEIVED_AT = datetime(2026, 8, 11, 11, 59, tzinfo=UTC)
NEXT_FUNDING_AT = datetime(2026, 8, 11, 16, 0, tzinfo=UTC)


def make_instrument(
    *, quote: str = "USDT", is_active: bool | None = None
) -> Instrument:
    return Instrument(
        venue="binance",
        venue_symbol=f"BTC/{quote}:{quote}",
        base="BTC",
        quote=quote,
        settlement=quote,
        market_type=MarketType.PERPETUAL,
        contract_type=ContractType.LINEAR,
        is_active=is_active,
    )


def make_funding_rate(*, rate: Decimal = Decimal("0.0001")) -> FundingRate:
    return FundingRate(
        instrument=make_instrument(),
        rate=rate,
        interval=timedelta(hours=8),
        next_funding_at=NEXT_FUNDING_AT,
        exchange_timestamp=RECEIVED_AT,
        received_at=RECEIVED_AT,
    )


def make_fee(*, source: FeeSource = FeeSource.API) -> TradingFee:
    return TradingFee(
        venue="binance",
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0004"),
        source=source,
        instrument=make_instrument(),
    )


def make_funding_opportunity() -> FundingOpportunity:
    long_rate = make_funding_rate(rate=Decimal("-0.0001"))
    short_rate = make_funding_rate(rate=Decimal("0.0002"))
    fee = make_fee()
    return FundingOpportunity(
        long_funding=long_rate,
        short_funding=short_rate,
        as_of=AS_OF,
        comparison_horizon=timedelta(hours=24),
        long_normalized_rate=Decimal("-0.0003"),
        short_normalized_rate=Decimal("0.0006"),
        gross_edge=Decimal("0.0009"),
        estimated_fee_adjusted_edge=Decimal("-0.0007"),
        long_open_fee=fee,
        short_open_fee=fee,
        long_close_fee=fee,
        short_close_fee=fee,
        round_trip_fee_rate=Decimal("0.0016"),
        long_next_funding_at=NEXT_FUNDING_AT,
        short_next_funding_at=NEXT_FUNDING_AT,
        long_time_until_next_funding=timedelta(hours=4),
        short_time_until_next_funding=timedelta(hours=4),
        match_quality=MatchQuality.EXACT,
    )


def test_instrument_is_frozen_slotted_and_distinguishes_quote_currency() -> None:
    btc_usdt = make_instrument()
    btc_usdc = make_instrument(quote="USDC")

    assert btc_usdt == Instrument(
        venue="binance",
        venue_symbol="BTC/USDT:USDT",
        base="BTC",
        quote="USDT",
        settlement="USDT",
        market_type=MarketType.PERPETUAL,
        contract_type=ContractType.LINEAR,
    )
    assert btc_usdt != btc_usdc
    assert btc_usdt.quote == "USDT"
    assert btc_usdc.quote == "USDC"
    assert not hasattr(btc_usdt, "__dict__")

    with pytest.raises(FrozenInstanceError):
        btc_usdt.quote = "USDC"  # type: ignore[misc]


@pytest.mark.parametrize("is_active", [True, False, None])
def test_instrument_accepts_explicit_or_unknown_active_status(
    is_active: bool | None,
) -> None:
    instrument = make_instrument(is_active=is_active)

    assert instrument.is_active is is_active


def test_instrument_rejects_non_boolean_active_status() -> None:
    with pytest.raises(TypeError, match="is_active"):
        make_instrument(is_active="false")  # type: ignore[arg-type]


def test_active_status_does_not_change_instrument_identity_or_hash() -> None:
    active = make_instrument(is_active=True)
    inactive = make_instrument(is_active=False)

    assert active == inactive
    assert hash(active) == hash(inactive)
    assert len({active, inactive}) == 1


def test_market_data_preserves_decimal_values_and_aware_timestamps() -> None:
    rate = make_funding_rate(rate=Decimal("-0.000125"))
    ticker = Ticker(
        instrument=make_instrument(),
        bid=Decimal("100000.10"),
        ask=Decimal("100000.20"),
        last=Decimal("100000.15"),
        mark=Decimal("100000.14"),
        index=Decimal("100000.13"),
        exchange_timestamp=RECEIVED_AT,
        received_at=RECEIVED_AT,
    )

    assert rate.rate == Decimal("-0.000125")
    assert rate.received_at.tzinfo is not None
    assert ticker.bid == Decimal("100000.10")
    assert ticker.ask == Decimal("100000.20")
    assert ticker.exchange_timestamp == RECEIVED_AT


def test_top_of_book_preserves_decimal_amounts_and_is_immutable() -> None:
    top_of_book = TopOfBook(
        instrument=make_instrument(),
        bid_price=Decimal("100000.10"),
        bid_amount=Decimal("0"),
        ask_price=Decimal("100000.20"),
        ask_amount=Decimal("1.25"),
        received_at=RECEIVED_AT,
    )

    assert top_of_book.bid_price == Decimal("100000.10")
    assert top_of_book.bid_amount == Decimal("0")
    assert top_of_book.ask_amount == Decimal("1.25")
    assert not hasattr(top_of_book, "__dict__")
    with pytest.raises(FrozenInstanceError):
        top_of_book.bid_price = Decimal("100000")  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("bid_price", Decimal("0")),
        ("ask_price", Decimal("-1")),
        ("bid_amount", Decimal("-1")),
        ("ask_amount", Decimal("-1")),
    ),
)
def test_top_of_book_rejects_invalid_prices_and_amounts(
    field_name: str, value: Decimal
) -> None:
    values: dict[str, object] = {
        "instrument": make_instrument(),
        "bid_price": Decimal("100"),
        "bid_amount": Decimal("1"),
        "ask_price": Decimal("101"),
        "ask_amount": Decimal("1"),
        "received_at": RECEIVED_AT,
    }
    values[field_name] = value

    with pytest.raises(ValueError):
        TopOfBook(**values)  # type: ignore[arg-type]


def test_top_of_book_rejects_non_finite_values_and_naive_received_at() -> None:
    with pytest.raises(ValueError, match="finite"):
        TopOfBook(
            instrument=make_instrument(),
            bid_price=Decimal("NaN"),
            bid_amount=Decimal("1"),
            ask_price=Decimal("101"),
            ask_amount=Decimal("1"),
            received_at=RECEIVED_AT,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        TopOfBook(
            instrument=make_instrument(),
            bid_price=Decimal("100"),
            bid_amount=Decimal("1"),
            ask_price=Decimal("101"),
            ask_amount=Decimal("1"),
            received_at=datetime(2026, 8, 11, 11, 59),
        )


def test_top_of_book_rejects_crossed_prices() -> None:
    with pytest.raises(ValueError, match="bid_price"):
        TopOfBook(
            instrument=make_instrument(),
            bid_price=Decimal("101"),
            bid_amount=Decimal("1"),
            ask_price=Decimal("100"),
            ask_amount=Decimal("1"),
            received_at=RECEIVED_AT,
        )


def test_market_data_models_reject_non_instrument_boundary_values() -> None:
    raw_instrument = {"symbol": "BTC/USDT"}
    bid = OrderBookLevel(price=Decimal("100"), amount=Decimal("1"))
    ask = OrderBookLevel(price=Decimal("101"), amount=Decimal("1"))

    with pytest.raises(TypeError, match="instrument"):
        FundingRate(
            instrument=raw_instrument,  # type: ignore[arg-type]
            rate=Decimal("0.0001"),
            interval=timedelta(hours=8),
            next_funding_at=NEXT_FUNDING_AT,
            exchange_timestamp=RECEIVED_AT,
            received_at=RECEIVED_AT,
        )
    with pytest.raises(TypeError, match="instrument"):
        Ticker(
            instrument=raw_instrument,  # type: ignore[arg-type]
            bid=Decimal("100"),
            ask=Decimal("101"),
            last=None,
            mark=None,
            index=None,
            exchange_timestamp=RECEIVED_AT,
            received_at=RECEIVED_AT,
        )
    with pytest.raises(TypeError, match="instrument"):
        OrderBook(
            instrument=raw_instrument,  # type: ignore[arg-type]
            bids=(bid,),
            asks=(ask,),
            exchange_timestamp=RECEIVED_AT,
            received_at=RECEIVED_AT,
        )
    with pytest.raises(TypeError, match="instrument"):
        TopOfBook(
            instrument=raw_instrument,  # type: ignore[arg-type]
            bid_price=Decimal("100"),
            bid_amount=Decimal("1"),
            ask_price=Decimal("101"),
            ask_amount=Decimal("1"),
            received_at=RECEIVED_AT,
        )


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_models_reject_non_finite_decimal_values(value: Decimal) -> None:
    with pytest.raises(ValueError, match="finite"):
        Ticker(
            instrument=make_instrument(),
            bid=value,
            ask=Decimal("100"),
            last=None,
            mark=None,
            index=None,
            exchange_timestamp=None,
            received_at=RECEIVED_AT,
        )


def test_ticker_rejects_invalid_quote_and_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="bid"):
        Ticker(
            instrument=make_instrument(),
            bid=Decimal("101"),
            ask=Decimal("100"),
            last=None,
            mark=None,
            index=None,
            exchange_timestamp=None,
            received_at=RECEIVED_AT,
        )

    with pytest.raises(ValueError, match="timezone-aware"):
        FundingRate(
            instrument=make_instrument(),
            rate=Decimal("0.0001"),
            interval=timedelta(hours=8),
            next_funding_at=None,
            exchange_timestamp=None,
            received_at=datetime(2026, 8, 11, 11, 59),
        )


def test_funding_rate_requires_a_positive_interval() -> None:
    with pytest.raises(ValueError, match="interval"):
        FundingRate(
            instrument=make_instrument(),
            rate=Decimal("0.0001"),
            interval=timedelta(),
            next_funding_at=None,
            exchange_timestamp=None,
            received_at=RECEIVED_AT,
        )


def test_order_book_rejects_mutable_or_unsorted_levels_and_is_immutable() -> None:
    bid = OrderBookLevel(price=Decimal("100"), amount=Decimal("1.5"))
    ask = OrderBookLevel(price=Decimal("101"), amount=Decimal("2"))
    book = OrderBook(
        instrument=make_instrument(),
        bids=(bid,),
        asks=(ask,),
        exchange_timestamp=None,
        received_at=RECEIVED_AT,
    )

    assert book.bids == (bid,)
    assert book.asks == (ask,)
    with pytest.raises(FrozenInstanceError):
        setattr(book, "bids", (bid, bid))

    with pytest.raises(ValueError, match="tuple"):
        OrderBook(
            instrument=make_instrument(),
            bids=[bid],  # type: ignore[arg-type]
            asks=(ask,),
            exchange_timestamp=None,
            received_at=RECEIVED_AT,
        )
    with pytest.raises(ValueError, match="highest-price-first"):
        OrderBook(
            instrument=make_instrument(),
            bids=(bid, OrderBookLevel(price=Decimal("101"), amount=Decimal("1"))),
            asks=(ask,),
            exchange_timestamp=None,
            received_at=RECEIVED_AT,
        )


def test_order_book_level_requires_positive_price_and_non_negative_amount() -> None:
    with pytest.raises(ValueError, match="price"):
        OrderBookLevel(price=Decimal("0"), amount=Decimal("1"))
    with pytest.raises(ValueError, match="amount"):
        OrderBookLevel(price=Decimal("1"), amount=Decimal("-1"))


def test_unknown_fees_have_no_invented_rate() -> None:
    unknown_fee = TradingFee(
        venue="binance",
        maker_fee=None,
        taker_fee=None,
        source=FeeSource.UNKNOWN,
        instrument=make_instrument(),
    )

    assert unknown_fee.taker_fee is None
    with pytest.raises(ValueError, match="UNKNOWN"):
        TradingFee(
            venue="binance",
            maker_fee=None,
            taker_fee=Decimal("0"),
            source=FeeSource.UNKNOWN,
            instrument=make_instrument(),
        )


def test_opportunity_models_preserve_analysis_values_timing_and_fee_sources() -> None:
    long_rate = make_funding_rate(rate=Decimal("-0.0001"))
    short_rate = make_funding_rate(rate=Decimal("0.0002"))
    fee = make_fee()
    funding_opportunity = FundingOpportunity(
        long_funding=long_rate,
        short_funding=short_rate,
        as_of=AS_OF,
        comparison_horizon=timedelta(hours=24),
        long_normalized_rate=Decimal("-0.0003"),
        short_normalized_rate=Decimal("0.0006"),
        gross_edge=Decimal("0.0009"),
        estimated_fee_adjusted_edge=Decimal("-0.0007"),
        long_open_fee=fee,
        short_open_fee=fee,
        long_close_fee=fee,
        short_close_fee=fee,
        round_trip_fee_rate=Decimal("0.0016"),
        long_next_funding_at=NEXT_FUNDING_AT,
        short_next_funding_at=NEXT_FUNDING_AT,
        long_time_until_next_funding=timedelta(hours=4),
        short_time_until_next_funding=timedelta(hours=4),
        match_quality=MatchQuality.EXACT,
    )
    spread_opportunity = SpreadOpportunity(
        buy_instrument=make_instrument(),
        sell_instrument=Instrument(
            venue="hyperliquid",
            venue_symbol="BTC/USDT:USDT",
            base="BTC",
            quote="USDT",
            settlement="USDT",
            market_type=MarketType.PERPETUAL,
            contract_type=ContractType.LINEAR,
        ),
        buy_ask=Decimal("100"),
        sell_bid=Decimal("101"),
        gross_spread=Decimal("0.01"),
        buy_fee=fee,
        sell_fee=fee,
        estimated_net_spread=Decimal("0.0092"),
        match_quality=MatchQuality.EXACT,
        buy_exchange_timestamp=RECEIVED_AT,
        sell_exchange_timestamp=RECEIVED_AT,
        buy_received_at=RECEIVED_AT,
        sell_received_at=RECEIVED_AT,
    )

    assert funding_opportunity.long_normalized_rate == Decimal("-0.0003")
    assert funding_opportunity.long_open_fee.source is FeeSource.API
    assert funding_opportunity.round_trip_fee_rate == Decimal("0.0016")
    assert funding_opportunity.long_time_until_next_funding == timedelta(hours=4)
    assert spread_opportunity.buy_ask == Decimal("100")
    assert spread_opportunity.estimated_net_spread == Decimal("0.0092")
    assert spread_opportunity.buy_fee.source is FeeSource.API
    assert spread_opportunity.buy_received_at == RECEIVED_AT


@pytest.mark.parametrize("leg", ["long", "short"])
def test_funding_opportunity_rejects_timing_that_disagrees_with_its_source(
    leg: str,
) -> None:
    opportunity = make_funding_opportunity()

    with pytest.raises(ValueError, match=f"{leg}_next_funding_at"):
        replace(
            opportunity,
            **{f"{leg}_next_funding_at": AS_OF},  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match=f"{leg}_time_until_next_funding"):
        replace(
            opportunity,
            **{f"{leg}_time_until_next_funding": timedelta(hours=3)},  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("leg", ["long", "short"])
def test_funding_opportunity_requires_absent_source_timing_to_remain_absent(
    leg: str,
) -> None:
    opportunity = make_funding_opportunity()
    source_funding = replace(
        getattr(opportunity, f"{leg}_funding"), next_funding_at=None
    )
    source_changes = {
        f"{leg}_funding": source_funding,
        f"{leg}_next_funding_at": None,
        f"{leg}_time_until_next_funding": None,
    }
    opportunity_without_timing = replace(opportunity, **source_changes)

    assert getattr(opportunity_without_timing, f"{leg}_next_funding_at") is None
    assert getattr(opportunity_without_timing, f"{leg}_time_until_next_funding") is None
    with pytest.raises(ValueError, match=f"{leg}_next_funding_at"):
        replace(
            opportunity_without_timing,
            **{f"{leg}_next_funding_at": NEXT_FUNDING_AT},  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("leg", ["long", "short"])
def test_funding_opportunity_rejects_negative_time_until_next_funding(
    leg: str,
) -> None:
    opportunity = make_funding_opportunity()
    past_next_funding_at = AS_OF - timedelta(hours=1)
    source_funding = replace(
        getattr(opportunity, f"{leg}_funding"),
        next_funding_at=past_next_funding_at,
    )
    changes = {
        f"{leg}_funding": source_funding,
        f"{leg}_next_funding_at": past_next_funding_at,
        f"{leg}_time_until_next_funding": timedelta(hours=-1),
    }

    with pytest.raises(ValueError, match=f"{leg}_time_until_next_funding"):
        replace(opportunity, **changes)
