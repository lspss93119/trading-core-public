import pytest

from trading_core.matching import (
    CompatibilityPolicy,
    MatchQuality,
    match_instruments,
)
from trading_core.models import ContractType, Instrument, MarketType


def make_instrument(
    *,
    venue: str = "binance",
    venue_symbol: str = "BTC/USDT:USDT",
    base: str = "BTC",
    quote: str = "USDT",
    settlement: str = "USDT",
    market_type: MarketType = MarketType.PERPETUAL,
    contract_type: ContractType = ContractType.LINEAR,
    is_active: bool | None = None,
) -> Instrument:
    return Instrument(
        venue=venue,
        venue_symbol=venue_symbol,
        base=base,
        quote=quote,
        settlement=settlement,
        market_type=market_type,
        contract_type=contract_type,
        is_active=is_active,
    )


STABLECOIN_PERPETUAL_POLICY = CompatibilityPolicy(
    allowed_quote_settlement_pairs=frozenset(
        {
            frozenset({("USDT", "USDT"), ("USDC", "USDC")}),
        }
    )
)


def test_match_instruments_returns_exact_for_same_canonical_linear_perpetual() -> None:
    left = make_instrument(venue="binance", venue_symbol="BTC/USDT:USDT")
    right = make_instrument(venue="hyperliquid", venue_symbol="BTC-PERP")

    match = match_instruments(left, right, policy=CompatibilityPolicy())

    assert match.quality is MatchQuality.EXACT
    assert match.reason is None
    assert match.left is left
    assert match.right is right
    assert match.left.venue == "binance"
    assert match.right.venue_symbol == "BTC-PERP"


def test_match_instruments_requires_explicit_policy_for_stablecoin_quote_conversion() -> (
    None
):
    usdt = make_instrument()
    usdc = make_instrument(
        venue="bybit",
        venue_symbol="BTCUSDC",
        quote="USDC",
        settlement="USDC",
    )

    without_policy = match_instruments(usdt, usdc, policy=CompatibilityPolicy())
    with_policy = match_instruments(usdt, usdc, policy=STABLECOIN_PERPETUAL_POLICY)

    assert without_policy.quality is MatchQuality.INCOMPATIBLE
    assert with_policy.quality is MatchQuality.COMPATIBLE
    assert with_policy.reason is None


def test_match_instruments_stablecoin_policy_is_symmetric() -> None:
    usdt = make_instrument()
    usdc = make_instrument(
        venue="bybit",
        venue_symbol="BTCUSDC",
        quote="USDC",
        settlement="USDC",
    )

    forward = match_instruments(usdt, usdc, policy=STABLECOIN_PERPETUAL_POLICY)
    reverse = match_instruments(usdc, usdt, policy=STABLECOIN_PERPETUAL_POLICY)

    assert forward.quality is MatchQuality.COMPATIBLE
    assert reverse.quality is MatchQuality.COMPATIBLE
    assert forward.left is usdt
    assert reverse.left is usdc


def test_match_instruments_ignores_active_status_for_compatibility() -> None:
    inactive = make_instrument(is_active=False)
    active = make_instrument(
        venue="hyperliquid",
        venue_symbol="BTC/USDC:USDC",
        quote="USDC",
        settlement="USDC",
        is_active=True,
    )

    match = match_instruments(
        inactive,
        active,
        policy=STABLECOIN_PERPETUAL_POLICY,
    )

    assert match.quality is MatchQuality.COMPATIBLE


@pytest.mark.parametrize(
    "right",
    [
        make_instrument(
            venue="coinbase",
            venue_symbol="BTC-USD-INVERSE",
            quote="USD",
            settlement="BTC",
            contract_type=ContractType.INVERSE,
        ),
        make_instrument(
            venue="kraken",
            venue_symbol="BTC/USD",
            quote="USD",
            settlement="USD",
            market_type=MarketType.SPOT,
            contract_type=ContractType.NONE,
        ),
        make_instrument(
            venue="okx",
            venue_symbol="BTC-USDT-SWAP",
            contract_type=ContractType.NONE,
        ),
    ],
)
def test_match_instruments_rejects_same_base_with_incompatible_or_ambiguous_contract(
    right: Instrument,
) -> None:
    match = match_instruments(
        make_instrument(), right, policy=STABLECOIN_PERPETUAL_POLICY
    )

    assert match.quality is MatchQuality.INCOMPATIBLE
    assert match.reason is not None


def test_match_instruments_rejects_unrelated_settlement_pair_even_with_policy() -> None:
    usdt = make_instrument()
    usd = make_instrument(
        venue="deribit",
        venue_symbol="BTC-USD-PERPETUAL",
        quote="USD",
        settlement="USD",
    )

    match = match_instruments(usdt, usd, policy=STABLECOIN_PERPETUAL_POLICY)

    assert match.quality is MatchQuality.INCOMPATIBLE
    assert match.reason is not None
