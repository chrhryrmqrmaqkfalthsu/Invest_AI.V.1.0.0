"""
학습 오케스트레이션
- learn(ticker) 한 줄로 학습 전체 실행
- 어댑터 → 데이터 로드 → 시장 컨텍스트 → GA 실행 → 결과 반환
"""
import time
from dataclasses import dataclass
from typing import Callable, Optional

import pandas as pd

from engine.adapters.factory import get_adapter
from engine.core.config import config
from engine.core.logger import get_logger
from engine.learning.backtest import BacktestResult, run_backtest
from engine.learning.genetic import GAConfig, GAResult, run_ga
from engine.market.context import get_market_context
from engine.strategies.rulebook import Rulebook, default_rulebook

log = get_logger("learner")


@dataclass
class LearnResult:
    ticker: str
    best_rulebook: Rulebook
    backtest: BacktestResult
    ga_result: GAResult
    elapsed_sec: float
    asset_meta: dict


def learn(
    ticker: str,
    years: int = None,
    position_limit_krw: float = 120000.0,
    ga_config: Optional[GAConfig] = None,
    seed_rulebooks: Optional[list] = None,
    on_generation: Optional[Callable] = None,
) -> LearnResult:
    """
    종목 1개를 학습. 어댑터 자동 매핑.

    Args:
        ticker: 종목 코드
        years: 학습 데이터 기간 (기본 policy.yaml의 learning.data_years)
        position_limit_krw: 백테스트용 한도 금액
        ga_config: GA 설정 (None이면 policy.yaml 기본값)
        seed_rulebooks: 시드 룰북 리스트
        on_generation: 세대별 콜백 (gen, best, avg)

    Returns:
        LearnResult
    """
    t0 = time.time()
    years = years or int(config.get("learning.data_years", 5))

    # 1) 어댑터 + 메타
    adapter = get_adapter(ticker)
    meta = adapter.meta
    log.info(f"학습 시작: {ticker} ({meta.name}, {meta.direction})")

    # 2) 시세 + 지표
    df = adapter.load_history(years=years)

    # 3) 시장 컨텍스트 (캐시 사용)
    mc = get_market_context()
    # 종목 섹터 매핑 (간이): name에서 키워드로 추정
    sector_score = _detect_sector_score(meta.name, mc.sector_strength)

    log.info(
        f"시장 컨텍스트: score={mc.score:.1f} ({mc.regime}), "
        f"sector={sector_score:.1f}, vix={mc.vix_level:.1f}"
    )

    # 4) 기본 룰북
    base_rb = default_rulebook(
        ticker=meta.ticker,
        asset_type=meta.asset_type,
        direction=meta.direction,
    )

    # 5) 평가 함수
    def evaluate_fn(rb: Rulebook) -> float:
        try:
            res = run_backtest(
                rb, df,
                market_score=mc.score,
                sector_score=sector_score,
                vix_level=mc.vix_level,
                position_limit_krw=position_limit_krw,
            )
            return res.fitness
        except Exception as e:
            log.warning(f"backtest failed: {e}")
            return -100.0

    # 6) GA 실행
    cfg = ga_config or GAConfig(
        population=int(config.get("learning.population", 40)),
        generations=int(config.get("learning.generations", 25)),
        elite_ratio=float(config.get("learning.elite_ratio", 0.2)),
        mutation_rate=float(config.get("learning.mutation_rate", 0.15)),
        seed_pattern_ratio=float(config.get("learning.seed_pattern_ratio", 0.33)),
    )
    ga_result = run_ga(
        base_rulebook=base_rb,
        evaluate_fn=evaluate_fn,
        ga_config=cfg,
        seed_rulebooks=seed_rulebooks,
        on_generation=on_generation,
    )

    # 7) 최종 백테스트 (상세 결과)
    best_rb = ga_result.best
    final_bt = run_backtest(
        best_rb, df,
        market_score=mc.score,
        sector_score=sector_score,
        vix_level=mc.vix_level,
        position_limit_krw=position_limit_krw,
    )

    elapsed = time.time() - t0
    log.info(
        f"학습 완료: {ticker} fitness={final_bt.fitness:.2f}, "
        f"trades={final_bt.trade_count}, win={final_bt.win_rate:.1f}%, "
        f"expectancy={final_bt.expectancy_pct:+.3f}%, "
        f"elapsed={elapsed:.1f}s"
    )

    return LearnResult(
        ticker=ticker,
        best_rulebook=best_rb,
        backtest=final_bt,
        ga_result=ga_result,
        elapsed_sec=elapsed,
        asset_meta=meta.to_dict(),
    )


