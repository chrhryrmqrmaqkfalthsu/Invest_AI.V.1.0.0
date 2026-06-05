from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.core.metadata import compute_member_hash, compute_rulebook_hash  # noqa: E402
from engine.strategies.rulebook import default_rulebook  # noqa: E402
import scripts.pipeline.promote_candidates as promote  # noqa: E402


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def make_member(ticker: str, *, qualified: bool, member_score: float, rank: int, expectancy: float) -> dict:
    rb = default_rulebook(ticker, asset_type="us_stock", direction="long")
    rb.signal_threshold = 2.0 + rank / 100.0
    rb.expectancy_pct = expectancy
    rb.trade_count = 20
    rulebook = rb.to_dict()
    return {
        "rank": rank,
        "qualified": qualified,
        "member_score": member_score,
        "member_hash": compute_member_hash(rulebook),
        "rulebook_hash": compute_rulebook_hash(rulebook),
        "fitness": 100.0 - rank,
        "trade_count": 20,
        "win_rate": 60.0,
        "expectancy_pct": expectancy,
        "max_drawdown_pct": -10.0,
        "rulebook": rulebook,
    }


def write_source_ticker(
    rolling_root: Path,
    full_root: Path,
    ticker: str,
    *,
    stock_score: float,
    pass_count: int,
    oos_expectancy: float,
    members: list[dict],
) -> None:
    rdir = rolling_root / ticker
    fdir = full_root / ticker
    rdir.mkdir(parents=True, exist_ok=True)
    fdir.mkdir(parents=True, exist_ok=True)
    rolling = {
        "ticker": ticker,
        "data_start": "2020-01-01",
        "data_end": "2025-12-31",
        "asset_meta": {
            "ticker": ticker,
            "name": ticker,
            "asset_type": "us_stock",
            "direction": "long",
            "currency": "USD",
            "market": "NYSE/NASDAQ",
        },
        "stock_score": {
            "stock_score": stock_score,
            "raw_metrics": {
                "pass_count": pass_count,
                "avg_expectancy_pct_all": oos_expectancy,
            },
        },
    }
    (rdir / "rolling_validation.json").write_text(json.dumps(rolling), encoding="utf-8")
    (fdir / "members.jsonl").write_text(
        "".join(json.dumps(member) + "\n" for member in members),
        encoding="utf-8",
    )


def make_valid_payload(ticker: str, promotion_id: str = "p1") -> tuple[dict, dict]:
    member = make_member(ticker, qualified=True, member_score=0.9, rank=1, expectancy=5.0)
    rulebook = promote.validate_member(ticker, member)
    metrics = {
        "stock_score": 80.0,
        "pass_count": 2,
        "oos_avg_expectancy_pct": 2.0,
        "asset_meta": {
            "ticker": ticker,
            "asset_type": "us_stock",
            "direction": "long",
            "currency": "USD",
            "market": "NYSE/NASDAQ",
        },
        "data_start": "2020-01-01",
        "data_end": "2025-12-31",
    }
    payload = promote.build_parameters_payload(
        ticker=ticker,
        rulebook=rulebook,
        member=member,
        rolling_metrics=metrics,
        promotion_id=promotion_id,
        rolling_run_id="roll",
        full_training_run_id="full",
        min_pass_count=2,
        min_stock_score=60.0,
        max_overfit_gap_pp=5.0,
        created_at="2026-06-05T00:00:00Z",
    )
    return payload, member


def test_best_member_requires_qualified_and_uses_tie_breaker() -> None:
    unqualified = make_member("AAA", qualified=False, member_score=1.0, rank=1, expectancy=10.0)
    qualified_late = make_member("AAA", qualified=True, member_score=0.9, rank=5, expectancy=5.0)
    qualified_early = make_member("AAA", qualified=True, member_score=0.9, rank=2, expectancy=4.0)
    selected = promote.select_best_qualified_member([unqualified, qualified_late, qualified_early])
    assert_true(selected["qualified"] is True, "unqualified member must never be promoted")
    assert_true(selected["rank"] == 2, "lower GA rank must win member_score tie")


