# trading-core

`trading-core` is a reusable Python package for shared cryptocurrency market-data, exchange-integration, arbitrage, and automated-trading infrastructure.

**Status:** MVP market-data and opportunity-analysis infrastructure

## Public instrument discovery

Consumers can discover canonical instruments through the capability-oriented provider API. They do not need to inspect an exchange client's markets or depend on CCXT payloads:

```python
from typing import cast

from trading_core.exchanges import (
    Capability,
    InstrumentCatalogProvider,
    ProviderRegistry,
)
from trading_core.models import Instrument, MarketType


async def discover_perpetuals(
    registry: ProviderRegistry,
    venue: str,
) -> tuple[Instrument, ...]:
    if not registry.supports(venue, Capability.INSTRUMENT_CATALOG):
        raise ValueError(f"venue {venue!r} does not provide an instrument catalog")

    provider = cast(InstrumentCatalogProvider, registry.require(venue))
    instruments = await provider.list_instruments()
    return tuple(
        instrument
        for instrument in instruments
        if instrument.market_type is MarketType.PERPETUAL
    )
```

Provider composition, such as constructing a `CCXTAdapter`, remains outside the consumer's discovery logic. `Instrument` values are canonical trading-core models; venue-specific market dictionaries do not cross the provider boundary.

An instrument catalog may expose optional active-status metadata on each `Instrument`:

- `is_active=True`: the provider explicitly reports the market as active.
- `is_active=False`: the provider explicitly reports the market as inactive or delisted.
- `is_active=None`: the provider does not supply a reliable active status.

Catalog discovery preserves recognized inactive instruments; consumers decide whether they are eligible for a particular workflow.

## Optional bulk funding

The existing `FundingProvider.fetch_funding_rate()` API remains available for one instrument. Providers that support the optional `Capability.BULK_FUNDING` capability may also implement `BulkFundingProvider.fetch_funding_rates()`:

```python
from typing import cast

from trading_core.exchanges import BulkFundingProvider, Capability


if registry.supports(venue, Capability.BULK_FUNDING):
    provider = cast(BulkFundingProvider, registry.require(venue))
    result = await provider.fetch_funding_rates(instruments)
    # result.data contains canonical FundingRate values.
    # result.errors contains explicit per-instrument failures.
```

Bulk results reuse `CollectionResult`: `requested_count`, `successful_count`, and `failed_count` expose the outcome counts, while each `CollectionError` identifies its canonical instrument. Duplicate canonical instruments are coalesced in first-seen order, and a partial response is never silently reduced to successful data only.

## Optional bulk top of book

Providers that advertise `Capability.BULK_TOP_OF_BOOK` may implement `BulkTopOfBookProvider.fetch_top_of_books()`. The result contains canonical `TopOfBook` values with bid/ask prices and amounts, plus explicit per-instrument errors for missing or malformed bulk responses. It represents only the best bid/ask, not full depth, VWAP, or slippage. CCXT support uses the backend's normalized `fetchBidsAsks` capability; CCXT remains an optional dependency.