def _detect_sector_score(name: str, sector_strength: dict) -> float:
    """종목명 키워드로 섹터 매핑"""
    name_lower = name.upper() + " " + name
    mapping = [
        (["S&P500", "NASDAQ", "기술", "TECH", "반도체"], "tech"),
        (["금융", "FINANCE", "은행"], "finance"),
        (["에너지", "ENERGY", "오일"], "energy"),
        (["헬스", "HEALTH", "바이오"], "healthcare"),
        (["산업재", "INDUSTRIAL"], "industrials"),
        (["소비재", "CONSUMER"], "consumer_disc"),
    ]
    for keywords, sector in mapping:
        if any(k in name_lower for k in keywords):
            return float(sector_strength.get(sector, 50.0))
    # 기본: tech (S&P500은 사실상 기술 비중 큼)
    return float(sector_strength.get("tech", 50.0))


if __name__ == "__main__":
    print("=" * 60)
    print("learner 통합 테스트 (379800, 짧은 GA 설정)")
    print("=" * 60)

    cfg = GAConfig(
        population=15,
        generations=5,
        elite_ratio=0.2,
        mutation_rate=0.2,
        random_seed=42,
    )
    result = learn(
        "379800",
        years=5,
        position_limit_krw=120000,
        ga_config=cfg,
    )

    rb = result.best_rulebook
    bt = result.backtest

    print()
    print("=" * 60)
    print(f"학습 결과 — {result.ticker} ({result.asset_meta['name']})")
    print("=" * 60)
    print(f"  소요 시간:       {result.elapsed_sec:.1f}초")
    print(f"  거래 수:         {bt.trade_count}")
    print(f"  승률:            {bt.win_rate:.1f}%")
    print(f"  기대값:          {bt.expectancy_pct:+.3f}%")
    print(f"  MDD:             {bt.max_drawdown_pct:.2f}%")
    print(f"  Profit Factor:   {bt.profit_factor:.2f}")
    print(f"  Fitness:         {bt.fitness:.2f}")
    print()
    print(f"  최적 룰북 주요 파라미터:")
    print(f"    signal_threshold:       {rb.signal_threshold:.2f}")
    print(f"    exit_strategy:          {rb.exit_strategy}")
    print(f"    stop_loss_atr:          {rb.stop_loss_atr:.2f}")
    print(f"    take_profit_atr:        {rb.take_profit_atr:.2f}")
    print(f"    trailing_atr:           {rb.trailing_atr:.2f}")
    print(f"    max_holding_days:       {rb.max_holding_days}")
    print(f"    position_sizing:        {rb.position_sizing_strategy}")
    print(f"    base_position_ratio:    {rb.base_position_ratio:.2f}")
    print(f"    add_buy_enabled:        {rb.add_buy_enabled}")
    if rb.add_buy_enabled:
        print(f"    add_buy_trigger_pct:    {rb.add_buy_trigger_profit_pct:.2f}%")
        print(f"    add_buy_max_count:      {rb.add_buy_max_count}")
        print(f"    add_buy_size_ratio:     {rb.add_buy_size_ratio:.2f}")
    print(f"    market_score_weight:    {rb.market_score_weight:+.2f}")
    print(f"    sector_strength_weight: {rb.sector_strength_weight:+.2f}")
    print(f"    vix_sensitivity:        {rb.vix_sensitivity:+.2f}")
