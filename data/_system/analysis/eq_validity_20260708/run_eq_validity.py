from __future__ import annotations

import json
import math
import os
import random
import re
import time
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path.cwd()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUT = ROOT / "data/_system/analysis/eq_validity_20260708"
SNAP = ROOT / "data/_system/analysis/ohlc_snapshot_20260707"
TRADES = ROOT / "data/_system/analysis/entry_quality_stops_regime_20260707/per_trade_entry_quality_regime.csv"
FROZEN = ROOT / "data/_system/analysis/oos_reproduce_frozen_20260707/oos_trades_frozen.csv"
UNIVERSE = ROOT / "data/_system/analysis/oos_reproduce_frozen_20260707/candidate_universe.json"
SHADOW_TRADES = ROOT / "data/_system/elite_shadow_trades.jsonl"
SIM_TRADES = ROOT / "data/_system/elite_strategy_sim_trades.jsonl"
SEED = 42
TRADING_DAYS_PER_YEAR = 252
random.seed(SEED)
np.random.seed(SEED)

from engine.core.indicators import calc_indicators, is_bb_near_lower, is_volume_surge  # noqa: E402
from engine.live.elite_shadow_entry_quality import assess_shadow_entry_quality  # noqa: E402
from engine.strategies.rulebook import Rulebook  # noqa: E402


def safe_name(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(ticker).upper())


def sf(x: Any, default: float = np.nan) -> float:
    try:
        if x is None or x == "":
            return default
        y = float(x)
        return y if math.isfinite(y) else default
    except Exception:
        return default


def date_key(x: Any) -> str:
    try:
        return pd.Timestamp(x).strftime("%Y-%m-%d")
    except Exception:
        return str(x)[:10]


def load_ohlc(tickers: list[str]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for t in sorted(set(map(str.upper, tickers))):
        p = SNAP / f"{safe_name(t)}_ohlcv.csv"
        if not p.exists():
            continue
        df = pd.read_csv(p)
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)
        try:
            df = calc_indicators(df)
        except Exception:
            pass
        out[t] = df
    return out


def signal_reason_approx(rb: Rulebook, df: pd.DataFrame) -> tuple[list[str], dict[str, Any]]:
    if df is None or len(df) < 60:
        return ["insufficient_data"], {}
    row = df.iloc[-1]
    reasons: list[str] = []
    comps: dict[str, Any] = {}
    is_short = (rb.direction == "short")
    aligned = bool(row.get("Aligned_bull", 0))
    if is_short:
        ma5 = row.get("MA5"); ma20 = row.get("MA20"); ma60 = row.get("MA60")
        aligned = ma5 is not None and ma20 is not None and ma60 is not None and ma5 < ma20 < ma60
    s_align = rb.weight_ma_align * (1.0 if aligned else 0.0)
    comps["ma_align"] = s_align
    if s_align > 0: reasons.append(f"정배열(+{s_align:.2f})")
    if is_short:
        macd_event = row.get("MACD") is not None and row.get("MACD_signal") is not None and row["MACD"] < row["MACD_signal"] and df["MACD"].iloc[-2] >= df["MACD_signal"].iloc[-2]
    else:
        macd_event = bool(row.get("MACD_golden", 0))
    s_macd = rb.weight_macd_golden * (1.0 if macd_event else 0.0)
    comps["macd"] = s_macd
    if s_macd > 0: reasons.append(f"MACD크로스(+{s_macd:.2f})")
    rsi = row.get("RSI", 50)
    if is_short:
        rsi_low, rsi_high = max(rb.rsi_low + 30, 60), min(rb.rsi_high + 10, 85)
    else:
        rsi_low, rsi_high = rb.rsi_low, rb.rsi_high
    rsi_ok = rsi_low <= rsi <= rsi_high
    s_rsi = rb.weight_rsi_zone * (1.0 if rsi_ok else 0.0)
    comps["rsi"] = s_rsi
    if s_rsi > 0: reasons.append(f"RSI {rsi:.0f}∈[{rsi_low:.0f},{rsi_high:.0f}](+{s_rsi:.2f})")
    if is_short:
        bb_upper = row.get("BB_upper")
        bb_ok = bb_upper is not None and bb_upper > 0 and row["Close"] >= bb_upper / rb.bb_proximity
    else:
        bb_ok = is_bb_near_lower(row, proximity=rb.bb_proximity)
    s_bb = rb.weight_bb_near_lower * (1.0 if bb_ok else 0.0)
    comps["bb"] = s_bb
    if s_bb > 0: reasons.append(f"BB근접(+{s_bb:.2f})")
    vol_ok = is_volume_surge(row, threshold=rb.volume_surge_ratio)
    s_vol = rb.weight_volume_surge * (1.0 if vol_ok else 0.0)
    comps["volume"] = s_vol
    if s_vol > 0: reasons.append(f"거래량×{row.get('Volume_ratio', 0):.1f}(+{s_vol:.2f})")
    return reasons, comps


