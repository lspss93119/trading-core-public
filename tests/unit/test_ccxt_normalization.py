from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.support.fake_ccxt import (
    BINANCE_FUNDING,
    BINANCE_FUNDING_INTERVAL,
    BINANCE_MARKET,
    BINANCE_ORDER_BOOK,
    BINANCE_TOP_OF_BOOK,
    BINANCE_TICKER,
    replace_payload,
)
from trading_core.exceptions import InvalidExchangeData
from trading_core.models import ContractType, Instrument, MarketType, TopOfBook
from trading_core.normalization import FundingSignConvention, RateUnit
from trading_core.normalization.ccxt import (
    CCXTMarketMetadata,
    normalize_ccxt_bulk_top_of_book,
    normalize_ccxt_funding_rate,
    normalize_ccxt_instrument,
    normalize_ccxt_market,
    normalize_ccxt_order_book,
    normalize_ccxt_ticker,
)


RECEIVED_AT = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def binance_market_metadata(
    raw_market: dict[str, object] = BINANCE_MARKET,
    instrument: Instrument | None = None,
) -> CCXTMarketMetadata:
    if instrument is None:
        instrument = normalize_ccxt_instrument(raw_market, venue="binance")
    return normalize_ccxt_market(
        {instrument.venue_symbol: raw_market},
        instrument=instrument,
        venue="binance",
    )


def test_ccxt_market_metadata_constructs_a_canonical_instrument() -> None:
    instrument = normalize_ccxt_instrument(BINANCE_MARKET, venue="binance")

    assert instrument.venue == "binance"
    assert instrument.venue_symbol == "BTC/USDT:USDT"
    assert instrument.base == "BTC"
    assert instrument.quote == "USDT"
    assert instrument.settlement == "USDT"
    assert instrument.market_type is MarketType.PERPETUAL
    assert instrument.contract_type is ContractType.LINEAR
    assert instrument.is_active is True


@pytest.mark.parametrize("active", [True, False, None])
def test_ccxt_normalizes_optional_active_status_without_truthiness_conversion(
    active: bool | None,
) -> None:
    instrument = normalize_ccxt_instrument(
        replace_payload(BINANCE_MARKET, active=active),
        venue="binance",
    )

    assert instrument.is_active is active


def test_ccxt_missing_active_status_is_unknown() -> None:
    market = dict(BINANCE_MARKET)
    market.pop("active")

    instrument = normalize_ccxt_instrument(market, venue="binance")

    assert instrument.is_active is None


@pytest.mark.parametrize("active", ["false", 0, 1])
def test_ccxt_rejects_malformed_active_status(active: object) -> None:
    with pytest.raises(InvalidExchangeData) as error:
        normalize_ccxt_instrument(
            replace_payload(BINANCE_MARKET, active=active),
            venue="binance",
        )

    assert error.value.operation == "normalize_ccxt_instrument"


def test_ccxt_ticker_uses_executable_top_of_book_when_ticker_omits_bid_ask() -> None:
    instrument = normalize_ccxt_instrument(BINANCE_MARKET, venue="binance")

    ticker = normalize_ccxt_ticker(
        BINANCE_TICKER,
        raw_order_book=BINANCE_ORDER_BOOK,
        require_top_of_book=True,
        market=binance_market_metadata(instrument=instrument),
        received_at=RECEIVED_AT,
    )

    assert ticker.instrument == instrument
    assert ticker.bid == Decimal("100001")
    assert ticker.ask == Decimal("100002")
    assert ticker.last == Decimal("100001")
    assert ticker.mark == Decimal("100000.5")
    assert ticker.index == Decimal("100000.25")
    assert ticker.exchange_timestamp == datetime(
        2026, 8, 11, 11, 0, 0, 100000, tzinfo=UTC
    )
    assert ticker.received_at == RECEIVED_AT
    assert "do-not-export" not in repr(ticker)
    assert not hasattr(ticker, "info")


