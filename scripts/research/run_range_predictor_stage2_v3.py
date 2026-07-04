#!/usr/bin/env python3
"""
Sequential Stage2-style next-day high/low range predictor GA with quantile-band rules.

구조:
- train_1 survivor만 train_2 seed population으로 전달
- train_2 survivor만 train_3 seed population으로 전달
- 최종 survivor를 stress / OOS에서 검증

규칙:
- 단일 threshold가 아니라 feature 분위수 band(q_low~q_high)를 진화시킨다.
- softness로 band 근처 값을 느슨하게 인정한다.
- D-1 피처는 스윙 진입식으로 더 촘촘히 만들고, D-2~D-5는 비교적 기본 피처만 둔다.
- 호가/미체결/수급 컬럼이 OHLCV 캐시에 있으면 자동으로 feature에 포함한다.

Read/write scope:
- OHLCV/cache/news csv는 read-only로 읽는다.
- 결과는 지정 out_dir 아래 연구 산출물만 생성한다.
- run_live, 실거래, 캐시 갱신 없음.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import random
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

POPULATION = 100
GENERATIONS = 50
PATIENCE = 15
ELITE_RATIO = 0.20
MUTATION_RATE = 0.18
MUTATION_STRENGTH = 0.20
TOURNAMENT_SIZE = 3
RULE_COUNT = 70
LOOKBACK = 5
SURVIVOR_COUNT = 20
RANDOM_IMMIGRANT_RATIO = 0.10

MIN_BAND_WIDTH_Q = 0.10
MAX_BAND_WIDTH_Q = 0.70
MIN_SOFTNESS = 0.00
MAX_SOFTNESS = 1.25

CACHE = PROJECT_ROOT / "data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache"
MARKET_SYMS = ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU", "XLRE", "XLC", "SMH", "ARKK"]
TRAIN_SPLITS = [
    {"label": "train_1", "train_start": "2022-07-01", "train_end": "2023-06-30"},
    {"label": "train_2", "train_start": "2023-07-01", "train_end": "2024-06-30"},
    {"label": "train_3", "train_start": "2024-07-01", "train_end": "2025-06-30"},
]
FINAL_PERIODS_TEMPLATE = [
    {"label": "stress_pre_2022h1", "kind": "stress", "start": None, "end": "2022-06-30", "order": 1},
    {"label": "oos_2025h2", "kind": "oos", "start": "2025-07-01", "end": None, "order": 2},
]
BIN_LABELS = ["0.0_0.5", "0.5_1.0", "1.0_2.0", "2.0_3.0", "3.0_5.0", "5.0_plus"]
BIN_COUNT = 6
CONCENTRATION_CAP_PCT = 45.0
CONCENTRATION_PENALTY_STRENGTH = 0.35
RARE_BIN_ACTUAL_MAX_PCT = 5.0
RARE_BIN_PRED_ALLOW_PCT = 10.0
RARE_BIN_PENALTY_STRENGTH = 0.45
NARROW_BAND_PENALTY_STRENGTH = 0.10
WIDE_BAND_PENALTY_STRENGTH = 0.04

# 있으면 자동 수용할 수급/호가/미체결 후보 컬럼. 현재 CW 캐시에는 없음.
FLOW_COLUMN_CANDIDATES = [
    "buy_unfilled", "sell_unfilled", "buy_unfilled_qty", "sell_unfilled_qty",
    "bid_size", "ask_size", "bid_depth", "ask_depth", "bid_volume", "ask_volume",
    "order_imbalance", "book_imbalance", "bid_ask_imbalance",
    "buy_volume", "sell_volume", "buy_tick_volume", "sell_tick_volume",
    "trade_strength", "execution_strength", "taker_buy_volume", "taker_sell_volume",
]


@dataclass(frozen=True)
class GateConfig:
    min_samples: int = 100
    min_member_score: float = 10.0
    train_min_exact_lift_pp: float = 0.0
    train_min_adjacent_lift_pp: float = 0.0
    stress_min_exact_lift_pp: float = 0.0
    stress_min_adjacent_lift_pp: float = 0.0
    oos_min_exact_lift_pp: float = 0.0
    oos_min_adjacent_lift_pp: float = 0.0
    max_total_penalty: float = 10.0
    max_pred_share_pct: float = 65.0


DEFAULT_GATE = GateConfig()


@dataclass
class RuleGene:
    target: str
    feature: str
    q_low: float
    q_high: float
    bin: int
    weight: float
    softness: float


@dataclass
class PredictorIndividual:
    rules: list[RuleGene]
    default_high_bin: int
    default_low_bin: int
    baseline_spec: dict[str, Any]
    fitness: float = -1e9
    metrics: dict[str, Any] | None = None
    signature: str | None = None


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if dataclasses.is_dataclass(value):
        return json_safe(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    return str(value)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(json_safe(row), ensure_ascii=False, sort_keys=True) + "\n")


def default_seed_base(ticker: str) -> int:
    return 2026070400 + sum((i + 1) * ord(ch) for i, ch in enumerate(ticker.upper()))


def auto_out_dir(ticker: str) -> Path:
    prefix = f"exp_{ticker.lower()}_range_predictor_stage2_v3_qband_{time.strftime('%Y%m%d')}_"
    for idx in range(1, 10000):
        candidate = PROJECT_ROOT / f"{prefix}{idx:04d}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("cannot allocate output directory")


def load_ohlcv(ticker: str) -> pd.DataFrame:
    path = CACHE / f"{ticker.upper()}.pkl"
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_pickle(path).sort_index()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index)
    return df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])


def label_bin(value_pct: float) -> int:
    if value_pct < 0.5:
        return 0
    if value_pct < 1.0:
        return 1
    if value_pct < 2.0:
        return 2
    if value_pct < 3.0:
        return 3
    if value_pct < 5.0:
        return 4
    return 5


def add_market_maps() -> tuple[dict[str, dict[str, float]], list[dict[str, str]]]:
    by_date: dict[str, dict[str, float]] = {}
    meta: list[dict[str, str]] = []
    for sym in MARKET_SYMS:
        path = CACHE / f"{sym}.pkl"
        if not path.exists():
            continue
        m = load_ohlcv(sym)
        O = m["Open"].astype(float).to_numpy(); H = m["High"].astype(float).to_numpy()
        L = m["Low"].astype(float).to_numpy(); C = m["Close"].astype(float).to_numpy()
        rng = (H - L) / C * 100.0
        for i in range(21, len(m)):
            d = m.index[i].strftime("%Y-%m-%d")
            by_date.setdefault(d, {})
            by_date[d].update({
                f"MKT_{sym}_gap_d0": (O[i] / C[i - 1] - 1.0) * 100.0,
                f"MKT_{sym}_prev_ret1": (C[i - 1] / C[i - 2] - 1.0) * 100.0,
                f"MKT_{sym}_ret5": (C[i - 1] / C[i - 6] - 1.0) * 100.0,
                f"MKT_{sym}_vol5": float(np.nanmean(rng[i - 5:i])),
                f"MKT_{sym}_vol20": float(np.nanmean(rng[i - 20:i])),
            })
        for name in ["gap_d0", "prev_ret1", "ret5", "vol5", "vol20"]:
            meta.append({"feature": f"MKT_{sym}_{name}", "source": "market", "lookahead": "D0 open gap or D-1 confirmed ETF value"})
    return by_date, meta


def add_news_map() -> tuple[dict[str, dict[str, float]], list[dict[str, str]]]:
    by_prev_date: dict[str, dict[str, float]] = {}
    meta: list[dict[str, str]] = []
    for path, prefix, source in [
        (PROJECT_ROOT / "data/_system/market_history.csv", "MH1_", "market_history_Dminus1"),
        (PROJECT_ROOT / "data/_system/market_history_v2.csv", "MH2_", "news_event_Dminus1"),
    ]:
        if not path.exists():
            continue
        df = pd.read_csv(path)
        if "date" not in df.columns:
            continue
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        cols = [c for c in df.columns if c != "date" and pd.api.types.is_numeric_dtype(df[c])]
        for _, row in df.iterrows():
            d = row["date"]
            by_prev_date.setdefault(d, {})
            for col in cols:
                by_prev_date[d][prefix + col] = float(row[col]) if pd.notna(row[col]) else np.nan
        for col in cols:
            meta.append({"feature": prefix + col, "source": source, "lookahead": "joined from D-1 date only"})
    return by_prev_date, meta


def _pct(a: float, b: float) -> float:
    return (a / b - 1.0) * 100.0 if b else np.nan


def build_dataset(ticker: str) -> tuple[pd.DataFrame, list[dict[str, str]]]:
    df = load_ohlcv(ticker)
    market_by_date, market_meta = add_market_maps()
    news_by_prev_date, news_meta = add_news_map()
    O = df["Open"].astype(float).to_numpy(); H = df["High"].astype(float).to_numpy()
    L = df["Low"].astype(float).to_numpy(); C = df["Close"].astype(float).to_numpy(); V = df["Volume"].astype(float).to_numpy()
    rng = (H - L) / C * 100.0
    intr = (C - O) / O * 100.0
    rows: list[dict[str, Any]] = []
    feature_meta: dict[str, dict[str, str]] = {}

    def add(row: dict[str, Any], name: str, value: Any, source: str, lookahead: str) -> None:
        row[name] = value
        feature_meta.setdefault(name, {"feature": name, "source": source, "lookahead": lookahead})

    for i in range(max(21, LOOKBACK + 1), len(df)):
        d = df.index[i]
        high_pct = (H[i] / O[i] - 1.0) * 100.0
        low_mag = (O[i] / L[i] - 1.0) * 100.0 if L[i] > 0 else np.nan
        row: dict[str, Any] = {
            "date": d.strftime("%Y-%m-%d"),
            "year": int(d.year),
            "high_pct_label": high_pct,
            "low_mag_pct_label": low_mag,
            "high_bin": label_bin(high_pct),
            "low_bin": label_bin(low_mag),
        }
        add(row, "STK_gap_d0", _pct(O[i], C[i - 1]), "daily_stock_d0", "D0 open vs D-1 close; entry-time observable")

        for lag in range(1, LOOKBACK + 1):
            j = i - lag
            vol_base20 = np.nanmean(V[max(0, j - 20):j])
            add(row, f"STK_lag{lag}_ccret", _pct(C[j], C[j - 1]), "daily_stock_lag", f"D-{lag} close return")
            add(row, f"STK_lag{lag}_intr", intr[j], "daily_stock_lag", f"D-{lag} open-close return")
            add(row, f"STK_lag{lag}_range", rng[j], "daily_stock_lag", f"D-{lag} high-low range")
            add(row, f"STK_lag{lag}_gap", _pct(O[j], C[j - 1]), "daily_stock_lag", f"D-{lag} gap")
            add(row, f"STK_lag{lag}_volratio20", V[j] / vol_base20 if vol_base20 else np.nan, "daily_stock_lag", f"D-{lag} volume vs prior 20d avg")

        # D-1은 스윙 진입식으로 촘촘하게 본다. 모두 D-1 종가까지 확정된 값만 사용한다.
        j = i - 1
        day_range = max(H[j] - L[j], 1e-12)
        body = C[j] - O[j]
        upper = H[j] - max(O[j], C[j])
        lower = min(O[j], C[j]) - L[j]
        add(row, "D1_body_pct", body / O[j] * 100.0, "daily_stock_d1_tight", "D-1 candle body pct")
        add(row, "D1_abs_body_pct", abs(body) / O[j] * 100.0, "daily_stock_d1_tight", "D-1 absolute body pct")
        add(row, "D1_upper_wick_pct", upper / O[j] * 100.0, "daily_stock_d1_tight", "D-1 upper wick pct")
        add(row, "D1_lower_wick_pct", lower / O[j] * 100.0, "daily_stock_d1_tight", "D-1 lower wick pct")
        add(row, "D1_body_to_range", abs(body) / day_range, "daily_stock_d1_tight", "D-1 candle body/range")
        add(row, "D1_upper_wick_to_range", upper / day_range, "daily_stock_d1_tight", "D-1 upper wick/range")
        add(row, "D1_lower_wick_to_range", lower / day_range, "daily_stock_d1_tight", "D-1 lower wick/range")
        add(row, "D1_close_pos_candle", (C[j] - L[j]) / day_range * 100.0, "daily_stock_d1_tight", "D-1 close position in candle")
        add(row, "D1_open_pos_candle", (O[j] - L[j]) / day_range * 100.0, "daily_stock_d1_tight", "D-1 open position in candle")
        add(row, "D1_is_bullish", 1.0 if C[j] > O[j] else 0.0, "daily_stock_d1_tight", "D-1 bullish candle flag")
        add(row, "D1_close_vs_prev_high_pct", _pct(C[j], H[j - 1]), "daily_stock_d1_tight", "D-1 close vs D-2 high")
        add(row, "D1_close_vs_prev_low_pct", _pct(C[j], L[j - 1]), "daily_stock_d1_tight", "D-1 close vs D-2 low")
        add(row, "D1_high_break_prev_high", 1.0 if H[j] > H[j - 1] else 0.0, "daily_stock_d1_tight", "D-1 high broke D-2 high")
        add(row, "D1_low_break_prev_low", 1.0 if L[j] < L[j - 1] else 0.0, "daily_stock_d1_tight", "D-1 low broke D-2 low")
        add(row, "D1_inside_bar", 1.0 if H[j] <= H[j - 1] and L[j] >= L[j - 1] else 0.0, "daily_stock_d1_tight", "D-1 inside bar")
        add(row, "D1_outside_bar", 1.0 if H[j] >= H[j - 1] and L[j] <= L[j - 1] else 0.0, "daily_stock_d1_tight", "D-1 outside bar")
        add(row, "D1_volratio5", V[j] / np.nanmean(V[max(0, j - 5):j]) if j >= 2 else np.nan, "daily_stock_d1_tight", "D-1 volume vs prior 5d avg")
        add(row, "D1_volratio10", V[j] / np.nanmean(V[max(0, j - 10):j]) if j >= 2 else np.nan, "daily_stock_d1_tight", "D-1 volume vs prior 10d avg")
        add(row, "D1_volume_chg1", _pct(V[j], V[j - 1]), "daily_stock_d1_tight", "D-1 volume change vs D-2")
        add(row, "D1_volume_chg3", _pct(V[j], np.nanmean(V[max(0, j - 3):j])), "daily_stock_d1_tight", "D-1 volume change vs prior 3d avg")
        add(row, "D1_price_volume_sign", (1.0 if C[j] >= O[j] else -1.0) * (V[j] / vol_base20 if vol_base20 else 0.0), "daily_stock_d1_tight", "D-1 signed volume ratio")
        atr = safe_float(df["ATR"].iloc[j], 0.0) if "ATR" in df.columns else 0.0
        add(row, "D1_range_vs_ATR", (H[j] - L[j]) / atr if atr else np.nan, "daily_stock_d1_tight", "D-1 range / ATR")
        add(row, "D1_range_vs_vol5", rng[j] / np.nanmean(rng[max(0, j - 5):j]) if j >= 2 else np.nan, "daily_stock_d1_tight", "D-1 range vs prior 5d avg range")
        add(row, "D1_range_vs_vol20", rng[j] / np.nanmean(rng[max(0, j - 20):j]) if j >= 2 else np.nan, "daily_stock_d1_tight", "D-1 range vs prior 20d avg range")
        for ma_col in ["MA5", "MA20", "MA60", "MA200", "BB_upper", "BB_middle", "BB_lower"]:
            if ma_col in df.columns:
                base = safe_float(df[ma_col].iloc[j], 0.0)
                add(row, f"D1_close_vs_{ma_col}_pct", _pct(C[j], base), "daily_stock_d1_tight", f"D-1 close vs {ma_col}")
        if "MA5" in df.columns and "MA20" in df.columns:
            add(row, "D1_MA5_vs_MA20_pct", _pct(float(df["MA5"].iloc[j]), float(df["MA20"].iloc[j])), "daily_stock_d1_tight", "D-1 MA5 vs MA20")
        if "BB_upper" in df.columns and "BB_lower" in df.columns:
            bb_span = safe_float(df["BB_upper"].iloc[j], 0.0) - safe_float(df["BB_lower"].iloc[j], 0.0)
            add(row, "D1_BB_position", (C[j] - safe_float(df["BB_lower"].iloc[j], 0.0)) / bb_span * 100.0 if bb_span else np.nan, "daily_stock_d1_tight", "D-1 close position in Bollinger band")
        for col in ["RSI", "ATR_pct", "BB_width", "Volume_ratio", "MACD", "MACD_signal", "MACD_hist", "Stoch_K", "Stoch_D", "Trend_pct", "Momentum_20d", "Aligned_bull", "MACD_golden"]:
            if col in df.columns:
                add(row, f"D1_{col}", float(df[col].iloc[j]), "daily_stock_d1_tight", f"{col} as of D-1")
                if j >= 2 and pd.notna(df[col].iloc[j - 1]):
                    add(row, f"D1_{col}_chg1", float(df[col].iloc[j]) - float(df[col].iloc[j - 1]), "daily_stock_d1_tight", f"{col} D-1 minus D-2")

        # 호가/미체결/체결강도 계열은 데이터가 있을 때만 D-1 중심으로 자동 수용한다.
        for col in FLOW_COLUMN_CANDIDATES:
            if col in df.columns:
                val = safe_float(df[col].iloc[j], np.nan)
                add(row, f"D1_FLOW_{col}", val, "flow_d1_optional", f"optional D-1 flow/orderbook field {col}")
                if j >= 2 and pd.notna(df[col].iloc[j - 1]):
                    add(row, f"D1_FLOW_{col}_chg1", val - safe_float(df[col].iloc[j - 1], 0.0), "flow_d1_optional", f"optional D-1 flow/orderbook 1d change {col}")

        for n in [3, 5, 10, 20]:
            add(row, f"STK_ret{n}", _pct(C[i - 1], C[i - 1 - n]), "daily_stock_aggregate", f"D-1 close vs D-{n + 1} close")
            add(row, f"STK_vol{n}", float(np.nanmean(rng[i - n:i])), "daily_stock_aggregate", f"D-{n}~D-1 average range")
        lo5, hi5 = np.nanmin(L[i - 5:i]), np.nanmax(H[i - 5:i])
        lo20, hi20 = np.nanmin(L[i - 20:i]), np.nanmax(H[i - 20:i])
        add(row, "STK_range_pos5", (C[i - 1] - lo5) / (hi5 - lo5) * 100.0 if hi5 != lo5 else 50.0, "daily_stock_aggregate", "D-1 close position in prior 5d range")
        add(row, "STK_range_pos20", (C[i - 1] - lo20) / (hi20 - lo20) * 100.0 if hi20 != lo20 else 50.0, "daily_stock_aggregate", "D-1 close position in prior 20d range")

        row.update(market_by_date.get(row["date"], {}))
        row.update(news_by_prev_date.get(df.index[i - 1].strftime("%Y-%m-%d"), {}))
        rows.append(row)
    data = pd.DataFrame(rows).replace([np.inf, -np.inf], np.nan)
    seen: set[str] = set(); unique_meta: list[dict[str, str]] = []
    for m in list(feature_meta.values()) + market_meta + news_meta:
        if m["feature"] in data.columns and m["feature"] not in seen:
            unique_meta.append(m); seen.add(m["feature"])
    return data, unique_meta


def feature_columns(data: pd.DataFrame) -> list[str]:
    banned = {"date", "year", "high_pct_label", "low_mag_pct_label", "high_bin", "low_bin"}
    return [c for c in data.columns if c not in banned and pd.api.types.is_numeric_dtype(data[c])]


def period_frame(data: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    dates = pd.to_datetime(data["date"])
    mask = pd.Series(True, index=data.index)
    if start is not None:
        mask &= dates >= pd.Timestamp(start)
    if end is not None:
        mask &= dates <= pd.Timestamp(end)
    return data.loc[mask].reset_index(drop=True)


def make_quantile_spec(train_df: pd.DataFrame, features: list[str]) -> dict[str, dict[str, list[float]]]:
    levels = [0.0, 0.02, 0.05, 0.10, 0.20, 0.33333, 0.50, 0.66667, 0.80, 0.90, 0.95, 0.98, 1.0]
    out: dict[str, dict[str, list[float]]] = {}
    for f in features:
        vals = train_df[f].dropna().to_numpy(dtype=float)
        vals = vals[np.isfinite(vals)]
        if len(vals) < 50 or float(np.nanmax(vals)) == float(np.nanmin(vals)):
            continue
        qs = np.nanpercentile(vals, [q * 100.0 for q in levels])
        clean = [float(x) for x in qs]
        if all(math.isfinite(x) for x in clean):
            out[f] = {"levels": list(levels), "values": clean}
    return out


def q_value(qspec: Mapping[str, list[float]], q: float) -> float:
    levels = list(qspec.get("levels") or [])
    values = list(qspec.get("values") or [])
    if not levels or not values or len(levels) != len(values):
        return float("nan")
    return float(np.interp(clamp(q, 0.0, 1.0), levels, values))


def normalize_band(q_low: float, q_high: float) -> tuple[float, float]:
    lo = clamp(min(q_low, q_high), 0.0, 1.0)
    hi = clamp(max(q_low, q_high), 0.0, 1.0)
    width = hi - lo
    if width < MIN_BAND_WIDTH_Q:
        mid = (lo + hi) / 2.0
        lo = clamp(mid - MIN_BAND_WIDTH_Q / 2.0, 0.0, 1.0 - MIN_BAND_WIDTH_Q)
        hi = lo + MIN_BAND_WIDTH_Q
    if hi - lo > MAX_BAND_WIDTH_Q:
        mid = (lo + hi) / 2.0
        lo = clamp(mid - MAX_BAND_WIDTH_Q / 2.0, 0.0, 1.0 - MAX_BAND_WIDTH_Q)
        hi = lo + MAX_BAND_WIDTH_Q
    return float(lo), float(hi)


def mode_bin(y: np.ndarray) -> int:
    return int(np.argmax(np.bincount(y.astype(int), minlength=BIN_COUNT)))


def best_adjacent_bin(y: np.ndarray) -> int:
    counts = np.bincount(y.astype(int), minlength=BIN_COUNT)
    return int(max(range(BIN_COUNT), key=lambda b: sum(int(counts[j]) for j in [b - 1, b, b + 1] if 0 <= j < BIN_COUNT)))


def make_baseline_spec(train_df: pd.DataFrame) -> dict[str, Any]:
    yh = train_df["high_bin"].to_numpy(dtype=int); yl = train_df["low_bin"].to_numpy(dtype=int)
    return {"exact_high_bin": mode_bin(yh), "exact_low_bin": mode_bin(yl), "adjacent_high_bin": best_adjacent_bin(yh), "adjacent_low_bin": best_adjacent_bin(yl), "source": "current train split only"}


def clone_individual(ind: PredictorIndividual) -> PredictorIndividual:
    return PredictorIndividual([RuleGene(**asdict(r)) for r in ind.rules], int(ind.default_high_bin), int(ind.default_low_bin), json.loads(json.dumps(json_safe(ind.baseline_spec))), float(ind.fitness), json.loads(json.dumps(json_safe(ind.metrics))) if ind.metrics is not None else None, ind.signature)


def individual_to_dict(ind: PredictorIndividual) -> dict[str, Any]:
    return {"rules": [asdict(r) for r in ind.rules], "default_high_bin": int(ind.default_high_bin), "default_low_bin": int(ind.default_low_bin), "baseline_spec": ind.baseline_spec, "fitness": safe_float(ind.fitness), "metrics": ind.metrics, "signature": ind.signature or predictor_signature(ind)}


def predictor_signature(ind: PredictorIndividual) -> str:
    payload = json.dumps({"h": ind.default_high_bin, "l": ind.default_low_bin, "rules": [asdict(r) for r in ind.rules]}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def random_rule(rng: random.Random, qspec: dict[str, dict[str, list[float]]]) -> RuleGene:
    feature = rng.choice(list(qspec.keys()))
    width = rng.uniform(MIN_BAND_WIDTH_Q, 0.45)
    lo = rng.uniform(0.0, 1.0 - width)
    return RuleGene(rng.choice(["HIGH", "LOW"]), feature, float(lo), float(lo + width), int(rng.randrange(BIN_COUNT)), float(rng.uniform(0.4, 3.0)), float(rng.uniform(0.10, 0.80)))


def random_individual(rng: random.Random, qspec: dict[str, dict[str, list[float]]], baseline_spec: dict[str, Any]) -> PredictorIndividual:
    return PredictorIndividual([random_rule(rng, qspec) for _ in range(RULE_COUNT)], safe_int(baseline_spec.get("exact_high_bin")), safe_int(baseline_spec.get("exact_low_bin")), dict(baseline_spec))


def band_match_strength(vals: np.ndarray, lo_val: float, hi_val: float, softness: float) -> np.ndarray:
    out = np.zeros(len(vals), dtype=float)
    finite = np.isfinite(vals)
    if not finite.any() or not math.isfinite(lo_val) or not math.isfinite(hi_val):
        return out
    lo, hi = min(lo_val, hi_val), max(lo_val, hi_val)
    width = max(1e-12, hi - lo)
    margin = max(0.0, softness) * width
    inside = finite & (vals >= lo) & (vals <= hi)
    out[inside] = 1.0
    if margin > 0:
        lower = finite & (vals < lo) & (vals >= lo - margin)
        upper = finite & (vals > hi) & (vals <= hi + margin)
        out[lower] = (vals[lower] - (lo - margin)) / margin
        out[upper] = ((hi + margin) - vals[upper]) / margin
    return np.clip(out, 0.0, 1.0)


def predict(ind: PredictorIndividual, X: pd.DataFrame, qspec: dict[str, dict[str, list[float]]]) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    n = len(X)
    hs = np.zeros((n, BIN_COUNT), dtype=float); ls = np.zeros((n, BIN_COUNT), dtype=float)
    hs[:, ind.default_high_bin] = 1.0; ls[:, ind.default_low_bin] = 1.0
    active_rule_count = 0; active_strength_sum = 0.0; band_widths = []
    for rule in ind.rules:
        if rule.feature not in X.columns or rule.feature not in qspec:
            continue
        q_low, q_high = normalize_band(rule.q_low, rule.q_high)
        lo_val = q_value(qspec[rule.feature], q_low); hi_val = q_value(qspec[rule.feature], q_high)
        strength = band_match_strength(X[rule.feature].to_numpy(dtype=float), lo_val, hi_val, rule.softness)
        if not np.any(strength > 0):
            continue
        active_rule_count += 1; active_strength_sum += float(np.mean(strength)); band_widths.append(q_high - q_low)
        (hs if rule.target == "HIGH" else ls)[:, int(rule.bin)] += strength * float(rule.weight)
    diag = {"active_rule_count": active_rule_count, "avg_rule_match_strength": active_strength_sum / active_rule_count if active_rule_count else 0.0, "avg_band_width_q": float(np.mean(band_widths)) if band_widths else 0.0}
    return hs.argmax(axis=1), ls.argmax(axis=1), diag


def score_predictions(yh: np.ndarray, yl: np.ndarray, ph: np.ndarray, pl: np.ndarray) -> dict[str, float]:
    he = float((ph == yh).mean() * 100.0) if len(yh) else 0.0
    le = float((pl == yl).mean() * 100.0) if len(yl) else 0.0
    ha = float((np.abs(ph - yh) <= 1).mean() * 100.0) if len(yh) else 0.0
    la = float((np.abs(pl - yl) <= 1).mean() * 100.0) if len(yl) else 0.0
    return {"high_exact_acc_pct": he, "low_exact_acc_pct": le, "high_adjacent_acc_pct": ha, "low_adjacent_acc_pct": la, "combined_exact_acc_pct": (he + le) / 2.0, "combined_adjacent_acc_pct": (ha + la) / 2.0}


def fixed_prediction_scores(df: pd.DataFrame, high_bin: int, low_bin: int) -> dict[str, float]:
    yh = df["high_bin"].to_numpy(dtype=int); yl = df["low_bin"].to_numpy(dtype=int)
    return score_predictions(yh, yl, np.full(len(df), int(high_bin), dtype=int), np.full(len(df), int(low_bin), dtype=int))


def baseline_metrics(df: pd.DataFrame, spec: Mapping[str, Any]) -> dict[str, Any]:
    return {"exact_baseline": fixed_prediction_scores(df, safe_int(spec.get("exact_high_bin")), safe_int(spec.get("exact_low_bin"))), "adjacent_baseline": fixed_prediction_scores(df, safe_int(spec.get("adjacent_high_bin")), safe_int(spec.get("adjacent_low_bin")))}


def share_by_bin(pred: np.ndarray) -> list[float]:
    counts = np.bincount(pred.astype(int), minlength=BIN_COUNT); total = max(1, int(counts.sum()))
    return [float(c / total * 100.0) for c in counts]


def band_shape_penalty(ind: PredictorIndividual) -> dict[str, float]:
    narrow = 0.0; wide = 0.0
    for rule in ind.rules:
        lo, hi = normalize_band(rule.q_low, rule.q_high); width = hi - lo
        narrow += max(0.0, MIN_BAND_WIDTH_Q * 1.5 - width); wide += max(0.0, width - 0.55)
    return {"narrow_band_penalty": narrow * NARROW_BAND_PENALTY_STRENGTH, "wide_band_penalty": wide * WIDE_BAND_PENALTY_STRENGTH}


def prediction_penalty(ind: PredictorIndividual, yh: np.ndarray, yl: np.ndarray, ph: np.ndarray, pl: np.ndarray) -> dict[str, Any]:
    hp, lp = share_by_bin(ph), share_by_bin(pl)
    ha, la = share_by_bin(yh), share_by_bin(yl)
    conc_excess = max(0.0, max(hp) - CONCENTRATION_CAP_PCT) + max(0.0, max(lp) - CONCENTRATION_CAP_PCT)
    rare_excess = 0.0
    for pred, actual in [(hp, ha), (lp, la)]:
        for p, a in zip(pred, actual):
            if a < RARE_BIN_ACTUAL_MAX_PCT:
                rare_excess += max(0.0, p - RARE_BIN_PRED_ALLOW_PCT)
    band_penalty = band_shape_penalty(ind)
    conc_penalty = conc_excess * CONCENTRATION_PENALTY_STRENGTH
    rare_penalty = rare_excess * RARE_BIN_PENALTY_STRENGTH
    total = conc_penalty + rare_penalty + band_penalty["narrow_band_penalty"] + band_penalty["wide_band_penalty"]
    return {"concentration_penalty": conc_penalty, "rare_bin_penalty": rare_penalty, **band_penalty, "total_penalty": total, "max_pred_share_high_pct": max(hp) if hp else 0.0, "max_pred_share_low_pct": max(lp) if lp else 0.0, "pred_distribution_high_pct": hp, "pred_distribution_low_pct": lp}


def predictor_fitness(metrics: Mapping[str, Any]) -> float:
    raw = safe_float(metrics.get("combined_exact_lift_pp")) + safe_float(metrics.get("combined_adjacent_lift_pp")) * 0.35 + safe_float(metrics.get("high_exact_lift_pp")) * 0.15 + safe_float(metrics.get("low_exact_lift_pp")) * 0.15
    return float(raw - safe_float(metrics.get("total_penalty")))


def evaluate_predictor(ind: PredictorIndividual, df: pd.DataFrame, features: list[str], qspec: dict[str, dict[str, list[float]]]) -> dict[str, Any]:
    yh = df["high_bin"].to_numpy(dtype=int); yl = df["low_bin"].to_numpy(dtype=int)
    ph, pl, pred_diag = predict(ind, df[features], qspec)
    scores = score_predictions(yh, yl, ph, pl); penalty = prediction_penalty(ind, yh, yl, ph, pl)
    bases = baseline_metrics(df, ind.baseline_spec); exact_base = bases["exact_baseline"]; adj_base = bases["adjacent_baseline"]
    metrics = {**scores, "sample_count": int(len(df)), "combined_exact_lift_pp": scores["combined_exact_acc_pct"] - exact_base["combined_exact_acc_pct"], "combined_adjacent_lift_pp": scores["combined_adjacent_acc_pct"] - adj_base["combined_adjacent_acc_pct"], "high_exact_lift_pp": scores["high_exact_acc_pct"] - exact_base["high_exact_acc_pct"], "low_exact_lift_pp": scores["low_exact_acc_pct"] - exact_base["low_exact_acc_pct"], "high_adjacent_lift_pp": scores["high_adjacent_acc_pct"] - adj_base["high_adjacent_acc_pct"], "low_adjacent_lift_pp": scores["low_adjacent_acc_pct"] - adj_base["low_adjacent_acc_pct"], "baseline_exact_combined_acc_pct": exact_base["combined_exact_acc_pct"], "baseline_adjacent_combined_acc_pct": adj_base["combined_adjacent_acc_pct"], **penalty, **pred_diag}
    metrics["fitness"] = predictor_fitness(metrics)
    return metrics


def mutate_rule(rule: RuleGene, rng: random.Random, qspec: dict[str, dict[str, list[float]]]) -> RuleGene:
    r = RuleGene(**asdict(rule)); action = rng.choice(["replace", "feature", "shift_band", "resize_band", "bin", "weight", "softness", "target"])
    if action == "replace" and qspec:
        return random_rule(rng, qspec)
    if action == "feature" and qspec:
        r.feature = rng.choice(list(qspec.keys()))
    elif action == "shift_band":
        delta = rng.gauss(0.0, MUTATION_STRENGTH * 0.18); r.q_low += delta; r.q_high += delta
    elif action == "resize_band":
        lo, hi = normalize_band(r.q_low, r.q_high); mid = (lo + hi) / 2.0; width = clamp((hi - lo) * rng.uniform(0.70, 1.35), MIN_BAND_WIDTH_Q, MAX_BAND_WIDTH_Q); r.q_low = mid - width / 2.0; r.q_high = mid + width / 2.0
    elif action == "bin":
        r.bin = int(max(0, min(BIN_COUNT - 1, r.bin + rng.choice([-2, -1, 1, 2]))))
    elif action == "weight":
        r.weight = float(max(0.1, min(5.0, r.weight + rng.gauss(0.0, MUTATION_STRENGTH))))
    elif action == "softness":
        r.softness = float(clamp(r.softness + rng.gauss(0.0, 0.18), MIN_SOFTNESS, MAX_SOFTNESS))
    elif action == "target":
        r.target = "LOW" if r.target == "HIGH" else "HIGH"
    r.q_low, r.q_high = normalize_band(r.q_low, r.q_high); r.softness = float(clamp(r.softness, MIN_SOFTNESS, MAX_SOFTNESS))
    return r


def mutate(ind: PredictorIndividual, rng: random.Random, qspec: dict[str, dict[str, list[float]]], baseline_spec: dict[str, Any] | None = None) -> PredictorIndividual:
    child = clone_individual(ind); child.fitness = -1e9; child.metrics = None; child.signature = None
    if baseline_spec is not None:
        child.baseline_spec = dict(baseline_spec); child.default_high_bin = safe_int(baseline_spec.get("exact_high_bin"), child.default_high_bin); child.default_low_bin = safe_int(baseline_spec.get("exact_low_bin"), child.default_low_bin)
    for i, rule in enumerate(child.rules):
        if rng.random() <= MUTATION_RATE:
            child.rules[i] = mutate_rule(rule, rng, qspec)
    return child


def crossover(a: PredictorIndividual, b: PredictorIndividual, rng: random.Random, baseline_spec: dict[str, Any]) -> PredictorIndividual:
    return PredictorIndividual([RuleGene(**asdict(ra if rng.random() < 0.5 else rb)) for ra, rb in zip(a.rules, b.rules)], safe_int(baseline_spec.get("exact_high_bin")), safe_int(baseline_spec.get("exact_low_bin")), dict(baseline_spec))


def tournament(pop: list[PredictorIndividual], rng: random.Random) -> PredictorIndividual:
    return max(rng.sample(pop, min(TOURNAMENT_SIZE, len(pop))), key=lambda x: x.fitness)


def percentile_ranks(values: list[float]) -> list[float]:
    n = len(values)
    if n == 0: return []
    if n == 1: return [1.0]
    indexed = sorted(enumerate(values), key=lambda x: x[1], reverse=True); ranks = [0.0] * n; i = 0
    while i < n:
        j = i
        while j + 1 < n and indexed[j + 1][1] == indexed[i][1]: j += 1
        pct = 1.0 - ((i + j) / 2.0) / (n - 1)
        for k in range(i, j + 1): ranks[indexed[k][0]] = pct
        i = j + 1
    return ranks


def score_period_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [dict(c) for c in candidates]
    if not rows: return []
    er = percentile_ranks([safe_float(r.get("combined_exact_lift_pp")) for r in rows]); ar = percentile_ranks([safe_float(r.get("combined_adjacent_lift_pp")) for r in rows]); pr = percentile_ranks([-safe_float(r.get("total_penalty")) for r in rows])
    out = []
    for i, row in enumerate(rows):
        score = max(0.0, min(1.0, er[i] * 0.70 + ar[i] * 0.20 + pr[i] * 0.10)) * 100.0
        r = dict(row); r["member_score"] = round(score, 6); r["member_score_components"] = {"exact_lift_percentile": round(er[i], 6), "adjacent_lift_percentile": round(ar[i], 6), "low_penalty_percentile": round(pr[i], 6)}; out.append(r)
    return out


def period_family(kind: str) -> str:
    k = str(kind or "").lower()
    if k == "stress" or "stress" in k: return "stress"
    if k == "oos" or "oos" in k: return "oos"
    return "train"


def fail_reasons(metrics: Mapping[str, Any], kind: str, config: GateConfig = DEFAULT_GATE) -> list[dict[str, Any]]:
    family = period_family(kind)
    min_exact = config.stress_min_exact_lift_pp if family == "stress" else config.oos_min_exact_lift_pp if family == "oos" else config.train_min_exact_lift_pp
    min_adj = config.stress_min_adjacent_lift_pp if family == "stress" else config.oos_min_adjacent_lift_pp if family == "oos" else config.train_min_adjacent_lift_pp
    max_share = max(safe_float(metrics.get("max_pred_share_high_pct")), safe_float(metrics.get("max_pred_share_low_pct")))
    checks = [("sample_count", safe_int(metrics.get("sample_count")), config.min_samples, ">="), ("member_score", safe_float(metrics.get("member_score")), config.min_member_score, ">="), ("combined_exact_lift_pp", safe_float(metrics.get("combined_exact_lift_pp")), min_exact, ">="), ("combined_adjacent_lift_pp", safe_float(metrics.get("combined_adjacent_lift_pp")), min_adj, ">="), ("total_penalty", safe_float(metrics.get("total_penalty")), config.max_total_penalty, "<="), ("max_pred_share_pct", max_share, config.max_pred_share_pct, "<=")]
    out = []
    for metric, value, threshold, rule in checks:
        if (rule == ">=" and value < threshold) or (rule == "<=" and value > threshold):
            out.append({"metric": metric, "value": value, "threshold": threshold, "rule": rule})
    return out


def evaluate_population(pop: list[PredictorIndividual], df: pd.DataFrame, features: list[str], qspec: dict[str, dict[str, list[float]]], label: str, kind: str) -> list[dict[str, Any]]:
    raw = []
    for rank, ind in enumerate(pop, 1):
        m = evaluate_predictor(ind, df, features, qspec); raw.append({"rank_before_score": rank, "signature": ind.signature or predictor_signature(ind), "period_label": label, "period_kind": kind, **m})
    scored = score_period_candidates(raw)
    for row in scored:
        row["fail_reasons"] = fail_reasons(row, kind); row["passed_gate"] = not row["fail_reasons"]
    return scored


def select_survivors(pop: list[PredictorIndividual], scored_rows: list[dict[str, Any]], survivor_count: int) -> tuple[list[PredictorIndividual], list[dict[str, Any]]]:
    by_sig = {ind.signature or predictor_signature(ind): ind for ind in pop}; passed = [r for r in scored_rows if r.get("passed_gate")]
    ordered = sorted(passed or scored_rows, key=lambda r: (safe_float(r.get("member_score")), safe_float(r.get("fitness"))), reverse=True)
    selected_rows = ordered[:max(1, survivor_count)]; survivors = []; seen = set()
    for row in selected_rows:
        sig = str(row.get("signature"))
        if sig in by_sig and sig not in seen:
            ind = clone_individual(by_sig[sig]); ind.fitness = safe_float(row.get("fitness")); ind.metrics = dict(row); ind.signature = sig; survivors.append(ind); seen.add(sig)
    return survivors, selected_rows


def prepare_population_for_split(seed_pop: list[PredictorIndividual] | None, rng: random.Random, qspec: dict[str, dict[str, list[float]]], baseline_spec: dict[str, Any]) -> list[PredictorIndividual]:
    if not seed_pop:
        return [random_individual(rng, qspec, baseline_spec) for _ in range(POPULATION)]
    pop = [mutate(ind, rng, qspec, baseline_spec) for ind in seed_pop]
    immigrant_n = max(0, int(POPULATION * RANDOM_IMMIGRANT_RATIO))
    while len(pop) < POPULATION - immigrant_n and len(seed_pop) >= 2:
        child = crossover(rng.choice(seed_pop), rng.choice(seed_pop), rng, baseline_spec); pop.append(mutate(child, rng, qspec, baseline_spec))
    while len(pop) < POPULATION:
        pop.append(random_individual(rng, qspec, baseline_spec))
    return pop[:POPULATION]


def run_ga_on_split(initial_pop: list[PredictorIndividual], train_df: pd.DataFrame, features: list[str], qspec: dict[str, dict[str, list[float]]], split: Mapping[str, str], seed: int) -> tuple[list[PredictorIndividual], list[dict[str, Any]]]:
    rng = random.Random(seed); pop = [clone_individual(ind) for ind in initial_pop]; history = []; best_fitness = -1e18; no_improve = 0
    for ind in pop:
        ind.metrics = evaluate_predictor(ind, train_df, features, qspec); ind.fitness = safe_float(ind.metrics.get("fitness")); ind.signature = predictor_signature(ind)
    for gen in range(1, GENERATIONS + 1):
        pop.sort(key=lambda x: x.fitness, reverse=True); best = pop[0]
        history.append({"train_label": split["label"], "generation": gen, "best_fitness": safe_float(best.fitness), "avg_fitness": float(np.mean([p.fitness for p in pop])), "best_signature": best.signature, "best_metrics": best.metrics})
        if best.fitness > best_fitness:
            best_fitness = best.fitness; no_improve = 0
        else:
            no_improve += 1
            if no_improve >= PATIENCE: break
        elite_n = max(1, int(POPULATION * ELITE_RATIO)); new_pop = [clone_individual(x) for x in pop[:elite_n]]
        while len(new_pop) < POPULATION:
            child = crossover(tournament(pop, rng), tournament(pop, rng), rng, pop[0].baseline_spec); child = mutate(child, rng, qspec, pop[0].baseline_spec)
            child.metrics = evaluate_predictor(child, train_df, features, qspec); child.fitness = safe_float(child.metrics.get("fitness")); child.signature = predictor_signature(child); new_pop.append(child)
        pop = new_pop
    pop.sort(key=lambda x: x.fitness, reverse=True)
    return pop, history


def build_final_periods(data: pd.DataFrame) -> list[dict[str, Any]]:
    data_start, data_end = str(data["date"].min()), str(data["date"].max())
    return [{**p, "start": p["start"] or data_start, "end": p["end"] or data_end} for p in FINAL_PERIODS_TEMPLATE]


def distribution(y: np.ndarray) -> dict[str, Any]:
    counts = np.bincount(y.astype(int), minlength=BIN_COUNT); total = int(counts.sum())
    return {BIN_LABELS[i]: {"count": int(counts[i]), "pct": float(counts[i] / total * 100.0) if total else 0.0} for i in range(BIN_COUNT)}


def run_sequential_stage2_predictor(ticker: str, out_dir: Path, seed_base: int, survivor_count: int) -> dict[str, Any]:
    started = time.time(); out_dir.mkdir(parents=True, exist_ok=False)
    data, feature_meta = build_dataset(ticker); all_features = feature_columns(data)
    seed_pop: list[PredictorIndividual] | None = None
    all_predictor_rows: list[dict[str, Any]] = []; all_history_rows: list[dict[str, Any]] = []; train_gate_rows: list[dict[str, Any]] = []; stage_survivor_rows: list[dict[str, Any]] = []; trace: list[dict[str, Any]] = []
    features_used_union: set[str] = set(); final_qspec: dict[str, dict[str, list[float]]] = {}
    for split_idx, split in enumerate(TRAIN_SPLITS, 1):
        rng = random.Random(seed_base + split_idx * 1000); train_df = period_frame(data, split["train_start"], split["train_end"])
        qspec = make_quantile_spec(train_df, all_features); usable_features = [f for f in all_features if f in qspec]
        features_used_union.update(usable_features); baseline_spec = make_baseline_spec(train_df)
        init_pop = prepare_population_for_split(seed_pop, rng, qspec, baseline_spec)
        pop, history = run_ga_on_split(init_pop, train_df, usable_features, qspec, split, seed_base + split_idx)
        scored = evaluate_population(pop, train_df, usable_features, qspec, split["label"], "train")
        survivors, selected_rows = select_survivors(pop, scored, survivor_count)
        for rank, ind in enumerate(pop, 1):
            all_predictor_rows.append({"ticker": ticker, "train_label": split["label"], "origin_rank": rank, "signature": ind.signature or predictor_signature(ind), "fitness": safe_float(ind.fitness), "metrics": ind.metrics, "predictor": individual_to_dict(ind), "stage": split_idx})
        for h in history:
            h["generations_run"] = len(history); h["early_stop_triggered"] = len(history) < GENERATIONS
        all_history_rows.extend(history)
        for row in scored:
            train_gate_rows.append({**dict(row), "ticker": ticker, "stage": split_idx, "train_start": split["train_start"], "train_end": split["train_end"]})
        for rank, row in enumerate(selected_rows, 1):
            stage_survivor_rows.append({"ticker": ticker, "stage": split_idx, "train_label": split["label"], "survivor_rank": rank, **row})
        trace.append({"stage": split_idx, "train_label": split["label"], "input_seed_count": len(seed_pop or []), "population": len(pop), "gate_passed_count": sum(1 for r in scored if r.get("passed_gate")), "selected_survivor_count": len(survivors), "fallback_used": sum(1 for r in scored if r.get("passed_gate")) == 0, "best_fitness": safe_float(pop[0].fitness), "best_signature": pop[0].signature, "feature_count": len(usable_features)})
        seed_pop = survivors; final_qspec = qspec
    final_pop = seed_pop or []; final_periods = build_final_periods(data); final_eval_rows: list[dict[str, Any]] = []; alive = final_pop; final_trace = []; features_final = sorted(f for f in features_used_union if f in final_qspec)
    for period in final_periods:
        pdf = period_frame(data, period["start"], period["end"]); scored = evaluate_population(alive, pdf, features_final, final_qspec, period["label"], period["kind"])
        passed_sigs = {str(r.get("signature")) for r in scored if r.get("passed_gate")}
        for row in scored:
            final_eval_rows.append({**dict(row), "ticker": ticker, "period_start": period["start"], "period_end": period["end"]})
        final_trace.append({"period_label": period["label"], "period_kind": period["kind"], "reached": len(alive), "passed": len(passed_sigs), "failed": len(alive) - len(passed_sigs)})
        alive = [ind for ind in alive if (ind.signature or predictor_signature(ind)) in passed_sigs]
    final_survivor_rows = [{"ticker": ticker, "signature": ind.signature or predictor_signature(ind), "predictor": individual_to_dict(ind)} for ind in alive]
    distributions = {p["label"]: {"high": distribution(period_frame(data, p["start"], p["end"])["high_bin"].to_numpy(dtype=int)), "low": distribution(period_frame(data, p["start"], p["end"])["low_bin"].to_numpy(dtype=int))} for p in final_periods}
    write_jsonl(out_dir / "predictors_all.jsonl", all_predictor_rows); write_jsonl(out_dir / "ga_history.jsonl", all_history_rows); write_jsonl(out_dir / "train_gate_metrics.jsonl", train_gate_rows); write_jsonl(out_dir / "stage_survivors.jsonl", stage_survivor_rows); write_jsonl(out_dir / "final_period_metrics.jsonl", final_eval_rows); write_jsonl(out_dir / "final_survivors.jsonl", final_survivor_rows)
    source_counts = Counter(m.get("source", "unknown") for m in feature_meta if m.get("feature") in features_final)
    config = {"ticker": ticker, "runner": "scripts/research/run_range_predictor_stage2_v3.py", "mode": "sequential_survivor_quantile_band_d1_tight", "train_splits": TRAIN_SPLITS, "final_periods": final_periods, "ga": {"population": POPULATION, "generations": GENERATIONS, "patience": PATIENCE, "elite_ratio": ELITE_RATIO, "mutation_rate": MUTATION_RATE, "rule_count": RULE_COUNT, "survivor_count": survivor_count, "random_immigrant_ratio": RANDOM_IMMIGRANT_RATIO, "seed_base": seed_base, "min_band_width_q": MIN_BAND_WIDTH_Q, "max_band_width_q": MAX_BAND_WIDTH_Q, "softness_range": [MIN_SOFTNESS, MAX_SOFTNESS]}, "gate": dataclasses.asdict(DEFAULT_GATE), "lookahead_report": {"pass": True, "stock_features": "D-5~D-2 basic bars, D-1 tight swing-style features, D0 open gap only", "flow_features": "optional D-1 orderbook/flow columns if cache provides them", "market_features": "ETF D0 gap or D-1 confirmed values only", "news_features": "market_history rows joined from D-1 date only", "final_eval_quantile_reference": "train_3 qspec only; no final-period distribution fitting", "excluded": ["D0 high/low/close as features", "future trading results"]}, "feature_count": len(features_final), "feature_sources": dict(source_counts), "bin_labels": BIN_LABELS, "distributions": distributions}
    write_json(out_dir / "config.json", config)
    summary = {"ticker": ticker, "mode": "sequential_survivor_quantile_band_d1_tight", "stage_trace": trace, "final_trace": final_trace, "final_survivor_count": len(alive), "final_survivor_signatures": [ind.signature or predictor_signature(ind) for ind in alive], "elapsed_sec": time.time() - started, "outputs": {"predictors_all": str(out_dir / "predictors_all.jsonl"), "ga_history": str(out_dir / "ga_history.jsonl"), "train_gate_metrics": str(out_dir / "train_gate_metrics.jsonl"), "stage_survivors": str(out_dir / "stage_survivors.jsonl"), "final_period_metrics": str(out_dir / "final_period_metrics.jsonl"), "final_survivors": str(out_dir / "final_survivors.jsonl"), "config": str(out_dir / "config.json"), "summary": str(out_dir / "summary.json")}}
    write_json(out_dir / "summary.json", summary); print(json.dumps(json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True)); return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sequential Stage2-style next-day high/low range predictor GA with D-1 tight quantile-band rules")
    p.add_argument("--ticker", required=True); p.add_argument("--out-dir", default=None); p.add_argument("--seed-base", type=int, default=None); p.add_argument("--survivor-count", type=int, default=SURVIVOR_COUNT); p.add_argument("--parallel", action="store_true", help="accepted for interface parity; not used")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv); ticker = str(args.ticker).strip().upper()
    if not ticker: raise SystemExit("--ticker must not be empty")
    out_dir = Path(args.out_dir).resolve() if args.out_dir else auto_out_dir(ticker)
    seed_base = int(args.seed_base) if args.seed_base is not None else default_seed_base(ticker)
    run_sequential_stage2_predictor(ticker, out_dir, seed_base, max(1, int(args.survivor_count)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
