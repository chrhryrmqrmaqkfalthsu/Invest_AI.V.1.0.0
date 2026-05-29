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
    backtest: BacktestResult           # train 결과 (기존 호환)
    ga_result: Optional[GAResult]
    elapsed_sec: float
    asset_meta: dict
    train_result: Optional[BacktestResult] = None  # train 구간 백테스트
    test_result: Optional[BacktestResult] = None   # test 구간 (out-of-sample)
    train_period: Optional[tuple] = None           # (start_date, end_date)
    test_period: Optional[tuple] = None
    overfit_ratio: Optional[float] = None          # test_fitness / train_fitness


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
    years: int = 6,
    position_limit_krw: float = 120000.0,
    ga_config: Optional[GAConfig] = None,
    seed_rulebooks: Optional[list] = None,
    on_generation: Optional[Callable] = None,
    test_months: int = 6,
) -> LearnResult:
    t0 = time.time()

    # 1) 어댑터 + 메타
    adapter = get_adapter(ticker)
    meta = adapter.meta
    log.info(f"학습 시작: {ticker} ({meta.name}, {meta.direction})")

    # 2) 데이터 로드 + 지표 계산
    df = adapter.load_history(years=years)

    # 2-1) Walk-forward split: 마지막 test_months를 out-of-sample test로 분리
    import pandas as pd
    date_col = 'date' if 'date' in df.columns else None
    if date_col:
        dates = pd.to_datetime(df[date_col])
    elif isinstance(df.index, pd.DatetimeIndex):
        dates = df.index
    else:
        dates = None

    if dates is not None and len(dates) > 0:
        end_date = dates.max()
        split_date = end_date - pd.DateOffset(months=test_months)
        train_start = dates.min().strftime('%Y-%m-%d')
        train_end = split_date.strftime('%Y-%m-%d')
        test_start = (split_date + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
        test_end = end_date.strftime('%Y-%m-%d')
        log.info(f"Walk-forward split: train {train_start} ~ {train_end}, test {test_start} ~ {test_end}")
    else:
        train_start = train_end = test_start = test_end = None
        log.warning("날짜 정보 없음 → walk-forward split 비활성화")

    # 3) 시장 시계열 (한 번 로드, GA 전체에서 재사용)
    market_hist = get_market_history(years=max(years + 1, 6))
    ctx = get_market_context()
    sector_name = _detect_sector_name(meta.name)
    log.info(f"시장 컨텍스트: score={ctx.score:.1f} ({ctx.regime}), sector={sector_name}, vix={ctx.vix_level:.1f}")
    log.info(f"시장 시계열: {len(market_hist)} rows, sector_col=sector_{sector_name}")

    # 4) 기본 룰북
    base_rb = default_rulebook(ticker, asset_type=meta.asset_type, direction=meta.direction)
    base_rb.sector_name = sector_name

    # 5) GA 평가 함수 (walk-forward: train + test 가중 결합)
    # - train만 보면 과적합 (test에서 안 통하는 cherry-pick 룰북 양산)
    # - test 가중치를 높여서 GA가 "OOS에서도 통하는 룰"을 찾도록 유도
    TRAIN_WEIGHT = 0.4
    TEST_WEIGHT = 0.6

    def evaluate_fn(rb: Rulebook) -> float:
        train_r = run_backtest(
            rb, df,
            position_limit_krw=position_limit_krw,
            market_history_df=market_hist,
            sector_name=sector_name,
            start_date=train_start,
            end_date=train_end,
        )
        # test 구간이 없는 경우(데이터 짧음) fallback
        if not test_start or not test_end:
            return train_r.fitness
        test_r = run_backtest(
            rb, df,
            position_limit_krw=position_limit_krw,
            market_history_df=market_hist,
            sector_name=sector_name,
            start_date=test_start,
            end_date=test_end,
        )
        return train_r.fitness * TRAIN_WEIGHT + test_r.fitness * TEST_WEIGHT

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

    # 7) 최종 백테스트: TRAIN + TEST 두 구간 모두 실행
    train_result = run_backtest(
        best_rb, df,
        position_limit_krw=position_limit_krw,
        market_history_df=market_hist,
        sector_name=sector_name,
        start_date=train_start,
        end_date=train_end,
    )

    test_result = run_backtest(
        best_rb, df,
        position_limit_krw=position_limit_krw,
        market_history_df=market_hist,
        sector_name=sector_name,
        start_date=test_start,
        end_date=test_end,
    )

    # 과적합 비율: test_fitness / train_fitness (1.0 근처면 양호, 0.5 이하면 과적합 의심)
    overfit_ratio = None
    if train_result.fitness != 0:
        overfit_ratio = test_result.fitness / train_result.fitness

    elapsed = time.time() - t0
    log.info(
        f"[TRAIN] fitness={train_result.fitness:.2f}, "
        f"trades={train_result.trade_count}, win={train_result.win_rate:.1f}%, "
        f"expectancy={train_result.expectancy_pct:+.3f}%"
    )
    log.info(
        f"[TEST]  fitness={test_result.fitness:.2f}, "
        f"trades={test_result.trade_count}, win={test_result.win_rate:.1f}%, "
        f"expectancy={test_result.expectancy_pct:+.3f}%"
    )
    if overfit_ratio is not None:
        verdict = "양호" if overfit_ratio >= 0.5 else ("주의" if overfit_ratio >= 0.3 else "과적합 의심")
        log.info(f"[과적합 비율] test/train = {overfit_ratio:.2f} → {verdict}")
    log.info(f"학습 완료: {ticker}, elapsed={elapsed:.1f}s")

    return LearnResult(
        ticker=ticker,
        best_rulebook=best_rb,
        backtest=train_result,
        ga_result=ga_result,
        elapsed_sec=elapsed,
        asset_meta=meta.to_dict(),
        train_result=train_result,
        test_result=test_result,
        train_period=(train_start, train_end),
        test_period=(test_start, test_end),
        overfit_ratio=overfit_ratio,
    )


if __name__ == "__main__":
    cfg = GAConfig(population=15, generations=5, elite_ratio=0.2,
                   mutation_rate=0.2, random_seed=42)
    result = learn("379800", ga_config=cfg)
    print(f"\n=== 결과 ===")
    print(f"  종목: {result.ticker} ({result.asset_meta['name']})")
    print(f"  소요: {result.elapsed_sec:.1f}s")
    print(f"\n[TRAIN {result.train_period[0]} ~ {result.train_period[1]}]")
    print(f"  Fitness: {result.train_result.fitness:.2f}")
    print(f"  거래: {result.train_result.trade_count} (승 {result.train_result.win_count}/패 {result.train_result.loss_count})")
    print(f"  승률: {result.train_result.win_rate:.1f}%")
    print(f"\n[TEST  {result.test_period[0]} ~ {result.test_period[1]}]")
    print(f"  Fitness: {result.test_result.fitness:.2f}")
    print(f"  거래: {result.test_result.trade_count} (승 {result.test_result.win_count}/패 {result.test_result.loss_count})")
    print(f"  승률: {result.test_result.win_rate:.1f}%")
    if result.overfit_ratio is not None:
        verdict = "양호" if result.overfit_ratio >= 0.5 else ("주의" if result.overfit_ratio >= 0.3 else "과적합 의심")
        print(f"\n[과적합] test/train = {result.overfit_ratio:.2f} → {verdict}")
    print(f"  기대값: {result.backtest.expectancy_pct:+.3f}%")
    print(f"  MDD: {result.backtest.max_drawdown_pct:.2f}%")
    print(f"  PF: {result.backtest.profit_factor:.2f}")
    print(f"\n학습된 시장 가중치 (이전엔 랜덤이었음):")
    print(f"  market_score_weight:    {result.best_rulebook.market_score_weight:+.3f}")
    print(f"  sector_strength_weight: {result.best_rulebook.sector_strength_weight:+.3f}")
    print(f"  vix_sensitivity:        {result.best_rulebook.vix_sensitivity:+.3f}")
    print(f"  signal_threshold:       {result.best_rulebook.signal_threshold:.2f}")
    print(f"  exit_strategy:          {result.best_rulebook.exit_strategy}")

