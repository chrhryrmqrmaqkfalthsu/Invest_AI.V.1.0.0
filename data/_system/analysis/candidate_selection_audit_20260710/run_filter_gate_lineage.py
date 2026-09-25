from __future__ import annotations

import csv
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
CHAIN_CSV = OUT / "filter_gate_chain.csv"
OUTPUT_CSV = OUT / "filter_gate_outputs.csv"
SNAPSHOT_JSON = OUT / "filter_gate_lineage_snapshot.json"
BATCH = ROOT / "exp_batch_stage123_2009_20260616_full"
TICKERS = BATCH / "tickers"


def utc_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else ""


def kst_iso(ts: float) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().isoformat()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def count_jsonl(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("rb") as handle:
        return sum(1 for line in handle if line.strip())


def glob_stats(pattern: str, *, count_lines: bool = False) -> dict[str, Any]:
    paths = sorted(path for path in ROOT.glob(pattern) if path.is_file())
    mtimes = [path.stat().st_mtime for path in paths]
    return {
        "path_count": len(paths),
        "size_bytes": sum(path.stat().st_size for path in paths),
        "record_count": sum(count_jsonl(path) for path in paths) if count_lines else "",
        "latest_modified_utc": utc_iso(max(mtimes)) if mtimes else "",
        "sample_paths": "|".join(path.relative_to(ROOT).as_posix() for path in paths[:3]),
    }


def one_stats(path_text: str, *, count_lines: bool = False) -> dict[str, Any]:
    path = ROOT / path_text
    if not path.is_file():
        return {"path_count": 0, "size_bytes": 0, "record_count": "", "latest_modified_utc": "", "sample_paths": ""}
    return {
        "path_count": 1,
        "size_bytes": path.stat().st_size,
        "record_count": count_jsonl(path) if count_lines else "",
        "latest_modified_utc": utc_iso(path.stat().st_mtime),
        "sample_paths": path_text,
    }


def effective_stage2_dirs() -> list[Path]:
    result: list[Path] = []
    for ticker_dir in TICKERS.iterdir():
        if not ticker_dir.is_dir():
            continue
        candidates = [
            path for path in ticker_dir.iterdir()
            if path.is_dir() and path.name.startswith("stage2") and (path / "summary.json").is_file()
        ]
        if candidates:
            result.append(max(candidates, key=lambda path: (path / "summary.json").stat().st_mtime))
    return sorted(result)


def aggregate_training_counts() -> dict[str, Any]:
    s2_totals: Counter[str] = Counter()
    first_fail: Counter[str] = Counter()
    s2_dirs = effective_stage2_dirs()
    for directory in s2_dirs:
        payload = load_json(directory / "summary.json") or {}
        for key in ("generated_rulebook_rows", "unique_rulebook_hashes", "survivor_count"):
            s2_totals[key] += int(payload.get(key) or 0)
        first_fail.update({key: int(value) for key, value in (payload.get("fail_counts_by_first_failed_period") or {}).items()})

    stage2_flow = []
    alive = int(s2_totals["unique_rulebook_hashes"])
    for label in ("stress_pre_2022h1", "train_3_eval", "train_2_eval", "train_1_eval", "oos_2025h2"):
        failed = int(first_fail.get(label, 0))
        stage2_flow.append({"label": label, "input": alive, "failed": failed, "passed": alive - failed})
        alive -= failed

    stage3: dict[str, Any] = {}
    specs = {
        "qualify": ("qualify_result.json", ("unique_candidate_count", "all3_pass_count")),
        "entry": ("entry_result.json", ("pool_count", "absolute_pass_count", "overlap_rejected_count", "selected_count")),
        "exit": ("exit_result.json", ("entry_count", "final_rulebook_count")),
        "validate": ("validate_result.json", ("candidate_count", "eligible_count", "ineligible_count")),
    }
    for key, (filename, fields) in specs.items():
        totals: Counter[str] = Counter()
        files = 0
        qualified = 0
        for path in TICKERS.glob(f"*/stage3/{filename}"):
            payload = load_json(path) or {}
            files += 1
            for field in fields:
                totals[field] += int(payload.get(field) or 0)
            if filename == "qualify_result.json" and payload.get("qualified") is True:
                qualified += 1
        stage3[key] = {"file_count": files, **dict(totals)}
        if filename == "qualify_result.json":
            stage3[key]["qualified_tickers"] = qualified
    return {
        "stage2_dirs": len(s2_dirs),
        "stage2_totals": dict(s2_totals),
        "stage2_first_fail": dict(first_fail),
        "stage2_flow": stage2_flow,
        "stage3": stage3,
    }


def elite_counts() -> dict[str, Any]:
    import engine.live.elite_shadow_report as elite

    stage2_all, skip2 = elite.collect_stage2_elite(max_unique=100000)
    stage3_all, skip3 = elite.collect_stage3_elite(max_unique=100000)
    stage2_cap, _ = elite.collect_stage2_elite(max_unique=60)
    stage3_cap, _ = elite.collect_stage3_elite(max_unique=80)
    pre_cap = stage2_cap + stage3_cap
    post_deny, deny = elite.apply_candidate_denylist(pre_cap)

    profile_keys: set[tuple[str, str]] = set()
    for path in TICKERS.glob("*/stage3/stage3_profile_catalog.jsonl"):
        ticker = path.parts[-3]
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                profile_keys.add((str(row.get("ticker") or ticker).upper(), str(row.get("rulebook_hash") or "")))

    pool_keys: dict[str, set[tuple[str, str]]] = {}
    for filename in ("stage3_live_pool.jsonl", "stage3_live_pool_filtered.jsonl"):
        path = ROOT / "data/_system/central/stage3_live_pool" / filename
        keys: set[tuple[str, str]] = set()
        if path.is_file():
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    if line.strip():
                        row = json.loads(line)
                        keys.add((str(row.get("ticker") or "").upper(), str(row.get("rulebook_hash") or "")))
        pool_keys[filename] = keys

    post_stage3 = [row for row in post_deny if row.get("stage") == "stage3"]
    post_stage3_keys = {(str(row.get("ticker") or "").upper(), str(row.get("rulebook_hash") or "")) for row in post_stage3}

    central_rows = sum(1 for row in elite._load_jsonl(elite.CENTRAL_INDEX) if row)
    stage2_rows = central_rows - int(skip2.get("not_eligible", 0)) - int(skip2.get("not_stage2", 0))
    stage2_static_pass = stage2_rows - sum(
        int(value) for key, value in skip2.items() if key not in {"not_eligible", "not_stage2"}
    )
    stage3_final_rows = sum(count_jsonl(path) for path in TICKERS.glob("*/stage3/final_rulebooks.jsonl"))
    stage3_static_pass = stage3_final_rows - sum(int(value) for value in skip3.values())

    return {
        "central_index_rows": central_rows,
        "stage2_eligible_rows": stage2_rows,
        "stage2_static_pass_rows": stage2_static_pass,
        "stage2_unique_tickers": len(stage2_all),
        "stage2_cap_count": len(stage2_cap),
        "stage2_skip_counts": dict(skip2),
        "stage3_final_rows": stage3_final_rows,
        "stage3_static_pass_rows": stage3_static_pass,
        "stage3_unique_tickers": len(stage3_all),
        "stage3_cap_count": len(stage3_cap),
        "stage3_skip_counts": dict(skip3),
        "pre_deny_count": len(pre_cap),
        "post_deny_count": len(post_deny),
        "post_deny_stage_counts": dict(Counter(str(row.get("stage") or "") for row in post_deny)),
        "denylist": deny,
        "stage3_live_profile_overlap": len(post_stage3_keys & profile_keys),
        "stage3_live_not_in_profile": len(post_stage3_keys - profile_keys),
        "stage3_live_pool_overlap": len(post_stage3_keys & pool_keys["stage3_live_pool.jsonl"]),
        "stage3_live_pool_filtered_overlap": len(post_stage3_keys & pool_keys["stage3_live_pool_filtered.jsonl"]),
        "profile_key_count": len(profile_keys),
    }


def current_state() -> dict[str, Any]:
    slots_path = ROOT / "data/_system/live_slots_state.json"
    dash_path = ROOT / "data/_system/real_dashboard_buy_candidates.json"
    gate_path = ROOT / "data/_system/live_candidate_list_20260707.json"
    deny_path = ROOT / "data/_system/candidate_denylist.json"
    slots = load_json(slots_path) or {}
    dash = load_json(dash_path) or {}
    gate = load_json(gate_path) or {}
    deny = load_json(deny_path) or {}
    pool_ids = {str(row.get("candidate_id")) for row in (slots.get("candidate_pool") or []) if row.get("candidate_id")}
    dash_ids = set((dash.get("candidates") or {}).keys())
    return {
        "slots": {
            "updated_at": slots.get("updated_at"),
            "candidate_pool": len(slots.get("candidate_pool") or []),
            "slots": len(slots.get("slots") or []),
            "waitlist": len(slots.get("waitlist") or []),
            "held_exclusions": len(slots.get("held_exclusions") or {}),
            "last_refresh": slots.get("last_refresh") or {},
            "decision_gate": slots.get("decision_gate") or {},
        },
        "dashboard": {
            "updated_at": dash.get("updated_at"),
            "candidate_count": len(dash.get("candidates") or {}),
            "export_meta": dash.get("export_meta") or {},
            "current_pool_overlap": len(pool_ids & dash_ids),
            "dashboard_only": sorted(dash_ids - pool_ids),
            "pool_only": sorted(pool_ids - dash_ids),
        },
        "live_gate_list": {
            "created_at": gate.get("created_at"),
            "candidate_count": len(gate.get("candidates") or []),
            "summary": gate.get("summary") or {},
            "gate_rule": gate.get("gate_rule"),
        },
        "denylist": {"version": deny.get("version"), "entry_count": len(deny.get("entries") or []), "match_policy": deny.get("match_policy")},
    }


def file_row(name: str, path_pattern: str, producer: str, status: str, live_use: str, regen: str, note: str, *, exact: bool = False, lines: bool = False) -> dict[str, Any]:
    stats = one_stats(path_pattern, count_lines=lines) if exact else glob_stats(path_pattern, count_lines=lines)
    return {
        "artifact": name,
        "path_pattern": path_pattern,
        "producer": producer,
        "live_usage": live_use,
        "verdict": status,
        "regeneration": regen,
        "note": note,
        **stats,
    }


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    training = aggregate_training_counts()
    elite = elite_counts()
    state = current_state()

    s2 = training["stage2_flow"]
    s3 = training["stage3"]
    refresh = state["slots"]["last_refresh"]
    chain: list[dict[str, Any]] = []

    def gate(order: int, branch: str, name: str, code: str, inp: str, out: str, criteria: str, input_count: int, pass_count: int, reject_count: int, count_note: str, conflict: str = "") -> None:
        chain.append({
            "order": order, "branch": branch, "gate_name": name, "code": code,
            "input": inp, "output": out, "criteria": criteria,
            "input_count": input_count, "pass_count": pass_count,
            "reject_count": reject_count, "count_note": count_note,
            "overlap_or_conflict": conflict,
        })

    gate(10, "Stage2", "GA hash 대표화", "run_stage2.py::build_representatives", "rulebooks_all", "in-memory representatives", "rulebook_hash 중복 제거; 같은 hash는 train_fitness 최고 대표", training["stage2_totals"]["generated_rulebook_rows"], training["stage2_totals"]["unique_rulebook_hashes"], training["stage2_totals"]["generated_rulebook_rows"]-training["stage2_totals"]["unique_rulebook_hashes"], "1997개 유효 Stage2 디렉터리 합계")
    labels = [
        (20, "stress", "expectancy>=1, MDD>=-20, expectancy*trades/abs(MDD)>1"),
        (30, "train_3", "trades>=5, member_score>=10, expectancy>=1"),
        (40, "train_2", "trades>=5, member_score>=10, expectancy>=1"),
        (50, "train_1", "trades>=5, member_score>=10, expectancy>=1"),
        (60, "oos_2025h2", "trades>=5, member_score>=10, expectancy>=1, MDD>=-15"),
    ]
    for flow, spec in zip(s2, labels):
        order, short, criteria = spec
        gate(order, "Stage2", f"Stage2 순차 gate: {short}", "stage2_gate.py::stage2_fail_reasons", flow["label"], "next alive set / period_metrics_all / early_cut_log", criteria, flow["input"], flow["passed"], flow["failed"], "첫 실패 시 후속 기간 미평가")
    gate(70, "Stage2", "Stage2 survivor 저장", "run_stage2.py::evaluate_periods", "5개 gate 통과 hash", "survivors.jsonl", "5개 기간 모두 통과", training["stage2_totals"]["unique_rulebook_hashes"], training["stage2_totals"]["survivor_count"], training["stage2_totals"]["unique_rulebook_hashes"]-training["stage2_totals"]["survivor_count"], "최종 survivor")

    q=s3["qualify"]; gate(100,"Stage3","qualify 3년 절대 gate","stage3_gate.py::stage3_qualify_fail_reasons","3개 split GA top hash","qualify_result.json","각 3년 모두 trades>=5, member_score>=10, expectancy>=2; 3/3",q.get("unique_candidate_count",0),q.get("all3_pass_count",0),q.get("unique_candidate_count",0)-q.get("all3_pass_count",0),f"{q.get('file_count')}개 qualify 결과; qualified ticker {q.get('qualified_tickers')}","개별 qualify 통과 룰북은 저장하지 않아 이후 재게이트 불가")
    e=s3["entry"]; gate(110,"Stage3","entry 절대 expectancy cut","run_stage3_aggressive.py::_select_diverse_entry_rows", "entry GA top100", "eligible entry rows", "train_3 expectancy>=2.0",e.get("pool_count",0),e.get("absolute_pass_count",0),e.get("pool_count",0)-e.get("absolute_pass_count",0),f"{e.get('file_count')}개 entry_result")
    gate(120,"Stage3","entry 중복 제거 + top20","run_stage3_aggressive.py::_select_diverse_entry_rows","absolute-pass entry","entry_rulebooks.jsonl","entry-date Jaccard>=0.7 reject; ticker당 최대20",e.get("absolute_pass_count",0),e.get("selected_count",0),e.get("overlap_rejected_count",0),f"top20 도달 후 나머지 {e.get('absolute_pass_count',0)-e.get('selected_count',0)-e.get('overlap_rejected_count',0)}개는 rejected 파일에도 기록되지 않음","중복 제거와 top20 cap이 한 루프에 섞여 탈락 계보가 불완전")
    x=s3["exit"]; gate(130,"Stage3","exit GA top3","run_stage3_aggressive.py::_run_exit_ga_for_entry","entry_rulebooks","final_rulebooks.jsonl","entry별 exit gene 60개체/25세대 후 composite_fitness 상위3",x.get("entry_count",0),x.get("final_rulebook_count",0),0,f"{x.get('file_count')}개 완료 exit_result; 출력은 입력당 3개")
    v=s3["validate"]; gate(140,"Stage3","pure-OOS basic eligibility","stage3_gate.py::stage3_basic_eligibility","final_rulebooks","stage3_profile_catalog / stage3_ineligible","train_1, train_2, recent_1y 각각 expectancy>=1; MDD/보유일은 label만",v.get("candidate_count",0),v.get("eligible_count",0),v.get("ineligible_count",0),f"{v.get('file_count')}개 validate_result","현재 elite live는 이 catalog를 우회하고 final_rulebooks를 직접 필터링")

    gate(200,"Active elite","Stage2 elite static filter","elite_shadow_report.py::collect_stage2_elite","central_index eligible Stage2 rows","ranked Stage2 candidates","OOS exp>=2.7, fitness>=70, trades>=15, win>=70, stress exp>=0.5, DD>-18, min trades>=8 + anti-pattern",elite["stage2_eligible_rows"],elite["stage2_static_pass_rows"],elite["stage2_eligible_rows"]-elite["stage2_static_pass_rows"],"static pass 후 ticker top1; 13 ticker","Stage2 survivor gate와 대부분 중복; stress 0.5는 기존 survivor 1.0보다 느슨해 실질 무효")
    gate(210,"Active elite","Stage3 elite static filter","elite_shadow_report.py::collect_stage3_elite","canonical final_rulebooks","ranked Stage3 candidates","bull/stress metrics exp>=2.7, fitness>=45, win>=70, trades>=8, DD>-18 + anti-pattern",elite["stage3_final_rows"],elite["stage3_static_pass_rows"],elite["stage3_final_rows"]-elite["stage3_static_pass_rows"],f"static pass→ticker top1 {elite['stage3_unique_tickers']}→top80","validate pure-OOS catalog 우회; 현역 Stage3 70개 중 profile catalog 포함 9개")
    gate(220,"Active elite","ticker dedup + stage cap","elite_shadow_report.py::collect_stage*_elite","static pass rows","Stage2<=60 + Stage3<=80","elite_score 내림차순; ticker당 1개",elite["stage2_static_pass_rows"]+elite["stage3_static_pass_rows"],elite["pre_deny_count"],elite["stage2_static_pass_rows"]+elite["stage3_static_pass_rows"]-elite["pre_deny_count"],f"Stage2 {elite['stage2_cap_count']} + Stage3 {elite['stage3_cap_count']}","denylist가 이 뒤에 적용되어 차단 ticker의 차순위 후보 fallback 없음")
    gate(230,"Active elite","candidate denylist","elite_shadow_report.py::apply_candidate_denylist","93 capped candidates","82 report candidates","candidate_id exact 또는 ticker/stage 제약 rule_hash; inactive entry 제외",elite["pre_deny_count"],elite["post_deny_count"],elite["pre_deny_count"]-elite["post_deny_count"],"Stage2 12 + Stage3 70","per-ticker top1 뒤 적용되어 11개 ticker가 대체 후보 없이 사라짐")

    gate(300,"Live slots","정규장 decision gate","regular_hours_gate.py::regular_hours_snapshot","report candidates","fresh evaluation 또는 cached pool","미국 평일 09:30<=ET<16:00",elite["post_deny_count"],elite["post_deny_count"],0,"장외에는 후보 재평가 없이 cached pool 재사용","거래소 휴장·조기폐장 미반영")
    gate(310,"Live slots","MAE/MFE cached gate join","live_candidate_slots.py::load_gate_list","82 report candidates","gate-known KEEP candidates","93 역사 후보표와 candidate_id join; missing 차단; DROP_BAD_MAE_CAPTURE 차단",refresh.get("candidate_count",0),refresh.get("evaluated",0),sum((refresh.get("blocked_summary") or {}).get(key,0) for key in ("gate_missing","DROP_BAD_MAE_CAPTURE")),"현재 gate_missing=9, drop=10, evaluated=63","gate list는 denylist 전 93개 스냅샷이라 현재 후보 9개를 missing으로 자동 차단")
    gate(320,"Live slots","실시간 should_buy","elite_shadow_trader.py::evaluate_candidate / evaluate_signal","gate KEEP candidates","buy candidate_pool","OHLCV/시장/섹터/VIX/뉴스/이벤트 반영 score>=rulebook threshold",refresh.get("evaluated",0),refresh.get("eligible_pool_count",0),(refresh.get("blocked_summary") or {}).get("not_buy_signal",0),"현재 63 평가→17 BUY","Stage2/3 정적 점수와 실시간 신호 gate가 서로 다른 지표 체계")
    gate(330,"Live slots","우선순위 + 8-slot cut","live_candidate_slots.py::sort_candidate_pool/rebuild_slots_from_pool","17 BUY candidates","8 slots + 9 waitlist","SPY DOWN & HIGH_VOL은 priority_group=1 후순위; final_score desc; held 제외",state["slots"]["candidate_pool"],state["slots"]["slots"],state["slots"]["waitlist"],"후순위는 탈락이 아니라 정렬","max_candidates=93은 denylist 전 역사 개수와 결합된 상수")
    gate(340,"Dashboard export","full rulebook + should_buy 재검증","export_real_dashboard_buy_candidates.py::build_export_payload","live_slots_state section","real_dashboard_buy_candidates.json","current elite ID match, full rulebook>=50 keys/필수키, source exists, evaluate_candidate ok, should_buy true",state["dashboard"]["export_meta"].get("live_slot_count",0),state["dashboard"]["candidate_count"],state["dashboard"]["export_meta"].get("skipped_count",0),"현재 파일은 과거 candidate_pool 18개 export; 현재 pool은 17","동일 should_buy를 두 번 평가하며 파일은 현재 state보다 약 5시간 오래됨")

    gate(400,"Inactive Stage3 mix","profile catalog first filter","build_stage3_live_pool.py::row_passes_first_filter","historical profile catalog 593 rows","stage3_live_pool 232 rows","eligible, 3 OOS, exp>=1, trades>=5, DD>=-40, PF>=1; ticker당<=10",593,232,166+195,"427 first-pass; 195 ticker cap","6/26 snapshot; 최신 catalog 2012행 미반영, 현재 process 미사용")
    gate(410,"Inactive Stage3 mix","payoff-ratio stricter filter","build_stage3_live_pool.py with min_worst_payoff_ratio=1","historical profile catalog 723 rows","stage3_live_pool_filtered 186 rows","기본 기준 + 각 OOS payoff_ratio>=1; ticker당<=10",723,186,479+58,"244 first-pass; 58 ticker cap","코드 consumer 없음; 서로 다른 시점/입력 593 vs 723으로 두 pool 비교 불가")

    outputs = [
        file_row("Stage2 period metrics", "exp_batch_stage123_2009_20260616_full/tickers/*/stage2*/period_metrics_all.csv", "run_stage2.py::evaluate_periods", "REGEN_OK", "not directly consumed", "rulebooks_all에서 재평가", "Stage2 gate 행별 근거"),
        file_row("Stage2 early-cut log", "exp_batch_stage123_2009_20260616_full/tickers/*/stage2*/early_cut_log.csv", "run_stage2.py::evaluate_periods", "REGEN_OK", "not directly consumed", "rulebooks_all에서 재평가", "첫 실패·미평가 기간 기록"),
        file_row("Stage2 survivors", "exp_batch_stage123_2009_20260616_full/tickers/*/stage2*/survivors.jsonl", "run_stage2.py::evaluate_periods", "ACTIVE_LIVE", "elite report and central-control direct", "rulebooks_all에서 재생성 가능하지만 현재 삭제 금지", "현재 live가 source_file/source_row_index로 직접 로드", lines=True),
        file_row("central index", "exp_batch_stage123_2009_20260616_full/central_index.jsonl", "run_stage23_batch.py::build_*_central_index_rows", "ACTIVE_LIVE", "Stage2 elite and central-control direct", "survivors/final outputs에서 재색인", "153,561행 중 다수 비적격·Stage3 기록 포함", exact=True, lines=True),
        file_row("Stage3 validation results", "exp_batch_stage123_2009_20260616_full/tickers/*/stage3*/validation_results.jsonl", "run_stage3_aggressive.py::run_validate", "REGEN_OK", "not active elite input", "final_rulebooks 재검증", "active elite가 우회", lines=True),
        file_row("Stage3 profile catalog", "exp_batch_stage123_2009_20260616_full/tickers/*/stage3*/stage3_profile_catalog.jsonl", "run_stage3_aggressive.py::run_validate", "REGEN_OK", "inactive stage3 mix source only", "final_rulebooks 재검증", "현역 Stage3 70개 중 9개만 catalog와 일치", lines=True),
        file_row("Stage3 ineligible", "exp_batch_stage123_2009_20260616_full/tickers/*/stage3*/stage3_ineligible.jsonl", "run_stage3_aggressive.py::run_validate", "REGEN_OK", "not consumed", "final_rulebooks 재검증", "validate 탈락 근거", lines=True),
        file_row("Stage3 live pool", "data/_system/central/stage3_live_pool/stage3_live_pool.jsonl", "build_stage3_live_pool.py", "STALE_OUTPUT", "supported only when run_live --central-stage3-mix on; currently off/not running", "최신 profile catalog에서 재생성", "6/26 593행 source snapshot; 최신 canonical catalog 2012행 미반영", exact=True, lines=True),
        file_row("Stage3 live pool filtered", "data/_system/central/stage3_live_pool/stage3_live_pool_filtered.jsonl", "build_stage3_live_pool.py custom output", "STALE_OUTPUT", "no code consumer found", "최신 profile catalog에서 재생성", "6/26 723행 source snapshot; payoff ratio variant", exact=True, lines=True),
        file_row("Stage3 live-pool summaries/rejected samples", "data/_system/central/stage3_live_pool/*summary*.json", "build_stage3_live_pool.py", "STALE_OUTPUT", "not current runtime", "pool rebuild와 함께 재생성", "stale pool metadata"),
        file_row("candidate denylist", "data/_system/candidate_denylist.json", "manual/audit policy", "ACTIVE_LIVE", "elite report direct", "not a generated output", "Stage2/3 cap·ticker dedup 뒤, merge 직후 적용", exact=True),
        file_row("MAE/MFE live gate list", "data/_system/live_candidate_list_20260707.json", "live_candidate_slots.py::derive_gate_list", "ACTIVE_LIVE", "live slot daemon direct", "analysis gate source CSVs에서 재생성", "93개 역사 후보 기반; 현재 report 82 중 9개 missing", exact=True),
        file_row("live slots state", "data/_system/live_slots_state.json", "live_candidate_slots.py::refresh_slots", "ACTIVE_LIVE", "daemon/API/export direct", "elite report+gate+signal에서 1분 단위 재생성", "현재 17 pool, 8 slots, 9 waitlist", exact=True),
        file_row("real dashboard buy candidates", "data/_system/real_dashboard_buy_candidates.json", "export_real_dashboard_buy_candidates.py", "ACTIVE_LIVE", "real dashboard/manual-buy API direct", "live_slots state에서 full-rulebook 재검증 후 재생성", "활성 소비 파일이지만 현재 pool보다 오래돼 BTE 1개가 stale", exact=True),
        file_row("live slot events", "data/_system/live_slots_events.jsonl", "live_candidate_slots.py::append_event", "REVIEW", "dashboard/audit history", "daemon event로 재축적 가능하나 과거 exact 복구 불가", "필터 산출물보다 운영 이력", exact=True, lines=True),
        file_row("historical MAE/MFE gate sources", "data/_system/analysis/entry_quality_stops_regime_20260707/entry_filter_candidates.csv", "analysis", "REGEN_OK", "only used when gate list missing/rebuilt", "analysis pipeline rerun", "analysis 절대 보존 대상", exact=True),
    ]

    write_rows(CHAIN_CSV, chain)
    write_rows(OUTPUT_CSV, outputs)
    snapshot = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "training_counts": training,
        "elite_counts": elite,
        "current_state": state,
        "output_verdict_counts": dict(Counter(row["verdict"] for row in outputs)),
        "key_conflicts": [
            "Stage3 validate catalog is bypassed by active elite; 61 of current 70 Stage3 candidates are not in profile catalog.",
            "candidate_denylist is applied after per-ticker top1, so denied ticker has no fallback selection.",
            "live MAE/MFE gate list was built for historical 93 candidates; current report has 9 candidate IDs missing and blocks them as gate_missing.",
            "real_dashboard_buy_candidates is actively consumed but older than live_slots_state and contains one stale BTE candidate.",
            "stage3_live_pool and filtered pool are old snapshots from different source sizes and are not current active inputs.",
        ],
        "recommended_gate_points": [
            {
                "rank": 1,
                "point": "after loading Stage2 survivors and Stage3 final_rulebooks, before elite_score ticker dedup/cap and before denylist",
                "reason": "no GA retraining; full candidate fallback remains available; one unified auditable gate can replace stale 93-ID join and avoid Stage3 validate bypass",
            },
            {
                "rank": 2,
                "point": "Stage3 final_rulebooks -> run_validate/profile catalog boundary",
                "reason": "best for pure-OOS historical gate; active elite must then be changed to consume the new catalog or gate output",
            },
            {
                "rank": 3,
                "point": "after evaluate_candidate should_buy and before slot top8",
                "reason": "best only for real-time market/regime constraints; too late for historical quality filtering",
            },
        ],
    }
    SNAPSHOT_JSON.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "chain_rows": len(chain),
        "output_rows": len(outputs),
        "output_verdicts": snapshot["output_verdict_counts"],
        "stage2_flow": training["stage2_flow"],
        "stage3": training["stage3"],
        "elite": {key: elite[key] for key in ("stage2_static_pass_rows", "stage3_static_pass_rows", "pre_deny_count", "post_deny_count", "stage3_live_profile_overlap", "stage3_live_not_in_profile")},
        "live_refresh": refresh,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
