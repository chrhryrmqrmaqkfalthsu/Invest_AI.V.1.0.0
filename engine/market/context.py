"""
시장 컨텍스트 분석 모듈 (v2 - Colab v3.2 통합)
- S&P500/VIX/섹터 ETF 기반 가격 점수 (KOSPI 제거)
- NewsAPI 실시간 뉴스 + 키워드 필터 + GPT-4o-mini 해석
- 11개 이벤트 카테고리 자동 감지
- data/_system/market_state.json 캐시 (60분)

호환성: 기존 인터페이스 100% 유지
- 함수: build_market_context, get_market_context, build_market_history,
        get_market_history, lookup_market_at
- 필드: 모든 기존 필드 보존, kospi_trend_pct=0.0 고정
- 신규 필드: event_adjustment, active_events, news_sentiment_avg
"""
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Any

import numpy as np
import pandas as pd
import yfinance as yf
import requests

from engine.core.config import config
from engine.core.logger import get_logger

log = get_logger("market_context")


MARKET_STATE_PATH = config.system_dir() / "market_state.json"
CACHE_TTL_MIN = config.get("cycle.market_context_cache_min", 60)

NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")


# =====================================================================
# MarketContext (호환성 유지 + 신규 필드)
# =====================================================================
@dataclass
class MarketContext:
    timestamp: str
    score: float
    regime: str
    kospi_trend_pct: float
    sp500_trend_pct: float
    vix_level: float
    sector_strength: dict
    risk_events: list
    benefit_events: list
    buy_multiplier: float
    threshold_multiplier: float
    # 신규
    event_adjustment: float = 0.0
    active_events: dict = field(default_factory=dict)
    news_sentiment_avg: float = 0.0

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "score": round(self.score, 1),
            "regime": self.regime,
            "kospi_trend_pct": round(self.kospi_trend_pct, 2),
            "sp500_trend_pct": round(self.sp500_trend_pct, 2),
            "vix_level": round(self.vix_level, 2),
            "sector_strength": {k: round(v, 1) for k, v in self.sector_strength.items()},
            "risk_events": self.risk_events,
            "benefit_events": self.benefit_events,
            "buy_multiplier": round(self.buy_multiplier, 3),
            "threshold_multiplier": round(self.threshold_multiplier, 3),
            "event_adjustment": round(self.event_adjustment, 2),
            "active_events": self.active_events,
            "news_sentiment_avg": round(self.news_sentiment_avg, 3),
        }


# =====================================================================
# 안전 헬퍼 (NaN 방어)
# =====================================================================
def _safe_pct_change(series: pd.Series, days: int) -> float:
    if series is None or len(series) < days + 1:
        return 0.0
    try:
        clean = series.dropna()
        if len(clean) < days + 1:
            return 0.0
        val = float((clean.iloc[-1] / clean.iloc[-days - 1] - 1) * 100)
        if np.isnan(val) or np.isinf(val):
            return 0.0
        return val
    except Exception:
        return 0.0


