"""Fee values and provenance for normalized market analysis."""

from dataclasses import dataclass
from decimal import Decimal

from .enums import FeeSource
from .instruments import Instrument, _require_non_empty_string
from .market_data import _require_non_negative_decimal


@dataclass(frozen=True, slots=True)
class TradingFee:
    """Venue fee assumptions; unknown fees deliberately have no numeric value."""

    venue: str
    maker_fee: Decimal | None
    taker_fee: Decimal | None
    source: FeeSource
    instrument: Instrument | None

    def __post_init__(self) -> None:
        _require_non_empty_string(self.venue, "venue")
        if not isinstance(self.source, FeeSource):
            raise TypeError("source must be a FeeSource")
        if self.instrument is not None and not isinstance(self.instrument, Instrument):
            raise TypeError("instrument must be an Instrument or None")
        for field_name in ("maker_fee", "taker_fee"):
            value = getattr(self, field_name)
            if value is not None:
                _require_non_negative_decimal(value, field_name)
        if self.source is FeeSource.UNKNOWN and (
            self.maker_fee is not None or self.taker_fee is not None
        ):
            raise ValueError(
                "UNKNOWN fee source must not have an invented numeric rate"
            )
