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
from engine.learning.stock_grade import evaluate_swing_stock_grade
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment
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
    stock_grade: Optional[dict] = None             # 스윙 단타용 종목 등급


def _detect_sector_name(meta_name: str) -> str:
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
    test_months: int = 24,
    fitness_mode: str = "legacy",
) -> LearnResult:
    t0 = time.time()
    adapter = get_adapter(ticker)
    meta = adapter.meta
    log.info(f"학습 시작: {ticker} ({meta.name}, {meta.direction})")
    df = adapter.load_history(years=years)

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

    market_hist = get_market_history(years=max(years + 1, 6))
    ctx = get_market_context()
    sector_name = _detect_sector_name(meta.name)
    log.info(f"시장 컨텍스트: score={ctx.score:.1f} ({ctx.regime}), sector={sector_name}, vix={ctx.vix_level:.1f}")
    log.info(f"시장 시계열: {len(market_hist)} rows, sector_col=sector_{sector_name}")

    ticker_sentiment = load_ticker_sentiment(ticker)
    if ticker_sentiment:
        log.info(f"종목 감성 CSV 로드: {len(ticker_sentiment)} days")
    else:
        log.info(f"종목 감성 CSV 없음 (news_sentiment=0.0 폴백)")

    base_rb = default_rulebook(ticker, asset_type=meta.asset_type, direction=meta.direction)
    base_rb.sector_name = sector_name

    def evaluate_fn(rb: Rulebook) -> float:
        """GA selection은 TRAIN 구간만 사용한다.

        TEST 구간은 아래 학습 종료 후 best_rb 검증에서만 평가한다.
        """
        train_r = run_backtest(
            rb,
            df,
            position_limit_krw=position_limit_krw,
            market_history_df=market_hist,
            sector_name=sector_name,
            start_date=train_start,
            end_date=train_end,
            ticker_sentiment=ticker_sentiment,
            fitness_mode=fitness_mode,
        )
        return train_r.fitness

    ga_cfg = ga_config or GAConfig()
    ga_result = run_ga(base_rulebook=base_rb, evaluate_fn=evaluate_fn, ga_config=ga_cfg, seed_rulebooks=seed_rulebooks, on_generation=on_generation)

    try:
        import json
        from pathlib import Path
        _top = sorted(ga_result.final_population, key=lambda x: (x.fitness if x.fitness is not None else -1e9), reverse=True)
        _dump = []
        for _rank, _rb in enumerate(_top[:20], 1):
            _d = _rb.to_dict()
            _d_out = {"rank": _rank, "fitness": _rb.fitness}
            _d_out.update(_d)
            _d_out["fitness"] = _rb.fitness
            _dump.append(_d_out)
        _dump_dir = Path("data/_system")
        _dump_dir.mkdir(parents=True, exist_ok=True)
        _dump_path = _dump_dir / f"ga_population_dump_{ticker}.json"
        with open(_dump_path, "w") as _f:
            json.dump(_dump, _f, indent=2, default=str)
        log.info(f"final_population 상위 {len(_dump)}개 덤프 완료: {_dump_path}")
    except Exception as _e:
        log.warning(f"final_population 덤프 실패(학습은 계속 진행): {_e}")

    stock_grade = None
    try:
        stock_grade = evaluate_swing_stock_grade(
            ticker=ticker,
            population=ga_result.final_population,
            df=df,
            position_limit_krw=position_limit_krw,
            market_history_df=market_hist,
            sector_name=sector_name,
            ticker_sentiment=ticker_sentiment,
            fitness_mode=fitness_mode,
        )
        _summary = stock_grade.get("summary", {})
        log.info(
            f"[SWING 등급] {stock_grade.get('grade')} ({stock_grade.get('mode')}) "
            f"pass={_summary.get('pass_count')}/{_summary.get('periods')}, "
            f"weak={_summary.get('weak_count')}, avg_exp={_summary.get('avg_expectancy_pct', 0):+.2f}%"
        )
    except Exception as _e:
        log.warning(f"스윙 종목 등급 산정 실패(학습은 계속 진행): {_e}")

    best_rb = ga_result.best
    best_rb.ticker = ticker
    best_rb.asset_type = meta.asset_type
    best_rb.direction = meta.direction
    best_rb.sector_name = sector_name

    train_result = run_backtest(best_rb, df, position_limit_krw=position_limit_krw, market_history_df=market_hist, sector_name=sector_name, start_date=train_start, end_date=train_end, ticker_sentiment=ticker_sentiment, fitness_mode=fitness_mode)
    test_result = run_backtest(best_rb, df, position_limit_krw=position_limit_krw, market_history_df=market_hist, sector_name=sector_name, start_date=test_start, end_date=test_end, ticker_sentiment=ticker_sentiment, fitness_mode=fitness_mode)

    overfit_ratio = None
    if train_result.fitness != 0:
        overfit_ratio = test_result.fitness / train_result.fitness

    elapsed = time.time() - t0
    log.info(f"[TRAIN] fitness={train_result.fitness:.2f}, trades={train_result.trade_count}, win={train_result.win_rate:.1f}%, expectancy={train_result.expectancy_pct:+.3f}%")
    log.info(f"[TEST]  fitness={test_result.fitness:.2f}, trades={test_result.trade_count}, win={test_result.win_rate:.1f}%, expectancy={test_result.expectancy_pct:+.3f}%")
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
        stock_grade=stock_grade,
    )
