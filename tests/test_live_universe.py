from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.live.market_clock import select_market_clock  # noqa: E402
from engine.live.runner import Runner  # noqa: E402
from engine.live.universe import (  # noqa: E402
    DEFAULT_LIVE_PROMOTION_ID,
    LEGACY_STAGE1_PROMOTION_ID,
    LiveUniverseConfig,
    LiveUniverseError,
    load_live_universe,
)
from engine.strategies.rulebook import default_rulebook  # noqa: E402


LEGACY_STAGE1_TICKERS = (
    "CAKE",
    "CRWD",
    "CW",
    "EME",
    "ETR",
    "HSBC",
    "ITT",
    "KT",
    "LASR",
    "MPC",
    "MPLX",
    "MTB",
    "NBIX",
    "WAB",
    "WELL",
    "WPM",
)

CURRENT_STAGE2_SAMPLE_TICKERS = ("AAPL", "GOOGL", "FIX", "ANET")


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def write_parameters(
    root: Path,
    ticker: str,
    *,
    market: str,
    currency: str,
    asset_type: str,
    promotion_id: str | None,
    rulebook_ticker: str | None = None,
    direction: str = "long",
) -> None:
    directory = root / ticker
    directory.mkdir(parents=True, exist_ok=True)
    rb = default_rulebook(rulebook_ticker or ticker, asset_type=asset_type, direction=direction)
    payload = {
        "saved_at": "2026-06-05T00:00:00Z",
        "asset_meta": {
            "ticker": ticker,
            "market": market,
            "currency": currency,
            "asset_type": asset_type,
            "direction": direction,
        },
        "rulebook": rb.to_dict(),
    }
    if promotion_id is not None:
        payload["promotion"] = {"promotion_id": promotion_id}
    (directory / "parameters.json").write_text(json.dumps(payload), encoding="utf-8")


def test_repository_policy_counts() -> None:
    symbols_dir = ROOT / "data/symbols"
    promoted = load_live_universe(
        LiveUniverseConfig(
            market="US",
            universe_mode="promoted",
            promotion_id=DEFAULT_LIVE_PROMOTION_ID,
            symbols_dir=symbols_dir,
        )
    )
    legacy_stage1 = load_live_universe(
        LiveUniverseConfig(
            market="US",
            universe_mode="promoted",
            promotion_id=LEGACY_STAGE1_PROMOTION_ID,
            symbols_dir=symbols_dir,
        )
    )
    us_parameters = load_live_universe(
        LiveUniverseConfig(market="US", universe_mode="parameters", symbols_dir=symbols_dir)
    )
    krx_parameters = load_live_universe(
        LiveUniverseConfig(market="KRX", universe_mode="parameters", symbols_dir=symbols_dir)
    )
    assert_true(DEFAULT_LIVE_PROMOTION_ID == "stage123_stage2_live_20260622", "default live promotion must be dated Stage2 live universe")
    assert_true(LEGACY_STAGE1_PROMOTION_ID == "lr8d_stage1_20260609", "legacy stage1 id must remain available as a quarantine fence")
    assert_true(len(promoted.symbols) == 158, "current Stage2 live promotion must select 158 US tickers")
    assert_true(len(legacy_stage1.symbols) == 16, "legacy LR8D stage1 promotion id must select only the original 16 US tickers")
    assert_true(len(us_parameters.symbols) == 235, "US parameters mode must select 235 tickers")
    assert_true(len(krx_parameters.symbols) == 4, "KRX parameters mode must select 4 tickers")
    assert_true("143850" not in krx_parameters.symbols, "parameters-missing 143850 must be excluded")
    for ticker in LEGACY_STAGE1_TICKERS:
        assert_true(ticker in legacy_stage1.symbols, f"stage1 ticker {ticker} must remain quarantined in legacy promotion")
    for ticker in CURRENT_STAGE2_SAMPLE_TICKERS:
        assert_true(ticker in promoted.symbols, f"Stage2 materialized ticker {ticker} must be in current live promotion")
        assert_true(ticker not in legacy_stage1.symbols, f"non-stage1 {ticker} must stay excluded from legacy promotion")
        assert_true(ticker in us_parameters.symbols, f"parameters mode must include {ticker}")


def test_market_metadata_mismatch_fails_fast() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_parameters(
            root,
            "AAPL",
            market="KRX",
            currency="KRW",
            asset_type="korean_stock",
            promotion_id="p1",
        )
        try:
            load_live_universe(
                LiveUniverseConfig(market="US", universe_mode="promoted", promotion_id="p1", symbols_dir=root)
            )
            raise AssertionError("market mismatch must fail fast")
        except LiveUniverseError as exc:
            assert_true("market metadata mismatch" in str(exc), "mismatch error must be explicit")


