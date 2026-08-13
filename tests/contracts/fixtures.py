"""Minimal protocol-compatible providers shared by exchange contract tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_core.exchanges import Capability
from trading_core.models import (
    ContractType,
    FundingRate,
    Instrument,
    MarketType,
    Ticker,
)


def make_contract_instrument(venue: str) -> Instrument:
    """Return the stable linear-perpetual instrument used by provider contracts."""
    return Instrument(
        venue=venue,
        venue_symbol="BTC/USDT:USDT",
        base="BTC",
        quote="USDT",
        settlement="USDT",
        market_type=MarketType.PERPETUAL,
        contract_type=ContractType.LINEAR,
    )


@dataclass(slots=True)
class FundingTickerProvider:
    """A provider that deliberately exposes only funding and ticker snapshots."""

    venue: str
    capabilities: frozenset[Capability] = field(
        default_factory=lambda: frozenset(
            {Capability.FUNDING_SNAPSHOT, Capability.TICKER_SNAPSHOT}
        )
    )

    async def fetch_ticker(self, instrument: Instrument) -> Ticker:
        """Return a canonical ticker for the requested instrument."""
        received_at = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
        return Ticker(
            instrument=instrument,
            bid=Decimal("100"),
            ask=Decimal("101"),
            last=None,
            mark=None,
            index=None,
            exchange_timestamp=received_at,
            received_at=received_at,
        )

    async def fetch_funding_rate(self, instrument: Instrument) -> FundingRate:
        """Return a canonical funding observation for the requested instrument."""
        received_at = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
        return FundingRate(
            instrument=instrument,
            rate=Decimal("0.0001"),
            interval=timedelta(hours=8),
            next_funding_at=None,
            exchange_timestamp=received_at,
            received_at=received_at,
        )
