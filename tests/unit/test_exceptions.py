import pytest

from trading_core import (
    AuthenticationError,
    ExchangeError,
    ExchangeRateLimited,
    ExchangeTimeout,
    ExchangeUnavailable,
    InvalidExchangeData,
    TradingCoreError,
    UnsupportedCapability,
)


def test_exchange_errors_preserve_context_and_form_a_stable_hierarchy() -> None:
    error = ExchangeError(
        "binance",
        "fetch_ticker",
        retryable=True,
    )

    assert isinstance(error, TradingCoreError)
    assert isinstance(error, ExchangeError)
    assert error.venue == "binance"
    assert error.operation == "fetch_ticker"
    assert error.retryable is True
    assert error.cause is None
    assert "binance" not in str(error)
    assert "fetch_ticker" not in str(error)


@pytest.mark.parametrize(
    ("error_type", "retryable"),
    [
        (ExchangeTimeout, True),
        (ExchangeRateLimited, True),
        (ExchangeUnavailable, True),
        (AuthenticationError, False),
        (UnsupportedCapability, False),
        (InvalidExchangeData, False),
    ],
)
def test_specialized_exchange_errors_have_conservative_retryable_defaults(
    error_type: type[ExchangeError], retryable: bool
) -> None:
    error = error_type("kraken", "fetch_order_book")

    assert isinstance(error, ExchangeError)
    assert error.retryable is retryable


def test_exchange_error_retains_cause_without_rendering_cause_details() -> None:
    cause = RuntimeError("api_key=do-not-render")

    try:
        raise ExchangeTimeout(
            "coinbase",
            "fetch_funding_rate",
            cause=cause,
        ) from cause
    except ExchangeTimeout as error:
        assert error.cause is cause
        assert error.__cause__ is cause
        assert "do-not-render" not in str(error)
        assert "api_key" not in str(error)


@pytest.mark.parametrize(
    ("venue", "operation"),
    [
        (
            "binance?apiKey=venue-secret&timeout=10",
            "fetch_ticker",
        ),
        (
            "kraken",
            "request(config={'api_secret': 'operation-secret'})",
        ),
    ],
)
def test_exchange_error_keeps_raw_context_fields_out_of_rendered_message(
    venue: str, operation: str
) -> None:
    error = ExchangeTimeout(venue, operation)

    rendered = str(error)

    assert error.venue == venue
    assert error.operation == operation
    assert "venue-secret" not in rendered
    assert "operation-secret" not in rendered
    assert venue not in rendered
    assert operation not in rendered
    assert rendered == "ExchangeTimeout: exchange operation failed"


@pytest.mark.parametrize(
    "unsafe_context",
    [
        "Authorization: Bearer REAL-BEARER-SECRET",
        "api_secret: 'REAL SECRET WITH SPACE'",
        "jwt=REAL-JWT, refresh_token=REAL-REFRESH, client_id=REAL-ID",
        "?access_token=[REDACTED]&foo=bar",
        "config={'timeout': 10, 'sandbox': True}",
        "eyJhbGciOiJIUzI1NiJ9.REAL-JWT-SUBJECT.REAL-JWT-SIGNATURE",
        "sk_live_plausible_opaque_api_key_123456",
    ],
)
def test_exchange_error_redacts_entire_unsafe_context_field(
    unsafe_context: str,
) -> None:
    error = ExchangeTimeout("binance", unsafe_context)

    rendered = str(error)

    assert unsafe_context not in rendered
    assert "REAL-BEARER-SECRET" not in rendered
    assert "REAL SECRET WITH SPACE" not in rendered
    assert "REAL-JWT" not in rendered
    assert "REAL-REFRESH" not in rendered
    assert "REAL-ID" not in rendered
    assert "access_token" not in rendered
    assert "foo=bar" not in rendered
    assert "sandbox" not in rendered
    assert rendered == "ExchangeTimeout: exchange operation failed"


def test_exchange_error_preserves_strictly_safe_context_identifiers() -> None:
    error = ExchangeTimeout("binance", "fetch_order_book")

    assert error.venue == "binance"
    assert error.operation == "fetch_order_book"
    assert str(error) == "ExchangeTimeout: exchange operation failed"


def test_specialized_errors_remain_distinguishable() -> None:
    unsupported = UnsupportedCapability("bybit", "fetch_funding_rate")
    invalid = InvalidExchangeData("bybit", "normalize_order_book")
    timeout = ExchangeTimeout("bybit", "fetch_ticker")

    assert type(unsupported) is UnsupportedCapability
    assert type(invalid) is InvalidExchangeData
    assert type(timeout) is ExchangeTimeout
    assert not isinstance(unsupported, InvalidExchangeData)
    assert not isinstance(invalid, ExchangeTimeout)
