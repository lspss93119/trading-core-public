"""Backend-independent exchange provider capability contracts."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Generic, Never, Protocol, SupportsIndex, TypeVar

from trading_core.models import (
    FundingRate,
    Instrument,
    OrderBook,
    Ticker,
    TopOfBook,
    TradingFee,
)

if TYPE_CHECKING:
    from trading_core.collectors.results import CollectionResult


class Capability(StrEnum):
    """A public market-data snapshot capability offered by a provider."""

    TICKER_SNAPSHOT = "ticker_snapshot"
    ORDER_BOOK_SNAPSHOT = "order_book_snapshot"
    FUNDING_SNAPSHOT = "funding_snapshot"
    BULK_FUNDING = "bulk_funding"
    BULK_TOP_OF_BOOK = "bulk_top_of_book"
    INSTRUMENT_CATALOG = "instrument_catalog"


def apply_capability_overrides(
    discovered: frozenset[Capability],
    *,
    enabled: frozenset[Capability] = frozenset(),
    disabled: frozenset[Capability] = frozenset(),
) -> frozenset[Capability]:
    """Apply verified capability overrides with explicit disable precedence."""
    for name, values in (
        ("discovered", discovered),
        ("enabled", enabled),
        ("disabled", disabled),
    ):
        if not isinstance(values, frozenset):
            raise TypeError(f"{name} must be a frozenset of Capability values")
        if not all(isinstance(value, Capability) for value in values):
            raise TypeError(f"{name} must contain only Capability values")
    return (discovered | enabled) - disabled


_MappingKey = TypeVar("_MappingKey")
_MappingValue = TypeVar("_MappingValue")


class _RedactedMapping(Mapping[object, object]):
    """A serialization-only placeholder that contains no configuration data."""

    def __getitem__(self, key: object) -> object:
        raise KeyError(key)

    def __iter__(self) -> Iterator[object]:
        return iter(())

    def __len__(self) -> int:
        return 0

    def __repr__(self) -> str:
        return "<redacted>"


class _ReadOnlyMapping(
    Mapping[_MappingKey, _MappingValue],
    Generic[_MappingKey, _MappingValue],
):
    """An immutable mapping that redacts itself from dataclass serialization."""

    __slots__ = ("_items",)

    def __init__(self, values: Mapping[_MappingKey, _MappingValue]) -> None:
        self._items: tuple[tuple[_MappingKey, _MappingValue], ...] = tuple(
            values.items()
        )

    def __getitem__(self, key: _MappingKey) -> _MappingValue:
        for candidate_key, value in self._items:
            if candidate_key == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[_MappingKey]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __hash__(self) -> int:
        return hash(frozenset(self._items))

    def __deepcopy__(self, memo: dict[int, object]) -> _RedactedMapping:
        """Return no values when dataclasses.asdict or astuple traverses this field."""
        return _RedactedMapping()

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        """Reject pickle instead of serializing configuration mapping contents."""
        raise TypeError("Read-only exchange configuration mappings cannot be pickled")


@dataclass(frozen=True, slots=True, init=False)
class ExchangeConfig:
    """Explicit configuration with read-only sensitive mapping accessors."""

    venue: str
    credentials: Mapping[str, str] | None = field(default=None, repr=False)
    timeout: timedelta = timedelta(seconds=10)
    sandbox: bool = False
    fee_overrides: Mapping[Instrument, TradingFee] = field(
        default_factory=dict,
        repr=False,
    )

    def __init__(
        self,
        venue: str,
        credentials: Mapping[str, str] | None = None,
        timeout: timedelta = timedelta(seconds=10),
        sandbox: bool = False,
        fee_overrides: Mapping[Instrument, TradingFee] | None = None,
    ) -> None:
        object.__setattr__(self, "venue", venue)
        object.__setattr__(
            self,
            "credentials",
            None if credentials is None else _ReadOnlyMapping(credentials),
        )
        object.__setattr__(self, "timeout", timeout)
        object.__setattr__(self, "sandbox", sandbox)
        object.__setattr__(
            self,
            "fee_overrides",
            _ReadOnlyMapping({} if fee_overrides is None else fee_overrides),
        )

    def __copy__(self) -> ExchangeConfig:
        """Return this immutable configuration without traversing sensitive mappings."""
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> ExchangeConfig:
        """Return this immutable configuration without redacting its live state."""
        memo[id(self)] = self
        return self

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        """Reject pickle instead of risking credential serialization or state loss."""
        raise TypeError(
            "ExchangeConfig pickle serialization is disabled for credentials"
        )


class Provider(Protocol):
    """The capability metadata shared by every exchange provider."""

    venue: str
    capabilities: frozenset[Capability]


class InstrumentCatalogProvider(Provider, Protocol):
    """A provider capable of listing canonical venue instruments."""

    async def list_instruments(self) -> tuple[Instrument, ...]:
        """Return the currently discoverable canonical instruments."""


class TickerProvider(Provider, Protocol):
    """A provider capable of returning canonical ticker snapshots."""

    async def fetch_ticker(self, instrument: Instrument) -> Ticker:
        """Fetch a normalized ticker for an instrument."""


class OrderBookProvider(Provider, Protocol):
    """A provider capable of returning canonical order-book snapshots."""

    async def fetch_order_book(self, instrument: Instrument) -> OrderBook:
        """Fetch a normalized order book for an instrument."""


class FundingProvider(Provider, Protocol):
    """A provider capable of returning canonical funding-rate snapshots."""

    async def fetch_funding_rate(self, instrument: Instrument) -> FundingRate:
        """Fetch a normalized funding rate for an instrument."""


class BulkFundingProvider(Provider, Protocol):
    """A provider capable of returning canonical funding-rate batches."""

    async def fetch_funding_rates(
        self, instruments: Sequence[Instrument]
    ) -> CollectionResult[FundingRate]:
        """Fetch funding rates with one explicit outcome per requested instrument."""


class BulkTopOfBookProvider(Provider, Protocol):
    """A provider capable of returning canonical best bid/ask batches."""

    async def fetch_top_of_books(
        self, instruments: Sequence[Instrument]
    ) -> CollectionResult[TopOfBook]:
        """Fetch top-of-book snapshots with one explicit outcome per instrument."""