def test_rulebook_ticker_and_long_only_fail_fast() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_parameters(
            root,
            "AAPL",
            market="NYSE/NASDAQ",
            currency="USD",
            asset_type="us_stock",
            promotion_id="p1",
            rulebook_ticker="MSFT",
        )
        try:
            load_live_universe(
                LiveUniverseConfig(market="US", universe_mode="promoted", promotion_id="p1", symbols_dir=root)
            )
            raise AssertionError("rulebook ticker mismatch must fail fast")
        except LiveUniverseError as exc:
            assert_true("rulebook ticker mismatch" in str(exc), "ticker mismatch error must be explicit")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_parameters(
            root,
            "AAPL",
            market="NYSE/NASDAQ",
            currency="USD",
            asset_type="us_stock",
            promotion_id="p1",
            direction="short",
        )
        try:
            load_live_universe(
                LiveUniverseConfig(market="US", universe_mode="promoted", promotion_id="p1", symbols_dir=root)
            )
            raise AssertionError("short rulebook must fail fast")
        except LiveUniverseError as exc:
            assert_true("long-only" in str(exc), "long-only error must be explicit")


def test_promotion_mismatch_and_missing_parameters_are_excluded() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_parameters(
            root,
            "AAPL",
            market="NYSE/NASDAQ",
            currency="USD",
            asset_type="us_stock",
            promotion_id="old",
        )
        (root / "AMD").mkdir()
        result = load_live_universe(
            LiveUniverseConfig(market="US", universe_mode="promoted", promotion_id="p1", symbols_dir=root)
        )
        assert_true(result.symbols == (), "wrong promotion and missing parameters must be excluded")
        assert_true(
            result.excluded_reason_counts == {"PARAMETERS_MISSING": 1, "PROMOTION_ID_MISMATCH": 1},
            "exclusion reasons must be audited",
        )


def test_filtered_universes_keep_clock_fail_fast_as_final_guard() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_parameters(root, "AAPL", market="NYSE/NASDAQ", currency="USD", asset_type="us_stock", promotion_id="p1")
        write_parameters(root, "005930", market="KRX", currency="KRW", asset_type="korean_stock", promotion_id="p1")
        us = load_live_universe(
            LiveUniverseConfig(market="US", universe_mode="promoted", promotion_id="p1", symbols_dir=root)
        )
        krx = load_live_universe(
            LiveUniverseConfig(market="KRX", universe_mode="promoted", promotion_id="p1", symbols_dir=root)
        )
        assert_true(select_market_clock(us.symbols).name == "US", "US filtered universe must select US clock")
        assert_true(select_market_clock(krx.symbols).name == "KRX", "KRX filtered universe must select KRX clock")
        try:
            select_market_clock(list(us.symbols) + list(krx.symbols))
            raise AssertionError("mixed final universe must still fail fast")
        except ValueError as exc:
            assert_true("mixed-market" in str(exc), "mixed final guard error must remain explicit")


class DummyRulebook:
    def __init__(self) -> None:
        self._rulebook_cache = {"AAA": object(), "BBB": object(), "005930": object()}

    def name(self) -> str:
        return "dummy"


class DummyClock:
    name = "US"


class DummyBroker:
    mode = "paper"


class DummySafety:
    pass


class DummyNotifier:
    pass


def test_runner_reload_uses_same_policy_and_blocks_cross_market_or_unpromoted() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_parameters(root, "AAA", market="NYSE/NASDAQ", currency="USD", asset_type="us_stock", promotion_id="p1")
        write_parameters(root, "BBB", market="NYSE/NASDAQ", currency="USD", asset_type="us_stock", promotion_id="old")
        write_parameters(root, "005930", market="KRX", currency="KRW", asset_type="korean_stock", promotion_id="p1")

        runner = Runner.__new__(Runner)
        runner.symbols = ["AAA"]
        runner.universe_config = LiveUniverseConfig(
            market="US", universe_mode="promoted", promotion_id="p1", symbols_dir=root
        )
        runner.clock = DummyClock()
        runner.rulebook = DummyRulebook()

        first = runner.reload_symbols()
        assert_true(first["added"] == [], "reload must not add unpromoted or KRX symbols")
        assert_true(runner.symbols == ["AAA"], "universe must remain unchanged")
        assert_true("BBB" in runner.rulebook._rulebook_cache, "blocked symbol cache must remain untouched")

        write_parameters(root, "CCC", market="NYSE/NASDAQ", currency="USD", asset_type="us_stock", promotion_id="p1")
        second = runner.reload_symbols()
        assert_true(second["added"] == ["CCC"], "eligible same-policy symbol must be added")
        assert_true(runner.symbols == ["AAA", "CCC"], "reload must only append eligible symbol")
        assert_true("BBB" in runner.rulebook._rulebook_cache, "unpromoted cache must not be invalidated")
        assert_true("005930" in runner.rulebook._rulebook_cache, "cross-market cache must not be invalidated")


def run_all() -> None:
    tests = [
        test_repository_policy_counts,
        test_market_metadata_mismatch_fails_fast,
        test_rulebook_ticker_and_long_only_fail_fast,
        test_promotion_mismatch_and_missing_parameters_are_excluded,
        test_filtered_universes_keep_clock_fail_fast_as_final_guard,
        test_runner_reload_uses_same_policy_and_blocks_cross_market_or_unpromoted,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL LIVE UNIVERSE TESTS PASSED")


if __name__ == "__main__":
    run_all()
