"""Immutable, estimate-oriented outputs from pure opportunity analysis."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from .enums import MatchQuality
from .fees import TradingFee
from .instruments import Instrument
from .market_data import (
    FundingRate,
    _require_aware_datetime,
    _require_finite_decimal,
    _require_positive_decimal,
)


def _require_optional_finite_decimal(value: Decimal | None, field_name: str) -> None:
    if value is not None:
        _require_finite_decimal(value, field_name)


def _require_optional_timedelta(value: timedelta | None, field_name: str) -> None:
    if value is not None and not isinstance(value, timedelta):
        raise TypeError(f"{field_name} must be a timedelta or None")


def _require_consistent_funding_timing(
    funding: FundingRate,
    as_of: datetime,
    next_funding_at: datetime | None,
    time_until_next_funding: timedelta | None,
    leg: str,
) -> None:
    source_next_funding_at = funding.next_funding_at
    next_field_name = f"{leg}_next_funding_at"
    time_field_name = f"{leg}_time_until_next_funding"
    if source_next_funding_at is None:
        if next_funding_at is not None:
            raise ValueError(f"{next_field_name} must be None when source is absent")
        if time_until_next_funding is not None:
            raise ValueError(f"{time_field_name} must be None when source is absent")
        return
    if next_funding_at != source_next_funding_at:
        raise ValueError(f"{next_field_name} must match the source FundingRate")
    expected_time_until = source_next_funding_at - as_of
    if expected_time_until < timedelta():
        raise ValueError(f"{time_field_name} must not be negative")
    if time_until_next_funding != expected_time_until:
        raise ValueError(f"{time_field_name} must equal {next_field_name} minus as_of")


@dataclass(frozen=True, slots=True)
class FundingOpportunity:
    """A static, fee-aware comparison of two observed funding rates."""

    long_funding: FundingRate
    short_funding: FundingRate
    as_of: datetime
    comparison_horizon: timedelta
    long_normalized_rate: Decimal
    short_normalized_rate: Decimal
    gross_edge: Decimal
    estimated_fee_adjusted_edge: Decimal | None
    long_open_fee: TradingFee
    short_open_fee: TradingFee
    long_close_fee: TradingFee
    short_close_fee: TradingFee
    round_trip_fee_rate: Decimal | None
    long_next_funding_at: datetime | None
    short_next_funding_at: datetime | None
    long_time_until_next_funding: timedelta | None
    short_time_until_next_funding: timedelta | None
    match_quality: MatchQuality

    def __post_init__(self) -> None:
        if not isinstance(self.long_funding, FundingRate) or not isinstance(
            self.short_funding, FundingRate
        ):
            raise TypeError("long_funding and short_funding must be FundingRate values")
        _require_aware_datetime(self.as_of, "as_of")
        if (
            not isinstance(self.comparison_horizon, timedelta)
            or self.comparison_horizon <= timedelta()
        ):
            raise ValueError("comparison_horizon must be positive")
        for field_name in (
            "long_normalized_rate",
            "short_normalized_rate",
            "gross_edge",
        ):
            _require_finite_decimal(getattr(self, field_name), field_name)
        _require_optional_finite_decimal(
            self.estimated_fee_adjusted_edge, "estimated_fee_adjusted_edge"
        )
        _require_optional_finite_decimal(
            self.round_trip_fee_rate, "round_trip_fee_rate"
        )
        for field_name in (
            "long_open_fee",
            "short_open_fee",
            "long_close_fee",
            "short_close_fee",
        ):
            if not isinstance(getattr(self, field_name), TradingFee):
                raise TypeError(f"{field_name} must be a TradingFee")
        _require_aware_datetime(self.long_next_funding_at, "long_next_funding_at")
        _require_aware_datetime(self.short_next_funding_at, "short_next_funding_at")
        _require_optional_timedelta(
            self.long_time_until_next_funding, "long_time_until_next_funding"
        )
        _require_optional_timedelta(
            self.short_time_until_next_funding, "short_time_until_next_funding"
        )
        _require_consistent_funding_timing(
            self.long_funding,
            self.as_of,
            self.long_next_funding_at,
            self.long_time_until_next_funding,
            "long",
        )
        _require_consistent_funding_timing(
            self.short_funding,
            self.as_of,
            self.short_next_funding_at,
            self.short_time_until_next_funding,
            "short",
        )
        if not isinstance(self.match_quality, MatchQuality):
            raise TypeError("match_quality must be a MatchQuality")


@dataclass(frozen=True, slots=True)
class SpreadOpportunity:
    """A static executable top-of-book spread estimate between two venues."""

    buy_instrument: Instrument
    sell_instrument: Instrument
    buy_ask: Decimal
    sell_bid: Decimal
    gross_spread: Decimal
    buy_fee: TradingFee
    sell_fee: TradingFee
    estimated_net_spread: Decimal | None
    match_quality: MatchQuality
    buy_exchange_timestamp: datetime | None
    sell_exchange_timestamp: datetime | None
    buy_received_at: datetime
    sell_received_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.buy_instrument, Instrument) or not isinstance(
            self.sell_instrument, Instrument
        ):
            raise TypeError(
                "buy_instrument and sell_instrument must be Instrument values"
            )
        _require_positive_decimal(self.buy_ask, "buy_ask")
        _require_positive_decimal(self.sell_bid, "sell_bid")
        _require_finite_decimal(self.gross_spread, "gross_spread")
        if not isinstance(self.buy_fee, TradingFee) or not isinstance(
            self.sell_fee, TradingFee
        ):
            raise TypeError("buy_fee and sell_fee must be TradingFee values")
        _require_optional_finite_decimal(
            self.estimated_net_spread, "estimated_net_spread"
        )
        if not isinstance(self.match_quality, MatchQuality):
            raise TypeError("match_quality must be a MatchQuality")
        _require_aware_datetime(self.buy_exchange_timestamp, "buy_exchange_timestamp")
        _require_aware_datetime(self.sell_exchange_timestamp, "sell_exchange_timestamp")
        _require_aware_datetime(self.buy_received_at, "buy_received_at")
        _require_aware_datetime(self.sell_received_at, "sell_received_at")
