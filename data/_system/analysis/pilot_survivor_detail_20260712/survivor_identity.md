# PILOT_PASS survivor 정체 및 학습 경로

## 조사 범위

- 기존 산출물만 조회했다.
- 재학습·코드 변경·설정 변경·daemon 변경은 하지 않았다.
- 코드 기준 커밋: `4b29e6bd64b9f98a663e1dd88ed8811571c53827`
- 파일럿 산출물 커밋: `0c4aa29d08b4772493f32df11311c902172a839b`
- survivor 원천: `data/_system/analysis/stage2_3_rediscovery_pilot_20260712/survivor_summary.csv`

## 최종 Stage2/Stage3 구분

AAP와 POWI는 모두 **Stage2 path-filter 경로의 survivor 후보**다. 별도 Stage3 학습 결과가 아니다.

실제 재실행 진입 경로는 다음과 같다.

```text
scripts/research/rolling_rediscovery/upstream_snapshot/
  scripts/research/run_stage2_path_filter.py
    → from scripts.research import run_stage2       (24행)
    → run_stage2.main()                              (154행)
    → run_pilot()                                    (run_stage2.py 1133~1138행)
    → 종목별 train_1/train_2/train_3 GA              (658~683행)
    → train/stress/OOS gate 판정                     (354~390행)
```

복사본 `run_stage3_aggressive.py`는 21~25행에서 같은 `run_stage2.main()`에 위임하지만, 이번 재실행 명령은 `run_stage2_path_filter.py --workers 6`이었다. 따라서 이번 survivor에 독립적인 Stage3 qualify/entry/exit GA 경로는 없다.

## Survivor 정체

| 종목 | Survivor 모델 hash | 학습 split | Train | Stress | OOS | 판정 |
|---|---|---|---:|---:|---:|---|
| AAP | `d42baa944275695a136220ac094f7e202c54d46eedb09eba10f8fce1117b7de2` | train_1 | 20건 / 55.00% | 29건 / 41.38% | 12건 / 66.67% | SURVIVOR |
| POWI | `3943bed5f517b8f667580cc8141ede8cc60d83ead4c8fefbbcbcd618d20f6901` | train_2 | 20건 / 60.00% | 31건 / 48.39% | 13건 / 53.85% | SURVIVOR |

두 모델의 pass probability와 decision threshold는 다음과 같다.

- AAP: `pass_probability=0.55`, `decision_threshold=0.45`
- POWI: `pass_probability=0.60`, `decision_threshold=0.45`

## Survivor와 종목별 champion은 서로 다름

파일럿 코드는 세 split 후보를 모두 gate 판정한 뒤 별도로 origin-train fitness가 가장 높은 후보를 종목별 비교 champion으로 선택한다.

- `run_stage2.py` 685행: `champion = max(candidates, key=best.fitness)`
- 686행: 세 청산 방식 백테스트는 이 champion으로 실행
- 696행: survivor 후보들은 별도 `survivor_rows`로 저장

그 결과:

| 종목 | 실제 survivor | 기존 비교표 champion | champion survivor 여부 |
|---|---|---|---|
| AAP | train_1 / `d42baa...` | train_2 / `85dc39...` | REJECTED |
| POWI | train_2 / `3943be...` | train_1 / `60d75c...` | REJECTED |

따라서 기존 `exit_method_comparison.csv`의 AAP·POWI 행은 survivor gene의 거래가 아니다. 이번 `aap_trades.csv`, `powi_trades.csv`는 저장된 survivor gene·domain·threshold와 OOS worker 입력을 사용해 `rolling_target_backtest()`를 재현한 결과다. 재학습은 수행하지 않았다.

## 상한 fallback 상태

상한 fallback은 `genetic.py` 99~135행에서 non-finite upper bound를 성공 라벨 표본의 feature 최대값으로 복구하는 절차다.

- AAP survivor GA 실행: fallback 이벤트 249건
- POWI survivor GA 실행: fallback 이벤트 222건
- 두 실행 모두 12개 feature 각각에서 최소 1회의 fallback 이벤트가 있었다.

다만 `run_stage2.py` 435~459행의 `fallback_applied_any_generation`과 `fallback_event_count`는 **최종 survivor chromosome 자체의 provenance가 아니라 해당 split GA 전체 population·generation에서 발생한 이벤트 합계**다. 산출물에는 최종 선택 chromosome의 각 상한이 fallback에서 직접 유래했는지를 추적하는 식별자가 저장되지 않았다.

따라서 정확한 표기는 다음과 같다.

- “해당 survivor 모델의 GA 탐색 중 해당 feature에 fallback이 적용됐는가?” → 24개 gene 모두 `YES`
- “최종 선택된 상한 자체가 fallback 대입값인가?” → `NOT_STORED / 판별 불가`

12개 전체 구간과 feature별 fallback 횟수는 `survivor_genes.csv`에 기록했다.