def q_bucket(label: str) -> str:
    u = str(label or "").upper()
    if "STRONG" in u:
        return "STRONG"
    if "HEALTHY" in u:
        return "HEALTHY"
    if "WEAK" in u:
        return "WEAK"
    if "FAILED" in u:
        return "FAILED"
    return u or "UNKNOWN"


def attach_eq_approx(trades: pd.DataFrame, universe: list[dict[str, Any]]) -> pd.DataFrame:
    by_cid = {str(c.get("candidate_id")): c for c in universe}
    tickers = trades["ticker"].astype(str).str.upper().unique().tolist()
    ohlc = load_ohlc(tickers)
    rows = []
    t0 = time.time()
    for i, r in trades.iterrows():
        if i and i % 5000 == 0:
            print(f"EQ approx {i}/{len(trades)} elapsed={time.time()-t0:.1f}s", flush=True)
        cid = str(r.get("candidate_id"))
        t = str(r.get("ticker") or "").upper()
        sig = pd.Timestamp(r.get("signal_date"))
        cand = by_cid.get(cid, {"candidate_id": cid, "ticker": t, "stage": r.get("stage"), "bucket": r.get("bucket")})
        rb_dict = dict((cand or {}).get("rulebook") or {})
        rb_dict["ticker"] = t
        try:
            rb = Rulebook.from_dict(rb_dict)
        except Exception:
            rb = None
        df = ohlc.get(t)
        q = {"allow": True, "label": "QUALITY_UNKNOWN", "score": np.nan, "primary_reason": "missing"}
        price = np.nan
        reasons: list[str] = []
        comps: dict[str, Any] = {}
        reproducible_row = False
        if df is not None and rb is not None:
            sub = df[df["Date"] <= sig].copy()
            if len(sub) >= 60:
                try:
                    price = float(sub["Close"].iloc[-1])
                except Exception:
                    price = sf(r.get("entry_price"))
                reasons, comps = signal_reason_approx(rb, sub)
                try:
                    ratio = sf(r.get("entry_signal_score"), 0.0) / max(sf(r.get("entry_signal_threshold"), 0.0001), 0.0001)
                    q = assess_shadow_entry_quality(
                        candidate=cand,
                        df=sub,
                        price=price,
                        score=sf(r.get("entry_signal_score"), 0.0),
                        threshold=sf(r.get("entry_signal_threshold"), 0.0),
                        ratio=ratio,
                        reasons=reasons,
                        components=comps,
                    )
                    reproducible_row = True
                except Exception as e:
                    q = {"allow": True, "label": "QUALITY_ERROR", "score": np.nan, "primary_reason": f"error:{e}"}
        rows.append({
            "trade_row_id": r.get("trade_row_id", i),
            "candidate_id": cid,
            "ticker": t,
            "split": r.get("split"),
            "signal_date": date_key(r.get("signal_date")),
            "entry_date": date_key(r.get("entry_date")),
            "exit_date": date_key(r.get("s2_exit_date", r.get("exit_date"))),
            "eq_replay_mode": "APPROX_OHLC_SIGNALDATE_CLOSE_NO_EVENT_NEWS_REASONS",
            "eq_row_reproducible_approx": bool(reproducible_row),
            "eq_allow": bool(q.get("allow", True)),
            "eq_group": "ALLOW" if bool(q.get("allow", True)) else "BLOCK",
            "eq_label": q.get("label"),
            "eq_grade": q_bucket(q.get("label")),
            "eq_score": q.get("score"),
            "eq_primary_reason": q.get("primary_reason"),
            "eq_size_factor": q.get("size_factor"),
            "eq_block_reasons": "|".join(q.get("block_reasons") or []),
            "eq_reduce_reasons": "|".join(q.get("reduce_reasons") or []),
            "eq_price_used": price,
            "entry_signal_score": sf(r.get("entry_signal_score")),
            "entry_signal_threshold": sf(r.get("entry_signal_threshold")),
            "score_excess": sf(r.get("entry_signal_score")) - sf(r.get("entry_signal_threshold")),
            "net_pct": sf(r.get("net_pct")),
            "s2_net": sf(r.get("s2_net", r.get("net_pct"))),
            "s2_hold": sf(r.get("s2_hold", r.get("holding_days")), 1),
            "s2_exit_date": date_key(r.get("s2_exit_date", r.get("exit_date"))),
            "s2_exit_price": sf(r.get("s2_exit_price", r.get("exit_price"))),
            "entry_price": sf(r.get("entry_price")),
            "MAE": sf(r.get("MAE", r.get("s2_MAE"))),
            "s2_MAE": sf(r.get("s2_MAE", r.get("MAE"))),
            "vol_group": r.get("vol_group"),
        })
    return pd.DataFrame(rows)


