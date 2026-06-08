"""
유전 알고리즘 (GA v4)
- 28개 수치 파라미터 + 3개 카테고리 파라미터 학습
- 엘리트 보존 + 토너먼트 선택 + 균등 교배 + 가우시안 돌연변이
- 시드 패턴(과거 우수 룰북) 1/3 주입
"""
import copy
import random
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from engine.core.config import config
from engine.core.logger import get_logger
from engine.core.metadata import compute_rulebook_hash
from engine.strategies.rulebook import (
    CATEGORICAL_PARAMS,
    PARAM_RANGES,
    Rulebook,
)

log = get_logger("ga")


@dataclass
class GAConfig:
    population: int = 40
    generations: int = 25
    elite_ratio: float = 0.2
    mutation_rate: float = 0.15
    mutation_strength: float = 0.2     # 가우시안 표준편차 (범위 대비 비율)
    tournament_size: int = 3
    seed_pattern_ratio: float = 0.33   # 시드 룰북 비율
    early_stop_no_improve: int = 8     # N세대 개선 없으면 조기 종료
    random_seed: Optional[int] = None


@dataclass
class GAResult:
    best: Rulebook
    fitness_history: list              # [(gen, best, avg)]
    final_population: list
    generations_run: int


def collect_top_rulebooks(ga_result: GAResult, n: int) -> list[Rulebook]:
    """GA 결과에서 fitness 기준 상위 N개 비중복 룰북을 반환한다.

    ``GAResult.best``는 마지막 세대 population이 아니라 과거 세대의
    best_overall일 수 있으므로 ``final_population``과 반드시 합집합으로
    다룬다. 중복 제거는 ``compute_rulebook_hash`` 기준이다.

    Note:
        ``compute_rulebook_hash``는 mask_schema_version 1+에서 mask 조합을
        hash에 반영한다. 따라서 마스크 조합이 다른 룰북은 서로 다른
        후보로 취급되며, 이는 Top-N 후보 다양성을 위한 의도된 동작이다.
    """
    try:
        limit = int(n)
    except Exception:
        limit = 0
    if ga_result is None or limit <= 0:
        return []

    candidates: list[Rulebook] = []
    best = getattr(ga_result, "best", None)
    if best is not None:
        candidates.append(best)
    candidates.extend(list(getattr(ga_result, "final_population", []) or []))

    by_hash: dict[str, Rulebook] = {}
    for rb in candidates:
        if rb is None:
            continue
        rulebook_hash = compute_rulebook_hash(rb)
        current = by_hash.get(rulebook_hash)
        rb_fitness = getattr(rb, "fitness", None)
        current_fitness = getattr(current, "fitness", None) if current is not None else None
        rb_score = float(rb_fitness) if rb_fitness is not None else float("-inf")
        current_score = float(current_fitness) if current_fitness is not None else float("-inf")
        if current is None or rb_score > current_score:
            by_hash[rulebook_hash] = rb

    ranked = sorted(
        by_hash.items(),
        key=lambda item: (
            -(float(getattr(item[1], "fitness", None)) if getattr(item[1], "fitness", None) is not None else float("-inf")),
            item[0],
        ),
    )
    return [rb for _, rb in ranked[:limit]]


# ---------- 룰북 생성/변이 ----------
def _rand_in(low, high, integer: bool = False):
    if integer:
        return random.randint(int(low), int(high))
    return random.uniform(low, high)


_INT_PARAMS = {"max_holding_days", "add_buy_max_count"}
_MASK_CATEGORICAL_PARAMS = {
    "use_news_global",
    "use_event_block",
    "use_market_entry_adjustment",
}


def _mark_mask_schema_if_needed(rb: Rulebook) -> None:
    """GA가 mask categorical을 다루는 산출물은 schema 1로 승격한다."""
    if any(k in CATEGORICAL_PARAMS and hasattr(rb, k) for k in _MASK_CATEGORICAL_PARAMS):
        rb.mask_schema_version = max(int(getattr(rb, "mask_schema_version", 0) or 0), 1)


def _clamp_float(value: object, low: float, high: float) -> float:
    try:
        v = float(value)
    except Exception:
        v = low
    return float(max(low, min(high, v)))


