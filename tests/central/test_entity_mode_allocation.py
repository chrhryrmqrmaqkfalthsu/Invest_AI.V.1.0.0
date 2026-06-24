from dataclasses import dataclass

from engine.central.allocation_policy import AllocationParams, BuyCandidate, decide_buys


@dataclass
class Position:
    entity_id: str
    ticker: str
    open_shares: float = 1.0
    avg_entry_price: float = 100.0
    add_buy_count: int = 0
    position_id: str = "pos"


class Ledger:
    def __init__(self, positions=None):
        self._positions = list(positions or [])

    def open_positions(self):
        return list(self._positions)


def cand(entity_id, ticker="AAA", confidence=1.0, strength=1.0, price=100.0):
    return BuyCandidate(entity_id, ticker, confidence=confidence, strength=strength, price=price)


def test_default_mode_still_dedupes_same_ticker_candidates():
    decisions = decide_buys(
        [cand("AAA_a", confidence=3.0), cand("AAA_b", confidence=2.0), cand("BBB_a", "BBB", confidence=1.0)],
        Ledger(),
        AllocationParams(max_positions=3, total_capital=10_000, per_ticker_exposure_cap=1.0, position_sizing="equal"),
    )

    assert [d.entity_id for d in decisions] == ["AAA_a", "BBB_a"]


def test_entity_mode_allows_multiple_entities_for_same_ticker():
    decisions = decide_buys(
        [cand("AAA_a", confidence=3.0), cand("AAA_b", confidence=2.0), cand("BBB_a", "BBB", confidence=1.0)],
        Ledger(),
        AllocationParams(
            max_positions=3,
            total_capital=10_000,
            per_ticker_exposure_cap=1.0,
            position_sizing="equal",
            allow_same_ticker_entities=True,
        ),
    )

    assert [d.entity_id for d in decisions] == ["AAA_a", "AAA_b", "BBB_a"]
    assert [d.ticker for d in decisions] == ["AAA", "AAA", "BBB"]


def test_entity_mode_max_positions_counts_open_entity_positions_not_tickers():
    held = [Position("held_a", "AAA"), Position("held_b", "AAA")]
    decisions = decide_buys(
        [cand("new_c", "CCC", confidence=2.0), cand("new_d", "DDD", confidence=1.0)],
        Ledger(held),
        AllocationParams(
            max_positions=3,
            total_capital=10_000,
            per_ticker_exposure_cap=1.0,
            position_sizing="equal",
            allow_same_ticker_entities=True,
        ),
    )

    assert len(decisions) == 1
    assert decisions[0].entity_id == "new_c"


def test_entity_mode_ticker_cap_is_aggregate_across_same_ticker_entities():
    stats = {}
    decisions = decide_buys(
        [cand("AAA_a", confidence=2.0), cand("AAA_b", confidence=1.0)],
        Ledger(),
        AllocationParams(
            max_positions=2,
            total_capital=10_000,
            per_ticker_exposure_cap=0.60,
            position_sizing="equal",
            allow_same_ticker_entities=True,
            allocation_stats=stats,
        ),
    )

    assert len(decisions) == 2
    assert sum(d.notional for d in decisions) <= 6_000.0 + 1e-6
    assert decisions[0].notional == 4_900.0
    assert decisions[1].notional == 1_100.0
    assert stats["ticker_cap_hit_events"] == 1
    assert stats["ticker_cap_hit_by_ticker"] == {"AAA": 1}


def test_entity_mode_blocks_existing_ticker_when_exposure_price_unknown_only_for_that_ticker():
    held = [Position("held_a", "AAA", open_shares=10, avg_entry_price=0.0)]
    decisions = decide_buys(
        [cand("AAA_b", "AAA", confidence=2.0), cand("BBB_a", "BBB", confidence=1.0)],
        Ledger(held),
        AllocationParams(
            max_positions=3,
            total_capital=10_000,
            per_ticker_exposure_cap=1.0,
            position_sizing="equal",
            allow_same_ticker_entities=True,
        ),
    )

    assert [d.entity_id for d in decisions] == ["BBB_a"]