def _fetch_index(symbol: str, period: str = "6mo") -> Optional[pd.DataFrame]:
    try:
        df = yf.download(symbol, period=period, progress=False, auto_adjust=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df is None or df.empty:
            return None
        return df
    except Exception as e:
        log.warning(f"_fetch_index failed {symbol}: {e}")
        return None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
        if np.isnan(v) or np.isinf(v):
            return default
        return v
    except Exception:
        return default


# =====================================================================
# 가격 기반 점수 (KOSPI 제거)
# =====================================================================
def _score_from_trends(sp500_60d: float, vix: float) -> tuple[float, str]:
    sp500_score = float(np.clip((sp500_60d + 10) * 2.5, 0, 50))
    vix_score = float(np.clip(50 - (vix - 10) * 1.67, 0, 50))
    total = sp500_score + vix_score * 0.5
    total = float(np.clip(total * (100 / 75), 0, 100))
    if total >= 70:
        regime = "bull"
    elif total >= 40:
        regime = "neutral"
    else:
        regime = "bear"
    return total, regime


# =====================================================================
# 섹터 강도
# =====================================================================
SECTOR_ETFS = {
    "tech": "XLK",
    "finance": "XLF",
    "energy": "XLE",
    "healthcare": "XLV",
    "consumer_disc": "XLY",
    "industrials": "XLI",
}


def _sector_strength() -> dict:
    result = {}
    for name, sym in SECTOR_ETFS.items():
        df = _fetch_index(sym, period="3mo")
        if df is None:
            result[name] = 50.0
            continue
        ret_60d = _safe_pct_change(df["Close"], min(60, len(df) - 1))
        result[name] = float(np.clip((ret_60d + 10) * 5, 0, 100))
    return result


# =====================================================================
# 가격 기반 매크로 이벤트
# =====================================================================
def _macro_events_price_based(vix: float, sp500_60d: float) -> tuple[list, list]:
    risks: list = []
    benefits: list = []
    if vix > 25:
        risks.append("변동성확대")
    if vix < 15:
        benefits.append("저변동성")
    if sp500_60d < -5:
        risks.append("미국증시약세")
    if sp500_60d > 5:
        benefits.append("미국증시강세")
    return risks, benefits


# =====================================================================
# Colab v3.2 통합: 실시간 뉴스 → 이벤트 분석
# =====================================================================
def _fetch_realtime_news(max_articles: int = 100) -> list:
    """NewsAPI 비즈니스 헤드라인 수집"""
    if not NEWSAPI_KEY:
        log.warning("NEWSAPI_KEY 없음 - 뉴스 분석 스킵")
        return []
    try:
        url = "https://newsapi.org/v2/top-headlines"
        params = {
            "country": "us",
            "category": "business",
            "pageSize": min(max_articles, 100),
            "apiKey": NEWSAPI_KEY,
        }
        r = requests.get(url, params=params, timeout=15)
        if r.status_code != 200:
            log.warning(f"NewsAPI failed: {r.status_code}")
            return []
        data = r.json()
        if data.get("status") != "ok":
            return []
        articles = data.get("articles", [])
        result = []
        for a in articles:
            result.append({
                "title": a.get("title", "") or "",
                "description": a.get("description", "") or "",
                "url": a.get("url", "") or "",
                "source": a.get("source") or {"name": "Unknown"},  # dict 유지 (콜랩 호환)
                "publishedAt": a.get("publishedAt", "") or "",
            })
        return result
    except Exception as e:
        log.warning(f"_fetch_realtime_news failed: {e}")
        return []


def _analyze_news_via_colab(articles: list) -> tuple:
    """
    Colab v3.2 파이프라인 실행.
    콜랩 함수 시그니처 정확히 맞춤:
    - keyword_filter() -> (candidates, filtered_out_negation)
      candidates: [{"article": art, "matched_event_types": [...]}, ...]
    - interpret_news_with_gpt(client, article, matched) -> dict | None
    - aggregate_events(interpreted) -> (active_events, total_impact, rejected, conflicts, conflict_penalty)
    """
    if not articles:
        return 0.0, {}, [], []
    try:
        from engine.market.colab_v32 import (
            deduplicate_articles, keyword_filter,
            interpret_news_with_gpt, aggregate_events,
            load_llm_cache, save_llm_cache, CONFIG,
        )
        from openai import OpenAI

        if not OPENAI_API_KEY:
            log.warning("OPENAI_API_KEY 없음")
            return 0.0, {}, [], []

        client = OpenAI(api_key=OPENAI_API_KEY)
        deduped = deduplicate_articles(articles)
        candidates, neg_filtered = keyword_filter(deduped)
        if not candidates:
            log.info("키워드 매칭 후보 없음")
            return 0.0, {}, [], []

        log.info(f"실시간 뉴스 분석: 후보 {len(candidates)}건, 부정어 필터 {len(neg_filtered)}건")

        # GPT 캐시 활용
        gpt_cache = load_llm_cache()
        max_calls = CONFIG.get("MAX_LLM_CALLS_PER_RUN", 15)
        interpreted = []
        new_calls = 0

        for cand in candidates:
            article = cand["article"]
            matched = cand["matched_event_types"]
            # 캐시 키: url 우선, 없으면 title[:100]
            url = article.get("url", "")
            cache_key = url if url else article.get("title", "")[:100]

            # 캐시 히트
            if cache_key in gpt_cache:
                cached = gpt_cache[cache_key]
                interp = cached.get("interpretation")
                if interp:
                    interpreted.append({
                        "article": article,
                        "matched_event_types": matched,
                        "interpretation": interp,
                    })
                continue

            # GPT 신규 호출 (한도 체크)
            if new_calls >= max_calls:
                continue
            interp = interpret_news_with_gpt(client, article, matched)
            new_calls += 1
            if interp:
                interpreted.append({
                    "article": article,
                    "matched_event_types": matched,
                    "interpretation": interp,
                })
                # 캐시 저장
                from datetime import datetime as _dt
                gpt_cache[cache_key] = {
                    "cached_at": _dt.now().isoformat(),
                    "interpretation": interp,
                }

        # 캐시 영구 저장
        if new_calls > 0:
            save_llm_cache(gpt_cache)

        log.info(f"GPT 해석: 캐시 히트 {len(interpreted) - new_calls}건, 신규 호출 {new_calls}건")

        # 집계
        active_events, total_impact, rejected, conflicts, conflict_penalty = aggregate_events(
            interpreted, verbose=False
        )

        # risk/benefit 분리
        risks, benefits = [], []
        for ev_name, ev_data in active_events.items():
            score = ev_data.get("total_impact_score", 0.0)
            if score < 0:
                risks.append(ev_name)
            elif score > 0:
                benefits.append(ev_name)

        return float(total_impact), active_events, risks, benefits
    except Exception as e:
        log.error(f"_analyze_news_via_colab failed: {e}", exc_info=True)
        return 0.0, {}, [], []


# =====================================================================
# 메인: build_market_context
# =====================================================================
def build_market_context(force_refresh: bool = False) -> MarketContext:
    cached_ctx = None
    if MARKET_STATE_PATH.exists():
        try:
            with open(MARKET_STATE_PATH, "r", encoding="utf-8") as f:
                cached = json.load(f)
            cached_ctx = _from_dict(cached)
            if not force_refresh:
                ts = datetime.fromisoformat(cached["timestamp"])
                if datetime.now() - ts < timedelta(minutes=CACHE_TTL_MIN):
                    log.debug("market context cache hit")
                    return cached_ctx
        except Exception as e:
            log.warning(f"cache read failed: {e}")

    log.info("building fresh market context...")

    sp500_df = _fetch_index("^GSPC", "6mo")
    vix_df = _fetch_index("^VIX", "1mo")

    if sp500_df is None:
        log.warning("S&P500 데이터 실패 - 캐시 값 사용")
        sp500_60d = cached_ctx.sp500_trend_pct if cached_ctx else 0.0
    else:
        sp500_60d = _safe_pct_change(sp500_df["Close"], 60)

    if vix_df is None or vix_df.empty:
        log.warning("VIX 데이터 실패 - 캐시 값 사용")
        vix_level = cached_ctx.vix_level if cached_ctx else 18.0
    else:
        vix_level = _safe_float(vix_df["Close"].iloc[-1], 18.0)

    price_score, _ = _score_from_trends(sp500_60d, vix_level)
    sectors = _sector_strength()
    price_risks, price_benefits = _macro_events_price_based(vix_level, sp500_60d)

    articles = _fetch_realtime_news(max_articles=100)
    event_adj, active_events, news_risks, news_benefits = _analyze_news_via_colab(articles)

    final_score = float(np.clip(price_score + event_adj, 0, 100))
    if final_score >= 70:
        regime = "bull"
    elif final_score >= 40:
        regime = "neutral"
    else:
        regime = "bear"

    all_risks = list(set(price_risks + news_risks))
    all_benefits = list(set(price_benefits + news_benefits))

    ctx = MarketContext(
        timestamp=datetime.now().isoformat(),
        score=final_score,
        regime=regime,
        kospi_trend_pct=0.0,
        sp500_trend_pct=sp500_60d,
        vix_level=vix_level,
        sector_strength=sectors,
        risk_events=all_risks,
        benefit_events=all_benefits,
        buy_multiplier=1.0,
        threshold_multiplier=1.0,
        event_adjustment=event_adj,
        active_events=active_events,
        news_sentiment_avg=0.0,
    )

    MARKET_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MARKET_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(ctx.to_dict(), f, ensure_ascii=False, indent=2)
    log.info(f"market saved: score={final_score:.1f}, regime={regime}, "
             f"sp500={sp500_60d:+.2f}%, vix={vix_level:.2f}, "
             f"event_adj={event_adj:+.2f}, events={len(active_events)}")
    return ctx


def _from_dict(d: dict) -> MarketContext:
    return MarketContext(
        timestamp=d["timestamp"],
        score=_safe_float(d.get("score", 50.0), 50.0),
        regime=d.get("regime", "neutral"),
        kospi_trend_pct=_safe_float(d.get("kospi_trend_pct", 0.0), 0.0),
        sp500_trend_pct=_safe_float(d.get("sp500_trend_pct", 0.0), 0.0),
        vix_level=_safe_float(d.get("vix_level", 18.0), 18.0),
        sector_strength=d.get("sector_strength", {}),
        risk_events=d.get("risk_events", []),
        benefit_events=d.get("benefit_events", []),
        buy_multiplier=_safe_float(d.get("buy_multiplier", 1.0), 1.0),
        threshold_multiplier=_safe_float(d.get("threshold_multiplier", 1.0), 1.0),
        event_adjustment=_safe_float(d.get("event_adjustment", 0.0), 0.0),
        active_events=d.get("active_events", {}),
        news_sentiment_avg=_safe_float(d.get("news_sentiment_avg", 0.0), 0.0),
    )


def get_market_context() -> MarketContext:
    return build_market_context(force_refresh=False)


# =====================================================================
# 과거 시장 시계열 빌더 (백테스트용)
# =====================================================================
def _market_history_cache_path():
    return config.system_dir() / "market_history.csv"


def _market_history_v2_path():
    return config.system_dir() / "market_history_v2.csv"


def build_market_history(years: int = 6, force_refresh: bool = False) -> pd.DataFrame:
    """가격 기반 시계열 + v2 이벤트 컬럼 머지"""
    cache_path = _market_history_cache_path()

    if not force_refresh and cache_path.exists():
        try:
            df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
            last_date = pd.Timestamp(df.index[-1]).normalize()
            today = pd.Timestamp.now().normalize()
            if (today - last_date).days <= 1:
                log.info(f"market_history cache: {len(df)} rows")
                return _merge_v2_events(df)
        except Exception as e:
            log.warning(f"market_history cache read failed: {e}")

    log.info(f"building market_history ({years}y)...")
    period = f"{years}y"

    sp500 = _fetch_index("^GSPC", period=period)
    vix = _fetch_index("^VIX", period=period)
    sectors_etf = {
        "tech": _fetch_index("XLK", period=period),
        "finance": _fetch_index("XLF", period=period),
        "energy": _fetch_index("XLE", period=period),
        "healthcare": _fetch_index("XLV", period=period),
        "consumer": _fetch_index("XLY", period=period),
        "industrials": _fetch_index("XLI", period=period),
    }

    if sp500 is None or vix is None:
        raise RuntimeError("failed to fetch index data")

    idx = sp500.index
    records = []
    for d in idx:
        sp500_slice = sp500.loc[:d]["Close"]
        if d > vix.index[-1]:
            vix_slice = vix["Close"]
        else:
            vix_slice = vix.loc[:d]["Close"]
        if len(vix_slice) == 0:
            continue

        sp500_60d = _safe_pct_change(sp500_slice, 60)
        vix_level = _safe_float(vix_slice.iloc[-1], 18.0)
        score, regime = _score_from_trends(sp500_60d, vix_level)

        rec = {
            "date": d,
            "score": score,
            "regime": regime,
            "kospi_60d": 0.0,
            "sp500_60d": sp500_60d,
            "vix": vix_level,
        }
        for name, etf_df in sectors_etf.items():
            if etf_df is None:
                rec[f"sector_{name}"] = 50.0
                continue
            try:
                etf_slice = etf_df.loc[:d]["Close"]
                if len(etf_slice) < 60:
                    rec[f"sector_{name}"] = 50.0
                    continue
                trend = _safe_pct_change(etf_slice, 60)
                rec[f"sector_{name}"] = float(max(0, min(100, 50 + trend * 5)))
            except Exception:
                rec[f"sector_{name}"] = 50.0
        records.append(rec)

    df = pd.DataFrame(records).set_index("date")
    df.index = pd.to_datetime(df.index)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_path)
    log.info(f"market_history built: {len(df)} rows, cached at {cache_path}")

    return _merge_v2_events(df)