def _normalize_dependent_params(rb: Rulebook) -> None:
    """Normalize dependent genes so disabled categorical paths do not create hash noise."""
    if not bool(getattr(rb, "breakeven_enabled", False)):
        rb.breakeven_trigger_profit_pct = 0.0
        rb.breakeven_floor_profit_pct = 0.0
    else:
        trig_lo, trig_hi = PARAM_RANGES["breakeven_trigger_profit_pct"]
        floor_lo, floor_hi = PARAM_RANGES["breakeven_floor_profit_pct"]
        rb.breakeven_trigger_profit_pct = _clamp_float(getattr(rb, "breakeven_trigger_profit_pct", trig_lo), trig_lo, trig_hi)
        rb.breakeven_floor_profit_pct = _clamp_float(getattr(rb, "breakeven_floor_profit_pct", floor_lo), floor_lo, floor_hi)

    if not bool(getattr(rb, "sell_omen_enabled", False)):
        rb.sell_omen_threshold = 1.0
    else:
        th_lo, th_hi = PARAM_RANGES["sell_omen_threshold"]
        rb.sell_omen_threshold = _clamp_float(getattr(rb, "sell_omen_threshold", th_hi), th_lo, th_hi)


def _finalize_rulebook_genes(rb: Rulebook) -> Rulebook:
    _mark_mask_schema_if_needed(rb)
    _normalize_dependent_params(rb)
    return rb


def random_rulebook(base: Rulebook) -> Rulebook:
    rb = copy.deepcopy(base)
    # 수치 파라미터
    for k, (lo, hi) in PARAM_RANGES.items():
        if hasattr(rb, k):
            setattr(rb, k, _rand_in(lo, hi, integer=(k in _INT_PARAMS)))
    # 카테고리
    for k, choices in CATEGORICAL_PARAMS.items():
        if hasattr(rb, k):
            setattr(rb, k, random.choice(choices))
    return _finalize_rulebook_genes(rb)


def mutate(rb: Rulebook, mutation_rate: float, strength: float) -> Rulebook:
    new_rb = copy.deepcopy(rb)
    for k, (lo, hi) in PARAM_RANGES.items():
        if random.random() < mutation_rate and hasattr(new_rb, k):
            cur = getattr(new_rb, k)
            sigma = (hi - lo) * strength
            if k in _INT_PARAMS:
                val = int(round(cur + random.gauss(0, sigma)))
                val = max(int(lo), min(int(hi), val))
            else:
                val = cur + random.gauss(0, sigma)
                val = max(lo, min(hi, val))
            setattr(new_rb, k, val)

    for k, choices in CATEGORICAL_PARAMS.items():
        if random.random() < mutation_rate / 2 and hasattr(new_rb, k):
            setattr(new_rb, k, random.choice(choices))
    return _finalize_rulebook_genes(new_rb)


def crossover(p1: Rulebook, p2: Rulebook) -> Rulebook:
    """균등 교배 — 각 파라미터를 50:50으로 부모로부터 상속"""
    child = copy.deepcopy(p1)
    for k in PARAM_RANGES.keys():
        if hasattr(child, k) and random.random() < 0.5:
            setattr(child, k, getattr(p2, k))
    for k in CATEGORICAL_PARAMS.keys():
        if hasattr(child, k) and random.random() < 0.5:
            setattr(child, k, getattr(p2, k))
    return _finalize_rulebook_genes(child)


def tournament_select(population: list, k: int) -> Rulebook:
    """k명 토너먼트에서 적합도 최고 선택"""
    contenders = random.sample(population, min(k, len(population)))
    return max(contenders, key=lambda x: x.fitness)


