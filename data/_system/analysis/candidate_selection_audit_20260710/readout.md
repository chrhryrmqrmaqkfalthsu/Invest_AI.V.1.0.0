# 현재 후보 선별 기준 역추적 readout

입력 파일: `data/_system/live_slots_state.json`, `data/_system/real_dashboard_buy_candidates.json`

주의: 이번 산출물은 코드/설정/데이터 원본을 변경하지 않고, 현재 상태 파일·룰북·트레이드 산출물만 읽어 계산한 시뮬레이션이다. 새 축은 적용하지 않았고 OOS 개선 검증도 하지 않았다.

## 현재 관측값

- `live_slots_state.updated_at`: `2026-07-09T19:00:47.940809+00:00`
- 마지막 refresh: candidate_count `93`, evaluated `72`, buy_signal_count `26`, eligible_pool_count `26`
- 현재 candidate_pool: `26`개, slots: `8`개, waitlist: `18`개
- blocked_summary: `{"DROP_BAD_MAE_CAPTURE": 12, "gate_missing": 9, "not_buy_signal": 46}`
- real_dashboard export: source_section `candidate_pool`, limit `26`, report_candidate_count `93`, exported_count `26`

## 정확한 실행 파일·함수·라인

| 구간 | 파일라인 | 확인내용 |
| --- | --- | --- |
| 93 후보 report 생성 | `engine/live/elite_shadow_report.py:456-460` | stage2/stage3 수집 후 `bucket != A_core`, `-elite_score` 기준 정렬 |
| Stage2 필터/정렬/topN | `engine/live/elite_shadow_report.py:221-279` | eligible, stage2, OOS/stress metrics, anti-pattern, elite_score 정렬, ticker 중복 제거, max_unique |
| Stage3 필터/정렬/topN | `engine/live/elite_shadow_report.py:282-368` | final_rulebooks 스캔, bull/stress metrics, anti-pattern, elite_score 정렬, ticker 중복 제거, max_unique |
| 93 topN 컷 | `data/_system/ops/live_candidate_slots.py:45,346,381-382` | `MAX_CANDIDATES=93`, `report.candidates[:max_candidates]` |
| 26 live pool 컷 | `data/_system/ops/live_candidate_slots.py:392-418,451-464` | gate 존재/KEEP/held 제외/evaluate ok/should_buy 후 final_score 정렬 저장 |
| 8 슬롯 컷 | `data/_system/ops/live_candidate_slots.py:322-340` | candidate_pool 정렬 후 `SLOT_COUNT=8` 슬롯, 나머지 waitlist |
| export 26 재검증 | `scripts/export_real_dashboard_buy_candidates.py:243-248,468-505,510-538` | source_section+limit으로 source row 선택 후 full rulebook/evaluate/should_buy 재검증 |

## 현행 정렬 키 확정

1. 93개 후보 풀: `engine/live/elite_shadow_report.py:460`에서 `(bucket != 'A_core', -elite_score)` 순으로 정렬한다. 그 전에 stage2/stage3 내부에서는 각각 `(elite_score, oos_fitness, oos_expectancy_pct)` 내림차순으로 정렬한다(`elite_shadow_report.py:268`, `357`).
2. live candidate_pool 26개: `data/_system/ops/live_candidate_slots.py:318-319`에서 `(priority_group asc, final_score desc, ticker asc, candidate_id asc)`로 정렬한다. 즉 **ratio가 아니라 final_score 우선**이다. `priority_group=1`은 SPY DOWN + HIGH_VOL 후순위다.
3. export JSON 26개: `scripts/export_real_dashboard_buy_candidates.py:229-240`도 `candidate_pool`/`waitlist` 사용 시 `(priority_group asc, final_score desc, ticker, candidate_id)` 정렬 후 `limit` 컷이다.

## 현행 컷 방식 확정

