"""
어댑터 팩토리
- 티커 → 적합한 AssetAdapter 자동 선택
- 캐시로 중복 생성 방지
"""
from typing import Optional

from engine.adapters.base import (
    AssetAdapter,
    detect_asset_type,
    is_korean_etf,
)
from engine.adapters.korean_etf import KoreanETFAdapter
from engine.adapters.korean_stock import KoreanStockAdapter
from engine.adapters.us_etf import USETFAdapter
from engine.adapters.us_stock import USStockAdapter
from engine.core.logger import get_logger

log = get_logger("adapter_factory")


# 미국 ETF 알려진 티커 (개별주와 구분용)
US_ETF_TICKERS = {
    "SPY", "QQQ", "VOO", "IVV", "VTI", "DIA", "IWM", "EFA", "EEM",
    "GLD", "SLV", "USO", "TLT", "IEF", "HYG", "LQD",
    "XLF", "XLK", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE",
    "SH", "SDS", "SPXU", "SQQQ", "PSQ", "QID", "DOG", "DXD",
    "TZA", "TWM", "RWM", "SOXS", "TECS",
    "ARKK", "ARKG", "ARKW", "SMH", "SOXX",
    "VEA", "VWO", "BND", "AGG",
}


# 어댑터 캐시
_ADAPTER_CACHE: dict[str, AssetAdapter] = {}


def is_us_etf(ticker: str) -> bool:
    return ticker.upper() in US_ETF_TICKERS


def get_adapter(ticker: str, force_type: Optional[str] = None) -> AssetAdapter:
    """
    티커를 받아 적합한 어댑터 인스턴스를 반환.

    Args:
        ticker: '379800', 'AAPL' 등
        force_type: 'korean_etf' | 'korean_stock' | 'us_etf' | 'us_stock' 강제 지정

    Returns:
        AssetAdapter 인스턴스
    """
    cache_key = f"{ticker}_{force_type or 'auto'}"
    if cache_key in _ADAPTER_CACHE:
        return _ADAPTER_CACHE[cache_key]

    if force_type:
        adapter = _create_by_type(ticker, force_type)
    else:
        adapter = _auto_detect(ticker)

    _ADAPTER_CACHE[cache_key] = adapter
    log.info(f"adapter created: {ticker} → {adapter.__class__.__name__}")
    return adapter


def _create_by_type(ticker: str, asset_type: str) -> AssetAdapter:
    mapping = {
        "korean_etf": KoreanETFAdapter,
        "korean_stock": KoreanStockAdapter,
        "us_etf": USETFAdapter,
        "us_stock": USStockAdapter,
    }
    cls = mapping.get(asset_type)
    if cls is None:
        raise ValueError(f"unknown asset_type: {asset_type}")
    return cls(ticker)


def _auto_detect(ticker: str) -> AssetAdapter:
    region = detect_asset_type(ticker)  # 'korean' or 'us'
    if region == "korean":
        if is_korean_etf(ticker):
            return KoreanETFAdapter(ticker)
        return KoreanStockAdapter(ticker)
    else:
        if is_us_etf(ticker):
            return USETFAdapter(ticker)
        return USStockAdapter(ticker)


def clear_cache() -> None:
    _ADAPTER_CACHE.clear()


if __name__ == "__main__":
    print("=" * 50)
    print("Adapter Factory 테스트")
    print("=" * 50)

    test_tickers = [
        "379800",   # 한국 ETF
        "360750",   # 한국 ETF
        "225030",   # 한국 ETF (인버스)
        "005930",   # 한국 개별주 (삼성전자)
        "035420",   # 한국 개별주 (NAVER)
        "SPY",      # 미국 ETF
        "QQQ",      # 미국 ETF
        "SH",       # 미국 ETF (인버스)
        "AAPL",     # 미국 개별주
        "NVDA",     # 미국 개별주
    ]

    for tk in test_tickers:
        try:
            a = get_adapter(tk)
            m = a.meta
            print(f"  {tk:8} → {a.__class__.__name__:20} | {m.name[:30]:30} | {m.direction}")
        except Exception as e:
            print(f"  {tk:8} → ERROR: {e}")
