"""Order-book normalization with explicit contract quantity semantics."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Sequence, cast

from trading_core.exceptions import InvalidExchangeData
from trading_core.models import Instrument, OrderBook, OrderBookLevel

from .numeric import to_decimal


class RawAmountUnit(StrEnum):
    """The unit used for raw order-book level amounts."""

    BASE_ASSET = "base_asset"
    CONTRACT = "contract"


class ContractSizeDenomination(StrEnum):
    """The asset denomination used by a contract multiplier."""

    BASE_ASSET = "base_asset"
    QUOTE_ASSET = "quote_asset"
    INVERSE = "inverse"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ContractSizeMetadata:
    """Explicit evidence for converting contract counts to base amounts."""

    denomination: ContractSizeDenomination
    multiplier: Decimal | None

    def __post_init__(self) -> None:
        if not isinstance(self.denomination, ContractSizeDenomination):
            raise TypeError("denomination must be a ContractSizeDenomination")
        if self.multiplier is not None:
            if not isinstance(self.multiplier, Decimal):
                raise TypeError("multiplier must be a Decimal or None")
            if not self.multiplier.is_finite():
                raise ValueError("multiplier must be finite")


def normalize_amount_to_base(
    raw_amount: str | int | float | Decimal,
    *,
    amount_unit: RawAmountUnit,
    contract_metadata: ContractSizeMetadata | None,
) -> Decimal:
    """Convert one raw amount to a canonical base-asset quantity."""
    multiplier = _contract_multiplier(
        amount_unit=amount_unit, contract_metadata=contract_metadata
    )
    amount = to_decimal(raw_amount, field_name="amount")
    if multiplier is not None:
        amount *= multiplier
    return amount


def normalize_order_book(
    *,
    instrument: Instrument,
    bids: Sequence[tuple[object, object]],
    asks: Sequence[tuple[object, object]],
    amount_unit: RawAmountUnit,
    contract_metadata: ContractSizeMetadata | None,
    exchange_timestamp: datetime | None,
    received_at: datetime,
) -> OrderBook:
    """Produce a sorted base-asset OrderBook or reject ambiguous source data."""
    venue = instrument.venue if isinstance(instrument, Instrument) else "unknown"
    try:
        _contract_multiplier(
            amount_unit=amount_unit, contract_metadata=contract_metadata
        )
        normalized_bids = _normalize_levels(
            bids,
            amount_unit=amount_unit,
            contract_metadata=contract_metadata,
            reverse=True,
        )
        normalized_asks = _normalize_levels(
            asks,
            amount_unit=amount_unit,
            contract_metadata=contract_metadata,
            reverse=False,
        )
        return OrderBook(
            instrument=instrument,
            bids=normalized_bids,
            asks=normalized_asks,
            exchange_timestamp=exchange_timestamp,
            received_at=received_at,
        )
    except (ArithmeticError, TypeError, ValueError) as error:
        raise InvalidExchangeData(venue, "normalize_order_book", cause=error) from error


def _contract_multiplier(
    *,
    amount_unit: RawAmountUnit,
    contract_metadata: ContractSizeMetadata | None,
) -> Decimal | None:
    if not isinstance(amount_unit, RawAmountUnit):
        raise TypeError("amount_unit must be a RawAmountUnit")
    if amount_unit is RawAmountUnit.BASE_ASSET:
        return None
    if contract_metadata is None:
        raise ValueError("contract amounts require contract metadata")
    if contract_metadata.denomination is not ContractSizeDenomination.BASE_ASSET:
        raise ValueError("contract multiplier must be denominated in the base asset")
    multiplier = contract_metadata.multiplier
    if multiplier is None or not multiplier.is_finite() or multiplier <= 0:
        raise ValueError("contract multiplier must be a positive finite Decimal")
    return multiplier


def _normalize_levels(
    levels: Sequence[tuple[object, object]],
    *,
    amount_unit: RawAmountUnit,
    contract_metadata: ContractSizeMetadata | None,
    reverse: bool,
) -> tuple[OrderBookLevel, ...]:
    normalized: list[OrderBookLevel] = []
    for level in levels:
        if not isinstance(level, tuple) or len(level) != 2:
            raise ValueError("each order-book level must be a price and amount tuple")
        raw_price, raw_amount = level
        price = to_decimal(
            cast(str | int | float | Decimal, raw_price), field_name="price"
        )
        amount = normalize_amount_to_base(
            cast(str | int | float | Decimal, raw_amount),
            amount_unit=amount_unit,
            contract_metadata=contract_metadata,
        )
        normalized.append(OrderBookLevel(price=price, amount=amount))
    return tuple(sorted(normalized, key=lambda level: level.price, reverse=reverse))
