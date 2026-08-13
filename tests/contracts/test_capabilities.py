from __future__ import annotations

import pytest

from trading_core.exchanges import Capability, apply_capability_overrides


def test_capability_overrides_enable_and_disable_with_disable_precedence() -> None:
    discovered = frozenset({Capability.TICKER_SNAPSHOT})

    effective = apply_capability_overrides(
        discovered,
        enabled=frozenset(
            {Capability.ORDER_BOOK_SNAPSHOT, Capability.FUNDING_SNAPSHOT}
        ),
        disabled=frozenset({Capability.TICKER_SNAPSHOT, Capability.FUNDING_SNAPSHOT}),
    )

    assert effective == frozenset({Capability.ORDER_BOOK_SNAPSHOT})


@pytest.mark.parametrize("capability", list(Capability))
def test_capability_override_result_is_a_frozen_capability_set(
    capability: Capability,
) -> None:
    result = apply_capability_overrides(frozenset(), enabled=frozenset({capability}))

    assert type(result) is frozenset
    assert result == frozenset({capability})
