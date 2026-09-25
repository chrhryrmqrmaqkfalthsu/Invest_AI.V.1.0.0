from __future__ import annotations

"""위험구조 3단계: 저장 룰 임계와 학습기간 일봉 분포의 도달가능성 read-only 진단."""

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
CACHE = ROOT / "data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache"
CANDIDATES = OUT / "sparse_indicator_entry_structure_full.csv"
STAGE2_ACTIVITY = OUT / "high_vol_volume_activity_stage2_all_stage1.csv"
STRICT = OUT / "high_vol_volume_activity_stage2_strict.csv"
RELAXED = OUT / "high_vol_volume_activity_stage2_relaxed.csv"

DETAIL = OUT / "threshold_reachability_stage3_never_rare.csv"
CAUSE = OUT / "threshold_reachability_stage3_cause_summary.csv"
BOIL = OUT / "threshold_reachability_stage3_boil_parity.csv"
FULL_DETAIL = OUT / "threshold_reachability_stage3_full_indicator_detail.csv.gz"
FULL_SUMMARY = OUT / "threshold_reachability_stage3_full_indicator_summary.csv"
FORM = OUT / "threshold_reachability_stage3_condition_forms.csv"
SUMMARY = OUT / "threshold_reachability_stage3_summary.json"
READOUT = OUT / "threshold_reachability_stage3_readout.md"

EPS = 1e-12
COMPONENTS = ("ma", "macd", "rsi", "bb", "volume")


def finite(values: pd.Series) -> np.ndarray:
    a = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    a = a[np.isfinite(a)]
    return a


def q(a: np.ndarray, p: float) -> float:
    return float(np.quantile(a, p)) if a.size else math.nan


def safe_float(v: Any, default: float = math.nan) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def boolish(v: Any) -> bool:
    return v if isinstance(v, bool) else str(v).strip().lower() in {"1", "true", "yes"}


