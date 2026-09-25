# AAP EEC 벌점 완화 v6 readout

## 결론

판정: **RELAX_STILL_NO_ALL3**

EEC 벌점을 `target 6 / floor 0.5`에서 `target 4 / floor 0.7`로 완화했지만, qualify 결과는 `all3=0`이었다. all2는 v5의 4개에서 61개로 크게 늘었고 fold별 pass 수도 증가했지만, 3-fold 동시 통과 개체는 나오지 않았다. 따라서 이번 완화는 all3를 만들지 못했고, 현재 feature/entry 재료의 한계 쪽으로 보는 것이 맞다.

## 실행 조건 확인

| 항목 | 값 |
|---|---|
| 대상 | AAP 단일 |
| qualify | population 100 / generation 40 × 3 folds |
| seed | `2026071401` |
| workers 요청 | `28` |
| workers 로그 검증 | `generation_best_fitness.jsonl` 전 구간 `evaluation_workers=28` |
| parallel axis | `population_fitness_evaluation` |
| merge order | `input_index_order` |
| out-dir | `data/_system/analysis/stage3_aap_eec_relax_v6_20260715/AAP` |
| auto fetch/regenerate | 비활성 |
| 보호파일 | 읽기/SHA 대조만 수행 |

주의: 현재 연결 도구는 Windows 노트북 셸에 직접 접속하지 못한다. 이번 v6는 v5와 같은 seed·28-worker·host-role notebook·local read/write 프로토콜로 실행했지만, 실제 `official_final_summary.json`의 host는 `invest-bot`으로 기록됐다. 물리 호스트까지 v5의 Windows notebook과 동일하다고 주장하지 않는다.

## 코드 diff / 전후 SHA

수정 파일: `scripts/research/stage23_rework_20260713/engine/learning/execution_mode_backtest_eec_v5.py`

```diff
-ENTRY_FITNESS_EEC_TARGET = 6.0
-ENTRY_FITNESS_EEC_FLOOR = 0.5
+ENTRY_FITNESS_EEC_TARGET = 4.0
+ENTRY_FITNESS_EEC_FLOOR = 0.7
```

| 항목 | SHA/commit |
|---|---|
| 수정 전 백업 commit | `9deb663` |
| 파라미터 변경 commit | `3e2b6c7` |
| 파일 SHA before | `161ee1a88fa617116a1ec017233655502a65bc0fd8e2df2843edf6667ea415dc` |
| 파일 SHA after | `d0882a88918b842b2b987cfff1abcaa2742a7d077a2a56e4b1b2aa483db560ac` |

진입/청산·should_buy·strict interval·legacy·fixed-sizing·거래수 factor·win_rate gate는 변경하지 않았다.

## 정적 검증

| 검증 | 결과 |
|---|---:|
| py_compile | PASS |
| EEC=4 → multiplier 1.0 무보정 | PASS |
| EEC=2 → multiplier 0.7 floor 감쇠 | PASS |
| legacy import/result bitwise 불변 | PASS |
| execution module auto-patch 없음 | PASS |
| mutation helper AST SHA 불변 | `aab7163f9194cf5f989ad01973e8d2967dad48be53f7d52ee09747eea502077d` |

## 핵심 결과

| 항목 | 결과 |
|---|---:|
| all3/all2/all1/all0 | `0/61/200/39` |
| all3 count | `0` |
| fold별 pass 수 train_1/train_2/train_3 | `80/94/148` |
| stop reason | `qualify_failed` |
| 판정 | `RELAX_STILL_NO_ALL3` |

all3가 없으므로 all3 통과 개체의 EEC·최대 클러스터 비중·몰빵 회귀 여부는 평가 대상이 없다. 빈 로그는 `all3_eec_cluster_log.json`에 기록했다.

## v5 대비 비교표

| Metric | v5 (target6/floor0.5) | This run (target4/floor0.7) |
|---|---:|---:|
| all3/all2/all1/all0 | `0/4/245/51` | `0/61/200/39` |
| fold별 pass 수 | `90/80/83` | `80/94/148` |
| fold-best EEC | `4.96/6.21/8.05` | `4.15/4.57/5.83` |
| fold-best 최대 클러스터 비중 | `23.8%/19.1%/23.1%` | `31.6%/31.6%/23.1%` |
| fold-best 거래수 | `21/21/13` | `19/19/13` |
| fold-best fitness | `0.8734/1.5040/1.4454` | `1.1706/1.8557/1.7296` |

완화 후 fold-best fitness는 상승했지만, fold-best EEC는 v5보다 낮아졌고 train_1/train_2의 최대 클러스터 비중은 31.6%로 상승했다. 다만 all3가 없으므로 “all3가 나왔지만 몰빵으로 회귀”한 상황은 아니다.

## Fold-best EEC·클러스터

| fold | 거래수 | 비중복 체결 | EEC | multiplier | 최대 클러스터 비중 | 클러스터 기간·체결수 |
|---|---:|---:|---:|---:|---:|---|
| train_1 | 19 | 19 | 4.149425 | 1.0 | 31.58% | `2022-07-01~2022-07-18:4(21.1%); 2022-10-11~2022-10-21:6(31.6%); 2023-01-30:1(5.3%); 2023-04-10~2023-04-14:5(26.3%); 2023-05-04~2023-05-08:3(15.8%)` |
| train_2 | 19 | 19 | 4.569620 | 1.0 | 31.58% | `2023-11-28~2023-12-01:3(15.8%); 2024-01-12~2024-01-24:6(31.6%); 2024-02-14~2024-02-23:3(15.8%); 2024-04-15~2024-04-17:3(15.8%); 2024-04-30~2024-05-10:4(21.1%)` |
| train_3 | 13 | 13 | 5.827586 | 1.0 | 23.08% | `2024-07-26~2024-07-29:2(15.4%); 2024-08-12:1(7.7%); 2024-10-10~2024-10-16:3(23.1%); 2024-10-30~2024-11-04:3(23.1%); 2025-04-10:1(7.7%); 2025-05-13~2025-05-14:2(15.4%); 2025-05-29:1(7.7%)` |

## Gate / factor 요약

| fold | 후보 | pass | EEC penalized | mean EEC | mean multiplier | win_rate gate 병목 |
|---|---:|---:|---:|---:|---:|---:|
| train_1 | 300 | 80 | 206 (68.67%) | 2.276992 | 0.813505 | 46 (15.33%) |
| train_2 | 300 | 94 | 221 (73.67%) | 2.532828 | 0.795599 | 33 (11.00%) |
| train_3 | 300 | 148 | 201 (67.00%) | 3.778648 | 0.834531 | 138 (46.00%) |

## 보호파일 / daemon / manifest

| 항목 | 시작 SHA | 종료 SHA | 상태 |
|---|---|---|---|
| `.env` | `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` | `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` | 불변 |
| `data/_system/market_history.csv` | `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38` | `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38` | 불변 |
| `data/_system/market_history_v2.csv` | `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611` | `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611` | 불변 |

- daemon PID `494330`: 유지 확인
- daemon starttime ticks: `36014393`
- manifest gate: PASS
- 병렬 재현성 probe: PASS
- generation log: 120 lines, all qualify folds 완료

## 산출물

- `readout.md`
- `SHA256SUMS.txt`
- `launch_command.json`
- `v6_relax_comparison.json`
- `all3_eec_cluster_log.json`
- `qualify_result.json`
- `qualify_cross_fold_matrix.jsonl`
- `fold_best_eec_summary.json`
- `fold_best_trade_level.jsonl`
