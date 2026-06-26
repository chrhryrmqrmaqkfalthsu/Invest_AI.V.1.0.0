from engine.central.allocation_policy import AllocationParams, BuyCandidate, decide_buys
from engine.central.entity_loader import EntityRecord
from engine.live.central_control import LiveCentralControlConfig, LiveCentralController
from engine.live.manual_buy_intent import candidate_from_decision
from engine.strategies.demo_rulebook import Signal, SignalResult


def _controller(*, stage3_mix_enabled: bool):
    ctl = LiveCentralController.__new__(LiveCentralController)
    ctl.config = LiveCentralControlConfig(
        stage3_mix_enabled=stage3_mix_enabled,
        central_strength_cap=4.0,
        central_stage3_strength_cap=3.0,
        central_stage3_min_confidence=0.0,
    )
    return ctl


def _entity(entity_id="AAA_hash", ticker="AAA", *, stage="stage2", confidence=1.0):
    tags = {} if stage == "stage2" else {"stage": stage}
    return EntityRecord(
        entity_id=entity_id,
        ticker=ticker,
        rulebook={"ticker": ticker, "direction": "long"},
        rulebook_hash=entity_id.split("_")[-1],
        validation_metrics={},
        validation_periods=[],
        tags=tags,
        confidence=confidence,
    )


def _buy_signal(score=10.0, threshold=1.0):
    return SignalResult(ticker="AAA", signal=Signal.BUY, price=100.0, score=score, threshold=threshold)


def test_strength_guard_is_inert_when_stage3_mix_off():
    ctl = _controller(stage3_mix_enabled=False)
    sig = _buy_signal(score=10.0, threshold=1.0)

    strength, orig_strength, reason = ctl._candidate_strength_for_entity(_entity(), sig, confidence=1.0)

    assert orig_strength == 10.0
    assert strength == 10.0
    assert reason == ""
    assert not hasattr(sig, "orig_strength")


def test_strength_guard_caps_stage2_only_when_stage3_mix_on():
    ctl = _controller(stage3_mix_enabled=True)
    sig = _buy_signal(score=10.0, threshold=1.0)

    strength, orig_strength, reason = ctl._candidate_strength_for_entity(_entity(), sig, confidence=1.0)

    assert orig_strength == 10.0
    assert strength == 4.0
    assert reason == ""
    assert sig.orig_strength == 10.0
    assert sig.effective_strength == 4.0
    assert sig.strength_guard == "central_strength_cap=4.0000"


def test_strength_guard_rejects_stage3_confidence_zero():
    ctl = _controller(stage3_mix_enabled=True)
    sig = _buy_signal(score=10.0, threshold=1.0)
    entity = _entity(entity_id="ADI_hash", ticker="ADI", stage="stage3_live_pool", confidence=0.0)

    strength, orig_strength, reason = ctl._candidate_strength_for_entity(entity, sig, confidence=0.0)

    assert orig_strength == 10.0
    assert strength is None
    assert "stage3 confidence" in reason
    assert sig.effective_strength == 0.0
    assert sig.strength_guard == reason


def test_strength_guard_caps_stage3_positive_confidence_to_stage3_cap():
    ctl = _controller(stage3_mix_enabled=True)
    sig = _buy_signal(score=11.3967, threshold=1.0)
    entity = _entity(entity_id="ADI_hash", ticker="ADI", stage="stage3_live_pool", confidence=1.1289)

    strength, orig_strength, reason = ctl._candidate_strength_for_entity(entity, sig, confidence=1.1289)

    assert orig_strength == 11.3967
    assert strength == 3.0
    assert reason == ""
    assert sig.orig_strength == 11.3967
    assert sig.effective_strength == 3.0
    assert sig.strength_guard == "stage3_strength_cap=3.0000"


def test_stage3_guard_makes_acls_rank_above_adi_under_additive_score():
    # ADI: confidence positive but runaway strength. After Stage3 cap=3,
    # ACLS should outrank it under the current additive score formula.
    ctl = _controller(stage3_mix_enabled=True)
    adi_sig = _buy_signal(score=11.3967, threshold=1.0)
    acls_sig = _buy_signal(score=2.9680, threshold=1.0)
    adi = _entity(entity_id="ADI_hash", ticker="ADI", stage="stage3_live_pool", confidence=1.1289)
    acls = _entity(entity_id="ACLS_hash", ticker="ACLS", stage="stage3_live_pool", confidence=2.1033)
    adi_strength, _, _ = ctl._candidate_strength_for_entity(adi, adi_sig, confidence=1.1289)
    acls_strength, _, _ = ctl._candidate_strength_for_entity(acls, acls_sig, confidence=2.1033)
    decisions = decide_buys(
        [
            BuyCandidate(entity_id=adi.entity_id, ticker=adi.ticker, confidence=adi.confidence, strength=adi_strength, price=100.0),
            BuyCandidate(entity_id=acls.entity_id, ticker=acls.ticker, confidence=acls.confidence, strength=acls_strength, price=100.0),
        ],
        current_ledger=None,
        params=AllocationParams(max_positions=8, total_capital=100000, confidence_weight=0.5, signal_strength_weight=0.5),
    )

    assert decisions[0].ticker == "ACLS"
    assert decisions[0].score > decisions[1].score


def test_candidate_state_preserves_original_and_effective_strength():
    sig = _buy_signal(score=10.0, threshold=1.0)
    sig.orig_strength = 10.0
    sig.effective_strength = 3.0
    sig.strength_guard = "stage3_strength_cap=3.0000"
    decision = type(
        "Decision",
        (),
        {
            "entity_id": "ADI_hash",
            "ticker": "ADI",
            "notional": 1000.0,
            "shares": 2.0,
            "score": 2.0,
            "confidence": 1.0,
            "strength": 3.0,
        },
    )()

    row = candidate_from_decision(decision, sig, 100.0, trade_date="2026-06-26")

    assert row["strength"] == 3.0
    assert row["orig_strength"] == 10.0
    assert row["effective_strength"] == 3.0
    assert row["strength_guard"] == "stage3_strength_cap=3.0000"
