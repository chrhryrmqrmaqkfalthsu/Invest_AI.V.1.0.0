# Stage 3 청산 GA 방식 원본 대조 — read-only

## 최종 판정

**EXIT_METHOD_PARTIAL**.

청산 GA의 내부 함수·파라미터·fitness·학습 구간은 원본과 동일하다. Probe는 원본 `_run_exit_ga_for_entry()`를 직접 호출했고, 그 함수가 사용하는 `apply_exit()`, `composite_exit_fitness()`, mutation/crossover/selection loop도 원본 구현 그대로다.

하지만 진입 개체 전제는 원본 full Stage 3와 다르다. 원본은 `run_entry_ga()`가 만든 `entry_rulebooks.jsonl`을 `run_exit_ga()`가 읽는다. 이번 probe는 v5 다종목 run의 `qualify_candidate_rulebooks.jsonl` 기반 fold-best/all3 entry rulebook을 직접 넣었다. 따라서 결과는 “v5 ADPT entry 후보에 원본 exit GA를 붙인 경우”로는 신뢰 가능하지만, “원본 full Stage 3 entry→exit→validate 전체와 완전 동일”은 아니다.

| 항목 | 원본 vs probe | 판정 | EXIT_PARTIAL 영향 가능성 |
|---|---|---|---|
| exit GA 호출 함수 | 원본 `_run_exit_ga_for_entry()` 직접 호출 | 동일 | 낮음 |
| pop/gen/topN | 60 / 25 / 3 | 동일 | 낮음 |
| mutation/crossover/selection | 같은 원본 함수 내부 사용 | 동일 | 낮음 |
| early stop | 없음. 25세대까지 진행 | 동일 | 낮음 |
| fitness | stress_pre_2022h1 + bull(train_3), default weights | 동일 | 낮음 |
| exit field overwrite | `EXIT_FIELDS`만 overwrite | 동일 | 낮음 |
| OOS/stress 평가 | train_1/train_2/recent_1y gate, stress reference | 사실상 동일 | 낮음 |
| entry source | 원본 `entry_rulebooks.jsonl` vs v5 qualify/fold-best/all3 | **불일치** | **높음** |
| seed | 수식은 `seed_base + 1000 + idx` 동일, entry order는 다름 | 부분 동일 | 중간 |
| entry-level rescue 라벨 | 원본에는 없음. probe가 best-recent 진단 추가 | 불일치/추가 | 중간 |

## STEP 0 — 호출 경로

원본 파일: `scripts/research/stage23_rework_20260713/scripts/research/run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001`, SHA `bc3e191a449d6b67980dd0884a2a510acf9ecda719a8b12e76b0e8178de33004`.

원본 호출 흐름:

1. `main()`의 `--stage exit`/`--stage all`이 `run_exit_ga()`를 호출한다. 원본 lines `1456-1459`, `1460-1467`.
2. `run_exit_ga()`는 `entry_rulebooks.jsonl`을 읽는다. 원본 lines `855-867`.
3. 각 entry row마다 `seed = seed_base + 1000 + idx`로 `_run_exit_ga_for_entry()`를 호출한다. 원본 lines `870-873`.
4. `_run_exit_ga_for_entry()`는 `entry_row["rulebook"]`을 base로 삼고 청산 gene만 진화시킨다. 원본 lines `790-852`.
5. `_evaluate_exit_gene()`는 `apply_exit(base, gene)` 후 stress+bull을 backtest하고 composite fitness를 계산한다. 원본 lines `752-787`.

Probe 파일: `scripts/research/stage23_rework_20260713/scripts/research/run_stage3_exit_rescue_probe.py`, SHA `871a1f6188e659d0d62e1c3073febf4fa2d97c424f1fa45cfd9bdcb2aec53faa`.

Probe 호출 흐름:

1. `candidate_to_entry_row()`가 v5 후보를 entry row 형태로 감싼다. probe lines `103-112`.
2. `exit_ga_worker()`가 v5 host/context를 로드한다. probe lines `123-130`.
3. 실제 호출은 `v5.runner.mod._base._run_exit_ga_for_entry(...)`다. probe lines `131-137`.
4. 검증은 `Rulebook.from_dict()` 후 `v5.runner.mod._base.run_backtest_period()`로 수행한다. probe lines `155-170`.
5. seed 구성과 병렬 실행은 probe lines `366-384`.

## STEP 1 — exit GA 파라미터 대조

