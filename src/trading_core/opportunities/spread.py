"""Pure executable top-of-book spread analysis."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal

from trading_core.matching import CompatibilityPolicy, match_instruments
from trading_core.models import (
    FeeSource,
    Instrument,
    MatchQuality,
    SpreadOpportunity,
    Ticker,
    TradingFee,
)
from trading_core.policies import FreshnessPolicy


class CrossExchangeSpreadFinder:
    """Compare executable top-of-book prices without contacting any exchange."""

    def find(
        self,
        tickers: Sequence[Ticker],
        *,
        fees: Mapping[Instrument, TradingFee],
        as_of: datetime,
        freshness_policy: FreshnessPolicy,
    ) -> tuple[SpreadOpportunity, ...]:
        """Return positive-gross exact-instrument spread estimates in stable order."""
        _require_aware_datetime(as_of, "as_of")
        if not isinstance(freshness_policy, FreshnessPolicy):
            raise TypeError("freshness_policy must be a FreshnessPolicy")

        fresh_tickers = tuple(
            ticker
            for ticker in tickers
            if _is_fresh_ticker(ticker, as_of=as_of, policy=freshness_policy)
        )
        opportunities: list[SpreadOpportunity] = []
        for left_index, left in enumerate(fresh_tickers):
            for right in fresh_tickers[left_index + 1 :]:
                if left.instrument.venue == right.instrument.venue:
                    continue
                match = match_instruments(
                    left.instrument,
                    right.instrument,
                    policy=CompatibilityPolicy(),
                )
                if match.quality is not MatchQuality.EXACT:
                    continue
                opportunities.extend(
                    candidate
                    for candidate in (
                        _make_candidate(
                            buy=left,
                            sell=right,
                            fees=fees,
                            match_quality=match.quality,
                        ),
                        _make_candidate(
                            buy=right,
                            sell=left,
                            fees=fees,
                            match_quality=match.quality,
                        ),
                    )
                    if candidate is not None
                )

        opportunities.sort(key=_sort_key)
        return tuple(opportunities)


def _is_fresh_ticker(
    ticker: Ticker,
    *,
    as_of: datetime,
    policy: FreshnessPolicy,
) -> bool:
    if not isinstance(ticker, Ticker):
        raise TypeError("tickers must contain Ticker values")
    return policy.is_fresh(received_at=ticker.received_at, as_of=as_of)


def _make_candidate(
    *,
    buy: Ticker,
    sell: Ticker,
    fees: Mapping[Instrument, TradingFee],
    match_quality: MatchQuality,
) -> SpreadOpportunity | None:
    if sell.bid <= buy.ask:
        return None
    gross_spread = (sell.bid - buy.ask) / buy.ask
    buy_fee = _fee_for(buy.instrument, fees)
    sell_fee = _fee_for(sell.instrument, fees)
    if buy_fee.taker_fee is None or sell_fee.taker_fee is None:
        estimated_net_spread = None
    else:
        estimated_net_spread = gross_spread - buy_fee.taker_fee - sell_fee.taker_fee
    return SpreadOpportunity(
        buy_instrument=buy.instrument,
        sell_instrument=sell.instrument,
        buy_ask=buy.ask,
        sell_bid=sell.bid,
        gross_spread=gross_spread,
        buy_fee=buy_fee,
        sell_fee=sell_fee,
        estimated_net_spread=estimated_net_spread,
        match_quality=match_quality,
        buy_exchange_timestamp=buy.exchange_timestamp,
        sell_exchange_timestamp=sell.exchange_timestamp,
        buy_received_at=buy.received_at,
        sell_received_at=sell.received_at,
    )


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


def _require_aware_datetime(value: datetime, field_name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field_name} must be timezone-aware")


def _sort_key(
    opportunity: SpreadOpportunity,
) -> tuple[Decimal, str, str, str, str]:
    buy = opportunity.buy_instrument
    sell = opportunity.sell_instrument
    return (
        -opportunity.gross_spread,
        buy.venue,
        buy.venue_symbol,
        sell.venue,
        sell.venue_symbol,
    )


__all__ = ["CrossExchangeSpreadFinder"]
