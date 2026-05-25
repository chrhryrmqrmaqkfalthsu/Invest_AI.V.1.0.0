"""
시장 컨텍스트 분석 모듈
- KOSPI/S&P500 추세 → 시장 점수 0~100
- 섹터 강도 (yfinance 섹터 ETF)
- VIX 변동성, 매크로 이벤트
- data/_system/market_state.json에 캐시 (60분)
"""
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from engine.core.config import config
from engine.core.logger import get_logger

log = get_logger("market_context")


MARKET_STATE_PATH = config.system_dir() / "market_state.json"
CACHE_TTL_MIN = config.get("cycle.market_context_cache_min", 60)


@dataclass
class MarketContext:
    timestamp: str
    score: float                       # 0~100
    regime: str                        # 'bull' | 'neutral' | 'bear'
    kospi_trend_pct: float
    sp500_trend_pct: float
    vix_level: float
    sector_strength: dict              # {'tech': 95.2, 'finance': 60.1, ...}
    risk_events: list[str]             # ['금리인상', '변동성확대']
    benefit_events: list[str]          # ['금리인하']
    buy_multiplier: float              # 신호 점수 보정 (0.7~1.3)
    threshold_multiplier: float        # 임계값 보정 (역수)

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
        }


# ---------- 지표 다운로드 헬퍼 ----------
def _safe_pct_change(series: pd.Series, days: int) -> float:
    if len(series) < days + 1:
        return 0.0
    try:
        return float((series.iloc[-1] / series.iloc[-days - 1] - 1) * 100)
    except Exception:
        return 0.0


