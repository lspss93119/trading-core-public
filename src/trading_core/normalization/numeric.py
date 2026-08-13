"""Decimal conversion helpers for untrusted exchange values."""

from decimal import Decimal, InvalidOperation
from enum import StrEnum


class RateUnit(StrEnum):
    """The unit used by an exchange to express a rate."""

    DECIMAL_FRACTION = "decimal_fraction"
    PERCENT = "percent"
    BASIS_POINTS = "basis_points"


def to_decimal(value: str | int | float | Decimal, *, field_name: str) -> Decimal:
    """Return a finite Decimal without propagating binary float artifacts."""
    if isinstance(value, bool):
        raise TypeError(f"{field_name} must not be a boolean")
    if not isinstance(value, (str, int, float, Decimal)):
        raise TypeError(f"{field_name} must be a decimal-compatible value")

    try:
        result = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field_name} must be a valid Decimal") from error

    if not result.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return result


def to_decimal_fraction(
    value: str | int | float | Decimal,
    *,
    unit: RateUnit,
) -> Decimal:
    """Convert an explicitly labelled rate to canonical decimal-fraction form."""
    if not isinstance(unit, RateUnit):
        raise TypeError("unit must be a RateUnit")

    rate = to_decimal(value, field_name="rate")
    if unit is RateUnit.DECIMAL_FRACTION:
        return rate
    if unit is RateUnit.PERCENT:
        return rate / Decimal("100")
    return rate / Decimal("10000")
