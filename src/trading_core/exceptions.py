"""Stable, backend-independent errors for trading-core integrations."""

from __future__ import annotations

from typing import ClassVar


class TradingCoreError(Exception):
    """Base class for errors exposed by the trading-core public API."""


class ExchangeError(TradingCoreError):
    """An exchange operation failed with safe venue and operation context."""

    default_retryable: ClassVar[bool] = False

    venue: str
    operation: str
    retryable: bool
    cause: BaseException | None

    def __init__(
        self,
        venue: str,
        operation: str,
        *,
        retryable: bool | None = None,
        cause: BaseException | None = None,
    ) -> None:
        self.venue = venue
        self.operation = operation
        self.retryable = self.default_retryable if retryable is None else retryable
        self.cause = cause
        super().__init__(f"{type(self).__name__}: exchange operation failed")


class ExchangeTimeout(ExchangeError):
    """An exchange operation exceeded its timeout."""

    default_retryable: ClassVar[bool] = True


class ExchangeRateLimited(ExchangeError):
    """An exchange rejected an operation because of rate limits."""

    default_retryable: ClassVar[bool] = True


class ExchangeUnavailable(ExchangeError):
    """An exchange service is temporarily unavailable."""

    default_retryable: ClassVar[bool] = True


class AuthenticationError(ExchangeError):
    """An exchange rejected the supplied authentication."""


class UnsupportedCapability(ExchangeError):
    """The venue does not support the requested operation."""


class InvalidExchangeData(ExchangeError):
    """An exchange response could not be normalized safely."""


__all__ = [
    "AuthenticationError",
    "ExchangeError",
    "ExchangeRateLimited",
    "ExchangeTimeout",
    "ExchangeUnavailable",
    "InvalidExchangeData",
    "TradingCoreError",
    "UnsupportedCapability",
]
