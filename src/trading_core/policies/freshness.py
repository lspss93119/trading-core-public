"""Deterministic freshness evaluation for timestamped normalized values."""

from dataclasses import dataclass
from datetime import datetime, timedelta


def _require_aware_datetime(value: datetime, field_name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field_name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class FreshnessPolicy:
    """Decide whether a received timestamp is eligible at an explicit time."""

    max_age: timedelta

    def __post_init__(self) -> None:
        if not isinstance(self.max_age, timedelta):
            raise TypeError("max_age must be a timedelta")
        if self.max_age < timedelta():
            raise ValueError("max_age must be non-negative")

    def age(self, *, received_at: datetime, as_of: datetime) -> timedelta:
        """Return the elapsed time from receipt to the supplied evaluation time."""
        _require_aware_datetime(received_at, "received_at")
        _require_aware_datetime(as_of, "as_of")
        if received_at > as_of:
            raise ValueError("received_at cannot be in the future relative to as_of")
        return as_of - received_at

    def is_fresh(self, *, received_at: datetime, as_of: datetime) -> bool:
        """Return whether age is at most the configured maximum age."""
        return self.age(received_at=received_at, as_of=as_of) <= self.max_age
