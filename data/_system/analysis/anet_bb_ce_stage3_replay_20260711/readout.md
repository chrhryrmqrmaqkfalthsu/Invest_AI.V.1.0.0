# ANET·BB·CE Stage3 평가구간 재현 백테스트 readout

## 판정

**REPRODUCTION_MISMATCH_WITH_ENTRY_COMPONENTS_AVAILABLE**

세 룰 모두 현재 `engine/learning/backtest.py` 경로에서 `entry_signal_components`, `entry_signal_score`, `entry_signal_raw_score`, `entry_signal_threshold`, `entry_market_adjustment`가 실제 거래 레코드에 생성되는 것을 확인했다. 따라서 향후 다른 개체도 동일 방법으로 component 포착이 가능하다.

그러나 현재 저장소/현재 데이터로 재실행한 결과는 원래 Stage3 `validation_results.jsonl`·`exit_trades.jsonl`과 대부분의 구간에서 일치하지 않았다. 12개 룰×구간 중 거래 수와 expectancy가 모두 일치한 것은 CE의 `recent_1y`, `train_2` 두 구간뿐이다. 따라서 현재 재실행 결과를 원래 Stage3 성과와 동일한 재현물로 간주하면 안 된다.

## 대상 룰

| ticker | Stage3 rulebook hash |
|---|---|
| ANET | `fe220620802b432f760bf18cb42e27465bdf78361ea803a0d00692c80848932b` |
| BB | `f1bdfe7f8ad9eab0337862d4166754166c299876cca58096ada5fa0845c76024` |
| CE | `998b0b638c6649fe10d3dff0fc74f890d9891ef9caa7ed61a2bae1e340288b78` |

## 원래 Stage3 평가구간

메타데이터 원천은 각 종목의 `stage3/manifest.json`, `stage3/validation_results.jsonl`이다.

| 구간 | 역할 | 시작 | 종료 |
|---|---|---|---|
| stress_pre_2022h1 | exit_check / stress | 데이터 시작 | 2022-06-30 |
| train_1 | pure_oos | 2022-07-01 | 2023-06-30 |
| train_2 | pure_oos | 2023-07-01 | 2024-06-30 |
| recent_1y (ANET) | pure_oos | 2025-07-01 | 2026-06-24 |
| recent_1y (BB·CE) | pure_oos | 2025-07-01 | 2026-06-26 |

현재 loader는 세 종목 모두 2020-06-11~2026-07-09, 1,526행을 읽었다. 원래 `recent_1y` 종료일은 메타데이터에 고정된 날짜를 그대로 사용했다.

## component 포착 확인

재실행 거래 dict에서 다음 필드를 직접 확인했다.

- `entry_signal_components`
- `entry_signal_score`
- `entry_signal_raw_score`
- `entry_signal_threshold`
- `entry_market_adjustment`
- `entry_date`, `entry_price`, `exit_date`, `exit_price`, `pnl_pct`

원래 Stage3 산출물에도 compact `exit_trades.jsonl` 외에 `rl_replay_trades.jsonl`이 존재하며, 대상 세 룰의 206건 거래(ANET 81, BB 46, CE 79)에 위 진입 신호 필드가 남아 있다. 이 파일을 원래 평가 당시의 상세 진입 snapshot 원천으로 사용했다.

## 재현 정합성

상세 수치는 `reproduction_check.csv` 참조.

- ANET: 4개 구간 모두 expectancy 불일치. stress -2건, train_1 -1건. recent_1y·train_2는 거래 수만 같고 성과 불일치.
- BB: 4개 구간 모두 불일치. recent_1y -2건, stress -1건, train_1 -2건. train_2는 거래 수만 같고 expectancy -3.0612%p 차이.
- CE: recent_1y와 train_2만 거래 수·expectancy 일치. stress -2건, train_1 -3건.

가장 가능성 높은 원인은 원래 Stage3 실행 이후 데이터 snapshot 또는 백테스트 관련 코드/시장·이벤트 입력이 변한 것이다. 현재 실행은 동일 날짜와 동일 룰북을 사용했지만, 원래 실행 시점의 frozen OHLC/뉴스/시장 컨텍스트 snapshot과 코드 커밋을 완전히 고정해 복원하지 못했다. 따라서 행 단위 진입일·진입가·pnl 완전 일치 검증은 실패 판정이다.

