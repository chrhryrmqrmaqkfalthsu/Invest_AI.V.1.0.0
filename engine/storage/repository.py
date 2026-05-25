"""
파일 시스템 저장소
- 종목 레지스트리 (data/_system/symbols.json)
- 룰북 / 백테스트 결과 저장 및 로드
- 시드 패턴 관리
- 실전 거래 기록 (CSV)
"""
import csv
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from engine.core.config import config
from engine.core.logger import get_logger
from engine.strategies.rulebook import Rulebook

log = get_logger("storage")


# ---------- 경로 헬퍼 ----------
def symbols_registry_path() -> Path:
    return config.system_dir() / "symbols.json"


def seed_patterns_path() -> Path:
    return config.system_dir() / "seed_patterns.json"


def parameters_path(ticker: str) -> Path:
    return config.symbol_dir(ticker) / "parameters.json"


def backtest_path(ticker: str) -> Path:
    return config.symbol_dir(ticker) / "backtest.json"


def fitness_history_path(ticker: str) -> Path:
    return config.symbol_dir(ticker) / "fitness_history.csv"


def trades_live_path(ticker: str) -> Path:
    return config.symbol_dir(ticker) / "trades_live.csv"


# ---------- 종목 레지스트리 ----------
def list_symbols() -> list[dict]:
    p = symbols_registry_path()
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("symbols", [])
    except Exception as e:
        log.warning(f"list_symbols failed: {e}")
        return []


def add_symbol(ticker: str, meta: dict) -> None:
    symbols = list_symbols()
    # 중복 제거
    symbols = [s for s in symbols if s.get("ticker") != ticker]
    record = {
        "ticker": ticker,
        "added_at": datetime.now().isoformat(),
        **meta,
    }
    symbols.append(record)
    _save_symbols(symbols)
    log.info(f"symbol added: {ticker}")


def remove_symbol(ticker: str) -> bool:
    symbols = list_symbols()
    new_symbols = [s for s in symbols if s.get("ticker") != ticker]
    if len(new_symbols) == len(symbols):
        return False
    _save_symbols(new_symbols)
    log.info(f"symbol removed: {ticker}")
    return True


def _save_symbols(symbols: list[dict]) -> None:
    payload = {
        "updated_at": datetime.now().isoformat(),
        "symbols": symbols,
    }
    p = symbols_registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(p)  # atomic write


# ---------- 룰북 저장/로드 ----------
def save_rulebook(rb: Rulebook, meta: Optional[dict] = None) -> Path:
    p = parameters_path(rb.ticker)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": datetime.now().isoformat(),
        "version": rb.version,
        "asset_meta": meta or {},
        "rulebook": rb.to_dict(),
    }
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(p)
    log.info(f"rulebook saved: {p}")
    return p


def load_rulebook(ticker: str) -> Optional[Rulebook]:
    p = parameters_path(ticker)
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return Rulebook.from_dict(data.get("rulebook", {}))
    except Exception as e:
        log.warning(f"load_rulebook failed for {ticker}: {e}")
        return None


# ---------- 백테스트 결과 ----------
def save_backtest(ticker: str, result_dict: dict) -> Path:
    p = backtest_path(ticker)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": datetime.now().isoformat(),
        "result": result_dict,
    }
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(p)
    log.info(f"backtest saved: {p}")
    return p


def load_backtest(ticker: str) -> Optional[dict]:
    p = backtest_path(ticker)
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f).get("result")
    except Exception as e:
        log.warning(f"load_backtest failed for {ticker}: {e}")
        return None


# ---------- Fitness 히스토리 (GA 추이) ----------
def save_fitness_history(ticker: str, history: list) -> Path:
    """history: list of (gen, best, avg)"""
    p = fitness_history_path(ticker)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["generation", "best_fitness", "avg_fitness", "timestamp"])
        for gen, best, avg in history:
            w.writerow([gen, best, avg, datetime.now().isoformat()])
    log.info(f"fitness history saved: {p}")
    return p


# ---------- 시드 패턴 ----------
def load_seed_rulebooks(top_n: int = 5) -> list[Rulebook]:
    """과거 우수 룰북들 (다른 종목 학습 시 시드로 사용)"""
    p = seed_patterns_path()
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("patterns", [])
        # fitness 내림차순 정렬 후 top_n
        items.sort(key=lambda x: x.get("fitness", 0), reverse=True)
        return [Rulebook.from_dict(it.get("rulebook", {})) for it in items[:top_n]]
    except Exception as e:
        log.warning(f"load_seed_rulebooks failed: {e}")
        return []


