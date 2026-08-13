"""Tests for conservative fee resolution and taker-fee accounting."""

from decimal import Decimal

import pytest

from trading_core.fees import resolve_taker_fee, round_trip_taker_fee
from trading_core.models import (
    ContractType,
    FeeSource,
    Instrument,
    MarketType,
    TradingFee,
)


def make_instrument(*, venue: str = "binance") -> Instrument:
    return Instrument(
        venue=venue,
        venue_symbol="BTC/USDT:USDT",
        base="BTC",
        quote="USDT",
        settlement="USDT",
        market_type=MarketType.PERPETUAL,
        contract_type=ContractType.LINEAR,
    )


def make_fee(
    *,
    venue: str = "binance",
    maker_fee: Decimal | None = Decimal("0.0002"),
    taker_fee: Decimal | None = Decimal("0.0004"),
    source: FeeSource = FeeSource.API,
    instrument: Instrument | None = None,
) -> TradingFee:
    return TradingFee(
        venue=venue,
        maker_fee=maker_fee,
        taker_fee=taker_fee,
        source=source,
        instrument=make_instrument(venue=venue) if instrument is None else instrument,
    )


def test_resolve_taker_fee_prefers_config_component_and_keeps_call_context() -> None:
    instrument = make_instrument(venue="bybit")

    resolved = resolve_taker_fee(
        venue="bybit",
        instrument=instrument,
        config_fee=make_fee(taker_fee=Decimal("0.0003"), source=FeeSource.API),
        api_fee=make_fee(taker_fee=Decimal("0.0005")),
        default_fee=make_fee(taker_fee=Decimal("0.0007")),
    )

    assert resolved == TradingFee(
        venue="bybit",
        maker_fee=None,
        taker_fee=Decimal("0.0003"),
        source=FeeSource.CONFIG,
        instrument=instrument,
    )


def test_resolve_taker_fee_prefers_api_component_over_default_component() -> None:
    resolved = resolve_taker_fee(
        venue="binance",
        instrument=None,
        config_fee=None,
        api_fee=make_fee(taker_fee=Decimal("0.0005"), source=FeeSource.DEFAULT),
        default_fee=make_fee(taker_fee=Decimal("0.0007")),
    )

    assert resolved.taker_fee == Decimal("0.0005")
    assert resolved.source is FeeSource.API


def test_resolve_taker_fee_falls_through_maker_only_higher_priority_candidate() -> None:
    resolved = resolve_taker_fee(
        venue="binance",
        instrument=make_instrument(),
        config_fee=make_fee(maker_fee=Decimal("0.0001"), taker_fee=None),
        api_fee=make_fee(taker_fee=Decimal("0.0005")),
        default_fee=make_fee(taker_fee=Decimal("0.0007")),
    )

    assert resolved.taker_fee == Decimal("0.0005")
    assert resolved.maker_fee is None
    assert resolved.source is FeeSource.API


def test_resolve_taker_fee_returns_unknown_without_substituting_maker_fee() -> None:
    instrument = make_instrument()

    resolved = resolve_taker_fee(
        venue="binance",
        instrument=instrument,
        config_fee=make_fee(maker_fee=Decimal("0.0001"), taker_fee=None),
        api_fee=make_fee(maker_fee=Decimal("0.0002"), taker_fee=None),
        default_fee=make_fee(maker_fee=Decimal("0.0003"), taker_fee=None),
    )

    assert resolved == TradingFee(
        venue="binance",
        maker_fee=None,
        taker_fee=None,
        source=FeeSource.UNKNOWN,
        instrument=instrument,
    )


def test_round_trip_taker_fee_sums_four_decimal_taker_legs_exactly() -> None:
    fee = make_fee(taker_fee=Decimal("0.00025"))

    result = round_trip_taker_fee(
        long_open=fee,
        short_open=fee,
        long_close=fee,
        short_close=fee,
    )

    assert result == Decimal("0.00100")


def test_round_trip_taker_fee_preserves_decimal_precision() -> None:
    result = round_trip_taker_fee(
        long_open=make_fee(taker_fee=Decimal("0.123456789123456789")),
        short_open=make_fee(taker_fee=Decimal("0.000000000000000001")),
        long_close=make_fee(taker_fee=Decimal("0.000000000000000002")),
        short_close=make_fee(taker_fee=Decimal("0.000000000000000003")),
    )

    assert result == Decimal("0.123456789123456795")


def test_round_trip_taker_fee_returns_unknown_when_any_leg_lacks_a_taker_fee() -> None:
    result = round_trip_taker_fee(
        long_open=make_fee(taker_fee=Decimal("0.00025")),
        short_open=make_fee(taker_fee=None),
        long_close=make_fee(taker_fee=Decimal("0.00025")),
        short_close=make_fee(taker_fee=Decimal("0.00025")),
    )

    assert result is None


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("maker_fee", Decimal("-0.0001"), "non-negative"),
        ("taker_fee", Decimal("-0.0001"), "non-negative"),
        ("maker_fee", Decimal("NaN"), "finite"),
        ("taker_fee", Decimal("Infinity"), "finite"),
    ],
)
def test_trading_fee_rejects_negative_and_non_finite_values(
    field_name: str,
    value: Decimal,
    message: str,
) -> None:
    values: dict[str, Decimal | None] = {
        "maker_fee": Decimal("0.0002"),
        "taker_fee": Decimal("0.0004"),
    }
    values[field_name] = value

    with pytest.raises(ValueError, match=message):
        TradingFee(
            venue="binance",
            maker_fee=values["maker_fee"],
            taker_fee=values["taker_fee"],
            source=FeeSource.API,
            instrument=make_instrument(),
        )
