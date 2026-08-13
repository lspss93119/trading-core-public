"""Pure funding-arbitrage analysis over normalized observations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from trading_core.fees import round_trip_taker_fee
from trading_core.matching import CompatibilityPolicy, match_instruments
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
from trading_core.normalization import normalize_funding_rate
from trading_core.policies import FreshnessPolicy


class FundingArbitrageFinder:
    """Compare observed funding rates without contacting any exchange."""

    def find(
        self,
        funding_rates: Sequence[FundingRate],
        *,
        fees: Mapping[Instrument, TradingFee],
        as_of: datetime,
        comparison_horizon: timedelta,
        freshness_policy: FreshnessPolicy,
        compatibility_policy: CompatibilityPolicy,
    ) -> tuple[FundingOpportunity, ...]:
        """Return positive-gross, fee-aware comparisons in stable order."""
        _require_aware_datetime(as_of, "as_of")
        if (
            not isinstance(comparison_horizon, timedelta)
            or comparison_horizon <= timedelta()
        ):
            raise ValueError("comparison_horizon must be positive")
        if not isinstance(freshness_policy, FreshnessPolicy):
            raise TypeError("freshness_policy must be a FreshnessPolicy")
        if not isinstance(compatibility_policy, CompatibilityPolicy):
            raise TypeError("compatibility_policy must be a CompatibilityPolicy")

        eligible: list[tuple[FundingRate, Decimal]] = []
        for funding_rate in funding_rates:
            if not isinstance(funding_rate, FundingRate):
                raise TypeError("funding_rates must contain FundingRate values")
            instrument = funding_rate.instrument
            if instrument.market_type is not MarketType.PERPETUAL:
                continue
            if instrument.contract_type not in (
                ContractType.LINEAR,
                ContractType.INVERSE,
            ):
                continue
            if not freshness_policy.is_fresh(
                received_at=funding_rate.received_at,
                as_of=as_of,
            ):
                continue
            normalized = normalize_funding_rate(
                funding_rate,
                horizon=comparison_horizon,
            )
            eligible.append((funding_rate, normalized))

        opportunities: list[FundingOpportunity] = []
        for left_index, (left_rate, left_normalized) in enumerate(eligible):
            for right_rate, right_normalized in eligible[left_index + 1 :]:
                if left_rate.instrument.venue == right_rate.instrument.venue:
                    continue
                match = match_instruments(
                    left_rate.instrument,
                    right_rate.instrument,
                    policy=compatibility_policy,
                )
                if match.quality is MatchQuality.INCOMPATIBLE:
                    continue
                if left_normalized == right_normalized:
                    continue

                if left_normalized < right_normalized:
                    long_rate, long_normalized = left_rate, left_normalized
                    short_rate, short_normalized = right_rate, right_normalized
                else:
                    long_rate, long_normalized = right_rate, right_normalized
                    short_rate, short_normalized = left_rate, left_normalized

                long_past, long_time_until = _time_until_next_funding(long_rate, as_of)
                short_past, short_time_until = _time_until_next_funding(
                    short_rate, as_of
                )
                if long_past or short_past:
                    continue

                long_fee = _fee_for(long_rate.instrument, fees)
                short_fee = _fee_for(short_rate.instrument, fees)
                round_trip = round_trip_taker_fee(
                    long_open=long_fee,
                    short_open=short_fee,
                    long_close=long_fee,
                    short_close=short_fee,
                )
                gross_edge = short_normalized - long_normalized
                fee_adjusted = None if round_trip is None else gross_edge - round_trip
                opportunities.append(
                    FundingOpportunity(
                        long_funding=long_rate,
                        short_funding=short_rate,
                        as_of=as_of,
                        comparison_horizon=comparison_horizon,
                        long_normalized_rate=long_normalized,
                        short_normalized_rate=short_normalized,
                        gross_edge=gross_edge,
                        estimated_fee_adjusted_edge=fee_adjusted,
                        long_open_fee=long_fee,
                        short_open_fee=short_fee,
                        long_close_fee=long_fee,
                        short_close_fee=short_fee,
                        round_trip_fee_rate=round_trip,
                        long_next_funding_at=long_rate.next_funding_at,
                        short_next_funding_at=short_rate.next_funding_at,
                        long_time_until_next_funding=long_time_until,
                        short_time_until_next_funding=short_time_until,
                        match_quality=match.quality,
                    )
                )

        opportunities.sort(key=_sort_key)
        return tuple(opportunities)


def _require_aware_datetime(value: datetime, field_name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field_name} must be timezone-aware")


def _time_until_next_funding(
    funding_rate: FundingRate,
    as_of: datetime,
) -> tuple[bool, timedelta | None]:
    next_funding_at = funding_rate.next_funding_at
    if next_funding_at is None:
        return False, None
    time_until = next_funding_at - as_of
    if time_until < timedelta():
        return True, None
    return False, time_until


def _fee_for(
    instrument: Instrument, fees: Mapping[Instrument, TradingFee]
) -> TradingFee:
    fee = fees.get(instrument)
    if fee is None or fee.taker_fee is None:
        return TradingFee(
            venue=instrument.venue,
            maker_fee=None,
            taker_fee=None,
            source=FeeSource.UNKNOWN,
            instrument=instrument,
        )
    return fee


def _sort_key(
    opportunity: FundingOpportunity,
) -> tuple[Decimal, str, str, str, str]:
    long_instrument = opportunity.long_funding.instrument
    short_instrument = opportunity.short_funding.instrument
    return (
        -opportunity.gross_edge,
        long_instrument.venue,
        long_instrument.venue_symbol,
        short_instrument.venue,
        short_instrument.venue_symbol,
    )


__all__ = ["FundingArbitrageFinder"]
