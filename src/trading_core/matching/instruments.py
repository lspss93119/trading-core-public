"""Canonical instrument matching with explicit compatibility policy."""

from dataclasses import dataclass

from trading_core.models import ContractType, Instrument, MarketType, MatchQuality


@dataclass(frozen=True, slots=True)
class CompatibilityPolicy:
    """Explicit quote and settlement pairs accepted for linear perpetuals."""

    allowed_quote_settlement_pairs: frozenset[frozenset[tuple[str, str]]] = frozenset()


@dataclass(frozen=True, slots=True)
class InstrumentMatch:
    """A match result that retains both venue-specific instruments."""

    left: Instrument
    right: Instrument
    quality: MatchQuality
    reason: str | None


def match_instruments(
    left: Instrument,
    right: Instrument,
    *,
    policy: CompatibilityPolicy,
) -> InstrumentMatch:
    """Compare canonical instruments without treating venue symbols as identity."""
    if not isinstance(left, Instrument):
        raise TypeError("left must be an Instrument")
    if not isinstance(right, Instrument):
        raise TypeError("right must be an Instrument")
    if not isinstance(policy, CompatibilityPolicy):
        raise TypeError("policy must be a CompatibilityPolicy")

    if _canonical_identity(left) == _canonical_identity(right):
        return InstrumentMatch(left, right, MatchQuality.EXACT, None)

    if left.base != right.base:
        return _incompatible(left, right, "canonical base assets differ")

    if not _are_linear_perpetuals(left, right):
        return _incompatible(
            left,
            right,
            "compatible matching requires two linear perpetual instruments",
        )

    quote_settlement_pair = frozenset(
        {(left.quote, left.settlement), (right.quote, right.settlement)}
    )
    if quote_settlement_pair not in policy.allowed_quote_settlement_pairs:
        return _incompatible(
            left,
            right,
            "quote and settlement pair is not explicitly allowed by policy",
        )

    return InstrumentMatch(left, right, MatchQuality.COMPATIBLE, None)


def _canonical_identity(
    instrument: Instrument,
) -> tuple[str, str, str, MarketType, ContractType]:
    return (
        instrument.base,
        instrument.quote,
        instrument.settlement,
        instrument.market_type,
        instrument.contract_type,
    )


def _are_linear_perpetuals(left: Instrument, right: Instrument) -> bool:
    return (
        left.market_type is MarketType.PERPETUAL
        and right.market_type is MarketType.PERPETUAL
        and left.contract_type is ContractType.LINEAR
        and right.contract_type is ContractType.LINEAR
    )


def _incompatible(left: Instrument, right: Instrument, reason: str) -> InstrumentMatch:
    return InstrumentMatch(left, right, MatchQuality.INCOMPATIBLE, reason)
