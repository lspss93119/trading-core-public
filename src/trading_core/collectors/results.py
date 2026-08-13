"""Structured results for multi-provider market-data collection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Generic, TypeVar

from trading_core.exceptions import TradingCoreError
from trading_core.models import Instrument


T = TypeVar("T")


def _require_aware_datetime(value: datetime, field_name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field_name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class CollectionError:
    """One isolated provider failure from a collection attempt."""

    venue: str
    operation: str
    instrument: Instrument | None
    error: TradingCoreError


@dataclass(frozen=True, slots=True)
class CollectionResult(Generic[T]):
    """Ordered successful data and structured failures from one collection."""

    data: tuple[T, ...]
    errors: tuple[CollectionError, ...]
    started_at: datetime
    completed_at: datetime
    requests_made: bool

    def __post_init__(self) -> None:
        if not isinstance(self.data, tuple):
            raise TypeError("data must be a tuple")
        if not isinstance(self.errors, tuple):
            raise TypeError("errors must be a tuple")
        _require_aware_datetime(self.started_at, "started_at")
        _require_aware_datetime(self.completed_at, "completed_at")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not be before started_at")
        if not self.requests_made and (self.data or self.errors):
            raise ValueError("requests_made=False requires empty data and errors")
        if self.requests_made and not self.data and not self.errors:
            raise ValueError("requests_made collections require data or errors")

    @property
    def partial(self) -> bool:
        """Whether some requests succeeded and some failed."""
        return bool(self.data) and bool(self.errors)

    @property
    def requested_count(self) -> int:
        """Return the number of logical requests represented by the result."""
        return len(self.data) + len(self.errors)

    @property
    def successful_count(self) -> int:
        """Return the number of successful logical requests."""
        return len(self.data)

    @property
    def failed_count(self) -> int:
        """Return the number of failed logical requests."""
        return len(self.errors)

    @property
    def complete(self) -> bool:
        """Whether the collection has no failures, including no requests."""
        return not self.errors

    @property
    def failed(self) -> bool:
        """Whether every requested result failed."""
        return not self.data and bool(self.errors)
