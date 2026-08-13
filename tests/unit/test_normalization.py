from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading_core import InvalidExchangeData
from trading_core.models import ContractType, FundingRate, Instrument, MarketType
from trading_core.normalization import (
    ContractSizeDenomination,
    ContractSizeMetadata,
    FundingSignConvention,
    RateUnit,
    RawAmountUnit,
    canonicalize_funding_value,
    normalize_funding_rate,
    normalize_order_book,
    to_decimal,
    to_decimal_fraction,
)


RECEIVED_AT = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


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


def make_funding_rate(
    *,
    rate: Decimal = Decimal("0.0001"),
    interval: timedelta = timedelta(hours=8),
) -> FundingRate:
    return FundingRate(
        instrument=make_instrument(),
        rate=rate,
        interval=interval,
        next_funding_at=None,
        exchange_timestamp=RECEIVED_AT,
        received_at=RECEIVED_AT,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("12.3400", Decimal("12.3400")),
        (3, Decimal("3")),
        (0.1, Decimal("0.1")),
        (Decimal("0.00000001"), Decimal("0.00000001")),
    ],
)
def test_to_decimal_produces_finite_decimal_without_float_artifacts(
    value: str | int | float | Decimal, expected: Decimal
) -> None:
    assert to_decimal(value, field_name="price") == expected


@pytest.mark.parametrize(
    "value",
    [True, Decimal("NaN"), Decimal("Infinity"), "not-a-number", object()],
)
def test_to_decimal_rejects_invalid_external_values(value: object) -> None:
    with pytest.raises((TypeError, ValueError), match="price"):
        to_decimal(value, field_name="price")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (Decimal("0.0001"), RateUnit.DECIMAL_FRACTION, Decimal("0.0001")),
        (Decimal("0.01"), RateUnit.PERCENT, Decimal("0.0001")),
        (Decimal("1"), RateUnit.BASIS_POINTS, Decimal("0.0001")),
    ],
)
def test_to_decimal_fraction_normalizes_explicit_rate_units(
    value: Decimal, unit: RateUnit, expected: Decimal
) -> None:
    assert to_decimal_fraction(value, unit=unit) == expected


def test_canonicalize_funding_value_converts_source_sign_to_long_pays_short() -> None:
    assert canonicalize_funding_value(
        Decimal("0.01"),
        unit=RateUnit.PERCENT,
        source_sign=FundingSignConvention.POSITIVE_LONG_PAYS_SHORT,
    ) == Decimal("0.0001")
    assert canonicalize_funding_value(
        Decimal("0.01"),
        unit=RateUnit.PERCENT,
        source_sign=FundingSignConvention.POSITIVE_SHORT_PAYS_LONG,
    ) == Decimal("-0.0001")


@pytest.mark.parametrize(
    "rate",
    [Decimal("0"), Decimal("-0.0001")],
)
def test_normalize_funding_rate_preserves_zero_and_negative_observations(
    rate: Decimal,
) -> None:
    assert normalize_funding_rate(
        make_funding_rate(rate=rate), horizon=timedelta(hours=24)
    ) == rate * Decimal("3")


def test_normalize_funding_rate_scales_observed_rate_with_decimal_precision() -> None:
    funding_rate = make_funding_rate(rate=Decimal("0.000123456789"))

    normalized = normalize_funding_rate(funding_rate, horizon=timedelta(hours=24))

    assert normalized == Decimal("0.000370370367")
    assert funding_rate.rate == Decimal("0.000123456789")
    assert funding_rate.interval == timedelta(hours=8)


def test_equivalent_observed_rates_have_the_same_horizon_normalization() -> None:
    hourly = make_funding_rate(rate=Decimal("0.000025"), interval=timedelta(hours=1))
    eight_hourly = make_funding_rate(
        rate=Decimal("0.0002"), interval=timedelta(hours=8)
    )

    assert normalize_funding_rate(hourly, horizon=timedelta(hours=8)) == Decimal(
        "0.0002"
    )
    assert normalize_funding_rate(eight_hourly, horizon=timedelta(hours=8)) == Decimal(
        "0.0002"
    )


@pytest.mark.parametrize("horizon", [timedelta(), timedelta(hours=-1)])
def test_normalize_funding_rate_rejects_non_positive_horizon(
    horizon: timedelta,
) -> None:
    with pytest.raises(ValueError, match="horizon"):
        normalize_funding_rate(make_funding_rate(), horizon=horizon)


@pytest.mark.parametrize("interval", [timedelta(), timedelta(hours=-1)])
def test_normalize_funding_rate_defensively_rejects_non_positive_interval(
    interval: timedelta,
) -> None:
    funding_rate = make_funding_rate()
    object.__setattr__(funding_rate, "interval", interval)

    with pytest.raises(ValueError, match="interval"):
        normalize_funding_rate(funding_rate, horizon=timedelta(hours=8))


