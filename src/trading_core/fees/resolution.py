"""Pure taker-fee resolution with explicit provenance."""

from decimal import Decimal

from trading_core.models import FeeSource, Instrument, TradingFee


def resolve_taker_fee(
    *,
    venue: str,
    instrument: Instrument | None,
    config_fee: TradingFee | None,
    api_fee: TradingFee | None,
    default_fee: TradingFee | None,
) -> TradingFee:
    """Resolve a taker fee without substituting maker fees or inventing a rate."""
    for candidate, source in (
        (config_fee, FeeSource.CONFIG),
        (api_fee, FeeSource.API),
        (default_fee, FeeSource.DEFAULT),
    ):
        if candidate is not None and candidate.taker_fee is not None:
            return TradingFee(
                venue=venue,
                maker_fee=None,
                taker_fee=candidate.taker_fee,
                source=source,
                instrument=instrument,
            )
    return TradingFee(
        venue=venue,
        maker_fee=None,
        taker_fee=None,
        source=FeeSource.UNKNOWN,
        instrument=instrument,
    )


def round_trip_taker_fee(
    *,
    long_open: TradingFee,
    short_open: TradingFee,
    long_close: TradingFee,
    short_close: TradingFee,
) -> Decimal | None:
    """Sum four required taker-fee legs, or preserve an unknown result."""
    fees = (
        long_open.taker_fee,
        short_open.taker_fee,
        long_close.taker_fee,
        short_close.taker_fee,
    )
    if any(fee is None for fee in fees):
        return None
    known_fees = (fee for fee in fees if fee is not None)
    return sum(known_fees, start=Decimal("0"))
