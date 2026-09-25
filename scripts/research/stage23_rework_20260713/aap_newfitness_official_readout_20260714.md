# AAP 새 fitness 정식 Stage 3 재학습 readout

- 실행 대상: AAP 단일 종목
- 실행 규모: **정식 qualify population 100 / generations 40, entry population 100 / generations 50, exit 14-field, validate**
- seed base: `2026071401`
- 실행 코드 커밋: `83121d8`
- 병렬 worker: 6
- 실행 결과: **qualify 실패 (`all3=0`)**
- Entry / Exit / Validate: qualify 실패로 미실행
- 총 실행시간: 1185.56초

## 1. 병렬 축과 재현성

AAP 단일 종목이므로 종목 병렬이 아니라 **GA population fitness backtest 평가**를 6개 process에 분산했다.

- qualify 3개 fold는 seed 순서를 보존하기 위해 순차 실행
- 각 fold 내부 population fitness 평가만 6 worker 병렬
- 후보 생성, tournament selection, crossover, mutation과 모든 RNG 소비는 부모 process에서 기존 순서대로 수행
- worker 결과는 완료 순서가 아니라 **입력 candidate index 순서**로 병합
- cross-fold 300×3 평가도 candidate/fold index 순서로 병합
- qualify 통과 시 exit는 entry candidate 축, validate는 final candidate 축으로 기존 6-worker 병렬 경로를 사용하도록 구성했으나 이번에는 미실행

실행 전 재현성 probe:

| 검증 | 결과 |
|---|---|
| 1 worker vs 6 worker best hash | 동일 |
| 세대별 fitness history | 동일 |
| final population hash·fitness·순서 | 동일 |
| 실제 AAP entry-scope 축소 probe | 동일 |

일반 probe의 공통 best hash는 `4c9db562068ce3e6ce66683ed933d9b49b76299e45bfb1089906c3bee6213677`이다. 병렬화가 seed, 평가 순서, selection 결과를 바꾸지 않음을 확인한 뒤 정식 실행을 시작했다.

## 2. 새 fitness 활성 확인

entry GA는 `gene_scope='entry'`로 실행됐으며 다음 원칙이 활성화됐다.

```text
primary = mean(net realized pnl_pct / max(holding_days, 1))
mae_penalty = mean(max(0, -2.0 - mae_pct))
fitness_before_gate = primary - mae_penalty
win = net realized pnl_pct > 0
win_rate < 60% -> fitness -1,000,000,000
```

- MAE source: 보유기간 daily low로 계산된 기존 `max_loss_during_hold`
- mutation local search: 진입 후 최대 7거래일 이내
- earlier/later 정보: fitness·승패·실격·qualify pass에 미사용, interval width mutation 힌트로만 사용
- `gene_scope` 기본값은 `legacy`로 유지돼 Stage 2 경로는 기존 fitness를 사용

신규 fitness activation probe의 6개 체크가 모두 통과했다.

## 3. Qualify 최종 결과

전체 3개 fold final population을 폐기하지 않고 각각 100개씩, 총 300개를 보존했다. unique chromosome도 300개였으며 각 후보를 3개 fold에 모두 평가해 cross-fold matrix 900행을 남겼다.

| 통과 fold 수 | 후보 수 |
|---|---:|
| all3 | 0 |
| all2 | 6 |
| all1 | 229 |
| all0 | 65 |

fold별 단독 pass 수:

| fold | pass 후보 |
|---|---:|
| train_1 | 13 / 300 |
| train_2 | 90 / 300 |
| train_3 | 138 / 300 |

### all2 6개 상세

all2 후보 6개는 모두 **train_1 + train_3 통과, train_2 실패** 조합이다.

- 6개 전부 train_2 거래수 미달
- 6개 전부 train_2 기대값 미달
- 6개 전부 train_2 승률 60% gate 실격
- 1개는 member score도 미달
- 거래수: 1~2건
- 승률: 0~50%
- 기대값: 약 -0.22%~-3.45%
- 최종 fitness: 전부 `-1,000,000,000`

따라서 all2가 존재하지만 train_2에서 support·성과·승률 gate가 동시에 무너져, 문턱에 근접한 all3 후보로 보기는 어렵다.

## 4. Gate 병목 집계

