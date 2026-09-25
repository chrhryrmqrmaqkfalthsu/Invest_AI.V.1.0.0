# ANET·BB·CE Stage3 평가구간 백테스트 재실행 readout

## 최종 판정

**PARTIAL_REPRODUCTION_MISMATCH_WITH_COMPONENT_CAPTURE_SUCCESS**

기존 `engine/learning/backtest.py` 계열의 `run_backtest_execution_mode`를 사용해 대상 3개 룰의 Stage3 원래 평가구간 12개를 재실행했다. 운영·라이브·주문·설정·재학습 코드는 수정하지 않았고, 분석 산출물만 이 디렉터리에 저장했다.

진입 component 포착은 성공했다.

- 원래 Stage3 snapshot: 206/206건에서 `entry_signal_components` 확인
- 이번 재실행: 195/195건에서 `entry_signal_components` 확인
- 함께 저장한 필드: score, raw score, threshold, market adjustment, score/threshold ratio, 양수 component 수, Top2 집중도, pnl

따라서 같은 방법으로 다른 Stage3 개체의 진입 시점 component도 포착할 수 있다.

다만 원래 거래와 완전히 일치한 구간은 12개 중 2개뿐이다. 현재 재실행 거래를 원래 Stage3 거래와 동일한 정답으로 간주하면 안 된다.

## 대상 룰

| ticker | rule ID | full rulebook hash |
|---|---|---|
| ANET | `stage3:ANET:fe220620802b` | `fe220620802b432f760bf18cb42e27465bdf78361ea803a0d00692c80848932b` |
| BB | `stage3:BB:f1bdfe7f8ad9` | `f1bdfe7f8ad9eab0337862d4166754166c299876cca58096ada5fa0845c76024` |
| CE | `stage3:CE:998b0b638c66` | `998b0b638c6649fe10d3dff0fc74f890d9891ef9caa7ed61a2bae1e340288b78` |

## Stage3 원래 구간

메타데이터 원천은 각 종목의 `stage3/manifest.json`과 대상 행의 `stage3/validation_results.jsonl`이다.

| 구간 | 역할 | 시작 | 종료 |
|---|---|---|---|
| `stress_pre_2022h1` | stress / exit check | 각 실행 당시 6년 데이터 시작 | 2022-06-30 |
| `train_1` | pure OOS validation | 2022-07-01 | 2023-06-30 |
| `train_2` | pure OOS validation | 2023-07-01 | 2024-06-30 |
| `recent_1y` ANET | pure OOS validation | 2025-07-01 | 2026-06-24 |
| `recent_1y` BB·CE | pure OOS validation | 2025-07-01 | 2026-06-26 |

stress의 실제 데이터 시작점도 원래 실행 경계로 복원했다.

- ANET: 2020-05-27, 1,527행, 데이터 종료 2026-06-24
- BB: 2020-05-29, 1,527행, 데이터 종료 2026-06-26
- CE: 2020-06-01, 1,526행, 데이터 종료 2026-06-26

`train_3`(2024-07-01~2025-06-30)는 bull/exit-learning 구간이며 최종 pure OOS validation 및 `exit_trades` 대조 대상이 아니므로 이번 12개 평가구간에는 포함하지 않았다.

## 동일 실행 의미

원래 Stage3 runner와 동일하게 다음 인자를 사용했다.

- 진입: `t_plus_1_open`
- 청산: `conservative_core`
- fold 종료: `fold_end_mark_to_market`
- live hard stop guard: 활성
- LLM event: 비활성
- fitness mode: `swing`
- position limit: 120,000 KRW

원래 실행 직전 커밋은 ANET `8390e6b`, BB `54f1b99`, CE `3785544`로 특정했다. `backtest.py`, `execution_mode_backtest.py`, `evaluator.py`, `exit_simulator.py`, `rulebook.py`, 데이터 로더·지표·시장 context 관련 파일은 해당 커밋들과 현재 HEAD 사이에 차이가 없음을 확인했다.

## 재현 정합성

| ticker | period | 원본 | 재실행 | 진입일 일치 | 완전 일치 | 판정 |
|---|---|---:|---:|---:|---:|---|
| ANET | stress | 23 | 22 | 20 | 15 | 불일치 |
| ANET | train_1 | 19 | 18 | 15 | 11 | 불일치 |
| ANET | train_2 | 17 | 17 | 14 | 10 | 불일치 |
| ANET | recent_1y | 22 | 22 | 22 | 19 | 불일치 |
| BB | stress | 12 | 11 | 9 | 6 | 불일치 |
| BB | train_1 | 11 | 9 | 6 | 3 | 불일치 |
| BB | train_2 | 11 | 11 | 10 | 9 | 불일치 |
| BB | recent_1y | 12 | 10 | 9 | 6 | 불일치 |
| CE | stress | 23 | 22 | 22 | 20 | 불일치 |
| CE | train_1 | 20 | 17 | 14 | 11 | 불일치 |
| CE | train_2 | 18 | 18 | 18 | 18 | 완전 일치 |
| CE | recent_1y | 18 | 18 | 18 | 18 | 완전 일치 |

전체로는 원본 206건, 재실행 195건, 진입일 일치 177건, 진입일·진입가·청산일·pnl 완전 일치 146건이다.

