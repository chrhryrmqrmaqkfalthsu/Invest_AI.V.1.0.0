"""
LearnedRuleBook - 학습된 Rulebook + MarketContext 통합 라이브 룰북.

PositionManager 연동 캐시:
- 가장 최근 ATR / Rulebook
- 매수 신호 평가에 실제 사용한 ticker별 MarketContext
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from engine.core.indicators import calc_indicators
from engine.live.per_ticker_news import get_news_score
from engine.market.context import MarketContext, get_market_context
from engine.strategies.demo_rulebook import RuleBook, Signal, SignalResult
from engine.strategies.evaluator import evaluate_signal
from engine.strategies.rulebook import Rulebook as LearnedRule

log = logging.getLogger("learned_rulebook")

SYMBOLS_DIR = Path("data/symbols")
SEED_PATTERNS_PATH = Path("data/_system/seed_patterns.json")
OHLCV_CACHE_TTL_SEC = 600


class LearnedRuleBook(RuleBook):
    """학습된 룰북 + 시장 컨텍스트 기반 라이브 룰북."""

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
        self._last_atr: dict[str, float] = {}
        self._rulebook_by_ticker: dict[str, LearnedRule] = {}
        self._last_market_context: dict[str, dict] = {}
        log.info(
            f"LearnedRuleBook 초기화: lookback={ohlcv_lookback_years}y, "
            f"cache_ttl={ohlcv_cache_ttl_sec}s"
        )

    def name(self) -> str:
        return "LearnedRuleBook(parameters.json + MarketContext)"

    def _load_rulebook(self, ticker: str) -> Optional[LearnedRule]:
        """ticker별 학습 룰북 로드: parameters.json → seed_patterns → None."""
        if ticker in self._rulebook_cache:
            return self._rulebook_cache[ticker]

        params_path = SYMBOLS_DIR / ticker / "parameters.json"
        if params_path.exists():
            try:
                with open(params_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                rb_dict = data.get("rulebook")
                if rb_dict:
                    rb = LearnedRule.from_dict(rb_dict)
                    self._rulebook_cache[ticker] = rb
                    log.info(
                        f"{ticker} 룰북 로드 (parameters.json): "
                        f"win_rate={rb.win_rate:.1f}%, fitness={rb.fitness:.2f}"
                    )
                    return rb
            except Exception as e:
                log.warning(f"{ticker} parameters.json 로드 실패: {e}")

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
                            log.info(f"{ticker} 룰북 로드 (seed_patterns.json): fitness={rb.fitness:.2f}")
                            return rb
            except Exception as e:
                log.warning(f"seed_patterns.json 로드 실패: {e}")

        log.warning(f"{ticker} 학습된 룰북 없음 - HOLD로 처리")
        self._rulebook_cache[ticker] = None
        return None

    def _get_ohlcv(self, ticker: str) -> Optional[pd.DataFrame]:
        """ticker 최근 OHLCV+지표 DataFrame. 캐시 10분."""
        now = time.time()
        if ticker in self._ohlcv_cache:
            df, ts = self._ohlcv_cache[ticker]
            if now - ts < self.cache_ttl:
                return df

        try:
            # Lazy import prevents KRX/KIS adapter side effects during US-only startup.
            from engine.adapters.factory import get_adapter

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

    def evaluate(self, ticker: str, price: float, df=None) -> SignalResult:
        rb = self._load_rulebook(ticker)
        if rb is None:
            return SignalResult(ticker=ticker, signal=Signal.HOLD, price=price, reason="학습된 룰북 없음")

        if df is None:
            df = self._get_ohlcv(ticker)
        try:
            if df is not None and "ATR" in df.columns and len(df) > 0:
                self._last_atr[ticker] = float(df["ATR"].iloc[-1])
            self._rulebook_by_ticker[ticker] = rb
        except Exception as exc:
            log.warning(f"{ticker} ATR 캐시 실패: {exc}")

        if df is None or len(df) < 60:
            return SignalResult(ticker=ticker, signal=Signal.HOLD, price=price, reason="OHLCV 데이터 부족")

        try:
            ctx: MarketContext = get_market_context()
        except Exception as e:
            log.warning(f"MarketContext 로드 실패, 중립 사용: {e}")
            ctx = None

        if ctx is not None:
            market_score = float(ctx.score)
            sector_score = float(ctx.sector_strength.get(rb.sector_name, 50.0))
            vix_level = float(ctx.vix_level)
            context_timestamp = str(ctx.timestamp)
        else:
            market_score, sector_score, vix_level = 50.0, 50.0, 18.0
            context_timestamp = ""

        self._last_market_context[ticker] = {
            "score": market_score,
            "market_score": market_score,
            "vix_level": vix_level,
            "sector_score": sector_score,
            "sector_strength": {str(rb.sector_name or ""): sector_score},
            "timestamp": context_timestamp,
        }

        news_normalized = 0.0
        try:
            meta = getattr(self, "meta", None)
            news_data = get_news_score(ticker, meta=meta)
            news_normalized = news_data.get("normalized_score", 0.0)
            log.info(
                f"{ticker} 뉴스 sentiment: raw={news_data.get('sentiment_score', 0):+.2f}, "
                f"normalized={news_normalized:+.3f} "
                f"(호재 {news_data.get('bullish_count', 0)}/악재 {news_data.get('bearish_count', 0)})"
            )
        except Exception as e:
            log.warning(f"{ticker} 뉴스 점수 조회 실패 (중립 0.0 사용): {e}")

        try:
            active = getattr(ctx, "active_events", {}) or {}
            try:
                event_flags = {
                    "has_war": int("전쟁" in active),
                    "has_rate_hike": int("금리정책_인상" in active),
                    "has_rate_cut": int("금리정책_인하" in active),
                    "has_geopolitical": int("지정학_긴장" in active),
                    "has_tariff": int("관세" in active),
                    "has_export_ban": int("수출규제" in active),
                    "has_earnings_shock": int("실적쇼크" in active),
                    "has_oil_surge": int("유가급등" in active),
                    "has_banking_crisis": int("은행위기" in active),
                    "has_inflation": int("인플레이션" in active),
                    "has_fed_statement": int("연준발언" in active),
                }
            except Exception:
                event_flags = None
            res = evaluate_signal(
                rb=rb,
                df=df,
                market_score=market_score,
                sector_score=sector_score,
                vix_level=vix_level,
                news_sentiment=news_normalized,
                event_flags=event_flags,
            )
        except Exception as e:
            log.error(f"{ticker} evaluate_signal 실패: {e}")
            return SignalResult(ticker=ticker, signal=Signal.HOLD, price=price, reason=f"evaluate 예외: {e}")

        news_tag = ""
        if abs(news_normalized) >= 0.1:
            kind = "호재" if news_normalized > 0 else "악재"
            news_tag = f" 뉴스{kind}({news_normalized:+.2f})"
        reason_str = (
            f"score={res.score:.2f}/threshold={res.threshold:.2f} "
            f"raw={res.raw_score:.2f} mkt_adj×{res.market_adjustment:.2f}{news_tag} "
            f"reasons=[{', '.join(res.reasons[:4])}]"
        )
        signal_kwargs = {
            "score": float(res.score),
            "raw_score": float(res.raw_score),
            "threshold": float(res.threshold),
            "market_adjustment": float(res.market_adjustment),
            "reasons": list(res.reasons),
        }
        if res.should_buy:
            return SignalResult(ticker=ticker, signal=Signal.BUY, price=price, reason=f"[{rb.direction}] {reason_str}", **signal_kwargs)
        return SignalResult(ticker=ticker, signal=Signal.HOLD, price=price, reason=f"미달({rb.direction}) {reason_str}", **signal_kwargs)

    def get_last_atr(self, ticker: str):
        return self._last_atr.get(ticker)

    def get_rulebook(self, ticker: str):
        return self._rulebook_by_ticker.get(ticker)

    def get_last_market_context(self, ticker: str):
        ctx = self._last_market_context.get(ticker)
        return dict(ctx) if isinstance(ctx, dict) else None


if __name__ == "__main__":
    print("LearnedRuleBook smoke")
    rb = LearnedRuleBook()
    print(rb.name())
