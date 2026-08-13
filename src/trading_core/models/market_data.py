"""Trusted normalized market-data values."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from .instruments import Instrument


def _require_finite_decimal(value: Decimal, field_name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")


def _require_positive_decimal(value: Decimal, field_name: str) -> None:
    _require_finite_decimal(value, field_name)
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")


def _require_non_negative_decimal(value: Decimal, field_name: str) -> None:
    _require_finite_decimal(value, field_name)
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")


def _require_aware_datetime(value: datetime | None, field_name: str) -> None:
    if value is None:
        return
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field_name} must be timezone-aware")


def _require_instrument(value: Instrument) -> None:
    if not isinstance(value, Instrument):
        raise TypeError("instrument must be an Instrument")


@dataclass(frozen=True, slots=True)
class FundingRate:
    """An observed, canonical funding rate without horizon normalization."""

    instrument: Instrument
    rate: Decimal
    interval: timedelta
    next_funding_at: datetime | None
    exchange_timestamp: datetime | None
    received_at: datetime

    def __post_init__(self) -> None:
        _require_instrument(self.instrument)
        _require_finite_decimal(self.rate, "rate")
        if not isinstance(self.interval, timedelta) or self.interval <= timedelta():
            raise ValueError("interval must be positive")
        _require_aware_datetime(self.next_funding_at, "next_funding_at")
        _require_aware_datetime(self.exchange_timestamp, "exchange_timestamp")
        _require_aware_datetime(self.received_at, "received_at")


@dataclass(frozen=True, slots=True)
class Ticker:
    """A normalized executable top-of-book ticker snapshot."""

    instrument: Instrument
    bid: Decimal
    ask: Decimal
    last: Decimal | None
    mark: Decimal | None
    index: Decimal | None
    exchange_timestamp: datetime | None
    received_at: datetime

    def __post_init__(self) -> None:
        _require_instrument(self.instrument)
        _require_positive_decimal(self.bid, "bid")
        _require_positive_decimal(self.ask, "ask")
        if self.bid > self.ask:
            raise ValueError("bid must not be greater than ask")
        for field_name in ("last", "mark", "index"):
            value = getattr(self, field_name)
            if value is not None:
                _require_positive_decimal(value, field_name)
        _require_aware_datetime(self.exchange_timestamp, "exchange_timestamp")
        _require_aware_datetime(self.received_at, "received_at")


@dataclass(frozen=True, slots=True)
class TopOfBook:
    """A normalized best bid/ask snapshot with base-asset bid/ask quantities."""

    instrument: Instrument
    bid_price: Decimal
    bid_amount: Decimal
    ask_price: Decimal
    ask_amount: Decimal
    received_at: datetime

    def __post_init__(self) -> None:
        _require_instrument(self.instrument)
        _require_positive_decimal(self.bid_price, "bid_price")
        _require_non_negative_decimal(self.bid_amount, "bid_amount")
        _require_positive_decimal(self.ask_price, "ask_price")
        _require_non_negative_decimal(self.ask_amount, "ask_amount")
        if self.bid_price > self.ask_price:
            raise ValueError("bid_price must not be greater than ask_price")
        _require_aware_datetime(self.received_at, "received_at")


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    """One price level with base-asset quantity."""

    price: Decimal
    amount: Decimal

    def __post_init__(self) -> None:
        _require_positive_decimal(self.price, "price")
        _require_non_negative_decimal(self.amount, "amount")


@dataclass(frozen=True, slots=True)
class OrderBook:
    """A normalized, price-sorted market-depth snapshot."""

    instrument: Instrument
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    exchange_timestamp: datetime | None
    received_at: datetime

    def __post_init__(self) -> None:
        _require_instrument(self.instrument)
        if not isinstance(self.bids, tuple) or not isinstance(self.asks, tuple):
            raise ValueError("bids and asks must be tuples")
        if not all(
            isinstance(level, OrderBookLevel) for level in self.bids + self.asks
        ):
            raise TypeError("bids and asks must contain OrderBookLevel values")
        if any(
            left.price < right.price for left, right in zip(self.bids, self.bids[1:])
        ):
            raise ValueError("bids must be highest-price-first")
        if any(
            left.price > right.price for left, right in zip(self.asks, self.asks[1:])
        ):
            raise ValueError("asks must be lowest-price-first")
        _require_aware_datetime(self.exchange_timestamp, "exchange_timestamp")
        _require_aware_datetime(self.received_at, "received_at")