def add_seed_rulebook(rb: Rulebook, min_fitness: float = 30.0) -> bool:
    """학습 결과가 충분히 좋으면 시드 풀에 추가"""
    if rb.fitness < min_fitness:
        return False
    p = seed_patterns_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"patterns": []}
    data["patterns"].append({
        "added_at": datetime.now().isoformat(),
        "source_ticker": rb.ticker,
        "fitness": rb.fitness,
        "rulebook": rb.to_dict(),
    })
    # 최대 50개 유지 (fitness 상위)
    data["patterns"].sort(key=lambda x: x.get("fitness", 0), reverse=True)
    data["patterns"] = data["patterns"][:50]
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(p)
    log.info(f"seed rulebook added (fitness={rb.fitness:.2f}, source={rb.ticker})")
    return True


# ---------- 실전 거래 기록 ----------
TRADE_CSV_COLUMNS = [
    "trade_id", "ticker", "side", "entry_date", "entry_price", "entry_shares",
    "exit_date", "exit_price", "exit_reason", "total_shares", "avg_cost",
    "pnl_pct", "pnl_krw", "commission", "add_buy_count", "notes",
]


def append_live_trade(ticker: str, trade_record: dict) -> None:
    p = trades_live_path(ticker)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_header = not p.exists()
    with open(p, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TRADE_CSV_COLUMNS, extrasaction="ignore")
        if write_header:
            w.writeheader()
        w.writerow(trade_record)
    log.info(f"live trade appended: {p}")


def read_live_trades(ticker: str) -> list[dict]:
    p = trades_live_path(ticker)
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------- 백업 ----------
def backup_symbol(ticker: str) -> Optional[Path]:
    """data/symbols/<ticker>를 backups/로 복사"""
    src = config.symbol_dir(ticker)
    if not src.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = config.backups_dir() / f"{ticker}_{ts}"
    shutil.copytree(src, dst)
    log.info(f"backup created: {dst}")
    return dst


if __name__ == "__main__":
    from engine.strategies.rulebook import default_rulebook

    print("=" * 60)
    print("storage 테스트")
    print("=" * 60)

    # 1) 종목 등록
    add_symbol("TEST123", {"name": "테스트종목", "direction": "long"})
    add_symbol("TEST456", {"name": "테스트2", "direction": "short"})
    print(f"\n등록 종목: {[s['ticker'] for s in list_symbols()]}")

    # 2) 룰북 저장/로드
    rb = default_rulebook("TEST123", "korean_etf", "long")
    rb.signal_threshold = 2.7
    rb.fitness = 45.5
    save_rulebook(rb, {"name": "테스트종목"})
    loaded = load_rulebook("TEST123")
    print(f"\n룰북 저장/로드: threshold={loaded.signal_threshold}, fitness={loaded.fitness}")

    # 3) 시드 패턴
    add_seed_rulebook(rb, min_fitness=30)
    seeds = load_seed_rulebooks(top_n=3)
    print(f"\n시드 패턴 {len(seeds)}개 (fitness 상위)")

    # 4) Fitness 히스토리
    save_fitness_history("TEST123", [(1, 30.0, 20.0), (2, 35.5, 25.1), (3, 40.0, 28.0)])
    print(f"\nFitness 히스토리 저장 완료")

    # 5) 백테스트 저장
    save_backtest("TEST123", {"trade_count": 23, "win_rate": 65.2, "fitness": 49.05})

    # 6) 실전 거래 기록
    append_live_trade("TEST123", {
        "trade_id": "T001", "ticker": "TEST123", "side": "BUY",
        "entry_date": "2026-05-25", "entry_price": 25600, "entry_shares": 4,
    })
    print(f"\n실전 거래 {len(read_live_trades('TEST123'))}건")

    # 7) 정리 (테스트 종목 제거)
    remove_symbol("TEST123")
    remove_symbol("TEST456")
    print(f"\n정리 후 종목: {[s['ticker'] for s in list_symbols()]}")

    print(f"\n✅ 모든 저장소 기능 정상")
