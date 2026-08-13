from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading_core.models import (
    ContractType,
    FundingRate,
    Instrument,
    MarketType,
    OrderBook,
    OrderBookLevel,
    Ticker,
)
from trading_core.policies import FreshnessPolicy


AS_OF = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
RECEIVED_AT = AS_OF - timedelta(seconds=30)


def make_instrument() -> Instrument:
    return Instrument(
        venue="binance",
        venue_symbol="BTC/USDT:USDT",
        base="BTC",
        quote="USDT",
        settlement="USDT",
        market_type=MarketType.PERPETUAL,
        contract_type=ContractType.LINEAR,
    )


def test_freshness_policy_accepts_data_within_max_age() -> None:
    policy = FreshnessPolicy(max_age=timedelta(minutes=1))

    assert policy.age(received_at=RECEIVED_AT, as_of=AS_OF) == timedelta(seconds=30)
    assert policy.is_fresh(received_at=RECEIVED_AT, as_of=AS_OF)


def test_freshness_policy_treats_exact_max_age_as_fresh() -> None:
    policy = FreshnessPolicy(max_age=timedelta(seconds=30))

    assert policy.is_fresh(received_at=RECEIVED_AT, as_of=AS_OF)


def test_freshness_policy_rejects_data_older_than_max_age() -> None:
    policy = FreshnessPolicy(max_age=timedelta(seconds=29))

    assert not policy.is_fresh(received_at=RECEIVED_AT, as_of=AS_OF)


@pytest.mark.parametrize("max_age", [timedelta(seconds=-1), timedelta(days=-1)])
def test_freshness_policy_rejects_negative_max_age(max_age: timedelta) -> None:
    with pytest.raises(ValueError, match="max_age"):
        FreshnessPolicy(max_age=max_age)


def test_freshness_policy_rejects_non_timedelta_max_age() -> None:
    with pytest.raises(TypeError, match="max_age"):
        FreshnessPolicy(max_age=30)  # type: ignore[arg-type]


@pytest.mark.parametrize("method", ["age", "is_fresh"])
def test_freshness_policy_rejects_naive_datetimes(method: str) -> None:
    policy = FreshnessPolicy(max_age=timedelta(minutes=1))

    with pytest.raises(ValueError, match="timezone-aware"):
        getattr(policy, method)(
            received_at=RECEIVED_AT.replace(tzinfo=None), as_of=AS_OF
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        getattr(policy, method)(
            received_at=RECEIVED_AT, as_of=AS_OF.replace(tzinfo=None)
        )


@pytest.mark.parametrize("method", ["age", "is_fresh"])
def test_freshness_policy_rejects_received_at_in_the_future(method: str) -> None:
    policy = FreshnessPolicy(max_age=timedelta(minutes=1))

    with pytest.raises(ValueError, match="future"):
        getattr(policy, method)(
            received_at=AS_OF + timedelta(microseconds=1), as_of=AS_OF
        )


def test_freshness_policy_is_deterministic_and_does_not_read_wall_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = FreshnessPolicy(max_age=timedelta(minutes=1))
    monkeypatch.setattr("time.time", lambda: (_ for _ in ()).throw(AssertionError))

    first = policy.age(received_at=RECEIVED_AT, as_of=AS_OF)
    second = policy.age(received_at=RECEIVED_AT, as_of=AS_OF)

    assert first == second == timedelta(seconds=30)


@pytest.mark.parametrize(
    "model",
    [
        Ticker(
            instrument=make_instrument(),
            bid=Decimal("100"),
            ask=Decimal("101"),
            last=None,
            mark=None,
            index=None,
            exchange_timestamp=None,
            received_at=RECEIVED_AT,
        ),
        OrderBook(
            instrument=make_instrument(),
            bids=(OrderBookLevel(price=Decimal("100"), amount=Decimal("1")),),
            asks=(OrderBookLevel(price=Decimal("101"), amount=Decimal("1")),),
            exchange_timestamp=None,
            received_at=RECEIVED_AT,
        ),
        FundingRate(
            instrument=make_instrument(),
            rate=Decimal("0.0001"),
            interval=timedelta(hours=8),
            next_funding_at=None,
            exchange_timestamp=None,
            received_at=RECEIVED_AT,
        ),
    ],
)
def test_freshness_policy_evaluates_normalized_model_received_at(model: object) -> None:
    policy = FreshnessPolicy(max_age=timedelta(minutes=1))

    assert policy.is_fresh(received_at=model.received_at, as_of=AS_OF)  # type: ignore[attr-defined]