def test_normalize_order_book_orders_immutable_base_asset_levels() -> None:
    book = normalize_order_book(
        instrument=make_instrument(),
        bids=[("100", "1.5"), ("101", "2")],
        asks=[("103", "4"), ("102", "3")],
        amount_unit=RawAmountUnit.BASE_ASSET,
        contract_metadata=None,
        exchange_timestamp=RECEIVED_AT,
        received_at=RECEIVED_AT,
    )

    assert tuple((level.price, level.amount) for level in book.bids) == (
        (Decimal("101"), Decimal("2")),
        (Decimal("100"), Decimal("1.5")),
    )
    assert tuple((level.price, level.amount) for level in book.asks) == (
        (Decimal("102"), Decimal("3")),
        (Decimal("103"), Decimal("4")),
    )
    with pytest.raises(FrozenInstanceError):
        book.bids = ()  # type: ignore[misc]


def test_normalize_order_book_converts_base_asset_contract_multiplier() -> None:
    book = normalize_order_book(
        instrument=make_instrument(),
        bids=[("100", "2")],
        asks=[("101", "2")],
        amount_unit=RawAmountUnit.CONTRACT,
        contract_metadata=ContractSizeMetadata(
            denomination=ContractSizeDenomination.BASE_ASSET,
            multiplier=Decimal("0.001"),
        ),
        exchange_timestamp=None,
        received_at=RECEIVED_AT,
    )

    assert book.bids[0].amount == Decimal("0.002")
    assert book.asks[0].amount == Decimal("0.002")


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        ContractSizeMetadata(
            denomination=ContractSizeDenomination.QUOTE_ASSET,
            multiplier=Decimal("1"),
        ),
        ContractSizeMetadata(
            denomination=ContractSizeDenomination.INVERSE,
            multiplier=Decimal("1"),
        ),
        ContractSizeMetadata(
            denomination=ContractSizeDenomination.UNKNOWN,
            multiplier=None,
        ),
        ContractSizeMetadata(
            denomination=ContractSizeDenomination.BASE_ASSET,
            multiplier=None,
        ),
        ContractSizeMetadata(
            denomination=ContractSizeDenomination.BASE_ASSET,
            multiplier=Decimal("0"),
        ),
    ],
)
def test_normalize_order_book_rejects_ambiguous_contract_counts(
    metadata: ContractSizeMetadata | None,
) -> None:
    with pytest.raises(InvalidExchangeData) as error:
        normalize_order_book(
            instrument=make_instrument(),
            bids=[("100", "2")],
            asks=[("101", "2")],
            amount_unit=RawAmountUnit.CONTRACT,
            contract_metadata=metadata,
            exchange_timestamp=None,
            received_at=RECEIVED_AT,
        )

    assert error.value.venue == "binance"
    assert error.value.operation == "normalize_order_book"


def test_contract_size_metadata_rejects_a_non_finite_multiplier() -> None:
    with pytest.raises(ValueError, match="finite"):
        ContractSizeMetadata(
            denomination=ContractSizeDenomination.BASE_ASSET,
            multiplier=Decimal("NaN"),
        )


def test_normalize_order_book_rejects_non_positive_contract_metadata() -> None:
    with pytest.raises(InvalidExchangeData):
        normalize_order_book(
            instrument=make_instrument(),
            bids=[("100", "2")],
            asks=[("101", "2")],
            amount_unit=RawAmountUnit.CONTRACT,
            contract_metadata=ContractSizeMetadata(
                denomination=ContractSizeDenomination.BASE_ASSET,
                multiplier=Decimal("-1"),
            ),
            exchange_timestamp=None,
            received_at=RECEIVED_AT,
        )


@pytest.mark.parametrize(
    ("bids", "asks", "exchange_timestamp", "received_at"),
    [
        ([("0", "1")], [("101", "1")], None, RECEIVED_AT),
        ([("100", "-1")], [("101", "1")], None, RECEIVED_AT),
        ([("100", "1")], [("101", "1")], datetime(2026, 8, 11, 12, 0), RECEIVED_AT),
        ([("100", "1")], [("101", "1")], None, datetime(2026, 8, 11, 12, 0)),
    ],
)
def test_normalize_order_book_maps_invalid_values_to_exchange_data_error(
    bids: list[tuple[str, str]],
    asks: list[tuple[str, str]],
    exchange_timestamp: datetime | None,
    received_at: datetime,
) -> None:
    with pytest.raises(InvalidExchangeData):
        normalize_order_book(
            instrument=make_instrument(),
            bids=bids,
            asks=asks,
            amount_unit=RawAmountUnit.BASE_ASSET,
            contract_metadata=None,
            exchange_timestamp=exchange_timestamp,
            received_at=received_at,
        )