def load_jsonl_rows(path: Path, cache: dict[Path, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    if path not in cache:
        before = (path.stat().st_size, path.stat().st_mtime_ns)
        rows = [json.loads(line) for line in path.open(encoding="utf-8", errors="strict") if line.strip()]
        after = (path.stat().st_size, path.stat().st_mtime_ns)
        if before != after:
            raise RuntimeError(f"source changed while reading: {path}")
        cache[path] = rows
    return cache[path]


def load_bars(ticker: str, cache: dict[str, pd.DataFrame]) -> pd.DataFrame:
    if ticker not in cache:
        p = CACHE / f"{ticker}.pkl"
        before = (p.stat().st_size, p.stat().st_mtime_ns)
        frame = pd.read_pickle(p).copy()
        after = (p.stat().st_size, p.stat().st_mtime_ns)
        if before != after:
            raise RuntimeError(f"bar cache changed while reading: {p}")
        frame.index = pd.to_datetime(frame.index, errors="coerce")
        cache[ticker] = frame[~frame.index.isna()].sort_index()
    return cache[ticker]


def prepared(frame: pd.DataFrame, start: str, end: str, direction: str) -> dict[str, Any]:
    w = frame.loc[(frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))].copy()
    short = str(direction).lower() == "short"
    if short:
        ma = (w["MA5"] < w["MA20"]) & (w["MA20"] < w["MA60"])
        pm = frame["MACD"].shift(1).reindex(w.index)
        ps = frame["MACD_signal"].shift(1).reindex(w.index)
        macd = (w["MACD"] < w["MACD_signal"]) & (pm >= ps)
        bb = (w["BB_upper"] / w["Close"]).where((w["BB_upper"] > 0) & (w["Close"] > 0))
    else:
        ma = w["Aligned_bull"].fillna(0).astype(float) != 0
        macd = w["MACD_golden"].fillna(0).astype(float) != 0
        bb = (w["Close"] / w["BB_lower"]).where((w["BB_lower"] > 0) & (w["Close"] > 0))
    return {
        "eligible_days": int(len(w)),
        "ma_count": int(ma.fillna(False).sum()),
        "macd_count": int(macd.fillna(False).sum()),
        "rsi": finite(w["RSI"]),
        "bb": finite(bb),
        "volume": finite(w["Volume_ratio"]),
    }


def train_meta(stage: str, row: dict[str, Any], rb: dict[str, Any], ticker: str, activity_lookup: dict[str, dict[str, Any]]) -> tuple[str, str, str, str]:
    direction = str(rb.get("direction", "long"))
    if stage == "stage3":
        ah = str(row.get("entry_rulebook_hash") or "")
        z = activity_lookup.get(f"STAGE3_ENTRY|{ticker}|{ah}")
        if z is None:
            raise KeyError(f"missing stage3 activity metadata {ticker} {ah}")
        return str(z["train_start"]), str(z["train_end"]), direction, ah
    origins = row.get("origins") or []
    if len(origins) != 1:
        raise RuntimeError(f"unexpected stage2 origins {ticker}: {len(origins)}")
    o = origins[0]
    return str(o["train_start"]), str(o["train_end"]), direction, str(row.get("rulebook_hash") or "")


def component_result(component: str, rb: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    direction = str(rb.get("direction", "long")).lower()
    if component == "ma":
        count = int(p["ma_count"]); n = int(p["eligible_days"])
        return {"condition_form": "BOOLEAN_EVENT", "condition_text": "MA5>MA20>MA60" if direction != "short" else "MA5<MA20<MA60", "threshold_low": math.nan, "threshold_high": math.nan, "observed_n": n, "fired_count": count, "reachability": "REACHABLE" if count > 0 else "UNREACHABLE"}
    if component == "macd":
        count = int(p["macd_count"]); n = int(p["eligible_days"])
        return {"condition_form": "BOOLEAN_CROSS_EVENT", "condition_text": "MACD golden cross" if direction != "short" else "MACD dead cross", "threshold_low": math.nan, "threshold_high": math.nan, "observed_n": n, "fired_count": count, "reachability": "REACHABLE" if count > 0 else "UNREACHABLE"}
    if component == "rsi":
        a = p["rsi"]
        if direction == "short":
            lo = max(safe_float(rb.get("rsi_low"), 0.0) + 30.0, 60.0)
            hi = min(safe_float(rb.get("rsi_high"), 100.0) + 10.0, 85.0)
        else:
            lo, hi = safe_float(rb.get("rsi_low")), safe_float(rb.get("rsi_high"))
        count = int(((a >= lo) & (a <= hi)).sum())
        return {"condition_form": "BAND_INCLUSIVE", "condition_text": f"{lo} <= RSI <= {hi}", "threshold_low": lo, "threshold_high": hi, "observed_n": int(a.size), "fired_count": count, "reachability": "REACHABLE" if count > 0 else "UNREACHABLE", "dist_min": float(a.min()) if a.size else math.nan, "dist_max": float(a.max()) if a.size else math.nan, "dist_p01": q(a,.01), "dist_p99": q(a,.99), "dist_median": q(a,.5)}
    if component == "bb":
        a = p["bb"]; th = safe_float(rb.get("bb_proximity")); count = int((a <= th).sum())
        return {"condition_form": "ONE_SIDED_LE", "condition_text": f"BB ratio <= {th}", "threshold_low": math.nan, "threshold_high": th, "observed_n": int(a.size), "fired_count": count, "reachability": "REACHABLE" if count > 0 else "UNREACHABLE", "dist_min": float(a.min()) if a.size else math.nan, "dist_max": float(a.max()) if a.size else math.nan, "dist_p01": q(a,.01), "dist_p99": q(a,.99), "dist_median": q(a,.5)}
    a = p["volume"]; th = safe_float(rb.get("volume_surge_ratio")); count = int((a >= th).sum())
    mx = float(a.max()) if a.size else math.nan; p99 = q(a,.99)
    if not a.size: reach = "UNJUDGED"
    elif th > mx: reach = "UNREACHABLE"
    elif th > p99: reach = "NEAR_UNREACHABLE"
    else: reach = "REACHABLE"
    percentile = float((a <= th).mean() * 100) if a.size else math.nan
    return {"condition_form": "ONE_SIDED_GE", "condition_text": f"Volume_ratio >= {th}", "threshold_low": th, "threshold_high": math.nan, "observed_n": int(a.size), "fired_count": count, "reachability": reach, "threshold_percentile_pct": percentile, "dist_min": float(a.min()) if a.size else math.nan, "dist_max": mx, "dist_p90": q(a,.90), "dist_p95": q(a,.95), "dist_p99": p99, "dist_median": q(a,.5)}


def main() -> int:
    candidates = pd.read_csv(CANDIDATES, low_memory=False)
    if len(candidates) != 17071:
        raise AssertionError(len(candidates))
    activity = pd.read_csv(STAGE2_ACTIVITY, low_memory=False)
    strict_ids = set(pd.read_csv(STRICT, usecols=["candidate_id"])["candidate_id"])
    relaxed_ids = set(pd.read_csv(RELAXED, usecols=["candidate_id"])["candidate_id"])
    rare_only_ids = relaxed_ids - strict_ids
    if (len(strict_ids), len(relaxed_ids), len(rare_only_ids)) != (84, 801, 717):
        raise AssertionError((len(strict_ids), len(relaxed_ids), len(rare_only_ids)))

    full_activity = pd.read_csv(OUT / "unvalidated_gene_rule_activity_full.csv.gz", usecols=["pool_scope","ticker","rulebook_hash","train_start","train_end"], low_memory=False)
    full_activity["key"] = full_activity.pool_scope.astype(str)+"|"+full_activity.ticker.astype(str)+"|"+full_activity.rulebook_hash.astype(str)
    full_activity = full_activity.drop_duplicates("key")
    activity_lookup = full_activity.set_index("key").to_dict("index")

    source_cache: dict[Path, list[dict[str, Any]]] = {}
    bar_cache: dict[str, pd.DataFrame] = {}
    prep_cache: dict[tuple[str,str,str,str], dict[str, Any]] = {}
    detail_rows: list[dict[str, Any]] = []
    full_rows: list[dict[str, Any]] = []
    candidate_unreachable: dict[str, set[str]] = defaultdict(set)

    for i, x in enumerate(candidates.itertuples(index=False), 1):
        path = ROOT / str(x.source_file)
        row = load_jsonl_rows(path, source_cache)[int(x.source_row_index)-1]
        rb = row.get("rulebook") or {}
        start, end, direction, activity_hash = train_meta(str(x.stage), row, rb, str(x.ticker), activity_lookup)
        key = (str(x.ticker), start, end, direction)
        if key not in prep_cache:
            prep_cache[key] = prepared(load_bars(str(x.ticker), bar_cache), start, end, direction)
        p = prep_cache[key]
        base = {"candidate_id":str(x.candidate_id),"stage":str(x.stage),"ticker":str(x.ticker),"rulebook_hash":str(x.rulebook_hash),"activity_rule_hash":activity_hash,"train_start":start,"train_end":end,"direction":direction,"eligible_days":int(p["eligible_days"])}
        weights = {"ma":safe_float(rb.get("weight_ma_align"),0),"macd":safe_float(rb.get("weight_macd_golden"),0),"rsi":safe_float(rb.get("weight_rsi_zone"),0),"bb":safe_float(rb.get("weight_bb_near_lower"),0),"volume":safe_float(rb.get("weight_volume_surge"),0)}
        for comp in COMPONENTS:
            res = component_result(comp, rb, p)
            active = abs(weights[comp]) > EPS
            rr = {**base,"component":comp,"weight":weights[comp],"active_weight":active,**res}
            full_rows.append(rr)
            if active and res["reachability"] == "UNREACHABLE":
                candidate_unreachable[str(x.candidate_id)].add(comp)
        if str(x.candidate_id) in relaxed_ids:
            vr = component_result("volume", rb, p)
            label = "NEVER_FIRED" if str(x.candidate_id) in strict_ids else "RARELY_ACTIVE"
            cause = "THRESHOLD_TOO_HIGH" if vr["reachability"] in {"UNREACHABLE","NEAR_UNREACHABLE"} else "NATURALLY_QUIET"
            arow = activity[activity.candidate_id.eq(str(x.candidate_id))].iloc[0]
            detail_rows.append({**base,"scope":"STRICT_NEVER" if label=="NEVER_FIRED" else "RELAXED_ADDITIONAL_RARE","activity_label":label,"stored_fired_count":int(arow.volume_surge_fired_count),"stored_fired_rate_pct":float(arow.volume_surge_fired_rate_pct),"volume_surge_ratio":safe_float(rb.get("volume_surge_ratio")),**vr,"cause":cause,"is_named_boil_9044":str(x.candidate_id)=="stage3:BOIL:9044dc2c67a3"})
        if i % 2000 == 0:
            print(f"SCAN {i}/{len(candidates)}", flush=True)

    detail = pd.DataFrame(detail_rows).sort_values(["scope","cause","ticker","candidate_id"])
    detail.to_csv(DETAIL,index=False)
    full = pd.DataFrame(full_rows)
    full.to_csv(FULL_DETAIL,index=False,compression="gzip")

    cause_rows=[]
    for scope,g in detail.groupby("scope"):
        for cause,n in g.cause.value_counts().items():
            cause_rows.append({"scope":scope,"cause":cause,"candidate_count":int(n),"scope_total":len(g),"rate_pct":float(n/len(g)*100)})
    for cause,n in detail.cause.value_counts().items():
        cause_rows.append({"scope":"RELAXED_TOTAL","cause":cause,"candidate_count":int(n),"scope_total":len(detail),"rate_pct":float(n/len(detail)*100)})
    pd.DataFrame(cause_rows).to_csv(CAUSE,index=False)

    boil = detail[detail.is_named_boil_9044].copy()
    if len(boil)!=1: raise AssertionError(len(boil))
    boil.to_csv(BOIL,index=False)

    summary_rows=[]
    for comp in COMPONENTS:
        g=full[(full.component==comp)&full.active_weight]
        ids_un=set(g.loc[g.reachability.eq("UNREACHABLE"),"candidate_id"])
        summary_rows.append({"component":comp,"condition_form":g.condition_form.mode().iloc[0] if len(g) else "","active_candidate_count":int(g.candidate_id.nunique()),"unreachable_candidate_count":len(ids_un),"unreachable_rate_pct":len(ids_un)/g.candidate_id.nunique()*100 if len(g) else 0})
    any_un=set(candidate_unreachable)
    summary_rows.append({"component":"ANY_ACTIVE_CORE","condition_form":"ANY","active_candidate_count":len(candidates),"unreachable_candidate_count":len(any_un),"unreachable_rate_pct":len(any_un)/len(candidates)*100})
    summary_df=pd.DataFrame(summary_rows);summary_df.to_csv(FULL_SUMMARY,index=False)

    forms = pd.DataFrame([
        {"component":"MA","rule_condition":"long: MA5>MA20>MA60; short: MA5<MA20<MA60","form":"BOOLEAN_EVENT","one_sided_or_band":"EVENT","engine_reference":"engine/strategies/evaluator.py"},
        {"component":"MACD","rule_condition":"long: golden cross; short: dead cross","form":"BOOLEAN_CROSS_EVENT","one_sided_or_band":"EVENT","engine_reference":"engine/strategies/evaluator.py"},
        {"component":"RSI","rule_condition":"rsi_low <= RSI <= rsi_high (short transforms bounds)","form":"BAND_INCLUSIVE","one_sided_or_band":"BAND","engine_reference":"engine/strategies/evaluator.py"},
        {"component":"BB","rule_condition":"normalized BB proximity ratio <= bb_proximity","form":"ONE_SIDED_LE","one_sided_or_band":"ONE_SIDED","engine_reference":"engine/strategies/evaluator.py"},
        {"component":"VOLUME","rule_condition":"Volume_ratio >= volume_surge_ratio","form":"ONE_SIDED_GE","one_sided_or_band":"ONE_SIDED","engine_reference":"engine/strategies/evaluator.py"},
        {"component":"FINAL_ENTRY","rule_condition":"final_score >= signal_threshold","form":"ONE_SIDED_GE","one_sided_or_band":"ONE_SIDED","engine_reference":"engine/strategies/evaluator.py"},
    ])
    forms.to_csv(FORM,index=False)

    b=boil.iloc[0]
    cause_counts=detail.groupby(["scope","cause"]).size().to_dict()
    result={"created_at":datetime.now(timezone.utc).isoformat(),"origin_candidate_count":len(candidates),"strict_never_count":len(strict_ids),"relaxed_total_count":len(relaxed_ids),"relaxed_additional_rare_count":len(rare_only_ids),"cause_counts":{"strict_never":{k[1]:int(v) for k,v in cause_counts.items() if k[0]=="STRICT_NEVER"},"relaxed_additional_rare":{k[1]:int(v) for k,v in cause_counts.items() if k[0]=="RELAXED_ADDITIONAL_RARE"},"relaxed_total":detail.cause.value_counts().astype(int).to_dict()},"boil_named":{"candidate_id":str(b.candidate_id),"threshold":float(b.volume_surge_ratio),"max":float(b.dist_max),"p99":float(b.dist_p99),"threshold_percentile_pct":float(b.threshold_percentile_pct),"reachability":str(b.reachability),"cause":str(b.cause)},"full_indicator_summary":summary_df.to_dict("records"),"condition_forms":forms.to_dict("records"),"method_notes":["UNREACHABLE: volume threshold > observed training max; other indicators: zero satisfying observations.","NEAR_UNREACHABLE: volume threshold > observed training p99 but <= max.","THRESHOLD_TOO_HIGH: UNREACHABLE or NEAR_UNREACHABLE; NATURALLY_QUIET: threshold is distribution-reachable but activity remained low.","Only indicators with non-zero stored weights count toward full-pool active-indicator defect totals."],"no_source_mutation":True,"no_live_change":True,"no_training":True,"no_order":True,"no_delete":True}
    SUMMARY.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")

    ssum=summary_df.set_index("component")
    lines=["# 위험구조 3단계 — 임계 도달가능성 진단","",f"- 기준 원본: {len(candidates):,}개","- 방식: 저장 룰 임계와 각 종목 학습기간 일봉 지표 분포만 대조","- 운영/원본/라이브 변경: 없음","","## 1. NEVER/RARELY 거래량 임계 원인","",f"- 엄격 NEVER_FIRED: {len(strict_ids):,}개",f"  - THRESHOLD_TOO_HIGH: {cause_counts.get(('STRICT_NEVER','THRESHOLD_TOO_HIGH'),0):,}개",f"  - NATURALLY_QUIET: {cause_counts.get(('STRICT_NEVER','NATURALLY_QUIET'),0):,}개",f"- 완화 추가 RARELY_ACTIVE: {len(rare_only_ids):,}개",f"  - THRESHOLD_TOO_HIGH: {cause_counts.get(('RELAXED_ADDITIONAL_RARE','THRESHOLD_TOO_HIGH'),0):,}개",f"  - NATURALLY_QUIET: {cause_counts.get(('RELAXED_ADDITIONAL_RARE','NATURALLY_QUIET'),0):,}개","","## 2. BOIL 원형", "",f"- 후보: {b.candidate_id}",f"- 임계: {b.volume_surge_ratio:.6g}",f"- 학습기간 max / p99: {b.dist_max:.6g} / {b.dist_p99:.6g}",f"- 도달가능성: **{b.reachability}**",f"- 원인: **{b.cause}**","","## 3. 원본 전체 활성 지표의 UNREACHABLE",""]
    for comp in COMPONENTS:
        z=ssum.loc[comp];lines.append(f"- {comp}: {int(z.unreachable_candidate_count):,} / {int(z.active_candidate_count):,} ({z.unreachable_rate_pct:.2f}%)")
    z=ssum.loc["ANY_ACTIVE_CORE"];lines += [f"- 하나 이상 죽은 활성 core 조건 보유: {int(z.unreachable_candidate_count):,} / {int(z.active_candidate_count):,} ({z.unreachable_rate_pct:.2f}%)","","## 4. 룰 조건 형태","","- MA와 MACD는 임계 밴드가 아니라 불리언/교차 이벤트다.","- RSI는 유일한 양방향 밴드 조건이다.","- BB와 거래량은 단방향 임계이며, 최종 진입도 `final_score >= signal_threshold` 단방향이다.","- 따라서 ‘임계만 넘으면 점수 부여/진입’ 구조는 거래량·BB·최종 점수의 시스템 기본형이며, 모든 기술지표가 단방향인 것은 아니다.","","## 5. 판정 기준과 한계","","- 거래량 UNREACHABLE은 임계가 학습기간 관측 max보다 큰 경우, NEAR_UNREACHABLE은 p99보다 큰 경우다.","- MA/MACD/RSI/BB 전수 집계의 UNREACHABLE은 해당 학습기간에 조건 충족 관측이 0회인 활성 가중치 조건이다.","- 관측 학습기간 밖 미래 도달 가능성을 물리 법칙처럼 부정하는 뜻은 아니며, 저장 룰이 학습 데이터에서 검증되지 않았다는 진단이다.","- Stage3의 3개 exit 변형은 동일 entry 임계·활동도를 공유하므로 후보 수와 고유 entry rule 수를 함께 해석해야 한다."]
    READOUT.write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