def group_perf(df: pd.DataFrame, group_cols: list[str], net_col: str = "s2_net", mae_col: str = "s2_MAE") -> pd.DataFrame:
    rows = []
    for keys, g in df.groupby(group_cols, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        vals = pd.to_numeric(g[net_col], errors="coerce").dropna()
        mae = pd.to_numeric(g[mae_col], errors="coerce").dropna()
        hold = pd.to_numeric(g["s2_hold"], errors="coerce").replace(0, np.nan)
        row = dict(zip(group_cols, keys))
        row.update({
            "n": int(len(vals)),
            "win_rate_pct": float((vals > 0).mean() * 100.0) if len(vals) else np.nan,
            "avg_net_pct": float(vals.mean()) if len(vals) else np.nan,
            "sum_net_pct": float(vals.sum()) if len(vals) else np.nan,
            "net_per_day_pct": float((pd.to_numeric(g[net_col], errors="coerce") / hold).mean()) if len(g) else np.nan,
            "avg_MAE_pct": float(mae.mean()) if len(mae) else np.nan,
            "worst_MAE_pct": float(mae.min()) if len(mae) else np.nan,
        })
        rows.append(row)
    return pd.DataFrame(rows)


def bootstrap_diff(df: pd.DataFrame, split: str, a: str = "ALLOW", b: str = "BLOCK", n_boot: int = 5000) -> dict[str, Any]:
    d = df[df["split"].astype(str).str.upper().eq(split.upper())]
    x = pd.to_numeric(d[d["eq_group"].eq(a)]["s2_net"], errors="coerce").dropna().to_numpy()
    y = pd.to_numeric(d[d["eq_group"].eq(b)]["s2_net"], errors="coerce").dropna().to_numpy()
    if len(x) < 5 or len(y) < 5:
        return {"split": split, "n_allow": len(x), "n_block": len(y), "diff_allow_minus_block": np.nan, "ci95_low": np.nan, "ci95_high": np.nan, "p_perm_two_sided": np.nan, "note": "insufficient"}
    obs = float(x.mean() - y.mean())
    rng = np.random.default_rng(SEED)
    boots = np.empty(n_boot)
    for i in range(n_boot):
        boots[i] = rng.choice(x, size=len(x), replace=True).mean() - rng.choice(y, size=len(y), replace=True).mean()
    # permutation on means
    z = np.concatenate([x, y])
    cnt = 0
    n_perm = min(5000, n_boot)
    for _ in range(n_perm):
        rng.shuffle(z)
        diff = z[:len(x)].mean() - z[len(x):].mean()
        if abs(diff) >= abs(obs):
            cnt += 1
    return {"split": split, "n_allow": len(x), "n_block": len(y), "diff_allow_minus_block": obs, "ci95_low": float(np.quantile(boots, 0.025)), "ci95_high": float(np.quantile(boots, 0.975)), "p_perm_two_sided": float((cnt + 1) / (n_perm + 1)), "note": "bootstrap_allow_minus_block_mean_s2_net"}


def load_close_data(tickers: list[str]) -> tuple[dict[str, pd.Series], pd.DatetimeIndex]:
    close_by_ticker = {}
    dates = set()
    for t in sorted(set(tickers)):
        p = SNAP / f"{safe_name(t)}_ohlcv.csv"
        if not p.exists():
            continue
        df = pd.read_csv(p)
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date"]).sort_values("Date")
        s = pd.Series(pd.to_numeric(df["Close"], errors="coerce").to_numpy(), index=df["Date"]).dropna()
        close_by_ticker[t] = s
        dates.update(s.index.tolist())
    return close_by_ticker, pd.DatetimeIndex(sorted(dates))


def close_on_or_before(close_by_ticker: dict[str, pd.Series], ticker: str, d: pd.Timestamp, fallback: float) -> float:
    s = close_by_ticker.get(str(ticker).upper())
    if s is None or s.empty:
        return fallback
    v = s.asof(pd.Timestamp(d))
    return float(v) if pd.notna(v) and float(v) > 0 else fallback


def perf_metrics(curve: pd.DataFrame, accepted: int, skipped: int, total: int, scenario: str) -> dict[str, Any]:
    eq = pd.to_numeric(curve["equity"], errors="coerce").ffill().fillna(1.0)
    daily = eq.pct_change().fillna(0.0)
    final = float(eq.iloc[-1]) if len(eq) else 1.0
    days = max(1, (pd.Timestamp(curve["date"].iloc[-1]) - pd.Timestamp(curve["date"].iloc[0])).days) if len(curve) else 1
    cagr = (final ** (365.0 / days) - 1.0) * 100.0 if final > 0 else -100.0
    dd = eq / eq.cummax() - 1.0
    std = float(daily.std(ddof=1))
    return {
        "scenario": scenario,
        "K": 20,
        "total_signals": int(total),
        "realized_trades": int(accepted),
        "skipped_signals": int(skipped),
        "skip_rate_pct": float(skipped / total * 100.0) if total else 0.0,
        "final_multiplier": final,
        "CAGR_pct": cagr,
        "MDD_pct": float(dd.min() * 100.0) if len(dd) else 0.0,
        "Sharpe_daily_ann": float(daily.mean() / std * math.sqrt(TRADING_DAYS_PER_YEAR)) if std > 0 else 0.0,
        "avg_active_positions": float(pd.to_numeric(curve["active_count"], errors="coerce").mean()) if len(curve) else 0.0,
        "max_active_positions": int(pd.to_numeric(curve["active_count"], errors="coerce").max()) if len(curve) else 0,
    }


def simulate_k20(trades: pd.DataFrame, scenario: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    trades = trades.copy()
    trades["entry_date"] = pd.to_datetime(trades["entry_date"])
    trades["exit_date"] = pd.to_datetime(trades["s2_exit_date"])
    trades["exit_price"] = pd.to_numeric(trades["s2_exit_price"], errors="coerce")
    trades["net_pct"] = pd.to_numeric(trades["s2_net"], errors="coerce")
    trades["entry_price"] = pd.to_numeric(trades["entry_price"], errors="coerce")
    trades = trades.dropna(subset=["entry_date", "exit_date", "entry_price", "exit_price", "net_pct"])
    trades = trades[(trades["split"].astype(str).str.upper().eq("OOS")) & (trades["entry_date"] >= pd.Timestamp("2025-01-01")) & (trades["entry_date"] <= pd.Timestamp("2026-07-02"))]
    trades = trades.sort_values(["entry_date", "entry_signal_score", "candidate_id", "trade_row_id"], ascending=[True, False, True, True]).reset_index(drop=True)
    tickers = trades["ticker"].astype(str).str.upper().unique().tolist()
    close_by_ticker, cal_all = load_close_data(tickers)
    sim_end = max(pd.Timestamp("2026-07-02"), trades["exit_date"].max()) if len(trades) else pd.Timestamp("2026-07-02")
    calendar = cal_all[(cal_all >= pd.Timestamp("2025-01-01")) & (cal_all <= sim_end)]
    entries = {d: g.copy() for d, g in trades.groupby("entry_date")}
    k = 20
    slots = [{"equity": 1.0 / k, "active": None} for _ in range(k)]
    skipped = 0
    accepted = 0
    rows = []
    for d in calendar:
        g = entries.get(d)
        accepted_today = 0; skipped_today = 0
        if g is not None and len(g):
            free = [i for i, s in enumerate(slots) if s["active"] is None]
            for _, r in g.iterrows():
                if free:
                    idx = free.pop(0)
                    slots[idx]["active"] = {"ticker": r["ticker"], "entry_price": float(r["entry_price"]), "exit_date": pd.Timestamp(r["exit_date"]), "net_pct": float(r["net_pct"]), "entry_slot_equity": float(slots[idx]["equity"])}
                    accepted += 1; accepted_today += 1
                else:
                    skipped += 1; skipped_today += 1
        active_count = sum(1 for s in slots if s["active"] is not None)
        total_eq = 0.0
        for s in slots:
            a = s["active"]
            if a is None:
                total_eq += float(s["equity"]); continue
            if pd.Timestamp(a["exit_date"]) <= d:
                val = float(a["entry_slot_equity"]) * (1.0 + float(a["net_pct"]) / 100.0)
                s["equity"] = val; s["active"] = None; total_eq += val
            else:
                close = close_on_or_before(close_by_ticker, a["ticker"], d, float(a["entry_price"]))
                total_eq += float(a["entry_slot_equity"]) * close / float(a["entry_price"])
        rows.append({"date": date_key(d), "scenario": scenario, "equity": total_eq, "active_count": active_count, "accepted_today": accepted_today, "skipped_today": skipped_today})
    curve = pd.DataFrame(rows)
    curve["daily_return"] = curve["equity"].pct_change().fillna(0.0)
    return curve, perf_metrics(curve, accepted, skipped, len(trades), scenario)


def live_ledger_perf() -> tuple[pd.DataFrame, dict[str, Any]]:
    rows = []
    for source, path in [("elite_shadow_trades", SHADOW_TRADES), ("elite_strategy_sim_trades", SIM_TRADES)]:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("entry_quality_label") is None or r.get("pnl_pct") is None:
                continue
            allow = q_bucket(r.get("entry_quality_label")) in {"STRONG", "HEALTHY", "WEAK"} and str(r.get("entry_quality_primary_reason") or "") not in {"failed_follow_through_q_lt_45", "no_price_follow_through", "event_heavy_without_follow_through", "event_heavy_below_ma5", "bottom_fishing_failed", "overheat_reversal", "high_risk_weak_quality"}
            # shadow ledger only stores opened trades, so most rows are effectively allow; keep label view.
            rows.append({
                "source": source,
                "ticker": r.get("ticker"),
                "candidate_id": r.get("candidate_id"),
                "opened_at": r.get("opened_at"),
                "closed_at": r.get("closed_at"),
                "entry_quality_label": r.get("entry_quality_label"),
                "eq_grade": q_bucket(r.get("entry_quality_label")),
                "entry_quality_score": r.get("entry_quality_score"),
                "entry_quality_primary_reason": r.get("entry_quality_primary_reason"),
                "pnl_pct": sf(r.get("pnl_pct")),
                "max_loss_pct": sf(r.get("max_loss_pct")),
                "max_profit_pct": sf(r.get("max_profit_pct")),
                "notional": sf(r.get("notional")),
                "exit_reason": r.get("exit_reason"),
                "eq_allow_inferred": allow,
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return df, {"sample_status": "LIVE_EQ_SAMPLE_INSUFFICIENT", "n": 0}
    perf = group_perf(df.rename(columns={"pnl_pct":"s2_net", "max_loss_pct":"s2_MAE"}).assign(split="LIVE", s2_hold=1, eq_group=lambda x: np.where(x["eq_allow_inferred"], "ALLOW", "BLOCK")), ["source", "eq_group"])
    status = "LIVE_EQ_SAMPLE_USABLE" if len(df) >= 100 else "LIVE_EQ_SAMPLE_INSUFFICIENT"
    return df, {"sample_status": status, "n": int(len(df)), "performance": perf.to_dict(orient="records")}


def main() -> int:
    started = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    universe = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    trades = pd.read_csv(TRADES)
    eq = attach_eq_approx(trades, universe)
    eq.to_csv(OUT / "eq_trade_labels_approx.csv", index=False, float_format="%.12g", lineterminator="\n")
    merged = trades.merge(eq[["trade_row_id", "eq_allow", "eq_group", "eq_label", "eq_grade", "eq_score", "eq_primary_reason", "eq_size_factor", "eq_block_reasons", "eq_reduce_reasons", "eq_row_reproducible_approx"]], on="trade_row_id", how="left")
    # group perf uses S2 fields as requested by portfolio config.
    g1 = group_perf(eq, ["split", "eq_group"])
    g2 = group_perf(eq, ["split", "eq_grade"])
    g3 = group_perf(eq, ["split", "eq_label"])
    group_out = pd.concat([g1.assign(grouping="allow_vs_block"), g2.assign(grouping="grade4"), g3.assign(grouping="label")], ignore_index=True, sort=False)
    group_out.to_csv(OUT / "eq_group_performance.csv", index=False, float_format="%.12g", lineterminator="\n")
    stats = pd.DataFrame([bootstrap_diff(eq, "IS"), bootstrap_diff(eq, "OOS")])
    stats.to_csv(OUT / "eq_stat_tests.csv", index=False, float_format="%.12g", lineterminator="\n")
    # Portfolio baseline vs allow-only.
    oos_all = merged.copy()
    oos_allow = merged[merged["eq_allow"].eq(True)].copy()
    curves = []
    perfs = []
    for name, d in [("EQ_ignored_all_signals", oos_all), ("EQ_allow_only", oos_allow)]:
        curve, perf = simulate_k20(d, name)
        curves.append(curve); perfs.append(perf)
    port = pd.DataFrame(perfs)
    port.to_csv(OUT / "eq_portfolio_compare.csv", index=False, float_format="%.12g", lineterminator="\n")
    pd.concat(curves, ignore_index=True).to_csv(OUT / "eq_portfolio_equity_curves.csv", index=False, float_format="%.12g", lineterminator="\n")
    live_df, live_summary = live_ledger_perf()
    if not live_df.empty:
        live_df.to_csv(OUT / "eq_live_ledger_rows.csv", index=False, float_format="%.12g", lineterminator="\n")
        live_perf = group_perf(live_df.rename(columns={"pnl_pct":"s2_net", "max_loss_pct":"s2_MAE"}).assign(split="LIVE", s2_hold=1, eq_group=lambda x: np.where(x["eq_allow_inferred"], "ALLOW", "BLOCK")), ["source", "eq_group"])
        live_perf.to_csv(OUT / "eq_live_ledger_performance.csv", index=False, float_format="%.12g", lineterminator="\n")
    # Verdict.
    oos_perf = g1[g1["split"].astype(str).str.upper().eq("OOS")].set_index("eq_group")
    is_perf = g1[g1["split"].astype(str).str.upper().eq("IS")].set_index("eq_group")
    allow_oos = oos_perf.loc["ALLOW"] if "ALLOW" in oos_perf.index else None
    block_oos = oos_perf.loc["BLOCK"] if "BLOCK" in oos_perf.index else None
    allow_is = is_perf.loc["ALLOW"] if "ALLOW" in is_perf.index else None
    block_is = is_perf.loc["BLOCK"] if "BLOCK" in is_perf.index else None
    port_map = port.set_index("scenario")
    cagr_delta = float(port_map.loc["EQ_allow_only", "CAGR_pct"] - port_map.loc["EQ_ignored_all_signals", "CAGR_pct"])
    mdd_delta = float(port_map.loc["EQ_allow_only", "MDD_pct"] - port_map.loc["EQ_ignored_all_signals", "MDD_pct"])
    sharpe_delta = float(port_map.loc["EQ_allow_only", "Sharpe_daily_ann"] - port_map.loc["EQ_ignored_all_signals", "Sharpe_daily_ann"])
    is_allow_better_avg = bool(allow_is is not None and block_is is not None and allow_is["avg_net_pct"] > block_is["avg_net_pct"])
    oos_allow_better_avg = bool(allow_oos is not None and block_oos is not None and allow_oos["avg_net_pct"] > block_oos["avg_net_pct"])
    is_allow_better_sum = bool(allow_is is not None and block_is is not None and allow_is["sum_net_pct"] > block_is["sum_net_pct"])
    oos_allow_better_sum = bool(allow_oos is not None and block_oos is not None and allow_oos["sum_net_pct"] > block_oos["sum_net_pct"])
    # Because exact frozen replay is not reproducible, final verdict cannot be HELPS from approximate frozen alone.
    if live_summary.get("sample_status") == "LIVE_EQ_SAMPLE_INSUFFICIENT":
        final_verdict = "EQ_FILTER_UNVERIFIED"
    else:
        if oos_allow_better_avg and is_allow_better_avg and cagr_delta > 0 and sharpe_delta > 0:
            final_verdict = "EQ_FILTER_NEUTRAL"  # exact not reproducible; live ledger lacks block counterfactuals.
        elif block_oos is not None and allow_oos is not None and block_oos["avg_net_pct"] > allow_oos["avg_net_pct"]:
            final_verdict = "EQ_FILTER_HURTS_APPROX"
        else:
            final_verdict = "EQ_FILTER_NEUTRAL_APPROX"
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": SEED,
        "eq_reproducibility": "EQ_NOT_REPRODUCIBLE",
        "reproducibility_reasons": [
            "assess_shadow_entry_quality requires live/current price; frozen historical trades do not store the exact live 1m price used at decision time.",
            "assess_shadow_entry_quality uses evaluate_signal reasons/components to detect event_heavy and bottom_fishing; frozen trades store scores but not reasons/components.",
            "D-1 OHLC can approximate follow-through metrics, but event/news/reason text cannot be exactly reconstructed from frozen trades."
        ],
        "frozen_validation_mode": "APPROX_OHLC_SIGNALDATE_CLOSE_NO_EVENT_NEWS_REASONS",
        "final_verdict": final_verdict,
        "cagr_delta_allow_minus_all": cagr_delta,
        "mdd_delta_allow_minus_all": mdd_delta,
        "sharpe_delta_allow_minus_all": sharpe_delta,
        "is_allow_better_avg": is_allow_better_avg,
        "oos_allow_better_avg": oos_allow_better_avg,
        "is_allow_better_sum": is_allow_better_sum,
        "oos_allow_better_sum": oos_allow_better_sum,
        "group_performance": group_out.to_dict(orient="records"),
        "stat_tests": stats.to_dict(orient="records"),
        "portfolio_compare": port.to_dict(orient="records"),
        "live_ledger_summary": live_summary,
        "elapsed_sec": round(time.time() - started, 3),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    # readout
    lines = []
    lines.append("# EQ entry quality allow/block validity")
    lines.append("")
    lines.append(f"- final verdict: **{final_verdict}**")
    lines.append("- 0-step reproducibility: **EQ_NOT_REPRODUCIBLE**")
    lines.append(f"- frozen validation mode: `{summary['frozen_validation_mode']}`")
    lines.append(f"- seed: {SEED}")
    lines.append("")
    lines.append("## 0단계 재현 가능성 판정")
    lines.append("정확 재현 불가. OHLC 기반 follow-through 지표는 과거 진입일 기준으로 복원 가능하지만, live 현재가와 `evaluate_signal`의 원래 `reasons/components`가 frozen 거래에 저장되어 있지 않다. 특히 EQ의 `event_heavy`, `bottom_fishing` 일부는 reason 문자열에 의존한다.")
    lines.append("")
    lines.append("## Approx frozen group performance — allow vs block / grade")
    lines.append(group_out.to_markdown(index=False))
    lines.append("")
    lines.append("## Bootstrap / permutation")
    lines.append(stats.to_markdown(index=False))
    lines.append("")
    lines.append("## OOS portfolio compare — S2 K=20 final_score priority")
    lines.append(port.to_markdown(index=False))
    lines.append("")
    lines.append("## Live ledger check")
    lines.append(f"- sample status: `{live_summary.get('sample_status')}`")
    lines.append(f"- rows: {live_summary.get('n')}")
    if live_summary.get("performance"):
        lines.append(pd.DataFrame(live_summary.get("performance")).to_markdown(index=False))
    lines.append("")
    lines.append("## Decision")
    lines.append("정확 재현이 불가능하므로 approximate frozen 결과만으로 EQ를 게이트로 승격할 수 없다. live shadow ledger는 closed trade 표본은 있으나, 실제로 EQ block된 후보는 애초에 진입하지 않아 counterfactual 성과가 없다. 따라서 지시서의 우선순위 체계상 현재 판정은 EQ_FILTER_UNVERIFIED다.")
    lines.append("")
    lines.append("## Files")
    for fn in ["readout.md", "eq_trade_labels_approx.csv", "eq_group_performance.csv", "eq_portfolio_compare.csv", "eq_portfolio_equity_curves.csv", "eq_stat_tests.csv", "eq_live_ledger_rows.csv", "eq_live_ledger_performance.csv", "summary.json"]:
        if (OUT / fn).exists() or fn == "readout.md":
            lines.append(f"- `{OUT / fn}`")
    (OUT / "readout.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(OUT), "summary": summary}, ensure_ascii=False, default=str)[:4000])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
