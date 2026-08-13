"""Canonical exchange instrument identity."""

from dataclasses import dataclass, field

from .enums import ContractType, MarketType


def _require_non_empty_string(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class Instrument:
    """A venue-specific instrument with canonical comparison fields."""

    venue: str
    venue_symbol: str
    base: str
    quote: str
    settlement: str
    market_type: MarketType
    contract_type: ContractType
    is_active: bool | None = field(default=None, compare=False, hash=False)

    def __post_init__(self) -> None:
        for field_name in ("venue", "venue_symbol", "base", "quote", "settlement"):
            _require_non_empty_string(getattr(self, field_name), field_name)
        if not isinstance(self.market_type, MarketType):
            raise TypeError("market_type must be a MarketType")
        if not isinstance(self.contract_type, ContractType):
            raise TypeError("contract_type must be a ContractType")
        if self.is_active is not None and not isinstance(self.is_active, bool):
            raise TypeError("is_active must be a bool or None")