| fold | 후보 | 승률 60% gate 실격 | MAE 감점 대상 | MAE 대상 평균 감점 | 승률 gate 후 잔존 | qualify pass |
|---|---:|---:|---:|---:|---:|---:|
| train_1 | 300 | 191 (63.67%) | 136 (45.33%) | 0.752160 | 109 | 13 |
| train_2 | 300 | 197 (65.67%) | 217 (72.33%) | 1.496840 | 103 | 90 |
| train_3 | 300 | 128 (42.67%) | 276 (92.00%) | 1.498844 | 172 | 138 |

가장 많은 개체를 직접 제거한 hard gate는 세 fold 모두 **승률 60% gate**다.

MAE는 hard gate가 아니라 fitness 감점이므로 제거 수와 동일하게 해석하면 안 된다. 다만 train_3에서 92%가 감점을 받아 위험 노출이 매우 광범위했고, 전체 후보 기준 평균 감점도 1.378936으로 가장 컸다.

전체 cross-fold 실패 metric 누계:

| fold | 거래수 미달 | 기대값 미달 | 승률 gate | member score 미달 |
|---|---:|---:|---:|---:|
| train_1 | 172 | 213 | 191 | 11 |
| train_2 | 161 | 204 | 197 | 4 |
| train_3 | 98 | 142 | 128 | 17 |

## 5. GA 진행과 수렴

| fold | population | 세대 | 최초/최종 best 주요 결과 | 최종 best fitness |
|---|---:|---:|---|---:|
| train_1 | 100 | 40/40 | 승률 100%, MAE 감점 0 | 2.930451 |
| train_2 | 100 | 40/40 | 승률 100%, MAE 감점 0 | 2.584954 |
| train_3 | 100 | 40/40 | primary 5.163436, MAE 감점 0.340818 | 4.822618 |

모든 fold가 정확히 40세대에서 종료됐다. worker 6개가 실행 중 유지됐고 무한루프, worker 이탈, `BrokenProcessPool`, traceback은 없었다.

평균 fitness가 큰 음수로 보이는 세대는 승률 gate 실격 개체의 `-1e9`가 평균에 포함됐기 때문이다. best fitness와 gate 통과 개체 진단은 별도 필드로 보존했다.

## 6. 전체 final population 진단

각 fold GA의 마지막 population 100개 기준:

| origin fold | 승률 gate 실격 | MAE 감점 대상 | 평균 primary | 전체 평균 MAE 감점 | mutation hint |
|---|---:|---:|---:|---:|---|
| train_1 | 13 | 16 | 1.972316 | 0.089743 | later 92 / earlier 7 / none 1 |
| train_2 | 1 | 24 | 2.179120 | 0.219366 | later 100 |
| train_3 | 25 | 100 | 2.570398 | 1.469580 | later 96 / earlier 4 |

train_3 population은 primary가 가장 높았지만 100개 전부 MAE 감점을 받았다. 새 fitness가 높은 수익 속도와 보유 중 위험을 실제로 상쇄하며 selection하고 있음을 보여준다.

## 7. Fold-best trade-level

총 12개 fold-best 거래를 보존했다.

| fold | 거래 | 승률 | 평균 실현수익 | 평균 하루당 수익 | worst MAE | MAE -2% 이탈 | 청산 사유 |
|---|---:|---:|---:|---:|---:|---:|---|
| train_1 | 1 | 100% | 5.860902% | 2.930451% | 0.000000% | 0 | interval-break 1 |
| train_2 | 5 | 100% | 6.121505% | 2.584954% | -0.215519% | 0 | interval-break 5 |
| train_3 | 6 | 100% | 10.326873% | 5.163436% | -4.044908% | 1 | interval-break 6 |

- fold-best 청산은 **12/12 전부 interval-break**
- 12개 거래 모두 진입 시 strict interval pass=true
- 12개 거래 모두 5-feature 값과 feature별 interval 판정 완전
- train_3 best는 MAE -4.0449% 거래 1건 때문에 평균 0.340818 감점을 받음

fold-best local-search 방향:

- train_1: later 1
- train_2: later 3, earlier 1, none 1
- train_3: later 5, earlier 1

## 8. Mutation 편향 작동 검증

세대별 population 진단은 generation 0을 포함해 fold당 41행, 총 123행이다.

