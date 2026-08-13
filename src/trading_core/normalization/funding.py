"""Pure funding-rate canonicalization and horizon scaling."""

from datetime import timedelta
from decimal import Decimal
from enum import StrEnum

from trading_core.models import FundingRate

from .numeric import RateUnit, to_decimal_fraction


class FundingSignConvention(StrEnum):
    """The source convention used for the sign of a funding value."""

    POSITIVE_LONG_PAYS_SHORT = "positive_long_pays_short"
    POSITIVE_SHORT_PAYS_LONG = "positive_short_pays_long"


def canonicalize_funding_value(
    raw_rate: str | int | float | Decimal,
    *,
    unit: RateUnit,
    source_sign: FundingSignConvention,
) -> Decimal:
    """Convert source rate units and signs to the canonical funding convention."""
    if not isinstance(source_sign, FundingSignConvention):
        raise TypeError("source_sign must be a FundingSignConvention")

    rate = to_decimal_fraction(raw_rate, unit=unit)
    if source_sign is FundingSignConvention.POSITIVE_SHORT_PAYS_LONG:
        return -rate
    return rate


def normalize_funding_rate(
    funding_rate: FundingRate,
    *,
    horizon: timedelta,
) -> Decimal:
    """Scale one observed funding rate to an explicit comparison horizon."""
    if not isinstance(funding_rate, FundingRate):
        raise TypeError("funding_rate must be a FundingRate")
    if not isinstance(funding_rate.rate, Decimal):
        raise TypeError("funding_rate.rate must be a Decimal")
    if not funding_rate.rate.is_finite():
        raise ValueError("funding_rate.rate must be finite")

    horizon_seconds = _positive_duration_seconds(horizon, field_name="horizon")
    interval_seconds = _positive_duration_seconds(
        funding_rate.interval, field_name="funding_rate.interval"
    )
    return funding_rate.rate * horizon_seconds / interval_seconds


def _positive_duration_seconds(value: timedelta, *, field_name: str) -> Decimal:
    if not isinstance(value, timedelta) or value <= timedelta():
        raise ValueError(f"{field_name} must be positive")

    return (
        Decimal(value.days) * Decimal("86400")
        + Decimal(value.seconds)
        + Decimal(value.microseconds) / Decimal("1000000")
    )