def _merge_v2_events(df: pd.DataFrame) -> pd.DataFrame:
    """market_history_v2.csv 이벤트 컬럼 머지"""
    v2_path = _market_history_v2_path()
    if not v2_path.exists():
        return df
    try:
        v2 = pd.read_csv(v2_path, parse_dates=["date"]).set_index("date")
        event_cols = [c for c in v2.columns if c.startswith("has_") or c in (
            "event_adjustment", "active_events_count", "av_sentiment_avg",
            "av_sentiment_std", "av_bullish_ratio", "av_bearish_ratio",
        )]
        if not event_cols:
            return df
        merged = df.join(v2[event_cols], how="left")
        for c in event_cols:
            if c.startswith("has_"):
                merged[c] = merged[c].fillna(0).astype(int)
            else:
                merged[c] = merged[c].fillna(0.0)
        if "event_adjustment" in merged.columns:
            merged["score_with_events"] = (merged["score"] + merged["event_adjustment"]).clip(0, 100)
        log.info(f"v2 이벤트 컬럼 {len(event_cols)}개 머지 완료")
        return merged
    except Exception as e:
        log.warning(f"v2 머지 실패: {e}")
        return df


def get_market_history(years: int = 6) -> pd.DataFrame:
    return build_market_history(years=years, force_refresh=False)


