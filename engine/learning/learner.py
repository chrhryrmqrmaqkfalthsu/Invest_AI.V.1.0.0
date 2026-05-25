"""
학습 오케스트레이션 모듈
- 종목 1개에 대해 GA v4 학습 전체 흐름 관리
- Adapter → 데이터 → 시장 시계열 → GA → 최종 백테스트 → 결과 반환
"""
import time
from dataclasses import dataclass
from typing import Optional, Callable

from engine.core.logger import get_logger
from engine.adapters.factory import get_adapter
from engine.market.context import get_market_context, get_market_history
from engine.strategies.rulebook import Rulebook, default_rulebook
from engine.learning.backtest import run_backtest, BacktestResult
from engine.learning.genetic import run_ga, GAConfig, GAResult

log = get_logger("learner")


@dataclass
class LearnResult:
    ticker: str
    best_rulebook: Rulebook
    backtest: BacktestResult
    ga_result: Optional[GAResult]
    elapsed_sec: float
    asset_meta: dict


def _detect_sector_name(meta_name: str) -> str:
    """종목명에서 섹터명 추정 (market_history의 sector_* 컬럼 매칭용)"""
    name = (meta_name or "").lower()
    if any(k in name for k in ["반도체", "tech", "qqq", "kodex", "tiger", "s&p", "나스닥", "semi", "it"]):
        return "tech"
    if any(k in name for k in ["에너지", "energy", "oil", "원유"]):
        return "energy"
    if any(k in name for k in ["금융", "finance", "bank", "은행", "보험"]):
        return "finance"
    if any(k in name for k in ["헬스", "health", "bio", "제약"]):
        return "healthcare"
    if any(k in name for k in ["소비", "consumer", "리테일"]):
        return "consumer"
    if any(k in name for k in ["산업", "industrial"]):
        return "industrials"
    return "tech"


def learn(
    ticker: str,
    years: int = 5,
    position_limit_krw: float = 120000.0,
    ga_config: Optional[GAConfig] = None,
    seed_rulebooks: Optional[list] = None,
    on_generation: Optional[Callable] = None,
) -> LearnResult:
    t0 = time.time()

    # 1) 어댑터 + 메타
    adapter = get_adapter(ticker)
    meta = adapter.meta
    log.info(f"학습 시작: {ticker} ({meta.name}, {meta.direction})")

    # 2) 데이터 로드 + 지표 계산
    df = adapter.load_history(years=years)

    # 3) 시장 시계열 (한 번 로드, GA 전체에서 재사용)
    market_hist = get_market_history(years=max(years + 1, 6))
    ctx = get_market_context()
    sector_name = _detect_sector_name(meta.name)
    log.info(f"시장 컨텍스트: score={ctx.score:.1f} ({ctx.regime}), sector={sector_name}, vix={ctx.vix_level:.1f}")
    log.info(f"시장 시계열: {len(market_hist)} rows, sector_col=sector_{sector_name}")

    # 4) 기본 룰북
    base_rb = default_rulebook(ticker, asset_type=meta.asset_type, direction=meta.direction)
    base_rb.sector_name = sector_name

    # 5) GA 평가 함수 (시점별 시장 컨텍스트 사용)
    def evaluate_fn(rb: Rulebook) -> float:
        result = run_backtest(
            rb, df,
            position_limit_krw=position_limit_krw,
            market_history_df=market_hist,
            sector_name=sector_name,
        )
        return result.fitness

    # 6) GA 실행
    ga_cfg = ga_config or GAConfig()
    ga_result = run_ga(
        base_rulebook=base_rb,
        evaluate_fn=evaluate_fn,
        ga_config=ga_cfg,
        seed_rulebooks=seed_rulebooks,
        on_generation=on_generation,
    )
    best_rb = ga_result.best
    # GA가 시드 룰북에서 가져온 ticker 오염 방지 — 학습 대상 종목으로 강제 설정
    best_rb.ticker = ticker
    best_rb.asset_type = meta.asset_type
    best_rb.direction = meta.direction
    best_rb.sector_name = sector_name

    # 7) 최종 백테스트 (최적 룰북 + 시계열)
    final_result = run_backtest(
        best_rb, df,
        position_limit_krw=position_limit_krw,
        market_history_df=market_hist,
        sector_name=sector_name,
    )

    elapsed = time.time() - t0
    log.info(
        f"학습 완료: {ticker} fitness={final_result.fitness:.2f}, "
        f"trades={final_result.trade_count}, win={final_result.win_rate:.1f}%, "
        f"expectancy={final_result.expectancy_pct:+.3f}%, elapsed={elapsed:.1f}s"
    )

    return LearnResult(
        ticker=ticker,
        best_rulebook=best_rb,
        backtest=final_result,
        ga_result=ga_result,
        elapsed_sec=elapsed,
        asset_meta=meta.to_dict(),
    )


if __name__ == "__main__":
    cfg = GAConfig(population=15, generations=5, elite_ratio=0.2,
                   mutation_rate=0.2, random_seed=42)
    result = learn("379800", ga_config=cfg)
    print(f"\n=== 결과 ===")
    print(f"  종목: {result.ticker} ({result.asset_meta['name']})")
    print(f"  소요: {result.elapsed_sec:.1f}s")
    print(f"  Fitness: {result.backtest.fitness:.2f}")
    print(f"  거래: {result.backtest.trade_count} (승 {result.backtest.win_count}/패 {result.backtest.loss_count})")
    print(f"  승률: {result.backtest.win_rate:.1f}%")
    print(f"  기대값: {result.backtest.expectancy_pct:+.3f}%")
    print(f"  MDD: {result.backtest.max_drawdown_pct:.2f}%")
    print(f"  PF: {result.backtest.profit_factor:.2f}")
    print(f"\n학습된 시장 가중치 (이전엔 랜덤이었음):")
    print(f"  market_score_weight:    {result.best_rulebook.market_score_weight:+.3f}")
    print(f"  sector_strength_weight: {result.best_rulebook.sector_strength_weight:+.3f}")
    print(f"  vix_sensitivity:        {result.best_rulebook.vix_sensitivity:+.3f}")
    print(f"  signal_threshold:       {result.best_rulebook.signal_threshold:.2f}")
    print(f"  exit_strategy:          {result.best_rulebook.exit_strategy}")

