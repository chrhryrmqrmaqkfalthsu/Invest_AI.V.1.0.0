# PILOT_PASS survivor 상세 조회

## 최종 결론

AAP와 POWI는 모두 **Stage2 path-filter 경로에서 생성된 survivor**다. 별도 Stage3 qualify/entry/exit GA 결과가 아니며 Stage3 exit GA도 사용되지 않았다.

이번 거래 내역은 기존 비교표의 종목별 champion이 아니라, 실제 survivor model hash와 저장된 OOS 입력을 사용해 read-only로 재구성했다.

## Survivor 모델

| 종목 | Survivor 모델 | split | OOS 신호일 | OOS 라벨 정밀도 |
|---|---|---|---:|---:|
| AAP | `d42baa944275695a136220ac094f7e202c54d46eedb09eba10f8fce1117b7de2` | train_1 | 12 | 66.67% |
| POWI | `3943bed5f517b8f667580cc8141ede8cc60d83ead4c8fefbbcbcd618d20f6901` | train_2 | 13 | 53.85% |

OOS 신호일 수와 거래 수가 다른 이유는 이미 포지션을 보유 중인 날의 추가 신호가 새 거래를 만들지 않고 목표일을 연장하기 때문이다.

## 실제 OOS 거래 패턴

거래 방식은 `rolling_target_2_sessions_tp_off`다. 진입은 신호일 시가, 목표일 청산은 종가, 순수익률은 왕복비용 10bp 차감 후 값이다.

| 지표 | AAP | POWI |
|---|---:|---:|
| OOS 신호일 | 12 | 13 |
| 실제 거래 | 9 | 10 |
| 평균 보유세션 | 2.333 | 2.300 |
| 최장 보유세션 | 3 | 3 |
| 순수익 기준 승률 | 66.67% | 60.00% |
| 보유 중 +3% 도달률 | 55.56% | 50.00% |
| 목표일 연장 거래 | 3 / 9, 33.33% | 3 / 10, 30.00% |
| 총 목표일 연장 횟수 | 3 | 3 |
| 거래당 평균 연장 | 0.333회 | 0.300회 |
| 거래당 평균 순수익률 | +0.1031% | +0.1820% |
| 순복리수익률 | -0.0924% | +0.9113% |
| 구간말 강제청산 | 0 | 0 |

AAP와 POWI 모두 연장된 거래는 최대 1회 연장이었고, 모든 거래가 `TARGET_DATE_REACHED`로 정상 종료됐다.

## 목표일 연장이 실제 발생한 거래

### AAP

| 진입일 | 최초 목표 | 연장 신호일 | 변경된 목표 | 청산일 |
|---|---|---|---|---|
| 2025-10-08 | 2025-10-10 | 2025-10-09 | 2025-10-13 | 2025-10-13 |
| 2026-02-25 | 2026-02-27 | 2026-02-26 | 2026-03-02 | 2026-03-02 |
| 2026-06-04 | 2026-06-08 | 2026-06-05 | 2026-06-09 | 2026-06-09 |

### POWI

| 진입일 | 최초 목표 | 연장 신호일 | 변경된 목표 | 청산일 |
|---|---|---|---|---|
| 2025-09-10 | 2025-09-12 | 2025-09-11 | 2025-09-15 | 2025-09-15 |
| 2026-02-02 | 2026-02-04 | 2026-02-03 | 2026-02-05 | 2026-02-05 |
| 2026-04-14 | 2026-04-16 | 2026-04-15 | 2026-04-17 | 2026-04-17 |

점수가 끊긴 날 즉시 청산된 거래는 없다. 연장 신호가 없으면 최초 또는 마지막 유효 목표일의 종가에 청산됐다.

## +3% 도달 판정 기준

`plus3_reached_during_holding`은 진입일부터 청산일까지 각 거래일의 D0 high 중 하나라도 진입가 대비 +3% 이상이었는지를 뜻한다. TP OFF 거래이므로 +3%에 도달해도 조기 청산하지 않았다.

이 값은 진입일의 `label_2d3pct`와 다를 수 있다.

- `label_2d3pct`: 진입일 기준 D+1~D+2 고가만 평가
- `plus3_reached_during_holding`: 진입일부터 연장된 실제 청산일까지 전체 보유구간 평가

## Gene와 fallback

두 survivor 모두 12개 feature에 양방향 `[하한, 상한]` gene을 가진다. 실제 feature 단위 구간은 `survivor_genes.csv`에 normalized 구간과 원 단위 구간을 모두 기록했다.

- AAP GA 실행의 fallback 이벤트: 249건
- POWI GA 실행의 fallback 이벤트: 222건
- 두 실행 모두 12개 feature 전체에서 탐색 중 fallback이 최소 한 번 발생

단, 산출물의 fallback 표시는 split GA 전체 population·generation 합계다. 최종 선택된 survivor chromosome의 특정 상한이 fallback 값에서 직접 유래했는지는 저장되지 않았다. 따라서 `survivor_genes.csv`의 `final_gene_fallback_provenance`는 모두 `NOT_STORED`다.

## 기존 비교표와의 주의점

기존 `exit_method_comparison.csv`는 survivor 여부와 무관하게 origin-train fitness가 가장 높은 종목별 champion을 사용했다.

- AAP 비교 champion: train_2 / `85dc39...` / 비-survivor
- AAP 실제 survivor: train_1 / `d42baa...`
- POWI 비교 champion: train_1 / `60d75c...` / 비-survivor
- POWI 실제 survivor: train_2 / `3943be...`

따라서 이 상세 조회의 AAP·POWI 거래 성과를 파일럿 전체 50종목 비교표의 해당 ticker 행과 직접 동일시하면 안 된다.

## 재구성 검증

- 저장된 survivor gene·domain·threshold 사용
- 저장된 `_worker_tmp/input_AAP.csv`, `input_POWI.csv` 사용
- 재학습 없음
- AAP OOS active day: 12, `survivor_summary.csv`의 OOS passed count 12와 일치
- POWI OOS active day: 13, OOS passed count 13과 일치
- 독립 목표일 trace의 진입일·청산일·보유세션·연장 횟수가 복사본 `rolling_target_backtest()` 반환 거래와 전부 일치
