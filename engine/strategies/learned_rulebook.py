"""
LearnedRuleBook - 학습된 Rulebook + MarketContext 통합 라이브 룰북.

흐름:
  1. ticker별 학습된 Rulebook 로드 (data/symbols/{ticker}/parameters.json)
  2. OHLCV 시계열 조달 (AssetAdapter 자동 분기: KIS/yfinance)
  3. MarketContext 로드 (캐시 1시간)
  4. evaluate_signal()로 평가 (시장보정 포함)
  5. RuleBook 인터페이스(SignalResult)로 변환

  long 룰북:  should_buy=True → BUY
  short 룰북: should_buy=True → BUY (인버스 ETF니까 결과적으로 시장 하락 베팅)
  
  손절/익절 시그널은 PositionManager가 별도 처리 (Phase B).
  여기서는 진입 시그널만 담당.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from engine.adapters.factory import get_adapter
from engine.core.indicators import calc_indicators
from engine.market.context import get_market_context, MarketContext
from engine.strategies.demo_rulebook import RuleBook, Signal, SignalResult
from engine.strategies.evaluator import evaluate_signal
from engine.strategies.rulebook import Rulebook as LearnedRule

log = logging.getLogger("learned_rulebook")

# 데이터 경로
SYMBOLS_DIR = Path("data/symbols")
SEED_PATTERNS_PATH = Path("data/_system/seed_patterns.json")

# OHLCV 캐시 TTL (초). 장중에 매분 호출되므로 캐시 필수.
OHLCV_CACHE_TTL_SEC = 600   # 10분


class LearnedRuleBook(RuleBook):
    """
    학습된 룰북 + 시장 컨텍스트 기반 라이브 룰북.
    """

    def __init__(
        self,
        ohlcv_lookback_years: int = 1,
        ohlcv_cache_ttl_sec: int = OHLCV_CACHE_TTL_SEC,
    ):
        self.lookback_years = ohlcv_lookback_years
        self.cache_ttl = ohlcv_cache_ttl_sec
        self._rulebook_cache: Dict[str, LearnedRule] = {}
        self._adapter_cache: Dict[str, object] = {}
        self._ohlcv_cache: Dict[str, tuple[pd.DataFrame, float]] = {}
        log.info(
            f"LearnedRuleBook 초기화: lookback={ohlcv_lookback_years}y, "
            f"cache_ttl={ohlcv_cache_ttl_sec}s"
        )

    def name(self) -> str:
        return "LearnedRuleBook(parameters.json + MarketContext)"

    # ==========================================================
    # Rulebook 로드
    # ==========================================================
    def _load_rulebook(self, ticker: str) -> Optional[LearnedRule]:
        """ticker별 학습 룰북 로드. 우선순위:
        1. data/symbols/{ticker}/parameters.json
        2. data/_system/seed_patterns.json 내 ticker 매칭
        3. None (학습 안 됨)
        """
        if ticker in self._rulebook_cache:
            return self._rulebook_cache[ticker]

        # 1) 종목 디렉토리의 parameters.json
        params_path = SYMBOLS_DIR / ticker / "parameters.json"
        if params_path.exists():
            try:
                with open(params_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                rb_dict = data.get("rulebook")
                if rb_dict:
                    rb = LearnedRule.from_dict(rb_dict)
                    self._rulebook_cache[ticker] = rb
                    log.info(f"{ticker} 룰북 로드 (parameters.json): "
                             f"win_rate={rb.win_rate:.1f}%, fitness={rb.fitness:.2f}")
                    return rb
            except Exception as e:
                log.warning(f"{ticker} parameters.json 로드 실패: {e}")

        # 2) seed_patterns.json fallback
        if SEED_PATTERNS_PATH.exists():
            try:
                with open(SEED_PATTERNS_PATH, "r", encoding="utf-8") as f:
                    seeds = json.load(f)
                for direction in ("long", "short"):
                    for seed in seeds.get(direction, []):
                        rb_dict = seed.get("rulebook", {})
                        if rb_dict.get("ticker") == ticker:
                            rb = LearnedRule.from_dict(rb_dict)
                            self._rulebook_cache[ticker] = rb
                            log.info(f"{ticker} 룰북 로드 (seed_patterns.json): "
                                     f"fitness={rb.fitness:.2f}")
                            return rb
            except Exception as e:
                log.warning(f"seed_patterns.json 로드 실패: {e}")

        log.warning(f"{ticker} 학습된 룰북 없음 - HOLD로 처리")
        self._rulebook_cache[ticker] = None  # 다음에 또 시도하지 않게
        return None

    # ==========================================================
    # OHLCV 조달 (캐시)
    # ==========================================================
    def _get_ohlcv(self, ticker: str) -> Optional[pd.DataFrame]:
        """ticker의 최근 OHLCV+지표 DataFrame. 캐시 10분."""
        now = time.time()
        if ticker in self._ohlcv_cache:
            df, ts = self._ohlcv_cache[ticker]
            if now - ts < self.cache_ttl:
                return df

        try:
            adapter = self._adapter_cache.get(ticker)
            if adapter is None:
                adapter = get_adapter(ticker)
                self._adapter_cache[ticker] = adapter

            df = adapter.load_history(years=self.lookback_years)
            if df is None or df.empty or len(df) < 60:
                log.warning(f"{ticker} OHLCV 부족: {0 if df is None else len(df)}봉")
                return None

            df = calc_indicators(df)
            self._ohlcv_cache[ticker] = (df, now)
            log.info(f"{ticker} OHLCV 로드: {len(df)}봉 (캐시 갱신)")
            return df

        except Exception as e:
            log.error(f"{ticker} OHLCV 조달 실패: {e}")
            return None

    # ==========================================================
    # 메인: evaluate
    # ==========================================================
    def evaluate(self, ticker: str, price: float, df=None) -> SignalResult:
        # 1) 학습 룰북 로드
        rb = self._load_rulebook(ticker)
        if rb is None:
            return SignalResult(
                ticker=ticker, signal=Signal.HOLD, price=price,
                reason="학습된 룰북 없음",
            )

        # 2) OHLCV (df 인자로 받으면 그것 사용, 아니면 어댑터로 조달)
        if df is None:
            df = self._get_ohlcv(ticker)
        if df is None or len(df) < 60:
            return SignalResult(
                ticker=ticker, signal=Signal.HOLD, price=price,
                reason="OHLCV 데이터 부족",
            )

        # 3) MarketContext (캐시 우선)
        try:
            ctx: MarketContext = get_market_context()
        except Exception as e:
            log.warning(f"MarketContext 로드 실패, 중립 사용: {e}")
            ctx = None

        if ctx is not None:
            market_score = ctx.score
            sector_score = ctx.sector_strength.get(rb.sector_name, 50.0)
            vix_level = ctx.vix_level
        else:
            market_score, sector_score, vix_level = 50.0, 50.0, 18.0

        # 4) evaluate_signal 호출 (Rulebook + 시장보정 통합)
        try:
            res = evaluate_signal(
                rb=rb, df=df,
                market_score=market_score,
                sector_score=sector_score,
                vix_level=vix_level,
                news_sentiment=0.0,   # 뉴스 감성 분석은 후속 작업
            )
        except Exception as e:
            log.error(f"{ticker} evaluate_signal 실패: {e}")
            return SignalResult(
                ticker=ticker, signal=Signal.HOLD, price=price,
                reason=f"evaluate 예외: {e}",
            )

        # 5) RuleBook 인터페이스로 변환
        reason_str = (
            f"score={res.score:.2f}/threshold={res.threshold:.2f} "
            f"raw={res.raw_score:.2f} mkt_adj×{res.market_adjustment:.2f} "
            f"reasons=[{', '.join(res.reasons[:4])}]"
        )

        if res.should_buy:
            # long/short 무관: 인버스 ETF도 매수 신호면 매수 (시장 하락 베팅)
            return SignalResult(
                ticker=ticker, signal=Signal.BUY, price=price,
                reason=f"[{rb.direction}] {reason_str}",
            )

        return SignalResult(
            ticker=ticker, signal=Signal.HOLD, price=price,
            reason=f"미달({rb.direction}) {reason_str}",
        )


# ==========================================================
# 단위 테스트
# ==========================================================
if __name__ == "__main__":
    import logging as _lg
    _lg.basicConfig(
        level=_lg.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )

    print("=" * 60)
    print("LearnedRuleBook 단위 테스트")
    print("=" * 60)

    rb = LearnedRuleBook()
    print(f"룰북: {rb.name()}\n")

    # 학습된 종목 2개 + 학습 안 된 종목 1개
    for ticker, price in [("379800", 13500), ("360750", 18000), ("UNKNOWN", 1000)]:
        print(f"--- {ticker} (현재가 {price}원) ---")
        res = rb.evaluate(ticker, price)
        print(f"  signal: {res.signal.value}")
        print(f"  reason: {res.reason}\n")

    # 캐시 확인 (두 번째 호출은 OHLCV 다운로드 없어야)
    print("--- 캐시 검증 (379800 재호출) ---")
    res2 = rb.evaluate("379800", 13510)
    print(f"  signal: {res2.signal.value}")
    print(f"  reason: {res2.reason}")

    print("\n" + "=" * 60)
    print("✅ LearnedRuleBook 검증 완료")
    print("=" * 60)