## CE형 정의

사례 관찰용 운영 정의를 다음처럼 고정했다.

- `ratio = entry_signal_score / entry_signal_threshold`
- 양수 component만 대상으로 `Top2 집중도 = 상위 2개 양수 기여 합 / 전체 양수 기여 합`
- **CE형 = Top2 집중도 ≥ 0.70 AND ratio ≤ 1.15**

이는 통계적 최적 cut이 아니라 “소수지표 몰빵 + 턱걸이”를 재현 가능하게 표시하기 위한 사전 명시 규칙이다.

## 룰별 관찰

| 룰 | 원래 거래 수 | CE형 수 | CE형 비율 | CE형 평균 pnl | 비CE 평균 pnl | 관찰 |
|---|---:|---:|---:|---:|---:|---|
| ANET | 81 | 17 | 21.0% | 0.14% | 2.33% | 대체로 CE형이 열위. 단 train_2에서는 CE형 3건 평균 4.82%로 예외 |
| BB | 46 | 8 | 17.4% | 6.83% | 0.53% | 이 사례에서는 CE형이 오히려 우위. CE형은 recent_1y·stress에만 존재 |
| CE | 79 | 24 | 30.4% | -2.25% | -0.50% | 세 룰 중 CE형 비율이 가장 높고 CE형 성과도 더 나쁨 |

구간별 상세는 `ce_type_performance.csv` 참조.

핵심 사례 관찰:

1. CE는 train_1에서 20건 중 11건, recent_1y에서 18건 중 5건이 CE형이었다. recent_1y CE형 5건은 평균 -9.20%, 승률 0%로 비CE형 평균 -3.55%보다 명확히 열위였다.
2. ANET도 recent_1y·stress·train_1에서 CE형이 비CE형보다 열위였지만 train_2는 반대였다.
3. BB는 CE형 8건이 오히려 비CE형보다 높은 평균 pnl을 보였다. 따라서 “몰빵·턱걸이이면 항상 실패”로 일반화할 수 없다.
4. 세 룰만 본 사례 관찰이며 표본 수가 작고 룰·구간별 이질성이 크다. 일반화나 신규 게이트 확정 근거로 사용하면 안 된다.

## 데이터 부재·제약

- 원래 실행 시점의 frozen 원본 데이터 snapshot과 정확한 코드 commit 조합을 현재 메타데이터만으로 완전 복원하지 못했다.
- 재실행 상세 거래는 메모리에서 component 생성까지 확인했으나, 원본과 불일치하므로 이를 별도 “정답 상세 CSV”로 저장하지 않았다.
- 대신 원래 Stage3 실행 당시 이미 저장된 `rl_replay_trades.jsonl`의 대상 룰 거래를 분석 원천으로 사용했다. 이 JSONL은 component·score·threshold·market adjustment·진입/청산·pnl을 모두 포함한다.
- 원래 재현 정답을 만들려면 당시 코드 commit과 OHLC/시장/뉴스 snapshot을 함께 checkout한 격리 환경이 필요하다.

## 산출물

- `reproduction_check.csv`: 원래 vs 현재 재실행 구간별 거래 수·expectancy 대조
- `ce_type_performance.csv`: 룰·구간별 CE형 vs 비CE형 성과 비교
- `readout.md`: 판정·구간·component 포착·불일치 원인·사례 관찰
- 원래 상세 진입 snapshot 원천: 각 ticker의 `stage3/rl_replay_trades.jsonl`

## 결론

component 포착 방법 자체는 성공했다. 하지만 현재 코드·현재 데이터로는 원래 Stage3 거래를 완전 재현하지 못했다. 원래 snapshot 기준 사례에서는 CE가 세 룰 중 몰빵·턱걸이 비중이 가장 높고 그 그룹의 성과가 더 나빴다. ANET도 대체로 같은 방향이지만 예외 구간이 있으며, BB는 반대 방향이다. 따라서 CE형 신호는 유용한 위험 표식 후보이지만 세 룰만으로 자동 차단 규칙을 확정할 수 없다.
