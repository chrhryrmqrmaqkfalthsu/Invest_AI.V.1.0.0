from engine.central.allocation_policy import AllocationParams, BuyCandidate, decide_buys
from engine.central.backtester import _cash_capped_shares


class EmptyLedger:
    def open_positions(self):
        return []


def test_allocation_policy_filters_ledger_epsilon_dust_share_decisions():
    candidates = [BuyCandidate("dust", "AAA", confidence=1.0, strength=1.0, price=5_000_000_000.0)]
    params = AllocationParams(
        max_positions=1,
        total_capital=10_000.0,
        per_ticker_exposure_cap=0.25,
        position_sizing="equal",
        min_notional=0.0,
    )
    assert decide_buys(candidates, EmptyLedger(), params) == []


def test_allocation_policy_allows_above_ledger_epsilon_share_decisions():
    candidates = [BuyCandidate("valid", "AAA", confidence=1.0, strength=1.0, price=100_000_000.0)]
    params = AllocationParams(
        max_positions=1,
        total_capital=10_000.0,
        per_ticker_exposure_cap=0.25,
        position_sizing="equal",
        min_notional=0.0,
    )
    decisions = decide_buys(candidates, EmptyLedger(), params)
    assert len(decisions) == 1
    assert decisions[0].shares > 1e-6


def test_cash_capped_shares_filters_ledger_epsilon_dust_after_cash_cap():
    assert _cash_capped_shares(cash=1.0, requested_shares=1.0, d_close_price=1_000_000.0, cash_buffer_ratio=0.98) == 0.0


def test_cash_capped_shares_allows_above_ledger_epsilon_after_cash_cap():
    assert _cash_capped_shares(cash=10.0, requested_shares=1.0, d_close_price=1_000_000.0, cash_buffer_ratio=0.98) > 1e-6
