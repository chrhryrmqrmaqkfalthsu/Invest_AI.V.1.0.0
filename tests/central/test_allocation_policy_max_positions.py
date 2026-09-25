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


def params(max_positions, *, total_capital=10_000.0):
    return AllocationParams(
        max_positions=max_positions,
        total_capital=total_capital,
        per_ticker_exposure_cap=10.0,
        position_sizing="equal",
        min_notional=0.0,
        min_confidence=0.0,
    )


def candidate(i, ticker, *, confidence=None, strength=1.0):
    score = float(confidence if confidence is not None else 100 - i)
    return BuyCandidate(
        entity_id=f"entity_{ticker}_{i}",
        ticker=ticker,
        confidence=score,
        strength=strength,
        price=100.0,
    )


def tickers(decisions):
    return [d.ticker for d in decisions]


def test_max_positions_with_no_holdings_selects_up_to_distinct_ticker_limit():
    candidates = [candidate(i, f"T{i}") for i in range(10)]

    decisions = decide_buys(candidates, Ledger(), params(5))

    assert len(decisions) == 5
    assert len(set(tickers(decisions))) == 5


def test_max_positions_blocks_new_tickers_when_already_at_ticker_limit():
    held = [Position(entity_id=f"held_{i}", ticker=f"H{i}") for i in range(5)]
    candidates = [candidate(i, f"N{i}") for i in range(10)]

    decisions = decide_buys(candidates, Ledger(held), params(5))

    assert decisions == []


def test_max_positions_allows_only_remaining_distinct_ticker_slots():
    held = [Position(entity_id=f"held_{i}", ticker=f"H{i}") for i in range(3)]
    candidates = [candidate(i, f"N{i}") for i in range(4)]

    decisions = decide_buys(candidates, Ledger(held), params(5))

    assert len(decisions) == 2
    assert len(set(tickers(decisions))) == 2


def test_max_positions_counts_duplicate_candidate_tickers_as_one_slot():
    candidates = [
        candidate(0, "AAA", confidence=100),
        candidate(1, "AAA", confidence=99),
        candidate(2, "AAA", confidence=98),
        candidate(3, "BBB", confidence=50),
        candidate(4, "CCC", confidence=40),
    ]

    decisions = decide_buys(candidates, Ledger(), params(3))

    assert tickers(decisions) == ["AAA", "BBB", "CCC"]


def test_max_positions_skips_candidate_for_already_held_ticker():
    held = [Position(entity_id="held_AAA", ticker="AAA")]
    candidates = [
        BuyCandidate(entity_id="other_AAA_rulebook", ticker="AAA", confidence=100, strength=1.0, price=100.0),
        BuyCandidate(entity_id="new_BBB", ticker="BBB", confidence=90, strength=1.0, price=100.0),
    ]

    decisions = decide_buys(candidates, Ledger(held), params(2))

    assert tickers(decisions) == ["BBB"]


def test_max_positions_with_no_candidates_returns_no_buys():
    assert decide_buys([], Ledger(), params(2)) == []