def test_ccxt_bulk_top_of_book_normalizes_unified_prices_and_amounts() -> None:
    instrument = normalize_ccxt_instrument(BINANCE_MARKET, venue="binance")

    top_of_book = normalize_ccxt_bulk_top_of_book(
        BINANCE_TOP_OF_BOOK,
        instrument=instrument,
        received_at=RECEIVED_AT,
    )

    assert isinstance(top_of_book, TopOfBook)
    assert top_of_book.instrument == instrument
    assert top_of_book.bid_price == Decimal("100000")
    assert top_of_book.bid_amount == Decimal("2")
    assert top_of_book.ask_price == Decimal("100003")
    assert top_of_book.ask_amount == Decimal("4")
    assert top_of_book.received_at == RECEIVED_AT
    assert "do-not-export" not in repr(top_of_book)


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"symbol": "ETH/USDT:USDT"}, id="wrong-symbol"),
        pytest.param({"bid": None}, id="missing-bid"),
        pytest.param({"bidVolume": None}, id="missing-bid-volume"),
        pytest.param({"ask": "0"}, id="zero-ask"),
        pytest.param({"askVolume": "-1"}, id="negative-ask-volume"),
        pytest.param({"bid": "Infinity"}, id="infinite-bid"),
    ],
)
def test_ccxt_bulk_top_of_book_rejects_malformed_unified_fields(
    changes: dict[str, object],
) -> None:
    instrument = normalize_ccxt_instrument(BINANCE_MARKET, venue="binance")

    with pytest.raises(InvalidExchangeData) as error:
        normalize_ccxt_bulk_top_of_book(
            replace_payload(BINANCE_TOP_OF_BOOK, **changes),
            instrument=instrument,
            received_at=RECEIVED_AT,
        )

    assert error.value.operation == "normalize_ccxt_bulk_top_of_book"


@pytest.mark.parametrize(
    "order_book_changes",
    [
        pytest.param({"bids": []}, id="empty-bids"),
        pytest.param({"asks": []}, id="empty-asks"),
        pytest.param({"bids": [["0", "2"]]}, id="zero-bid"),
        pytest.param({"asks": [["Infinity", "3"]]}, id="infinite-ask"),
        pytest.param({"symbol": "ETH/USDT:USDT"}, id="wrong-symbol"),
        pytest.param({"bids": [["100004", "1"]]}, id="crossed-book"),
    ],
)
def test_ccxt_ticker_fallback_rejects_unexecutable_order_book_prices(
    order_book_changes: dict[str, object],
) -> None:
    raw_order_book = replace_payload(BINANCE_ORDER_BOOK, **order_book_changes)

    with pytest.raises(InvalidExchangeData) as error:
        normalize_ccxt_ticker(
            BINANCE_TICKER,
            raw_order_book=raw_order_book,
            require_top_of_book=True,
            market=binance_market_metadata(),
            received_at=RECEIVED_AT,
        )

    assert error.value.operation == "normalize_ccxt_ticker"


def test_ccxt_ticker_fallback_requires_exact_present_order_book_symbol() -> None:
    raw_order_book = dict(BINANCE_ORDER_BOOK)
    del raw_order_book["symbol"]

    with pytest.raises(InvalidExchangeData) as error:
        normalize_ccxt_ticker(
            BINANCE_TICKER,
            raw_order_book=raw_order_book,
            require_top_of_book=True,
            market=binance_market_metadata(),
            received_at=RECEIVED_AT,
        )

    assert error.value.operation == "normalize_ccxt_ticker"


@pytest.mark.parametrize(
    "order_book_changes",
    [
        pytest.param(
            {"bids": [["100000", "2"], ["100001", "0"]]},
            id="zero-best-bid-amount",
        ),
        pytest.param(
            {"asks": [["100003", "4"], ["100002", None]]},
            id="none-best-ask-amount",
        ),
        pytest.param(
            {"bids": [["100000", "2"], ["100001", "not-numeric"]]},
            id="invalid-best-bid-amount",
        ),
        pytest.param(
            {"asks": [["100003", "4"], ["100002", "-1"]]},
            id="negative-best-ask-amount",
        ),
    ],
)
def test_ccxt_ticker_fallback_rejects_invalid_best_level_amounts(
    order_book_changes: dict[str, object],
) -> None:
    raw_order_book = replace_payload(BINANCE_ORDER_BOOK, **order_book_changes)

    with pytest.raises(InvalidExchangeData) as error:
        normalize_ccxt_ticker(
            BINANCE_TICKER,
            raw_order_book=raw_order_book,
            require_top_of_book=True,
            market=binance_market_metadata(),
            received_at=RECEIVED_AT,
        )

    assert error.value.operation == "normalize_ccxt_ticker"


def test_ccxt_inverse_ticker_fallback_is_price_only_but_order_book_stays_strict() -> (
    None
):
    inverse_market = replace_payload(
        BINANCE_MARKET,
        linear=False,
        inverse=True,
        contractSize="100",
    )
    market = binance_market_metadata(inverse_market)

    ticker = normalize_ccxt_ticker(
        BINANCE_TICKER,
        raw_order_book=BINANCE_ORDER_BOOK,
        require_top_of_book=True,
        market=market,
        received_at=RECEIVED_AT,
    )

    assert ticker.instrument.contract_type is ContractType.INVERSE
    assert ticker.bid == Decimal("100001")
    assert ticker.ask == Decimal("100002")
    with pytest.raises(InvalidExchangeData) as error:
        normalize_ccxt_order_book(
            BINANCE_ORDER_BOOK,
            market=market,
            received_at=RECEIVED_AT,
        )
    assert error.value.operation == "normalize_ccxt_order_book"


