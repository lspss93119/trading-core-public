"""Stable categories used by normalized trading domain models."""

from enum import StrEnum


class MarketType(StrEnum):
    """The broad market type for an instrument."""

    SPOT = "spot"
    PERPETUAL = "perpetual"


class ContractType(StrEnum):
    """Settlement shape for an instrument contract."""

    LINEAR = "linear"
    INVERSE = "inverse"
    NONE = "none"


class FeeSource(StrEnum):
    """Origin of an applicable trading fee."""

    CONFIG = "config"
    API = "api"
    DEFAULT = "default"
    UNKNOWN = "unknown"


class MatchQuality(StrEnum):
    """Compatibility result for two normalized instruments."""

    EXACT = "exact"
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