def _fetch_index(symbol: str, period: str = "6mo") -> Optional[pd.DataFrame]:
    try:
        df = yf.download(symbol, period=period, progress=False, auto_adjust=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df if df is not None and not df.empty else None
    except Exception as e:
        log.warning(f"_fetch_index failed {symbol}: {e}")
        return None


# ---------- 시장 점수 계산 ----------
def _score_from_trends(kospi_60d: float, sp500_60d: float, vix: float) -> tuple[float, str]:
    """
    트렌드와 VIX로 시장 점수 (0~100) 계산.
    - 60일 수익률 양수 + 낮은 VIX → 강세
    - 음수 + 높은 VIX → 약세
    """
    # 트렌드 점수 (각 -10% ~ +10% → 0~50)
    kospi_score = np.clip((kospi_60d + 10) * 2.5, 0, 50)
    sp500_score = np.clip((sp500_60d + 10) * 2.5, 0, 50)
    trend_score = kospi_score * 0.4 + sp500_score * 0.6   # 미국 비중↑

    # VIX 점수 (낮을수록 높은 점수, VIX 10 → 50, VIX 40 → 0)
    vix_score = np.clip(50 - (vix - 10) * 1.67, 0, 50)

    total = trend_score + vix_score * 0.5  # 0~75
    total = np.clip(total * (100 / 75), 0, 100)

    if total >= 70:
        regime = "bull"
    elif total >= 40:
        regime = "neutral"
    else:
        regime = "bear"
    return float(total), regime


# ---------- 섹터 강도 ----------
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
        # -10% ~ +10% → 0~100
        result[name] = float(np.clip((ret_60d + 10) * 5, 0, 100))
    return result


# ---------- 매크로 이벤트 (간이) ----------
def _macro_events(vix: float, kospi_60d: float, sp500_60d: float) -> tuple[list, list]:
    risks: list[str] = []
    benefits: list[str] = []

    if vix > 25:
        risks.append("변동성확대")
    if vix < 15:
        benefits.append("저변동성")

    if sp500_60d < -5:
        risks.append("미국증시약세")
    if sp500_60d > 5:
        benefits.append("미국증시강세")

    if kospi_60d < -5:
        risks.append("국내증시약세")
    if kospi_60d > 5:
        benefits.append("국내증시강세")

    # 금리 이벤트는 정확한 데이터 소스 필요 → 일단 플레이스홀더
    # TODO: FRED API로 연방기금금리 변동 감지 (Step 8)
    return risks, benefits


# ---------- 메인 함수 ----------
def build_market_context(force_refresh: bool = False) -> MarketContext:
    """캐시된 시장 컨텍스트를 반환. 만료 시 재계산."""
    if not force_refresh and MARKET_STATE_PATH.exists():
        try:
            with open(MARKET_STATE_PATH, "r", encoding="utf-8") as f:
                cached = json.load(f)
            ts = datetime.fromisoformat(cached["timestamp"])
            if datetime.now() - ts < timedelta(minutes=CACHE_TTL_MIN):
                log.debug("market context cache hit")
                return _from_dict(cached)
        except Exception as e:
            log.warning(f"cache read failed: {e}")

    log.info("building fresh market context...")

    # 1) KOSPI / S&P500 / VIX 다운로드
    kospi_df = _fetch_index("^KS11", "6mo")
    sp500_df = _fetch_index("^GSPC", "6mo")
    vix_df = _fetch_index("^VIX", "1mo")

    kospi_60d = _safe_pct_change(kospi_df["Close"], 60) if kospi_df is not None else 0.0
    sp500_60d = _safe_pct_change(sp500_df["Close"], 60) if sp500_df is not None else 0.0
    vix_level = float(vix_df["Close"].iloc[-1]) if vix_df is not None else 18.0

    # 2) 점수
    score, regime = _score_from_trends(kospi_60d, sp500_60d, vix_level)

    # 3) 섹터
    sectors = _sector_strength()

    # 4) 이벤트
    risks, benefits = _macro_events(vix_level, kospi_60d, sp500_60d)

    # 5) 보정 배수
    buy_mult = float(np.interp(score, [0, 30, 50, 70, 100], [0.6, 0.8, 1.0, 1.2, 1.4]))
    threshold_mult = 1.0 / buy_mult

    ctx = MarketContext(
        timestamp=datetime.now().isoformat(),
        score=score,
        regime=regime,
        kospi_trend_pct=kospi_60d,
        sp500_trend_pct=sp500_60d,
        vix_level=vix_level,
        sector_strength=sectors,
        risk_events=risks,
        benefit_events=benefits,
        buy_multiplier=buy_mult,
        threshold_multiplier=threshold_mult,
    )

    # 저장
    MARKET_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MARKET_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(ctx.to_dict(), f, ensure_ascii=False, indent=2)
    log.info(f"market context saved: score={score:.1f}, regime={regime}")
    return ctx


def _from_dict(d: dict) -> MarketContext:
    return MarketContext(
        timestamp=d["timestamp"],
        score=d["score"],
        regime=d["regime"],
        kospi_trend_pct=d["kospi_trend_pct"],
        sp500_trend_pct=d["sp500_trend_pct"],
        vix_level=d["vix_level"],
        sector_strength=d["sector_strength"],
        risk_events=d["risk_events"],
        benefit_events=d["benefit_events"],
        buy_multiplier=d["buy_multiplier"],
        threshold_multiplier=d["threshold_multiplier"],
    )


def get_market_context() -> MarketContext:
    """캐시 우선 반환 (편의 함수)"""
    return build_market_context(force_refresh=False)


if __name__ == "__main__":
    print("=" * 50)
    print("시장 컨텍스트 분석 시작 (수십 초 소요)")
    print("=" * 50)
    ctx = build_market_context(force_refresh=True)
    print()
    print(f"시장 점수:    {ctx.score:.1f}/100 ({ctx.regime})")
    print(f"KOSPI 60일:   {ctx.kospi_trend_pct:+.2f}%")
    print(f"S&P500 60일:  {ctx.sp500_trend_pct:+.2f}%")
    print(f"VIX:          {ctx.vix_level:.2f}")
    print()
    print("섹터 강도:")
    for k, v in ctx.sector_strength.items():
        bar = "█" * int(v / 5)
        print(f"  {k:14} {v:5.1f}  {bar}")
    print()
    print(f"위험 이벤트:  {ctx.risk_events}")
    print(f"호재 이벤트:  {ctx.benefit_events}")
    print(f"매수 배수:    ×{ctx.buy_multiplier:.3f}")
    print(f"임계값 배수:  ×{ctx.threshold_multiplier:.3f}")
    print()
    print(f"저장 위치:    {MARKET_STATE_PATH}")