def test_ccxt_order_book_converts_contract_counts_to_sorted_base_amounts() -> None:
    instrument = normalize_ccxt_instrument(BINANCE_MARKET, venue="binance")
    market = binance_market_metadata()

    book = normalize_ccxt_order_book(
        BINANCE_ORDER_BOOK,
        market=market,
        received_at=RECEIVED_AT,
    )

    assert book.instrument == instrument
    assert tuple((level.price, level.amount) for level in book.bids) == (
        (Decimal("100001"), Decimal("0.001")),
        (Decimal("100000"), Decimal("0.002")),
    )
    assert tuple((level.price, level.amount) for level in book.asks) == (
        (Decimal("100002"), Decimal("0.003")),
        (Decimal("100003"), Decimal("0.004")),
    )
    assert book.exchange_timestamp == datetime(
        2026, 8, 11, 11, 0, 0, 100000, tzinfo=UTC
    )
    assert "do-not-export" not in repr(book)


def test_ccxt_funding_normalizes_interval_next_time_units_and_source_sign() -> None:
    instrument = normalize_ccxt_instrument(BINANCE_MARKET, venue="binance")
    percent_short_pays = replace_payload(
        BINANCE_FUNDING,
        fundingRate="0.01",
        interval="8h",
    )

    funding = normalize_ccxt_funding_rate(
        percent_short_pays,
        instrument=instrument,
        received_at=RECEIVED_AT,
        unit=RateUnit.PERCENT,
        source_sign=FundingSignConvention.POSITIVE_SHORT_PAYS_LONG,
    )

    assert funding.rate == Decimal("-0.0001")
    assert funding.interval == timedelta(hours=8)
    assert funding.next_funding_at == datetime(2026, 8, 11, 15, 0, tzinfo=UTC)
    assert funding.exchange_timestamp == datetime(
        2026, 8, 11, 11, 0, 0, 300000, tzinfo=UTC
    )
    assert funding.received_at == RECEIVED_AT
    assert "do-not-export" not in repr(funding)


def test_ccxt_funding_blank_interval_uses_validated_fallback_payload() -> None:
    instrument = normalize_ccxt_instrument(BINANCE_MARKET, venue="binance")
    blank_interval = replace_payload(BINANCE_FUNDING, interval=" \t ")

    funding = normalize_ccxt_funding_rate(
        blank_interval,
        raw_funding_interval=BINANCE_FUNDING_INTERVAL,
        instrument=instrument,
        received_at=RECEIVED_AT,
    )

    assert funding.interval == timedelta(hours=8)


@pytest.mark.parametrize(
    "market_changes",
    [
        pytest.param({"contractSize": None}, id="missing"),
        pytest.param(
            {
                "linear": False,
                "inverse": True,
                "contractSize": "100",
            },
            id="quote-denominated",
        ),
    ],
)
def test_ccxt_order_book_rejects_unproved_base_contract_metadata(
    market_changes: dict[str, object],
) -> None:
    market = replace_payload(BINANCE_MARKET, **market_changes)
    metadata = binance_market_metadata(market)

    with pytest.raises(InvalidExchangeData) as error:
        normalize_ccxt_order_book(
            BINANCE_ORDER_BOOK,
            market=metadata,
            received_at=RECEIVED_AT,
        )

    assert error.value.venue == "binance"
    assert error.value.operation == "normalize_ccxt_order_book"


@pytest.mark.parametrize(
    ("normalizer", "payload", "changes"),
    [
        (normalize_ccxt_ticker, BINANCE_TICKER, {"bid": None, "ask": None}),
        (normalize_ccxt_order_book, BINANCE_ORDER_BOOK, {"bids": "invalid"}),
        (normalize_ccxt_funding_rate, BINANCE_FUNDING, {"interval": None}),
    ],
)
def test_ccxt_malformed_payloads_map_to_stable_invalid_data(
    normalizer: object,
    payload: dict[str, object],
    changes: dict[str, object],
) -> None:
    instrument = normalize_ccxt_instrument(BINANCE_MARKET, venue="binance")
    malformed = replace_payload(payload, **changes)

    with pytest.raises(InvalidExchangeData):
        if normalizer is normalize_ccxt_order_book:
            normalize_ccxt_order_book(
                malformed,
                market=binance_market_metadata(),
                received_at=RECEIVED_AT,
            )
        elif normalizer is normalize_ccxt_funding_rate:
            normalize_ccxt_funding_rate(
                malformed,
                instrument=instrument,
                received_at=RECEIVED_AT,
            )
        else:
            normalize_ccxt_ticker(
                malformed,
                require_top_of_book=True,
                market=binance_market_metadata(instrument=instrument),
                received_at=RECEIVED_AT,
            )
