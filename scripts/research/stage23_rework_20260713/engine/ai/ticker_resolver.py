"""
종목명 ↔ 코드 해석기 (Phase G-1)
================================
사용자 자연어 → 정확한 ticker 코드 변환.

데이터 소스:
  - 한국: FinanceDataReader (StockListing 'KRX' + 'ETF/KR')
  - 미국: yfinance (이미 us_etf.py / us_stock.py 에서 사용)

캐시:
  - data/_system/ticker_map.json (TTL 7일)
  - 한국 시장 전체 ~4000개 종목 메모리 캐시
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("ticker_resolver")

CACHE_PATH = Path("data/_system/ticker_map.json")
CACHE_TTL_DAYS = 7

# 메모리 캐시 (한 번 로드하면 프로세스 종료까지 유지)
_KR_MAP: Optional[dict] = None  # {"by_code": {code: {name, market, type}}, "by_name_norm": {norm: [code,...]}}
_LAST_LOAD_TS: float = 0.0



# 한글 ↔ 영문 운용사/시리즈명 동의어 (검색 시 양방향 변환)
NAME_ALIASES = {
    "코덱스": "kodex",
    "타이거": "tiger",
    "아리랑": "arirang",
    "키움": "kiwoom",
    "한투": "hanaro",
    "하나로": "hanaro",
    "코세프": "kosef",
    "에이스": "ace",
    "히어로": "hero",
    "마이티": "mighty",
    "마이다스": "midas",
    "플러스": "plus",
    "솔": "sol",
    "에스비아이": "sbi",
    "삼바": "samba",
    "스마트": "smart",
    "파워": "power",
    "트루": "tru",
    "글로벌엑스": "globalx",
    "리얼티": "realty",
    "나스닥": "nasdaq",
    "에스앤피": "sp",
    "에스앤피500": "sp500",
    "s&p": "sp",
    "s&p500": "sp500",
}


def _apply_aliases(s: str) -> str:
    """한글 별칭 → 영문 변환. 양방향 검색 가능하게."""
    for ko, en in NAME_ALIASES.items():
        s = s.replace(ko, en)
    return s

def _normalize_name(name: str) -> str:
    """이름 정규화: 공백/특수문자 제거, 소문자화, 한영 별칭 적용. 매칭용."""
    if not name:
        return ""
    s = name.lower()
    s = _apply_aliases(s)
    # 한글/영문/숫자만 남김
    s = re.sub(r"[^\w가-힣]", "", s)
    return s


def _cache_fresh() -> bool:
    if not CACHE_PATH.exists():
        return False
    age = time.time() - CACHE_PATH.stat().st_mtime
    return age < CACHE_TTL_DAYS * 86400


def _build_kr_map() -> dict:
    """FinanceDataReader로 한국 전체 종목 받아 매핑 빌드."""
    import FinanceDataReader as fdr

    by_code: dict[str, dict] = {}
    by_name_norm: dict[str, list[str]] = {}

    # 일반 주식 (KOSPI + KOSDAQ + KONEX)
    try:
        df = fdr.StockListing("KRX")
        log.info(f"KRX 일반종목 로드: {len(df)}개")
        for _, row in df.iterrows():
            code = str(row.get("Code", "")).strip()
            name = str(row.get("Name", "")).strip()
            market = str(row.get("Market", "")).strip()
            if not code or not name or len(code) != 6:
                continue
            by_code[code] = {"name": name, "market": market, "type": "stock"}
            norm = _normalize_name(name)
            if norm:
                by_name_norm.setdefault(norm, []).append(code)
    except Exception as e:
        log.warning(f"KRX 일반종목 로드 실패: {e}")

    # ETF
    try:
        etfs = fdr.StockListing("ETF/KR")
        log.info(f"ETF 로드: {len(etfs)}개")
        for _, row in etfs.iterrows():
            code = str(row.get("Symbol", "")).strip()
            name = str(row.get("Name", "")).strip()
            if not code or not name or len(code) != 6:
                continue
            # ETF가 일반종목 매핑 덮어쓰기 (ETF가 우선)
            by_code[code] = {"name": name, "market": "ETF", "type": "etf"}
            norm = _normalize_name(name)
            if norm:
                by_name_norm.setdefault(norm, []).append(code)
    except Exception as e:
        log.warning(f"ETF 로드 실패: {e}")

    return {
        "by_code": by_code,
        "by_name_norm": by_name_norm,
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(by_code),
    }


def _load_kr_map(force_rebuild: bool = False) -> dict:
    """캐시에서 로드 or 새로 빌드."""
    global _KR_MAP, _LAST_LOAD_TS

    # 메모리 캐시 (1시간 내 재호출 시 그대로)
    if _KR_MAP is not None and (time.time() - _LAST_LOAD_TS) < 3600 and not force_rebuild:
        return _KR_MAP

    # 디스크 캐시
    if not force_rebuild and _cache_fresh():
        try:
            with CACHE_PATH.open("r", encoding="utf-8") as f:
                _KR_MAP = json.load(f)
            _LAST_LOAD_TS = time.time()
            log.info(f"종목 매핑 캐시에서 로드: {_KR_MAP.get('total', 0)}개")
            return _KR_MAP
        except Exception as e:
            log.warning(f"캐시 로드 실패, 재빌드: {e}")

    # 새로 빌드
    log.info("종목 매핑 빌드 시작 (FinanceDataReader)")
    _KR_MAP = _build_kr_map()
    _LAST_LOAD_TS = time.time()

    # 디스크 저장
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CACHE_PATH.open("w", encoding="utf-8") as f:
            json.dump(_KR_MAP, f, ensure_ascii=False, indent=2)
        log.info(f"종목 매핑 캐시 저장: {CACHE_PATH} ({_KR_MAP.get('total', 0)}개)")
    except Exception as e:
        log.warning(f"캐시 저장 실패: {e}")

    return _KR_MAP


# ============================================================
# Public API
# ============================================================

def get_ticker_name(ticker: str) -> str:
    """코드 → 이름. 못 찾으면 'ticker' 그대로 반환."""
    ticker = str(ticker).strip().upper()

    # 한국 (6자리 숫자)
    if len(ticker) == 6 and ticker.isdigit():
        try:
            m = _load_kr_map()
            info = m["by_code"].get(ticker)
            if info:
                return info["name"]
        except Exception as e:
            log.warning(f"한국 종목명 조회 실패: {e}")
        # FDR 매핑에 없으면 ticker 그대로 반환 (pykrx 폴백 제거: 로그 노이즈 회피)
        return ticker

    # 미국 (알파벳)
    if ticker.isalpha():
        try:
            from engine.adapters.us_etf import _yf_get_name
            return _yf_get_name(ticker)
        except Exception:
            return ticker

    return ticker


def format_ticker(ticker: str) -> str:
    """'KODEX 200 (069500)' 형식. 알림용."""
    name = get_ticker_name(ticker)
    if name == ticker or not name:
        return ticker
    return f"{name} ({ticker})"


def resolve_ticker(query: str, limit: int = 5) -> dict:
    """
    이름 또는 코드 → 후보 리스트.

    Returns:
      {
        "exact": [{"code", "name", "market", "type"}],   # 정확 일치 (보통 0~1개)
        "partial": [...],                                  # 부분 일치 (최대 limit개)
        "query": "원본 쿼리",
      }
    """
    q = (query or "").strip()
    if not q:
        return {"exact": [], "partial": [], "query": q, "error": "빈 쿼리"}

    # 1) 6자리 숫자면 코드로 직접 조회
    if len(q) == 6 and q.isdigit():
        try:
            m = _load_kr_map()
            info = m["by_code"].get(q)
            if info:
                return {
                    "exact": [{"code": q, "name": info["name"], "market": info["market"], "type": info["type"]}],
                    "partial": [],
                    "query": q,
                }
        except Exception as e:
            log.warning(f"코드 조회 실패: {e}")
        return {"exact": [], "partial": [], "query": q, "error": "해당 코드 없음"}

    # 2) 미국 티커 (ASCII 알파벳만, 1~5자)
    if q.replace(".", "").isascii() and q.replace(".", "").isalpha() and len(q) <= 5:
        try:
            from engine.adapters.us_etf import _yf_get_name
            name = _yf_get_name(q.upper())
            if name and name != q.upper():
                # ETF인지 stock인지 판별
                from engine.adapters.factory import is_us_etf
                t = "etf" if is_us_etf(q.upper()) else "stock"
                return {
                    "exact": [{"code": q.upper(), "name": name, "market": "US", "type": t}],
                    "partial": [],
                    "query": q,
                }
        except Exception as e:
            log.warning(f"미국 티커 조회 실패: {e}")

    # 3) 한국 이름 검색 (fuzzy)
    try:
        m = _load_kr_map()
    except Exception as e:
        return {"exact": [], "partial": [], "query": q, "error": f"매핑 로드 실패: {e}"}

    norm_q = _normalize_name(q)
    if not norm_q:
        return {"exact": [], "partial": [], "query": q, "error": "정규화 후 빈 문자열"}

    exact: list[dict] = []
    partial: list[dict] = []

    by_name_norm = m.get("by_name_norm", {})
    by_code = m.get("by_code", {})

    # 정확 일치
    if norm_q in by_name_norm:
        for code in by_name_norm[norm_q]:
            info = by_code.get(code, {})
            exact.append({
                "code": code,
                "name": info.get("name", ""),
                "market": info.get("market", ""),
                "type": info.get("type", ""),
            })

    # 부분 일치 (정확 일치 제외)
    exact_codes = {e["code"] for e in exact}
    for norm_name, codes in by_name_norm.items():
        if norm_q in norm_name or norm_name in norm_q:
            for code in codes:
                if code in exact_codes:
                    continue
                if len(partial) >= limit:
                    break
                info = by_code.get(code, {})
                partial.append({
                    "code": code,
                    "name": info.get("name", ""),
                    "market": info.get("market", ""),
                    "type": info.get("type", ""),
                })
        if len(partial) >= limit:
            break

    # ETF 우선 정렬 (같은 점수면 ETF가 위로)
    partial.sort(key=lambda x: (0 if x["type"] == "etf" else 1, x["name"]))

    return {"exact": exact, "partial": partial, "query": q}


def refresh_cache() -> dict:
    """캐시 강제 재빌드. 관리 명령용."""
    m = _load_kr_map(force_rebuild=True)
    return {
        "total": m.get("total", 0),
        "built_at": m.get("built_at", ""),
    }


if __name__ == "__main__":
    # 간단 테스트
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    print("=== format_ticker ===")
    for t in ["069500", "379800", "005930", "SPY", "AAPL", "999999"]:
        print(f"  {t} -> {format_ticker(t)}")

    print("\n=== resolve_ticker ===")
    for q in ["코덱스200", "KODEX 200", "삼성전자", "kodex", "tiger 미국", "069500", "SPY", "없는종목xyz"]:
        r = resolve_ticker(q, limit=5)
        print(f"\n  query: '{q}'")
        if r.get("error"):
            print(f"    error: {r['error']}")
        for e in r["exact"]:
            print(f"    [정확] {e['name']} ({e['code']}) - {e['type']}")
        for p in r["partial"][:3]:
            print(f"    [부분] {p['name']} ({p['code']}) - {p['type']}")