| 항목 | 원본 | Probe | 판정 | 출처 |
|---|---:|---:|---|---|
| population | 60 | 60 | 동일 | 원본 `102`, `801-805`; probe summary |
| generations | 25 | 25 | 동일 | 원본 `103`, `807-827`; probe summary |
| top_n_per_entry | 3 | 3 | 동일 | 원본 `107`, `840-852`; probe summary |
| seed formula | `seed_base+1000+idx` | `seed_base+1000+idx` | 수식 동일 | 원본 `870-873`; probe `368-373` |
| initial population | seed gene + 10 mutated + random genes | 동일 원본 함수 | 동일 | 원본 `799-805` |
| mutation_rate | 0.25 default | 동일 | 동일 | 원본 `702-716` |
| mutation strength | 0.18 default | 동일 | 동일 | 원본 `702-716` |
| random reset prob | 0.08 | 동일 | 동일 | 원본 `711-715` |
| crossover | EXIT_FIELDS 균등 crossover | 동일 | 동일 | 원본 `719-721` |
| tournament k | 3 default | 동일 | 동일 | 원본 `747-749`, `835-837` |
| random child prob | 0.12 | 동일 | 동일 | 원본 `831-838` |
| elite count | `EXIT_POPULATION//5` | 동일 | 동일 | 원본 `828-830` |
| early stop | 없음 | 없음 | 동일 | 원본 `807-827` |
| weights | `DEFAULT_EXIT_FITNESS_WEIGHTS` | 동일 default | 동일 | 원본 `1426-1437`; probe `136` |

Probe 실제 seed: fold_best_train_1 `2026072402`, fold_best_train_2 `2026072403`, fold_best_train_3 `2026072404`, all3_1 `2026072405`. 수식은 원본과 같지만 entry set/order가 원본 `entry_rulebooks.jsonl`과 다르므로 seed별 결과는 원본 full pipeline과 직접 대응되지 않는다.

## STEP 2 — 학습 구간·fitness 대조

| 항목 | 원본 | Probe | 판정 |
|---|---|---|---|
| stress 학습 구간 | `stress_pre_2022h1`, start=None, end=2022-06-30 | 동일 원본 함수 내부 | 동일 |
| bull 학습 구간 | `train_3`, 2024-07-01~2025-06-30 | 동일 원본 함수 내부 | 동일 |
| train_1/train_2 | 학습 미사용, validate OOS | 동일 | 동일 |
| recent_1y | 학습 미사용, validate OOS | 동일 | 동일 |
| stress gate | validate에서는 gate 제외 reference | 동일 | 동일 |

근거: 원본 period constants lines `75-89`; `_evaluate_exit_gene()` lines `760-777`.

Fitness는 `engine/pipeline/exit_gene.py`의 `composite_exit_fitness()`와 동일하다.

| 구성 | 수식/값 | 출처 |
|---|---|---|
| default weights | `w_downside=2.0`, `w_bull_floor_penalty=3.0`, `bull_floor=1.0`, `w_stress_mdd=0.2`, `w_holding=0.1`, `holding_soft_cap=7.0`, timeout/deep-stop weights=0 | `exit_gene.py:54-65` |
| downside | `min(stress_exp, bull_exp)` | `exit_gene.py:220` |
| bull floor penalty | `max(0, bull_floor - bull_exp)` | `exit_gene.py:221` |
| stress MDD penalty | `abs(min(0, stress_mdd))` | `exit_gene.py:222` |
| holding penalty | `max(0, median_holding - holding_soft_cap)` | `exit_gene.py:223` |
| final fitness | bull exp + downside - penalties | `exit_gene.py:236-245` |
| exit field overwrite | `apply_exit()` copies base and overwrites only `EXIT_FIELDS` | `exit_gene.py:68-82` |

## STEP 3 — 진입 개체 전제 대조

원본은 `run_entry_ga()`가 만든 `entry_rulebooks.jsonl`을 전제로 한다.

- `run_entry_ga()`는 qualify 통과 후 train_3에서 entry GA를 수행한다. 원본 lines `574-616`.
- top rulebooks를 다시 평가해 metrics, entry_dates, rulebook dict를 만든다. 원본 lines `617-640`.
- overlap diversity로 selected row를 만들고 rank를 붙여 `entry_rulebooks.jsonl`에 쓴다. 원본 lines `642-649`.
- `run_exit_ga()`는 이 파일을 prerequisite로 읽는다. 원본 lines `855-867`.