def test_manifest_selects_exact_criteria_and_dry_run_does_not_touch_symbols() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        runs = root / "runs"
        rolling_root = runs / "roll"
        full_root = runs / "full"
        symbols = root / "symbols"
        backups = root / "backups"
        promotions = root / "promotions"
        rolling_root.mkdir(parents=True)
        candidates = {
            "candidates": [
                {"ticker": "AAA", "stock_score": 80.0, "pass_count": 2},
                {"ticker": "BBB", "stock_score": 80.0, "pass_count": 2},
                {"ticker": "CCC", "stock_score": 80.0, "pass_count": 1},
            ]
        }
        (rolling_root / "cutoff_60_candidates.json").write_text(json.dumps(candidates), encoding="utf-8")
        write_source_ticker(
            rolling_root,
            full_root,
            "AAA",
            stock_score=80.0,
            pass_count=2,
            oos_expectancy=2.0,
            members=[
                make_member("AAA", qualified=False, member_score=1.0, rank=1, expectancy=12.0),
                make_member("AAA", qualified=True, member_score=0.9, rank=2, expectancy=5.0),
            ],
        )
        write_source_ticker(
            rolling_root,
            full_root,
            "BBB",
            stock_score=80.0,
            pass_count=2,
            oos_expectancy=1.0,
            members=[make_member("BBB", qualified=True, member_score=0.9, rank=1, expectancy=7.0)],
        )
        write_source_ticker(
            rolling_root,
            full_root,
            "CCC",
            stock_score=80.0,
            pass_count=1,
            oos_expectancy=2.0,
            members=[make_member("CCC", qualified=True, member_score=0.9, rank=1, expectancy=4.0)],
        )
        old_payload, _ = make_valid_payload("AAA", promotion_id="old")
        old_path = symbols / "AAA" / "parameters.json"
        old_path.parent.mkdir(parents=True)
        old_path.write_text(json.dumps(old_payload), encoding="utf-8")
        old_bytes = old_path.read_bytes()

        manifest, plans = promote.build_promotion_manifest(
            rolling_run_id="roll",
            full_training_run_id="full",
            promotion_id="p1",
            runs_root=runs,
            symbols_root=symbols,
            backups_root=backups,
            created_at="2026-06-05T00:00:00Z",
        )
        assert_true(manifest["counts"] == {"source_candidates": 3, "selected": 1, "create": 0, "overwrite": 1, "excluded": 2, "errors": 0}, "manifest counts must match")
        assert_true([p.ticker for p in plans] == ["AAA"], "only AAA should pass")
        assert_true(plans[0].manifest_row["member_rank"] == 2, "qualified best member must be used")
        assert_true(old_path.read_bytes() == old_bytes, "manifest build must not touch live parameters")
        path = promote.save_manifest(manifest, promotions_root=promotions)
        assert_true(path.exists(), "dry-run manifest must be saved outside symbols")
        assert_true(old_path.read_bytes() == old_bytes, "saving manifest must not touch live parameters")
        assert_true(not backups.exists(), "dry-run must not create backups")


def test_apply_requires_exact_confirmation_and_overwrite_creates_backup() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        symbols = root / "symbols"
        backups = root / "backups"
        target = symbols / "AAA" / "parameters.json"
        target.parent.mkdir(parents=True)
        old_payload, _ = make_valid_payload("AAA", promotion_id="old")
        target.write_text(json.dumps(old_payload), encoding="utf-8")
        old_bytes = target.read_bytes()
        new_payload, new_member = make_valid_payload("AAA", promotion_id="p1")
        new_payload["rulebook"]["signal_threshold"] = 3.2
        new_payload["promotion"]["member_hash"] = compute_member_hash(new_payload["rulebook"])
        new_payload["promotion"]["rulebook_hash"] = compute_rulebook_hash(new_payload["rulebook"])
        backup = backups / "promote_p1" / "AAA"
        row = {"member_hash": new_payload["promotion"]["member_hash"]}
        plan = promote.PromotionPlan("AAA", target, backup, new_payload, row)

        try:
            promote.apply_promotion_plans([plan], promotion_id="p1", confirm_promotion_id="wrong")
            raise AssertionError("confirmation mismatch must fail")
        except promote.PromotionError:
            pass
        assert_true(target.read_bytes() == old_bytes, "failed confirmation must not touch target")
        assert_true(not backup.exists(), "failed confirmation must not create backup")

        result = promote.apply_promotion_plans([plan], promotion_id="p1", confirm_promotion_id="p1")
        assert_true(result["counts"] == {"PROMOTED": 1}, "valid apply must promote")
        assert_true((backup / "parameters.json").exists(), "overwrite must back up whole symbol dir")
        written = json.loads(target.read_text())
        assert_true(written["promotion"]["member_hash"] == new_payload["promotion"]["member_hash"], "new payload must be written")
        promote.validate_parameters_payload("AAA", written)


def test_post_write_failure_rolls_back_existing_parameters() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        target = root / "symbols/AAA/parameters.json"
        backup = root / "backups/promote_p1/AAA"
        target.parent.mkdir(parents=True)
        old_payload, _ = make_valid_payload("AAA", promotion_id="old")
        target.write_text(json.dumps(old_payload), encoding="utf-8")
        old_bytes = target.read_bytes()
        new_payload, _ = make_valid_payload("AAA", promotion_id="p1")
        # Force failure only after atomic write by making manifest expected hash wrong.
        plan = promote.PromotionPlan("AAA", target, backup, new_payload, {"member_hash": "WRONG"})
        result = promote.apply_promotion_plans([plan], promotion_id="p1", confirm_promotion_id="p1")
        assert_true(result["counts"] == {"ROLLED_BACK": 1}, "post-write failure must roll back")
        assert_true(target.read_bytes() == old_bytes, "original parameters must be restored")
        assert_true((backup / "parameters.json").exists(), "backup must remain for audit")


def run_all() -> None:
    tests = [
        test_best_member_requires_qualified_and_uses_tie_breaker,
        test_manifest_selects_exact_criteria_and_dry_run_does_not_touch_symbols,
        test_apply_requires_exact_confirmation_and_overwrite_creates_backup,
        test_post_write_failure_rolls_back_existing_parameters,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL PROMOTE CANDIDATES TESTS PASSED")


if __name__ == "__main__":
    run_all()