# ---------- 메인 GA 루프 ----------
def run_ga(
    base_rulebook: Rulebook,
    evaluate_fn: Callable[[Rulebook], float],
    ga_config: Optional[GAConfig] = None,
    seed_rulebooks: Optional[list] = None,
    on_generation: Optional[Callable[[int, Rulebook, float], None]] = None,
) -> GAResult:
    """
    Args:
        base_rulebook: 종목 메타가 채워진 초기 룰북
        evaluate_fn: rulebook → fitness 평가 함수 (백테스트 호출)
        ga_config: GA 설정
        seed_rulebooks: 시드 룰북들 (옵션)
        on_generation: 각 세대 종료 시 콜백 (gen, best, avg)
    """
    cfg = ga_config or GAConfig(
        population=config.get("learning.population", 40),
        generations=config.get("learning.generations", 25),
        elite_ratio=config.get("learning.elite_ratio", 0.2),
        mutation_rate=config.get("learning.mutation_rate", 0.15),
        seed_pattern_ratio=config.get("learning.seed_pattern_ratio", 0.33),
    )
    if cfg.random_seed is not None:
        random.seed(cfg.random_seed)
        np.random.seed(cfg.random_seed)

    # 초기 개체군 생성
    population: list = []
    seed_count = int(cfg.population * cfg.seed_pattern_ratio)
    if seed_rulebooks:
        for sr in seed_rulebooks[:seed_count]:
            rb = copy.deepcopy(sr)
            # 시드는 다른 종목 출신 — 종목 메타는 base_rulebook으로 덮어씀
            rb.ticker = base_rulebook.ticker
            rb.asset_type = base_rulebook.asset_type
            rb.direction = base_rulebook.direction
            rb.sector_name = base_rulebook.sector_name
            # 약간의 변이를 주어 다양성 확보
            rb = mutate(rb, mutation_rate=0.1, strength=0.1)
            population.append(rb)

    while len(population) < cfg.population:
        population.append(random_rulebook(base_rulebook))

    # 평가
    for rb in population:
        rb.fitness = evaluate_fn(rb)

    fitness_history: list = []
    best_overall = max(population, key=lambda x: x.fitness)
    no_improve = 0

    for gen in range(1, cfg.generations + 1):
        # 정렬
        population.sort(key=lambda x: x.fitness, reverse=True)
        best = population[0]
        avg = float(np.mean([rb.fitness for rb in population]))
        fitness_history.append((gen, best.fitness, avg))
        log.info(f"Gen {gen:2d}: best={best.fitness:.3f}, avg={avg:.3f}")
        if on_generation:
            on_generation(gen, best, avg)

        if best.fitness > best_overall.fitness:
            best_overall = copy.deepcopy(best)
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= cfg.early_stop_no_improve:
                log.info(f"early stop at gen {gen} (no improvement for {no_improve})")
                break

        # 엘리트
        elite_count = max(1, int(cfg.population * cfg.elite_ratio))
        elites = [copy.deepcopy(rb) for rb in population[:elite_count]]

        # 나머지는 교배 + 변이
        new_pop = elites
        while len(new_pop) < cfg.population:
            p1 = tournament_select(population, cfg.tournament_size)
            p2 = tournament_select(population, cfg.tournament_size)
            child = crossover(p1, p2)
            child = mutate(child, cfg.mutation_rate, cfg.mutation_strength)
            child.fitness = evaluate_fn(child)
            new_pop.append(child)

        population = new_pop

    return GAResult(
        best=best_overall,
        fitness_history=fitness_history,
        final_population=population,
        generations_run=len(fitness_history),
    )


if __name__ == "__main__":
    # 간이 테스트: 가짜 evaluate_fn 사용
    from engine.strategies.rulebook import default_rulebook

    base = default_rulebook("TEST", "korean_etf", "long")

    def fake_evaluate(rb: Rulebook) -> float:
        # signal_threshold=2.5, base_position_ratio=0.7에 가까울수록 높은 점수
        target_st = 2.5
        target_br = 0.7
        score = (
            -abs(rb.signal_threshold - target_st) * 10
            - abs(rb.base_position_ratio - target_br) * 20
            + 50
        )
        return score

    cfg = GAConfig(population=20, generations=10, random_seed=42)
    result = run_ga(base, fake_evaluate, ga_config=cfg)

    print("=" * 60)
    print(f"GA 테스트 결과 ({result.generations_run} 세대)")
    print("=" * 60)
    print(f"  최고 fitness:        {result.best.fitness:.3f}")
    print(f"  signal_threshold:    {result.best.signal_threshold:.3f} (target 2.5)")
    print(f"  base_position_ratio: {result.best.base_position_ratio:.3f} (target 0.7)")
    print(f"  exit_strategy:       {result.best.exit_strategy}")
    print(f"  세대별 추이 (gen, best, avg):")
    for gen, b, a in result.fitness_history:
        print(f"    Gen {gen:2d}: best={b:.2f}, avg={a:.2f}")
