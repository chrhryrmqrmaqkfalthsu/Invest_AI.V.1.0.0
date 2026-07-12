#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = json.loads((HERE / "analysis_state.json").read_text(encoding="utf-8"))
I = STATE["input"]
S = STATE["set_comparison"]
C = STATE["clusters"]
G = STATE["gap_inclusion"]

replay_verdict = "INCLUDED" if C["multi_day_cluster_count"] > 0 and G["during_logged_holding_interval"] > 0 else "STILL_SKIPPED"
filter_verdict = "CLEAN" if replay_verdict == "INCLUDED" else "HAS_GAP"
entity_verdict = "HAS_GAP"
conclusion = "2층 필터는 CLEAN이지만 1층 개체는 HAS_GAP이다. 필터 표본 재정의보다 개체의 position-independent 재학습 또는 연속신호 보조목표 도입을 우선 검토해야 한다."

summary = {
    "input": {
        "replay_universe_path": "data/_system/analysis/entry_filter_2d3pct_replay_20260712/replay_signal_universe.csv",
        "replay_rows": I["replay_rows"],
        "log_dataset_path": "data/_system/analysis/entry_filter_2d3pct_20260712/signal_dataset.csv",
        "log_rows": I["log_rows"],
        "candidate_count": I["candidate_count"],
    },
    "set_comparison": S,
    "clusters": C,
    "gap_inclusion": G,
    "verdicts": {
        "replay_continuous_signal": replay_verdict,
        "layer2_filter": filter_verdict,
        "layer1_entity": entity_verdict,
        "combined_conclusion": conclusion,
    },
    "backup": STATE["backup"],
}
(HERE / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

position_md = f"""# Replay 포지션 의존성 코드 감사

## 판정

**{replay_verdict} — replay `should_buy` 생성은 포지션 상태와 무관하다.**

- `engine/central/signal_collector.py:212-261`의 `signal_for_date(entity, date)`는 입력이 개체와 날짜뿐이다. 포지션/보유 상태 인자가 없으며 해당 날짜까지의 `df.iloc[:idx+1]`로 `evaluate_signal()`을 호출한 뒤 `sig.should_buy`를 그대로 반환한다.
- `data/_system/analysis/entry_filter_2d3pct_replay_20260712/finalize_replay_outputs.py:71-123`은 각 개체의 모든 OHLCV 거래일을 순회하고 `snap.should_buy`만 포함 조건으로 사용한다. 진입 후 날짜 점프나 보유 여부 조회가 없다.
- `engine/portfolio/daily_signal_replay.py:285-323, 440-496`도 진입일부터 청산일까지 각 날짜마다 evaluator를 다시 호출한다. 다만 이 모듈의 `daily_records`에는 명시적 `should_buy` 컬럼이 없고 `signal_valid`는 strength 계산 가능 여부이므로, 18,245행 universe의 직접 생성기는 아니다.

## 코드 검색 결론

`SignalCollector.signal_for_date`와 replay universe 생성 루프에는 `position`, `holding`, `open_position`, `in_position`을 조건으로 `should_buy`를 억제하는 분기가 없다. 따라서 2층 replay는 **순수 날짜별 should_buy 재평가**다.

## 역사 재현성 주의

이 replay는 과거 저장 로그의 bit-exact 복원이 아니라 **현재 rulebook + 현재 evaluator/context의 역사 재평가**다. 동일 candidate/date 기준으로 로그 전용 신호가 {S['log_only']:,}개 존재하므로, 포지션 누락은 메웠지만 과거 로그의 strict superset은 아니다.
"""
(HERE / "replay_position_dependency.md").write_text(position_md, encoding="utf-8")

readout = f"""# 연속/보유중 신호 학습 포함 여부 종합 판정

## 최종 판정

| 층 | 판정 | 핵심 근거 |
|---|---|---|
| 2층 replay 필터 | **{filter_verdict}** | 날짜별 position-independent replay에 연속 클러스터와 실제 로그 보유구간 신호가 존재 |
| 1층 Stage2/Stage3 개체 | **{entity_verdict}** | 공통 백테스트가 진입 후 청산+cooldown 다음 날짜로 점프해 보유중 should_buy를 평가하지 않음 |

**결론:** {conclusion}

## Step 1 — replay universe 실측

| 항목 | 값 |
|---|---:|
| replay should_buy 행 | {I['replay_rows']:,} |
| 로그 기반 행 | {I['log_rows']:,} |
| 동일 candidate/date 교집합 | {S['intersection']:,} |
| replay에만 존재 | {S['replay_only']:,} |
| 로그에만 존재 | {S['log_only']:,} |
| 단순 행수 차이 | {S['naive_row_delta']:,} |
| strict superset 여부 | 아니오 |

`replay에만 존재`하는 새 신호의 정확한 집합 수는 **{S['replay_only']:,}개**다. 단순 행수 차이 {S['naive_row_delta']:,}개와 다른 이유는 현재 evaluator/context 재평가에서 과거 로그 신호 {S['log_only']:,}개가 재현되지 않았기 때문이다.

### 연속 클러스터

| 항목 | 값 |
|---|---:|
| 전체 클러스터 | {C['cluster_count']:,} |
| 1일 클러스터 | {C['bucket_cluster_count'].get('1일', 0):,}개 / {C['bucket_signal_count'].get('1일', 0):,}신호 |
| 2일 클러스터 | {C['bucket_cluster_count'].get('2일', 0):,}개 / {C['bucket_signal_count'].get('2일', 0):,}신호 |
| 3일+ 클러스터 | {C['bucket_cluster_count'].get('3일+', 0):,}개 / {C['bucket_signal_count'].get('3일+', 0):,}신호 |
| 2일 이상 클러스터 | {C['multi_day_cluster_count']:,}개 |
| 2일 이상 클러스터 소속 신호 | {C['multi_day_signal_count']:,}개 |
| 최장 연속 | {C['max_cluster_length']:,} 거래일 |

### 진입 로그 사이·보유 중 신호

| 항목 | 값 |
|---|---:|
| replay-only 상세행 | {G['replay_only_rows']:,} |
| 실제 로그 보유구간 내부 | {G['during_logged_holding_interval']:,} |
| 연속 로그 진입일 사이 | {G['between_logged_entries']:,} |
| 같은 연속 클러스터에서 로그 진입 뒤 후속 신호 | {G['continuation_after_logged_entry_same_cluster']:,} |
| 위 증거의 합집합 | {G['union_clear_gap_evidence']:,} |

따라서 Step 1 판정은 **replay가 연속/보유중 신호를 {replay_verdict}**다.

## Step 2 — replay 포지션 의존성

`SignalCollector`는 날짜별 순수 evaluator이며 포지션 상태를 받지 않는다. replay universe 생성 루프도 전체 거래일을 순회해 `snap.should_buy`만 검사한다. 상세 코드 위치는 `replay_position_dependency.md`에 기록했다.

## Step 3 — Stage2/Stage3 원 학습 표본 정의

두 단계 모두 저장된 거래행을 supervised sample로 직접 학습하는 구조는 아니다. rulebook을 **상태ful 단일포지션 백테스트**에 넣고 체결 거래 성과를 GA fitness로 사용한다.

- flat 상태: 매 거래일 `evaluate_signal()` 실행.
- `should_buy=True`: 거래를 만들고 청산을 시뮬레이션.
- 보유 상태: `engine/learning/execution_mode_backtest.py:337-340`에서 청산 인덱스 뒤 `cooldown_days=1`까지 점프하므로 중간 should_buy를 평가하지 않는다.
- 저장: 실제 거래만 `trades.jsonl`/`rl_replay_trades.jsonl`에 남고 보유중·cooldown 신호 원 표본은 저장하지 않는다.

현재 {I['candidate_count']}개 개체(Stage2 {I['stage2_candidates']}, Stage3 {I['stage3_candidates']}) 모두 코드로 확인 가능하며 판정은 **HAS_GAP**이다. 이는 “미리 저장된 진입 로그만 다시 학습했다”는 뜻은 아니지만, 요청한 관점에서 보유중 연속 should_buy가 개체 fitness의 독립 표본으로 들어가지 않았다는 뜻이다.

## 해석과 우선순위

2층 필터 표본은 position gap을 메웠으므로 이 문제만을 이유로 replay 필터부터 다시 만들 필요는 없다. 1층 개체는 holding/cooldown 신호를 목적함수에서 보지 않으므로, 반복 지속성·신호 밀도·연속성까지 학습하려면 position-independent 전일자 signal objective 또는 auxiliary loss를 추가한 개체 재학습을 검토해야 한다.

## 제한사항

현재 replay는 과거 실행 당시 evaluator/context의 완전 복원이 아니다. 로그 전용 {S['log_only']:,}개가 이 drift를 보여준다. 따라서 2층 CLEAN은 **포지션 상태 때문에 날짜가 스킵되는가**에 대한 판정이며 역사적 신호 완전 재현성 판정은 아니다.
"""
(HERE / "readout.md").write_text(readout, encoding="utf-8")

backup = f"""# 사전 백업 기록

- 작업 전 HEAD: `{STATE['backup']['pre_head']}`
- 작업 전 annotated tag: `{STATE['backup']['tag']}`
- 원격 push: 완료
- 목적: 연속/보유중 신호 포함 검증 산출물 생성 전 저장소 복구점 보존
"""
(HERE / "backup_record.md").write_text(backup, encoding="utf-8")
print(json.dumps(summary["verdicts"], ensure_ascii=False))
