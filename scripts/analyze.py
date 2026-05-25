"""
종목 종합 분석 스크립트 (Step 11 통합 테스트)
- Adapter → Data → Market → Learning → Storage 전체 흐름 검증
- 사용: python scripts/analyze.py <ticker> [--quick]
"""
import sys
import argparse
import time
from datetime import datetime
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.core.logger import get_logger
from engine.adapters.factory import get_adapter
from engine.market.context import get_market_context
from engine.strategies.evaluator import evaluate_signal, calc_position_size_krw
from engine.learning.learner import learn
from engine.learning.genetic import GAConfig
from engine.storage import repository as repo

log = get_logger("analyze")


def _sector_score_from_context(meta, ctx) -> float:
    """종목 메타에서 섹터 점수 추정"""
    name = (meta.name or "").lower()
    sectors = ctx.sector_strength or {}
    if any(k in name for k in ["반도체", "tech", "qqq", "kodex", "tiger", "s&p", "나스닥", "semi"]):
        return sectors.get("tech", 50.0)
    if any(k in name for k in ["에너지", "energy", "oil"]):
        return sectors.get("energy", 50.0)
    if any(k in name for k in ["금융", "finance", "bank"]):
        return sectors.get("finance", 50.0)
    if any(k in name for k in ["헬스", "health", "bio"]):
        return sectors.get("healthcare", 50.0)
    return 50.0


def analyze(ticker: str, quick: bool = False, position_limit_krw: float = 120000.0):
    print("=" * 70)
    print(f"📊 종목 분석 시작: {ticker}")
    print(f"   시작 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    t0 = time.time()

    # 1) 어댑터 매핑
    print("\n[1/6] 어댑터 매핑...")
    adapter = get_adapter(ticker)
    meta = adapter.meta
    print(f"  ✅ {adapter.__class__.__name__}")
    print(f"     이름: {meta.name}")
    print(f"     타입: {meta.asset_type} / 방향: {meta.direction}")
    print(f"     통화: {meta.currency} / 시장: {meta.market}")
    print(f"     거래시간: {meta.trading_hours.open_time}~{meta.trading_hours.close_time} ({meta.trading_hours.timezone})")
    print(f"     장 열림: {adapter.is_market_open()}")

    # 2) 현재가
    print("\n[2/6] 현재가 조회...")
    price = adapter.current_price()
    if price:
        print(f"  ✅ 현재가: {price:,.2f} {meta.currency}")
    else:
        print(f"  ⚠️ 현재가 조회 실패")

    # 3) 시장 컨텍스트
    print("\n[3/6] 시장 컨텍스트 로딩...")
    ctx = get_market_context()
    sector_score = _sector_score_from_context(meta, ctx)
    print(f"  ✅ 시장점수: {ctx.score:.1f}/100 ({ctx.regime})")
    print(f"     KOSPI: {ctx.kospi_trend_pct:+.2f}% / S&P500: {ctx.sp500_trend_pct:+.2f}%")
    print(f"     VIX: {ctx.vix_level:.2f}")
    print(f"     섹터 점수: {sector_score:.1f}")
    print(f"     매수 배율: ×{ctx.buy_multiplier:.3f}")

    # 4) GA 학습
    print("\n[4/6] GA 학습 실행 중...")
    if quick:
        ga_cfg = GAConfig(population=15, generations=5, elite_ratio=0.2,
                          mutation_rate=0.2,random_seed=42)
        print(f"     (quick 모드: pop=15, gen=5)")
    else:
        ga_cfg = GAConfig(population=20, generations=10, elite_ratio=0.2,
                          mutation_rate=0.15,random_seed=42)
        print(f"     (표준 모드: pop=20, gen=10)")
    seed_rbs = repo.load_seed_rulebooks(top_n=3, direction=meta.direction)
    if seed_rbs:
        print(f"     시드 패턴 {len(seed_rbs)}개 활용")
    t_learn = time.time()
    result = learn(
        ticker=ticker,
        position_limit_krw=position_limit_krw,
        ga_config=ga_cfg,
        seed_rulebooks=seed_rbs,
    )
    print(f"  ✅ 학습 완료 ({time.time()-t_learn:.1f}s)")

    rb = result.best_rulebook
    bt = result.backtest
    print(f"     적합도: {bt.fitness:.2f}")
    print(f"     거래수: {bt.trade_count} (승 {bt.win_count}/패 {bt.loss_count})")
    print(f"     승률: {bt.win_rate:.1f}%")
    print(f"     기대값: {bt.expectancy_pct:+.3f}%")
    print(f"     MDD: {bt.max_drawdown_pct:.2f}%")
    print(f"     Profit Factor: {bt.profit_factor:.2f}")

    # 5) 현재 매수 신호
    print("\n[5/6] 현재 매수 신호 평가...")
    df = adapter.load_history(years=2)
    signal = evaluate_signal(
        rb, df,
        market_score=ctx.score,
        sector_score=sector_score,
        vix_level=ctx.vix_level,
        news_sentiment=0.0,
    )
    print(f"  매수 권고: {'🟢 BUY' if signal.should_buy else '⚪ HOLD'}")
    print(f"     점수: {signal.score:.2f} (raw {signal.raw_score:.2f}, 임계 {signal.threshold:.2f})")
    print(f"     시장보정: ×{signal.market_adjustment:.3f}")
    if signal.reasons:
        print(f"     근거: {', '.join(signal.reasons)}")
    if signal.should_buy and price:
        amt = calc_position_size_krw(rb, signal.score, position_limit_krw)
        shares = int(amt / price) if meta.currency == "KRW" else 0
        print(f"     투자금액: {amt:,.0f} KRW ({shares} 주)")

    # 6) 저장
    print("\n[6/6] Storage 저장...")
    repo.add_symbol(ticker, meta.to_dict())
    repo.save_rulebook(rb, meta.to_dict())
    repo.save_backtest(ticker, bt.to_dict())
    if result.ga_result and hasattr(result.ga_result, "history"):
        repo.save_fitness_history(ticker, result.ga_result.history)
    if bt.fitness >= 30:
        repo.add_seed_rulebook(rb, min_fitness=30)
        print(f"  ✅ 시드 패턴으로 등록 (fitness={bt.fitness:.2f})")
    print(f"  ✅ data/symbols/{ticker}/ 저장 완료")

    elapsed = time.time() - t0
    print("\n" + "=" * 70)
    print(f"✅ 분석 완료 (총 {elapsed:.1f}초)")
    print("=" * 70)
    return result


def main():
    parser = argparse.ArgumentParser(description="종목 종합 분석")
    parser.add_argument("ticker", help="종목 코드 (예: 379800, AAPL)")
    parser.add_argument("--quick", action="store_true", help="빠른 학습 (pop=15, gen=5)")
    parser.add_argument("--limit", type=float, default=120000.0, help="투자한도 KRW (기본 120000)")
    args = parser.parse_args()
    analyze(args.ticker, quick=args.quick, position_limit_krw=args.limit)


if __name__ == "__main__":
    main()