def lookup_market_at(history_df, date) -> dict:
    """특정 날짜 forward-fill 룩업"""
    if history_df is None or len(history_df) == 0:
        return {
            "score": 50.0, "vix": 18.0,
            "sector_tech": 50.0, "sector_finance": 50.0,
            "sector_energy": 50.0, "sector_healthcare": 50.0,
            "sector_consumer": 50.0, "sector_industrials": 50.0,
            "event_adjustment": 0.0,
        }
    ts = pd.Timestamp(date)
    idx_arr = history_df.index
    pos = idx_arr.searchsorted(ts, side="right") - 1
    if pos < 0:
        pos = 0
    if pos >= len(history_df):
        pos = len(history_df) - 1
    return history_df.iloc[pos].to_dict()


# =====================================================================
# 단독 실행 테스트
# =====================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("시장 컨텍스트 분석 (v2 - Colab v3.2 통합)")
    print("=" * 60)
    ctx = build_market_context(force_refresh=True)
    print()
    print(f"시장 점수:    {ctx.score:.1f}/100 ({ctx.regime})")
    print(f"S&P500 60일:  {ctx.sp500_trend_pct:+.2f}%")
    print(f"VIX:          {ctx.vix_level:.2f}")
    print(f"이벤트 보정:  {ctx.event_adjustment:+.2f}")
    print()
    print("섹터 강도:")
    for k, v in ctx.sector_strength.items():
        bar = "█" * int(v / 5)
        print(f"  {k:14} {v:5.1f}  {bar}")
    print()
    print(f"위험 이벤트:  {ctx.risk_events}")
    print(f"호재 이벤트:  {ctx.benefit_events}")
    print(f"활성 이벤트:  {list(ctx.active_events.keys())}")
    print()
    print(f"저장 위치:    {MARKET_STATE_PATH}")