Probe는 v5 `qualify_candidate_rulebooks.jsonl`, `fold_best_trade_level.jsonl`, `qualify_cross_fold_matrix.jsonl`에서 fold-best 3개와 all3 1개를 선택한다. `run_stage3_oos_stress_probe.py:213-239`. `candidate_to_entry_row()`는 `ticker/rank/rulebook_hash/candidate_id/selection_role/source_fold/rulebook`만 넘긴다. probe lines `103-112`.

`_run_exit_ga_for_entry()`가 실제 계산에 쓰는 필드는 `entry_row["rulebook"]`이고, metadata로 `ticker/rank/rulebook_hash`를 쓴다. 원본 lines `799`, `817`, `845-848`. 따라서 missing train metrics나 entry_dates는 exit GA 내부 계산에 직접 영향이 없다. 그러나 base rulebook 자체가 다르기 때문에 entry signal 날짜와 seed exit gene은 달라질 수 있다.

## EXIT_PARTIAL 결과 해석

Final row별 원본 OOS verdict는 아래와 같다. 원본 validate 관점에서는 12개 final row 중 **OOS_PASS는 0개**다.

| candidate | exit rank | verdict | comp | train_1 | train_2 | recent_1y | stress |
|---|---:|---|---:|---:|---:|---:|---:|
| all3_1 | 1 | OOS_FAIL_RECENT | 12.245 | 8.04 | 2.87 | -2.85 | -0.95 |
| all3_1 | 2 | OOS_FAIL_RECENT | 12.245 | 8.04 | 2.87 | -2.85 | -0.95 |
| all3_1 | 3 | OOS_FAIL_RECENT | 12.245 | 8.04 | 2.87 | -1.22 | -0.95 |
| fold_best_train_1 | 1 | OOS_FAIL_OTHER | 13.795 | 0.53 | 0.48 | 4.46 | 1.30 |
| fold_best_train_1 | 2 | OOS_FAIL_OTHER | 13.795 | 0.53 | 0.48 | 5.39 | 1.30 |
| fold_best_train_1 | 3 | OOS_FAIL_OTHER | 13.795 | 0.53 | 0.48 | 5.39 | 1.30 |
| fold_best_train_2 | 1 | OOS_FAIL_RECENT | 7.554 | -0.99 | 4.65 | -1.87 | 0.20 |
| fold_best_train_2 | 2 | OOS_FAIL_RECENT | 7.416 | -2.28 | 4.71 | -2.12 | 0.12 |
| fold_best_train_2 | 3 | OOS_FAIL_RECENT | 7.352 | -2.32 | 4.26 | -2.65 | 0.11 |
| fold_best_train_3 | 1 | OOS_FAIL_OTHER | 11.374 | -4.38 | -3.52 | 10.72 | -2.90 |
| fold_best_train_3 | 2 | OOS_FAIL_OTHER | 11.374 | -4.38 | -3.52 | 10.72 | -2.90 |
| fold_best_train_3 | 3 | OOS_FAIL_OTHER | 11.374 | -4.38 | -3.52 | 10.72 | -2.90 |

Probe의 `EXIT_PARTIAL`은 entry-level로 “top3 중 recent_1y만 회복한 row가 있음”을 나타낸 추가 진단이다. 이 추가 라벨은 원본 final selection 기준 자체는 아니다.

결론: `EXIT_PARTIAL`은 청산 GA 구현 차이로 생긴 artifact라고 보기 어렵다. 다만 입력 entry가 원본 entry GA 산출물이 아니므로, Stage 2 전환 논리는 이렇게 제한해야 한다.

> v5 ADPT entry 후보는 원본 exit GA를 붙여도 OOS 3구간 gate를 통과하지 못했다. 일부 recent_1y 회복은 있었지만 train_1/train_2와 트레이드오프가 생겼다. 이는 청산만의 문제가 아니라 entry 후보의 구간 안정성 문제라는 해석을 지지한다. 단, 원본 full Stage 3 entry GA 후보에 대한 결론은 별도 실행 없이는 미확인이다.

## 보호파일 / daemon / git

보호파일 시작·종료 SHA 동일:

| 파일 | SHA256 |
|---|---|
| `.env` | `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` |
| `data/_system/market_history.csv` | `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38` |
| `data/_system/market_history_v2.csv` | `b7db98bd5b17b7a95cc852cde6f6dbb86c6347548e9f4c611` |

- 실제 host: `invest-bot`.
- daemon PID `494330` 유지 확인.
- 산출 전 backup commit: `4dd66f4`.
