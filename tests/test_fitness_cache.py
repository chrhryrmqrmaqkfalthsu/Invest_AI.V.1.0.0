from engine.learning.fitness_cache import (
    FitnessCache,
    aggregate_fitness_cache_summaries,
    build_cache_key,
    make_cache_key_context,
    make_cached_evaluate_fn,
)


def _ctx(**overrides):
    base = make_cache_key_context(
        ticker="CW",
        period_label="train_3",
        start_date="2024-07-01",
        end_date="2025-06-30",
        entry_execution_mode="t_plus_1_open",
        exit_execution_mode="conservative_core",
        fold_exit_policy="fold_end_mark_to_market",
        fitness_mode="swing",
        code_commit="test-commit",
        add_buy_runtime_enabled=False,
    )
    base.update(overrides)
    return base


def test_cached_evaluate_fn_calls_raw_once_for_same_rulebook():
    calls = []
    rulebook = {"ticker": "CW", "signal_threshold": 2.5, "fitness": 999.0}
    cache = FitnessCache()

    def raw_evaluate_fn(rb):
        calls.append(rb)
        return 12.345

    cached = make_cached_evaluate_fn(raw_evaluate_fn, cache=cache, key_ctx=_ctx())

    assert cached(rulebook) == 12.345
    assert cached(dict(rulebook)) == 12.345
    assert len(calls) == 1
    assert cache.hits == 1
    assert cache.misses == 1
    assert cache.unique_keys == 1
    assert cache.hit_rate == 0.5


def test_cache_key_context_difference_causes_miss():
    calls = []
    rulebook = {"ticker": "CW", "signal_threshold": 2.5}
    cache = FitnessCache()

    def raw_evaluate_fn(rb):
        calls.append(rb)
        return float(len(calls))

    cached_train_3 = make_cached_evaluate_fn(raw_evaluate_fn, cache=cache, key_ctx=_ctx(period_label="train_3"))
    cached_train_2 = make_cached_evaluate_fn(raw_evaluate_fn, cache=cache, key_ctx=_ctx(period_label="train_2"))

    assert cached_train_3(rulebook) == 1.0
    assert cached_train_3(rulebook) == 1.0
    assert cached_train_2(rulebook) == 2.0
    assert len(calls) == 2
    assert cache.hits == 1
    assert cache.misses == 2
    assert cache.unique_keys == 2


def test_build_cache_key_includes_all_isolation_fields():
    rulebook = {"ticker": "CW", "signal_threshold": 2.5}
    key = dict(build_cache_key(rulebook, _ctx()))

    assert key["rulebook_hash"]
    assert key["ticker"] == "CW"
    assert key["period_label"] == "train_3"
    assert key["start_date"] == "2024-07-01"
    assert key["end_date"] == "2025-06-30"
    assert key["entry_execution_mode"] == "t_plus_1_open"
    assert key["exit_execution_mode"] == "conservative_core"
    assert key["fold_exit_policy"] == "fold_end_mark_to_market"
    assert key["fitness_mode"] == "swing"
    assert key["code_commit"] == "test-commit"
    assert key["add_buy_runtime_enabled"] is False
    assert key["cache_schema_version"] == 1


def test_aggregate_fitness_cache_summaries_counts_hit_rate():
    summary = aggregate_fitness_cache_summaries(
        [
            {"enabled": True, "hits": 2, "misses": 3, "unique_keys": 3},
            {"enabled": True, "hits": 1, "misses": 4, "unique_keys": 4},
        ]
    )

    assert summary["enabled"] is True
    assert summary["hits"] == 3
    assert summary["misses"] == 7
    assert summary["unique_keys"] == 7
    assert summary["hit_rate"] == 0.3