- 93개: `MAX_CANDIDATES=93` top-N 컷이다. 그 이전에 `build_elite_shadow_report(stage2_limit=60, stage3_limit=80)`에서 stage2/stage3별 성능·룰 필터를 통과하고 ticker 중복 제거된 후보만 들어온다.
- 26개: 별도 고정 N으로 직접 만든 것이 아니라, 현재 93개 중 `gate_keep=True`, 보유 제외 아님, `evaluate_candidate(...).ok=True`, `should_buy=True`를 통과한 개수다. 현재 상태에서는 그 결과가 26개다.
- 8개 슬롯: 26개 candidate_pool을 같은 정렬키로 정렬한 뒤 `SLOT_COUNT=8` 상위 8개만 슬롯에 배치하고 나머지는 waitlist다.
- export 파일의 26개: 현재 `real_dashboard_buy_candidates.json`의 `export_meta` 기준 `source_section=candidate_pool`, `limit=26`으로 candidate_pool 전체를 가져온 뒤 full rulebook과 should_buy를 재검증해 내보낸 결과다.

## 게이트 필드 전체 목록

### Stage2 report 게이트

`eligible=True`, `stage='stage2'`, `oos_expectancy_pct >= 2.7`, `oos_fitness >= 70`, `oos_trade_count >= 15`, `oos_win_rate >= 70`, `stress_expectancy_pct >= 0.5`, `worst_drawdown_pct > -18`, `min_trade_count >= 8`, source row 존재, anti-pattern 필터 통과.

anti-pattern 필터는 `rulebook.expectancy_pct >= 2.7`, `fitness >= 70(stage2)`, market adjustment OK, fixed exit이면 `max_holding_days <= 19`, 전체 `max_holding_days <= 24`, `take_profit_atr >= 1.2`, 나쁜 target/stop shape 배제다.

### Stage3 report 게이트

`bull_metrics` 또는 `stress_metrics` 기반 `expectancy_pct >= 2.7`, `fitness >= 45`, `win_rate >= 70`, `trade_count >= 8`, `max_drawdown_pct > -18`, anti-pattern 필터 통과다. Stage3에서 `stress_expectancy_pct`는 bucket/score 참고에는 남지만 현 코드의 hard gate는 아니다.

### Live 26 후보 게이트

`candidate_id`가 gate map에 존재, `gate_keep=True`, held exclusion 아님, `evaluate_candidate.ok=True`, `evaluate_signal.should_buy=True(final_score >= rulebook.signal_threshold)`다. Entry Quality는 코드 주석상 `EQ_FILTER_UNVERIFIED_REFERENCE_ONLY_NOT_A_GATE`로 후보 자격·정렬에서 제외된다.

### Export 26 게이트

source row가 현재 report에 존재, full rulebook 로딩 성공, full rulebook key 수/필수키 검증 통과, `evaluate_candidate.ok=True`, `should_buy=True`, candidate row validation 통과다.

## exit 실적 반영 판정

판정: **PARTIALLY_REFLECTED**

근거:

- 반영됨: report 단계에서 `expectancy_pct`, `fitness`, `win_rate`, `trade_count`, `max_drawdown_pct` 같은 precomputed 성능 지표가 hard gate와 `elite_score`에 들어간다. 관련 라인: `engine/live/elite_shadow_report.py:79-131`, `221-268`, `282-357`.
- 반영됨: live 26 단계에서 `live_candidate_list_20260707.json`의 `gate_keep`가 사용되며, 이 gate는 `entry_filter_candidates.csv`의 `drop_bad_mae_capture`에서 온다. 관련 라인: `data/_system/ops/live_candidate_slots.py:172-214`, `392-398`.
- 반영 안 됨: 현재 93/26 선별 실행 경로는 `include_trades=False`다. 따라서 `exit_trades.jsonl`에서 rule_hash별 평균 PnL, MAE/MFE, exit 분포를 직접 계산한 `trade_summary`는 현재 선별/정렬에 들어가지 않는다. 관련 라인: `data/_system/ops/live_candidate_slots.py:381`, `scripts/export_real_dashboard_buy_candidates.py:446`, 그리고 trade_summary 생성은 `engine/live/elite_shadow_report.py:427-452`이나 `build_elite_shadow_report(...):461-462`에서 `include_trades=True`일 때만 실행된다.
- 반영 안 됨: exit 분포(`stop_loss`, `time_out`, `take_profit` 분포)는 source row에 존재할 수 있지만 현재 selector의 hard gate/정렬키에서 직접 참조되지 않는다.

따라서 “과거 성능 요약 일부는 반영”되지만, 사용자가 지적한 **rule_hash별 실제 exit 파일 기반 평균 PnL·MAE/MFE·exit 분포는 현행 93→26 선별 기준에 직접 반영되지 않는다.**