행 단위 상세는 `entries/*.csv`와 `reproduction_trade_comparison.csv`, 구간 요약은 `reproduction_period_summary.csv`를 참조한다.

## 불일치 원인 판정

확인된 사실:

1. 룰북과 관련 엔진 코드는 원래 실행 시점 대비 변경되지 않았다.
2. OHLC의 시작·종료 경계는 원래 실행과 동일하게 복원했다.
3. 원래 실행 당시의 frozen `market_history`, `market_history_v2`, ticker sentiment 파일은 커밋에 포함되지 않아 격리 worktree에서 복원할 수 없었다.
4. OHLC도 동일 yfinance 소스를 동일 종료일로 다시 조회한 것이므로 공급자의 과거 데이터 수정 가능성이 남는다.
5. 원본과 재실행 모두 대상 거래의 sell-omen score는 포착된 값이 0건이어서 sell-omen을 직접 원인으로 지목할 근거는 없다.

여러 거래에서 진입일·진입가·component는 같지만 청산일과 pnl이 달라졌다. 관련 청산 코드는 동일하므로, 원래 frozen 시장/VIX context 부재가 청산 경로 차이의 가장 가능성 높은 원인이라는 사례 추론이다. yfinance 과거 OHLC 수정 가능성도 배제할 수 없다. 정확한 원인 확정에는 당시 시장 context 및 OHLC snapshot이 필요하다.

## CE형 판정 정의

사례 관찰용 정의를 사전에 고정했다.

- `ratio = entry_signal_score / entry_signal_threshold`
- 양수 component만 대상으로 `Top2 집중도 = 상위 2개 기여 합 / 전체 양수 기여 합`
- **CE형 = Top2 집중도 ≥ 0.70 AND ratio ≤ 1.15**

이 cut은 통계 최적화 결과가 아니라 “소수지표 몰빵 + 턱걸이”를 재현 가능하게 표시하기 위한 운영 정의다. 성과 관계 분석은 재실행 불일치의 영향을 피하기 위해 원래 평가 당시 저장된 `rl_replay_trades.jsonl` 206건을 기준으로 했다.

## 세 룰 비교

| 룰 | 거래 | CE형 | CE형 비율 | CE형 평균 pnl | 비CE 평균 pnl | 차이(CE-비CE) |
|---|---:|---:|---:|---:|---:|---:|
| ANET | 81 | 17 | 20.99% | 0.08% | 2.33% | -2.25%p |
| BB | 46 | 8 | 17.39% | 6.83% | 0.36% | +6.47%p |
| CE | 79 | 24 | 30.38% | -2.40% | -0.42% | -1.98%p |

관찰:

- CE는 세 룰 중 CE형 비율이 가장 높았다. CE형 그룹은 네 구간 모두 비CE형보다 평균 pnl이 낮았다.
- CE `recent_1y`의 CE형은 5/18건, 평균 -9.20%, 승률 0%였고 비CE형은 평균 -3.55%, 승률 30.77%였다.
- CE `train_1`은 11/20건이 CE형으로 가장 높은 구간 내 비중을 보였다.
- ANET은 stress, train_1, recent_1y에서 CE형이 열위였지만 train_2에서는 CE형 3건 평균 +4.82%로 반대였다.
- BB의 CE형은 stress와 recent_1y에만 8건 존재했고, 이 사례에서는 비CE형보다 성과가 높았다.

따라서 CE형 신호가 CE와 ANET에서 위험 표식으로 보이는 사례는 있으나, BB의 반대 사례 때문에 “몰빵·턱걸이면 항상 실패”라고 일반화할 수 없다. 룰 3개와 작은 구간별 표본에 대한 사례 관찰이며 신규 자동 차단 게이트를 확정할 통계 근거가 아니다.

## 산출물

- `entries/`: 3개 룰 × 4개 구간 진입 상세 CSV 12개. 원본·재실행 component, score/raw score/threshold/ratio, 시장 보정, 양수 component 수, Top2 집중도, pnl, 행 단위 일치 상태 포함
- `reproduction_period_summary.csv`: 구간별 원본/재실행 거래 수·성과·component·완전 일치 집계
- `reproduction_trade_comparison.csv`: 행 단위 진입일·진입가·청산일·pnl 대조
- `ce_type_performance.csv`: 룰·구간별 CE형 대 비CE형 성과
- `rule_comparison.csv`: 세 룰 전체 비교
- `periods_and_sources.csv`: 메타데이터 날짜·역할·데이터 원천·원래 코드 cutoff
- `data_gaps_and_mismatches.csv`: 데이터 부재와 불일치 명시
- `replay_*.py`, `generate_*.py`: 같은 분석을 재실행하기 위한 별도 분석 스크립트

## 결론

component 포착 방법은 성공했고 확장 가능하다. 완전 재현은 12개 구간 중 2개만 성공했으므로 원래 Stage3 거래의 정답 분석에는 당시 저장된 `rl_replay_trades.jsonl`을 사용해야 한다. 그 원본 snapshot에서 CE는 몰빵·턱걸이 비중이 가장 높고 해당 진입의 성과도 더 나빴으며, ANET은 대체로 같은 방향, BB는 반대 방향이었다.
