import json
from types import SimpleNamespace

import pytest

from scripts.research import run_stage3_aggressive as runner


class _DummyRulebook:
    fitness = 0.0


def _context():
    return {
        "base_rulebook": _DummyRulebook(),
        "df": object(),
        "market_history_df": object(),
        "sector_name": "test-sector",
        "ticker_sentiment": {},
        "data_start": "2020-01-01",
        "data_end": "2026-01-01",
    }


def _stub_lightweight_ga(monkeypatch):
    monkeypatch.setattr(runner, "resolve_code_commit", lambda root: "test-commit")
    monkeypatch.setattr(runner, "run_ga", lambda **kwargs: SimpleNamespace(best=None, generations_run=0))
    monkeypatch.setattr(runner, "collect_top_rulebooks", lambda ga, n: [])


def _prepare_stage_dir(tmp_path, stage_name):
    out_dir = tmp_path / stage_name
    out_dir.mkdir()
    if stage_name == "entry":
        (out_dir / "qualify_result.json").write_text(json.dumps({"qualified": True}), encoding="utf-8")
    elif stage_name == "exit":
        (out_dir / "entry_rulebooks.jsonl").write_text("", encoding="utf-8")
    elif stage_name == "validate":
        (out_dir / "final_rulebooks.jsonl").write_text("", encoding="utf-8")
    return out_dir


def _call_stage(stage_name, out_dir, *, context=None):
    if stage_name == "qualify":
        return runner.run_qualify("CW", out_dir, seed_base=42, code_commit="test-commit", context=context)
    if stage_name == "entry":
        return runner.run_entry_ga("CW", out_dir, seed_base=42, code_commit="test-commit", context=context)
    if stage_name == "exit":
        return runner.run_exit_ga("CW", out_dir, seed_base=42, context=context)
    if stage_name == "validate":
        return runner.run_validate("CW", out_dir, seed_base=42, context=context)
    raise AssertionError(stage_name)


@pytest.mark.parametrize("stage_name", ["qualify", "entry", "exit", "validate"])
def test_stage_functions_use_supplied_context_without_preparing(monkeypatch, tmp_path, stage_name):
    _stub_lightweight_ga(monkeypatch)
    supplied_context = _context()
    out_dir = _prepare_stage_dir(tmp_path, stage_name)

    def fail_prepare(ticker):
        raise AssertionError("prepare_ticker_context must not be called when context is supplied")

    monkeypatch.setattr(runner, "prepare_ticker_context", fail_prepare)

    result = _call_stage(stage_name, out_dir, context=supplied_context)

    assert result["ticker"] == "CW"
    assert result["stage"] == stage_name


@pytest.mark.parametrize("stage_name", ["qualify", "entry", "exit", "validate"])
def test_stage_functions_prepare_context_once_when_context_is_none(monkeypatch, tmp_path, stage_name):
    _stub_lightweight_ga(monkeypatch)
    out_dir = _prepare_stage_dir(tmp_path, stage_name)
    calls = []

    def fake_prepare(ticker):
        calls.append(ticker)
        return _context()

    monkeypatch.setattr(runner, "prepare_ticker_context", fake_prepare)

    result = _call_stage(stage_name, out_dir, context=None)

    assert result["ticker"] == "CW"
    assert result["stage"] == stage_name
    assert calls == ["CW"]


def test_stage_all_prepares_context_once_and_passes_to_each_stage(monkeypatch, tmp_path):
    supplied_context = _context()
    prepare_calls = []
    stage_calls = []

    def fake_prepare(ticker):
        prepare_calls.append(ticker)
        return supplied_context

    def fake_qualify(ticker, out_dir, *, seed_base, use_fitness_cache=False, code_commit=None, context=None):
        stage_calls.append(("qualify", ticker, context))
        return {"ticker": ticker, "stage": "qualify", "qualified": True}

    def fake_entry(ticker, out_dir, *, seed_base, use_fitness_cache=False, code_commit=None, context=None):
        stage_calls.append(("entry", ticker, context))
        return {"ticker": ticker, "stage": "entry"}

    def fake_exit(ticker, out_dir, *, seed_base, weights=runner.DEFAULT_EXIT_FITNESS_WEIGHTS, context=None):
        stage_calls.append(("exit", ticker, context))
        return {"ticker": ticker, "stage": "exit"}

    def fake_validate(ticker, out_dir, *, seed_base, context=None):
        stage_calls.append(("validate", ticker, context))
        return {"ticker": ticker, "stage": "validate"}

    monkeypatch.setattr(runner, "prepare_ticker_context", fake_prepare)
    monkeypatch.setattr(runner, "resolve_code_commit", lambda root: "test-commit")
    monkeypatch.setattr(runner, "run_qualify", fake_qualify)
    monkeypatch.setattr(runner, "run_entry_ga", fake_entry)
    monkeypatch.setattr(runner, "run_exit_ga", fake_exit)
    monkeypatch.setattr(runner, "run_validate", fake_validate)

    rc = runner.main(["--ticker", "CW", "--stage", "all", "--seed-base", "42", "--out-dir", str(tmp_path / "all")])

    assert rc == 0
    assert prepare_calls == ["CW"]
    assert [name for name, _, _ in stage_calls] == ["qualify", "entry", "exit", "validate"]
    assert all(ticker == "CW" for _, ticker, _ in stage_calls)
    assert all(context is supplied_context for _, _, context in stage_calls)
