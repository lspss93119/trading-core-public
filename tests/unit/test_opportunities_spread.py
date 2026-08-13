from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_core.models import (
    ContractType,
    FeeSource,
    Instrument,
    MarketType,
    MatchQuality,
    SpreadOpportunity,
    Ticker,
    TradingFee,
)
from trading_core.opportunities import CrossExchangeSpreadFinder
from trading_core.policies import FreshnessPolicy


AS_OF = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
FRESHNESS = FreshnessPolicy(max_age=timedelta(minutes=5))


def make_instrument(
    venue: str,
    *,
    quote: str = "USDT",
    settlement: str = "USDT",
) -> Instrument:
    return Instrument(
        venue=venue,
        venue_symbol=f"BTC/{quote}:{settlement}",
        base="BTC",
        quote=quote,
        settlement=settlement,
        market_type=MarketType.PERPETUAL,
        contract_type=ContractType.LINEAR,
    )


def make_ticker(
    instrument: Instrument,
    *,
    bid: str,
    ask: str,
    received_at: datetime = AS_OF - timedelta(seconds=1),
) -> Ticker:
    return Ticker(
        instrument=instrument,
        bid=Decimal(bid),
        ask=Decimal(ask),
        last=None,
        mark=None,
        index=None,
        exchange_timestamp=received_at,
        received_at=received_at,
    )


def make_fee(instrument: Instrument, taker: str | None) -> TradingFee:
    return TradingFee(
        venue=instrument.venue,
        maker_fee=None,
        taker_fee=None if taker is None else Decimal(taker),
        source=FeeSource.UNKNOWN if taker is None else FeeSource.CONFIG,
        instrument=instrument,
    )


def find(
    tickers: tuple[Ticker, ...],
    *,
    fees: dict[Instrument, TradingFee] | None = None,
) -> tuple[SpreadOpportunity, ...]:
    return CrossExchangeSpreadFinder().find(
        tickers,
        fees={} if fees is None else fees,
        as_of=AS_OF,
        freshness_policy=FRESHNESS,
    )


def test_spread_finder_buys_lower_ask_and_sells_higher_bid() -> None:
    buy = make_instrument("binance")
    sell = make_instrument("hyperliquid")
    buy_ticker = make_ticker(buy, bid="99", ask="100")
    sell_ticker = make_ticker(sell, bid="101", ask="102")
    fees = {
        buy: make_fee(buy, "0.001"),
        sell: make_fee(sell, "0.001"),
    }

    [opportunity] = find((sell_ticker, buy_ticker), fees=fees)

    assert opportunity.buy_instrument is buy
    assert opportunity.sell_instrument is sell
    assert opportunity.buy_ask == Decimal("100")
    assert opportunity.sell_bid == Decimal("101")
    assert opportunity.gross_spread == Decimal("0.01")
    assert opportunity.estimated_net_spread == Decimal("0.008")
    assert opportunity.buy_fee.source is FeeSource.CONFIG
    assert opportunity.sell_fee.source is FeeSource.CONFIG
    assert opportunity.match_quality is MatchQuality.EXACT
    assert opportunity.buy_received_at == buy_ticker.received_at
    assert opportunity.sell_received_at == sell_ticker.received_at


def test_spread_finder_excludes_zero_negative_stale_same_venue_and_quote_mismatch() -> (
    None
):
    first = make_instrument("binance")
    second = make_instrument("hyperliquid")
    assert (
        find(
            (
                make_ticker(first, bid="100", ask="100"),
                make_ticker(second, bid="100", ask="100"),
            )
        )
        == ()
    )
    assert (
        find(
            (
                make_ticker(first, bid="99", ask="102"),
                make_ticker(second, bid="98", ask="100"),
            )
        )
        == ()
    )
    assert (
        find(
            (
                make_ticker(
                    first, bid="99", ask="100", received_at=AS_OF - timedelta(minutes=6)
                ),
                make_ticker(second, bid="101", ask="102"),
            )
        )
        == ()
    )

    same_venue_other_quote = make_instrument("binance", quote="USDC", settlement="USDC")
    assert (
        find(
            (
                make_ticker(first, bid="99", ask="100"),
                make_ticker(same_venue_other_quote, bid="101", ask="102"),
            )
        )
        == ()
    )

    different_quote = make_instrument("okx", quote="USDC", settlement="USDC")
    assert (
        find(
            (
                make_ticker(first, bid="99", ask="100"),
                make_ticker(different_quote, bid="101", ask="102"),
            )
        )
        == ()
    )


def test_unknown_fee_keeps_gross_spread_but_withholds_estimated_net() -> None:
    buy = make_instrument("binance")
    sell = make_instrument("hyperliquid")

    [opportunity] = find(
        (
            make_ticker(buy, bid="99", ask="100"),
            make_ticker(sell, bid="101", ask="102"),
        ),
        fees={buy: make_fee(buy, None)},
    )

    assert opportunity.gross_spread == Decimal("0.01")
    assert opportunity.estimated_net_spread is None
    assert opportunity.buy_fee.source is FeeSource.UNKNOWN
    assert opportunity.sell_fee.source is FeeSource.UNKNOWN


def test_spread_finder_order_is_deterministic_and_uses_buy_ask_denominator() -> None:
    first = make_instrument("a-venue")
    second = make_instrument("b-venue")
    third = make_instrument("c-venue")
    tickers = (
        make_ticker(first, bid="99", ask="100"),
        make_ticker(second, bid="102", ask="103"),
        make_ticker(third, bid="104", ask="105"),
    )

    forward = find(tickers)
    reverse = find(tuple(reversed(tickers)))

    assert forward == reverse
    assert [
        (item.buy_instrument.venue, item.sell_instrument.venue) for item in forward
    ] == [
        ("a-venue", "c-venue"),
        ("a-venue", "b-venue"),
        ("b-venue", "c-venue"),
    ]
    assert forward[0].gross_spread == Decimal("0.04")
    assert forward[1].gross_spread == Decimal("0.02")
    assert forward[2].gross_spread == (Decimal("104") - Decimal("103")) / Decimal("103")


def test_spread_finder_is_pure_and_does_not_expose_provider_operations() -> None:
    buy = make_instrument("binance")
    sell = make_instrument("hyperliquid")
    tickers = (
        make_ticker(buy, bid="99", ask="100"),
        make_ticker(sell, bid="101", ask="102"),
    )

    first_result = find(tickers)
    second_result = find(tickers)

    assert first_result == second_result
    assert "ccxt" not in CrossExchangeSpreadFinder.__module__
    assert not hasattr(CrossExchangeSpreadFinder, "fetch_ticker")
    assert not hasattr(CrossExchangeSpreadFinder, "fetch_order_book")
