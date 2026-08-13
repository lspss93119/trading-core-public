"""CCXT raw-payload boundary for canonical trading-core market data."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

from trading_core.exceptions import InvalidExchangeData
from trading_core.exchanges.interfaces import Capability
from trading_core.models import (
    ContractType,
    FundingRate,
    Instrument,
    MarketType,
    OrderBook,
    Ticker,
    TopOfBook,
)

from .funding import FundingSignConvention, canonicalize_funding_value
from .numeric import RateUnit, to_decimal
from .order_book import (
    ContractSizeDenomination,
    ContractSizeMetadata,
    RawAmountUnit,
    normalize_amount_to_base,
    normalize_order_book,
)


_INTERVAL_PATTERN = re.compile(r"^(?P<count>[1-9][0-9]*)(?P<unit>[mhd])$")


@dataclass(frozen=True, slots=True)
class CCXTMarketMetadata:
    """Validated CCXT market facts needed beyond instrument identity."""

    instrument: Instrument
    amount_unit: RawAmountUnit
    contract_metadata: ContractSizeMetadata | None

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, Instrument):
            raise TypeError("instrument must be an Instrument")
        if not isinstance(self.amount_unit, RawAmountUnit):
            raise TypeError("amount_unit must be a RawAmountUnit")
        if self.contract_metadata is not None and not isinstance(
            self.contract_metadata, ContractSizeMetadata
        ):
            raise TypeError("contract_metadata must be ContractSizeMetadata or None")
        if self.amount_unit is RawAmountUnit.BASE_ASSET:
            if self.contract_metadata is not None:
                raise ValueError("base amounts must not have contract metadata")
        elif self.contract_metadata is None:
            raise ValueError("contract amounts require contract metadata")


def capabilities_from_ccxt(raw_has: object) -> frozenset[Capability]:
    """Translate current CCXT ``has`` metadata into public capabilities."""
    if not isinstance(raw_has, Mapping):
        return frozenset()

    capabilities: set[Capability] = set()
    order_book_supported = _ccxt_supports(raw_has.get("fetchOrderBook"))
    if _ccxt_supports(raw_has.get("fetchTicker")) and order_book_supported:
        capabilities.add(Capability.TICKER_SNAPSHOT)
    if order_book_supported:
        capabilities.add(Capability.ORDER_BOOK_SNAPSHOT)
    if _ccxt_supports(raw_has.get("fetchFundingRate")) or _ccxt_supports(
        raw_has.get("fetchFundingRates")
    ):
        capabilities.add(Capability.FUNDING_SNAPSHOT)
    if _ccxt_supports(raw_has.get("fetchFundingRates")):
        capabilities.add(Capability.BULK_FUNDING)
    if _ccxt_supports(raw_has.get("fetchBidsAsks")):
        capabilities.add(Capability.BULK_TOP_OF_BOOK)
    return frozenset(capabilities)


def funding_interval_missing_from_ccxt(raw_funding_rate: object) -> bool:
    """Return whether a unified funding response needs the interval endpoint."""
    return not (
        isinstance(raw_funding_rate, Mapping)
        and isinstance(raw_funding_rate.get("interval"), str)
        and bool(raw_funding_rate["interval"].strip())
    )


def funding_interval_supported_from_ccxt(raw_has: object) -> bool:
    """Return whether current CCXT metadata exposes an interval lookup path."""
    return isinstance(raw_has, Mapping) and (
        _ccxt_supports(raw_has.get("fetchFundingInterval"))
        or _ccxt_supports(raw_has.get("fetchFundingIntervals"))
    )


def ticker_order_book_required_from_ccxt(raw_has: object, raw_ticker: object) -> bool:
    """Return whether ticker top-of-book fields need order-book verification."""
    if isinstance(raw_has, Mapping) and raw_has.get("fetchTicker") == "emulated":
        return True
    return _ticker_top_of_book_missing(raw_ticker)


def _ticker_top_of_book_missing(raw_ticker: object) -> bool:
    if not isinstance(raw_ticker, Mapping):
        return False
    return any(
        value is None or (isinstance(value, str) and not value.strip())
        for value in (raw_ticker.get("bid"), raw_ticker.get("ask"))
    )


def normalize_ccxt_market(
    raw_markets: object,
    *,
    instrument: Instrument,
    venue: str,
) -> CCXTMarketMetadata:
    """Reduce one raw CCXT market to validated adapter metadata."""
    operation = "load_markets"
    try:
        markets = _require_mapping(raw_markets, field_name="markets")
        raw_market = markets.get(instrument.venue_symbol)
        if raw_market is None:
            for candidate in markets.values():
                if (
                    isinstance(candidate, Mapping)
                    and candidate.get("symbol") == instrument.venue_symbol
                ):
                    raw_market = candidate
                    break
        market = _require_mapping(raw_market, field_name="market")
        normalized = normalize_ccxt_instrument(market, venue=venue)
        if normalized != instrument:
            raise ValueError("market metadata does not match requested instrument")
        amount_unit, contract_metadata = _ccxt_amount_metadata(market)
        return CCXTMarketMetadata(
            instrument=instrument,
            amount_unit=amount_unit,
            contract_metadata=contract_metadata,
        )
    except InvalidExchangeData:
        raise
    except (ArithmeticError, TypeError, ValueError) as error:
        raise InvalidExchangeData(venue, operation, cause=error) from error


def normalize_ccxt_instrument(
    raw_market: object,
    *,
    venue: str,
) -> Instrument:
    """Construct a canonical Instrument from unified CCXT market metadata."""
    operation = "normalize_ccxt_instrument"
    try:
        market = _require_mapping(raw_market, field_name="market")
        symbol = _required_string(market, "symbol")
        base = _required_string(market, "base")
        quote = _required_string(market, "quote")
        is_active = _optional_bool(market, "active")
        spot = _required_bool(market, "spot")
        swap = _required_bool(market, "swap")
        contract = _required_bool(market, "contract")

        if spot and not swap and not contract:
            settlement = _optional_string(market, "settle") or quote
            market_type = MarketType.SPOT
            contract_type = ContractType.NONE
        elif swap and contract and not spot:
            settlement = _required_string(market, "settle")
            linear = _required_bool(market, "linear")
            inverse = _required_bool(market, "inverse")
            if linear == inverse:
                raise ValueError("perpetual contract direction is ambiguous")
            market_type = MarketType.PERPETUAL
            contract_type = ContractType.LINEAR if linear else ContractType.INVERSE
        else:
            raise ValueError("unsupported CCXT market type")

        return Instrument(
            venue=venue,
            venue_symbol=symbol,
            base=base,
            quote=quote,
            settlement=settlement,
            market_type=market_type,
            contract_type=contract_type,
            is_active=is_active,
        )
    except InvalidExchangeData:
        raise
    except (ArithmeticError, TypeError, ValueError) as error:
        raise InvalidExchangeData(venue, operation, cause=error) from error


def _normalize_ccxt_instruments(
    raw_markets: object,
    *,
    venue: str,
) -> tuple[Instrument, ...]:
    """Normalize every representable market in a CCXT market catalog."""
    operation = "normalize_ccxt_instruments"
    try:
        markets = _require_mapping(raw_markets, field_name="markets")
        return tuple(
            normalize_ccxt_instrument(market, venue=venue)
            for market in markets.values()
            if not _is_explicitly_unrepresentable_market(market)
        )
    except InvalidExchangeData as error:
        if error.operation == operation:
            raise
        raise InvalidExchangeData(venue, operation, cause=error) from error
    except (ArithmeticError, TypeError, ValueError) as error:
        raise InvalidExchangeData(venue, operation, cause=error) from error


def normalize_ccxt_ticker(
    raw_ticker: object,
    *,
    raw_order_book: object | None = None,
    require_top_of_book: bool,
    market: CCXTMarketMetadata,
    received_at: datetime,
) -> Ticker:
    """Convert a unified CCXT ticker while dropping arbitrary raw fields."""
    operation = "normalize_ccxt_ticker"
    instrument = market.instrument
    try:
        ticker = _require_mapping(raw_ticker, field_name="ticker")
        _validate_payload_symbol(ticker, instrument)
        if not isinstance(require_top_of_book, bool):
            raise TypeError("require_top_of_book must be a boolean")
        bid: Decimal
        ask: Decimal
        exchange_timestamp = _optional_timestamp(ticker.get("timestamp"))
        if require_top_of_book:
            bid, ask, exchange_timestamp = normalize_ccxt_top_of_book(
                raw_order_book,
                instrument=instrument,
            )
        elif _ticker_top_of_book_missing(ticker):
            raise ValueError("ticker top of book is unavailable")
        else:
            bid = _required_decimal(ticker, "bid")
            ask = _required_decimal(ticker, "ask")
        return Ticker(
            instrument=instrument,
            bid=bid,
            ask=ask,
            last=_optional_decimal(ticker, "last"),
            mark=_optional_decimal(ticker, "markPrice"),
            index=_optional_decimal(ticker, "indexPrice"),
            exchange_timestamp=exchange_timestamp,
            received_at=received_at,
        )
    except InvalidExchangeData as error:
        if error.operation == operation:
            raise
        raise InvalidExchangeData(instrument.venue, operation, cause=error) from error
    except (ArithmeticError, OverflowError, TypeError, ValueError) as error:
        raise InvalidExchangeData(instrument.venue, operation, cause=error) from error


def normalize_ccxt_top_of_book(
    raw_order_book: object,
    *,
    instrument: Instrument,
) -> tuple[Decimal, Decimal, datetime | None]:
    """Derive executable best prices without normalizing raw quantities."""
    operation = "normalize_ccxt_top_of_book"
    try:
        order_book = _require_mapping(raw_order_book, field_name="order_book")
        _validate_required_payload_symbol(order_book, instrument)
        best_bid = _best_raw_level_price(
            order_book.get("bids"), field_name="bids", highest=True
        )
        best_ask = _best_raw_level_price(
            order_book.get("asks"), field_name="asks", highest=False
        )
        if best_bid > best_ask:
            raise ValueError("best bid must not be greater than best ask")
        return (
            best_bid,
            best_ask,
            _optional_timestamp(order_book.get("timestamp")),
        )
    except (ArithmeticError, OverflowError, TypeError, ValueError) as error:
        raise InvalidExchangeData(instrument.venue, operation, cause=error) from error


def normalize_ccxt_bulk_top_of_book(
    raw_ticker: object,
    *,
    market: CCXTMarketMetadata,
    received_at: datetime,
) -> TopOfBook:
    """Convert one unified CCXT bulk ticker to a canonical top of book."""
    operation = "normalize_ccxt_bulk_top_of_book"
    instrument = market.instrument
    try:
        ticker = _require_mapping(raw_ticker, field_name="ticker")
        _validate_payload_symbol(ticker, instrument)
        return TopOfBook(
            instrument=instrument,
            bid_price=_required_decimal(ticker, "bid"),
            bid_amount=normalize_amount_to_base(
                _required_decimal(ticker, "bidVolume"),
                amount_unit=market.amount_unit,
                contract_metadata=market.contract_metadata,
            ),
            ask_price=_required_decimal(ticker, "ask"),
            ask_amount=normalize_amount_to_base(
                _required_decimal(ticker, "askVolume"),
                amount_unit=market.amount_unit,
                contract_metadata=market.contract_metadata,
            ),
            received_at=received_at,
        )
    except InvalidExchangeData as error:
        if error.operation == operation:
            raise
        raise InvalidExchangeData(instrument.venue, operation, cause=error) from error
    except (ArithmeticError, OverflowError, TypeError, ValueError) as error:
        raise InvalidExchangeData(instrument.venue, operation, cause=error) from error


def normalize_ccxt_order_book(
    raw_order_book: object,
    *,
    market: CCXTMarketMetadata,
    received_at: datetime,
) -> OrderBook:
    """Convert unified CCXT levels to sorted quote-price/base-amount levels."""
    operation = "normalize_ccxt_order_book"
    instrument = market.instrument
    try:
        order_book = _require_mapping(raw_order_book, field_name="order_book")
        _validate_payload_symbol(order_book, instrument)
        return normalize_order_book(
            instrument=instrument,
            bids=_raw_levels(order_book.get("bids"), field_name="bids"),
            asks=_raw_levels(order_book.get("asks"), field_name="asks"),
            amount_unit=market.amount_unit,
            contract_metadata=market.contract_metadata,
            exchange_timestamp=_optional_timestamp(order_book.get("timestamp")),
            received_at=received_at,
        )
    except InvalidExchangeData as error:
        if error.operation == operation:
            raise
        raise InvalidExchangeData(instrument.venue, operation, cause=error) from error
    except (ArithmeticError, OverflowError, TypeError, ValueError) as error:
        raise InvalidExchangeData(instrument.venue, operation, cause=error) from error


def normalize_ccxt_funding_rate(
    raw_funding_rate: object,
    *,
    raw_funding_interval: object | None = None,
    instrument: Instrument,
    received_at: datetime,
    unit: RateUnit = RateUnit.DECIMAL_FRACTION,
    source_sign: FundingSignConvention = (
        FundingSignConvention.POSITIVE_LONG_PAYS_SHORT
    ),
) -> FundingRate:
    """Convert unified CCXT funding fields to canonical units and timing."""
    operation = "normalize_ccxt_funding_rate"
    try:
        funding = _require_mapping(raw_funding_rate, field_name="funding_rate")
        _validate_payload_symbol(funding, instrument)
        next_timestamp = funding.get("nextFundingTimestamp")
        if next_timestamp is None:
            next_timestamp = funding.get("fundingTimestamp")
        interval_value = funding.get("interval")
        interval_needs_fallback = interval_value is None or (
            isinstance(interval_value, str) and not interval_value.strip()
        )
        if interval_needs_fallback and raw_funding_interval is not None:
            interval_payload = _require_mapping(
                raw_funding_interval, field_name="funding_interval"
            )
            _validate_payload_symbol(interval_payload, instrument)
            interval_value = interval_payload.get("interval")
        return FundingRate(
            instrument=instrument,
            rate=canonicalize_funding_value(
                _required_numeric(funding, "fundingRate"),
                unit=unit,
                source_sign=source_sign,
            ),
            interval=_parse_interval(
                _require_string_value(interval_value, field_name="interval")
            ),
            next_funding_at=_optional_timestamp(next_timestamp),
            exchange_timestamp=_optional_timestamp(funding.get("timestamp")),
            received_at=received_at,
        )
    except InvalidExchangeData:
        raise
    except (ArithmeticError, OverflowError, TypeError, ValueError) as error:
        raise InvalidExchangeData(instrument.venue, operation, cause=error) from error


def _ccxt_supports(value: object) -> bool:
    return value is True or value == "emulated"


def _is_explicitly_unrepresentable_market(raw_market: object) -> bool:
    if not isinstance(raw_market, Mapping):
        return False
    return raw_market.get("future") is True or raw_market.get("option") is True


def _ccxt_amount_metadata(
    market: Mapping[str, object],
) -> tuple[RawAmountUnit, ContractSizeMetadata | None]:
    contract = _required_bool(market, "contract")
    if not contract:
        return RawAmountUnit.BASE_ASSET, None

    multiplier_value = market.get("contractSize")
    multiplier = (
        None
        if multiplier_value is None
        else to_decimal(
            cast(str | int | float | Decimal, multiplier_value),
            field_name="contractSize",
        )
    )
    linear = _required_bool(market, "linear")
    inverse = _required_bool(market, "inverse")

    if inverse and not linear:
        denomination = ContractSizeDenomination.INVERSE
    elif linear and not inverse:
        denomination = ContractSizeDenomination.BASE_ASSET
    else:
        denomination = ContractSizeDenomination.UNKNOWN

    return (
        RawAmountUnit.CONTRACT,
        ContractSizeMetadata(
            denomination=denomination,
            multiplier=multiplier,
        ),
    )


def _raw_levels(
    value: object,
    *,
    field_name: str,
) -> tuple[tuple[object, object], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError(f"{field_name} must be a sequence")
    levels: list[tuple[object, object]] = []
    for level in value:
        if not isinstance(level, Sequence) or isinstance(level, (str, bytes)):
            raise TypeError("order-book level must be a sequence")
        if len(level) < 2:
            raise ValueError("order-book level requires price and amount")
        levels.append((level[0], level[1]))
    return tuple(levels)


def _best_raw_level_price(
    value: object,
    *,
    field_name: str,
    highest: bool,
) -> Decimal:
    levels = _raw_levels(value, field_name=field_name)
    if not levels:
        raise ValueError(f"{field_name} must not be empty")
    priced_levels = tuple(
        (
            to_decimal(
                cast(str | int | float | Decimal, raw_price),
                field_name=f"{field_name}.price",
            ),
            raw_amount,
        )
        for raw_price, raw_amount in levels
    )
    if any(price <= 0 for price, _raw_amount in priced_levels):
        raise ValueError(f"{field_name} prices must be positive")
    best_price = (
        max(price for price, _raw_amount in priced_levels)
        if highest
        else min(price for price, _raw_amount in priced_levels)
    )
    for price, raw_amount in priced_levels:
        if price != best_price:
            continue
        amount = to_decimal(
            cast(str | int | float | Decimal, raw_amount),
            field_name=f"{field_name}.amount",
        )
        if amount <= 0:
            raise ValueError(f"{field_name} best-level amounts must be positive")
    return best_price


def _validate_required_payload_symbol(
    payload: Mapping[str, object], instrument: Instrument
) -> None:
    symbol = payload.get("symbol")
    if symbol != instrument.venue_symbol:
        raise ValueError("payload symbol must match requested instrument")


def _validate_payload_symbol(
    payload: Mapping[str, object], instrument: Instrument
) -> None:
    symbol = payload.get("symbol")
    if symbol is not None and symbol != instrument.venue_symbol:
        raise ValueError("payload symbol does not match requested instrument")


def _require_mapping(value: object, *, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    return cast(Mapping[str, object], value)


def _required_string(value: Mapping[str, object], key: str) -> str:
    return _require_string_value(value.get(key), field_name=key)


def _require_string_value(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _optional_string(value: Mapping[str, object], key: str) -> str | None:
    result = value.get(key)
    if result is None:
        return None
    if not isinstance(result, str) or not result.strip():
        raise ValueError(f"{key} must be a non-empty string or None")
    return result


def _required_bool(value: Mapping[str, object], key: str) -> bool:
    result = value.get(key)
    if not isinstance(result, bool):
        raise TypeError(f"{key} must be a boolean")
    return result


def _optional_bool(value: Mapping[str, object], key: str) -> bool | None:
    result = value.get(key)
    if result is None:
        return None
    if not isinstance(result, bool):
        raise TypeError(f"{key} must be a boolean or None")
    return result


def _required_numeric(
    value: Mapping[str, object], key: str
) -> str | int | float | Decimal:
    result = value.get(key)
    if isinstance(result, bool) or not isinstance(result, (str, int, float, Decimal)):
        raise TypeError(f"{key} must be decimal-compatible")
    return result


def _required_decimal(value: Mapping[str, object], key: str) -> Decimal:
    return to_decimal(_required_numeric(value, key), field_name=key)


def _optional_decimal(value: Mapping[str, object], key: str) -> Decimal | None:
    result = value.get(key)
    if result is None:
        return None
    return to_decimal(
        cast(str | int | float | Decimal, result),
        field_name=key,
    )


def _optional_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    milliseconds = to_decimal(
        cast(str | int | float | Decimal, value),
        field_name="timestamp",
    )
    if milliseconds < 0 or milliseconds != milliseconds.to_integral_value():
        raise ValueError("timestamp must be a non-negative whole millisecond")
    whole_milliseconds = int(milliseconds)
    seconds, remainder = divmod(whole_milliseconds, 1000)
    return datetime.fromtimestamp(seconds, tz=UTC) + timedelta(milliseconds=remainder)


def _parse_interval(value: str) -> timedelta:
    match = _INTERVAL_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("interval must use a positive CCXT m, h, or d duration")
    count = int(match.group("count"))
    unit = match.group("unit")
    if unit == "m":
        return timedelta(minutes=count)
    if unit == "h":
        return timedelta(hours=count)
    return timedelta(days=count)


__all__ = [
    "CCXTMarketMetadata",
    "capabilities_from_ccxt",
    "funding_interval_missing_from_ccxt",
    "funding_interval_supported_from_ccxt",
    "normalize_ccxt_funding_rate",
    "normalize_ccxt_instrument",
    "normalize_ccxt_market",
    "normalize_ccxt_order_book",
    "normalize_ccxt_bulk_top_of_book",
    "normalize_ccxt_ticker",
    "normalize_ccxt_top_of_book",
    "ticker_order_book_required_from_ccxt",
]