## CE·BOIL 직접 exit 실적 요약

| ticker | ratio | avg_pnl | win | MAE | MFE | stop+timeout% | vol_w | sec_w | sector_score |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| BOIL | 1.250 | 1.155 | 67.3 | -8.453 | 11.468 | 32.7 | 0.000 | -0.544 | 100.0 |
| CE | 1.009 | -1.020 | 41.8 | -5.567 | 4.672 | 88.6 | 0.089 | -0.621 | 100.0 |

## 새 축 후보 시뮬레이션 결과 — 적용 금지

| axis | pass | fail | UNKNOWN | CE | BOIL | fail_tickers |
| --- | ---: | ---: | ---: | --- | --- | --- |
| score_ratio_ge_1_15 | 24 | 2 | 0 | FAIL | PASS | CIEN, CE |
| score_ratio_ge_1_25 | 20 | 6 | 0 | FAIL | PASS | BB, CDE, CIEN, ARKW, CRS, CE |
| direct_exit_avg_pnl_positive | 23 | 3 | 0 | FAIL | PASS | ALGT(stage3), CBRL, CE |
| direct_exit_win_rate_ge_50 | 18 | 8 | 0 | FAIL | PASS | ALGT(stage3), BKSY, AAP, BB, CDE, ARKW, CBRL, CE |
| direct_exit_stop_timeout_le_70pct | 17 | 9 | 0 | FAIL | PASS | BCS, ALGT(stage3), BKSY, ADMA, CDE, BWXT, CEF, CBRL, CE |
| direct_exit_mfe_gt_abs_mae | 25 | 1 | 0 | FAIL | PASS | CE |
| high_vol_requires_volume_weight_gt_0_25 | 24 | 2 | 0 | PASS | FAIL | ACMR, BOIL |
| bb_center_low_margin_block | 22 | 4 | 0 | FAIL | FAIL | CDE, CIEN, BOIL, CE |
| sector_negative_on_100_score_block | 20 | 6 | 0 | FAIL | FAIL | BCS, BWXT, ARKW, CRS, BOIL, CE |

## 축 정의

- `score_ratio_ge_1_15`: 현재 final_score/threshold >= 1.15
- `score_ratio_ge_1_25`: 현재 final_score/threshold >= 1.25
- `direct_exit_avg_pnl_positive`: 동일 rule_hash exit_trades 평균 pnl_pct > 0
- `direct_exit_win_rate_ge_50`: 동일 rule_hash exit_trades 승률 >= 50%
- `direct_exit_stop_timeout_le_70pct`: 동일 rule_hash stop_loss+time_out 비중 <= 70%
- `direct_exit_mfe_gt_abs_mae`: 동일 rule_hash 평균 MFE > |평균 MAE|
- `high_vol_requires_volume_weight_gt_0_25`: HIGH_VOL이면 weight_volume_surge > 0.25 필요
- `bb_center_low_margin_block`: BB 중심 + 낮은 점수여유 차단. bb>0, ratio<1.30, ma/macd/volume/events 기여합<0.25이면 탈락
- `sector_negative_on_100_score_block`: sector_score=100인데 sector_strength_weight<0이면 탈락

## 산출 CSV

- `filter_simulation.csv`: 축별 pass/fail/UNKNOWN, 탈락 리스트, CE/BOIL 판정

## 판정 요약

- 93개 기준은 stage2/stage3 사전 성능 게이트 + elite_score 정렬 + top-N이다.
- 26개 기준은 93개 중 gate_keep + 현재 should_buy 통과분이다.
- 26개 정렬은 ratio가 아니라 final_score 우선이다.
- exit 실적은 PARTIALLY_REFLECTED다. precomputed 성능과 MAE/MFE 기반 gate 일부는 반영되지만, 현재 selector는 rule_hash별 exit_trades 직접 평균 PnL·MAE/MFE·exit 분포를 계산해 쓰지 않는다.
- 새 축 시뮬레이션에서 CE는 대부분의 exit/ratio/sector/BB저여유 축에서 탈락한다.
- BOIL은 평균 PnL·승률 축은 통과하지만, HIGH_VOL 볼륨확인 축, BB저여유 축, sector negative 축에서 탈락한다.
