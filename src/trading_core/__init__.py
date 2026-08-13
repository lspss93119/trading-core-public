"""Reusable cryptocurrency trading infrastructure."""

from .exceptions import (
    AuthenticationError,
    ExchangeError,
    ExchangeRateLimited,
    ExchangeTimeout,
    ExchangeUnavailable,
    InvalidExchangeData,
    TradingCoreError,
    UnsupportedCapability,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "AuthenticationError",
    "ExchangeError",
    "ExchangeRateLimited",
    "ExchangeTimeout",
    "ExchangeUnavailable",
    "InvalidExchangeData",
    "TradingCoreError",
    "UnsupportedCapability",
]