| fold | earlier hint | later hint | earlier 방향 정합률 | later 방향 정합률 |
|---|---:|---:|---:|---:|
| train_1 | 245 | 3,830 | 81.03% | 88.91% |
| train_2 | 211 | 3,886 | 76.92% | 85.57% |
| train_3 | 121 | 3,979 | 60.00% | 89.05% |

later 힌트가 압도적으로 많았고 실제 개별 width mutation도 약 85.6~89.1%가 확대 방향과 일치했다. 따라서 mutation 편향은 실제로 작동했다.

population 평균 interval width의 처음→마지막 변화:

| fold | ma_trend | macd_hist | rsi | bb_position | volume_ratio |
|---|---:|---:|---:|---:|---:|
| train_1 | -2.7327 | -0.0732 | +1.3960 | -0.0728 | +0.2484 |
| train_2 | -5.1281 | +0.1292 | -6.4507 | +0.5201 | -0.1227 |
| train_3 | -2.9237 | -0.4079 | +12.4071 | -0.0178 | +0.2876 |

개별 mutation 방향 정합률과 population 평균 width 변화는 같은 개념이 아니다. 후자는 selection·crossover·feature별 생존 편향까지 합쳐진 결과라 모든 feature가 later 방향으로 넓어질 필요는 없다.

## 9. Entry / Exit / Validate 및 CE/BOIL

- qualify `all3=0`이므로 Entry GA 미실행
- entry survivor: 0
- exit candidate: 0
- validate survivor: 0
- CE/BOIL validator error/one-sided/missing-domain/quality override: 모두 0

`ce_boil_zero=true`이지만 audit candidate count도 0이다. 즉 **하류 후보에서 CE/BOIL 문제가 발생하지 않았다는 뜻이 아니라, qualify 실패로 검사할 하류 후보가 없었던 공집합 결과**다.

## 10. 실행 전후 안전성

manifest gate 전 항목 통과:

- repository-root SHA-pinned 단일 source
- `market_history.csv` SHA 고정
- `market_history_v2.csv` SHA 고정
- 필수 컬럼
- 거래일 freshness
- auto-fetch 차단
- auto-regenerate 차단
- fail-closed

freshness 기준:

- New York 기준일: 2026-07-13
- 기대 최신 미국 거래 세션: 2026-07-10
- snapshot 마지막 날짜: 2026-07-10

보호 파일 시작·종료 SHA 동일:

- `.env`: `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce`
- `data/_system/market_history.csv`: `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38`
- `data/_system/market_history_v2.csv`: `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611`

Daemon PID `494330`은 시작·종료 starttime tick `36014393`으로 동일했고 최종 확인 시에도 `Sl` 상태였다.

## 11. 주요 산출물

| 파일 | 행 수 / 역할 |
|---|---|
| `qualify_population_all.jsonl` | 300행, fold별 final population 전체 |
| `qualify_candidate_rulebooks.jsonl` | 300행, unique chromosome 본문 |
| `qualify_cross_fold_matrix.jsonl` | 900행, 후보×3 folds 신규 fitness·gate·pass |
| `qualify_candidate_pass_vectors.jsonl` | 300행, all3/all2/all1/all0 벡터 |
| `qualify_gate_bottleneck.json` | fold별 승률 gate·MAE 감점 병목 |
| `fold_best_trade_level.jsonl` | 12행, fold-best 거래 상세 |
| `generation_best_fitness.jsonl` | 120행, 3 folds×40세대 best/mean |
| `ga_population_history.jsonl` | 123행, generation 0 포함 population·mutation 진단 |
| `mutation_bias_summary.json` | earlier/later 및 width 방향 정합 집계 |
| `official_final_summary.json` | 최종 결과·보호 SHA·daemon·probe |
| `run.log` | 전체 진행 로그 |
| `SHA256SUMS.txt` | 산출물 SHA-256 |

## 12. 결론

새 fitness와 6-worker deterministic 병렬화는 의도대로 작동했다. 그러나 AAP는 정식 규모에서도 all3 후보를 만들지 못했다.

- 가장 큰 hard bottleneck: 승률 60% gate
- train_2: all2 6개가 모두 거래수·기대값·승률 gate에서 동시 실패
- train_3: 높은 primary와 동시에 광범위한 MAE 감점
- mutation bias: later 중심으로 실제 width mutation 방향에 반영됨

따라서 이번 결과는 단순 계산 규모 부족보다 **train_2 support/성과 재현성과 fold 간 안정성 부족**을 다시 확인한 결과다.
