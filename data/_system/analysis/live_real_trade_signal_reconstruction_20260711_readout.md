# 라이브 실거래 손실 진입의 신호 구성 재구성

## 최종 판정

**신호 복원 관문은 부분 통과, 손실 가설 검증은 불가**다.

확정 가능한 `alpaca_live` 체결은 CE 매수 1건뿐이며 아직 청산되지 않았다. 확정 매도 체결과 실현 손익 거래는 0건이다. 따라서 “실제 손실 진입이 소수 지표·BB 부재·ratio 턱걸이였는가”를 라이브 실현 손실에서 검증할 표본이 없다.

CE의 주문 제출 시점 신호는 실제 라이브 후보 snapshot에서 복원됐다. 다만 구형 snapshot이라 component dictionary가 없고 `reasons`의 반올림 표시값만 남아 있어 지표별 값은 표시 정밀도까지다. point-in-time 재계산은 사용하지 않았다.

## 실거래 전량

| 상태 | candidate_id | ticker | 진입 주문 제출 | 체결 평균가 | 수량 | 청산 | 실현 pnl |
|---|---|---|---|---:|---:|---|---|
| 보유 중 | `stage3:CE:998b0b638c66` | CE | 2026-07-08 14:27:15 UTC | 48.5715 | 13.270723 | 없음 | 없음 |

CE는 이후 `real_dashboard_alpaca_exit_orders.json`의 `position_snapshot.account_source=alpaca_live`에서 동일 candidate_id, entry price, shares가 확인돼 매수 체결로 확정했다. 매수 intent 파일 자체는 여전히 `pending_new`, filled quantity 0으로 남아 있어 실제 fill 시각은 복원되지 않는다.

CE 매도 OCO는 2026-07-08 18:03:50 UTC에 제출됐으나 parent status `pending_new`, filled quantity 0이며 stop leg도 `held`다. 따라서 확정 매도는 0건이다.

같은 snapshot의 중간 평가값은 current price 47.9350, unrealized pnl -1.3104%(-8.4468)이지만 이는 실현 손실이 아니다.

## 실거래와 제외 기록 구분

- `real_dashboard_trades_history.json`: `alpaca_live` 전용이나 거래 0건.
- `real_dashboard_manual_buy_intent.json`: direct live 주문 의도 8건. CE 외 7건은 fill/position 증거가 없어 체결로 채택하지 않았다.
- `trade_log.csv` 11건과 `positions.json` 3건은 Telegram dashboard의 `alpaca_paper` 모드와 연결돼 실거래 표본에서 제외했다.
- `broker_snapshot_20260625.txt`의 ANET·FNDF·ICHR는 account source가 기록되지 않아 live/paper를 확정할 수 없으므로 제외했다.

제출됐지만 체결 확정되지 않은 것은 BMI, ALGT, BCS, ADPT, BB, CDE, ANET이다. 모두 로컬 상태가 `pending_new`, filled quantity 0이고 이후 `alpaca_live` position evidence가 없다.

## CE 진입 신호 복원

신호 snapshot 시각: 2026-07-08 14:27:15 UTC. 주문 제출 직전 실제 candidate snapshot이다.

| 항목 | 값 | 신뢰도 |
|---|---:|---|
| score | 8.363246 | 실제 로그 full precision |
| raw score | 8.363246 | 실제 로그 full precision |
| threshold | 2.654187 | 실제 로그 full precision |
| ratio | 3.150964 | 실제 로그 full precision |
| market adjustment | 1.0 | final score=raw score에서 계산 |
| MACD | +1.17 | 실제 reasons 표시값, 반올림 |
| RSI | +1.73 | 실제 reasons 표시값, 반올림 |
| BB | +0.85 | 실제 reasons 표시값, 반올림 |
| Event | +4.62 | 실제 reasons 표시값, 반올림 |
| MA·Volume·News·NewsTopics | 0으로 추정 | reasons에 없음; explicit component dict 부재 |

활성 조합은 **MACD+RSI+BB+Event**, 양수 component 수는 4개다. BB가 존재하고 ratio도 3.15로 threshold 근처가 아니다.

## 과거 가설과의 대조

과거 가설의 위험 패턴은 다음이었다.

- 발화 지표 수가 적음
- BB 같은 확증 지표 부재
- MACD+RSI 단독
- ratio 턱걸이

유일한 확정 live 진입 CE는 위 네 패턴에 해당하지 않는다. 4개 component가 발화했고 BB가 있으며, ratio는 3.15다. 그러나 이 거래는 아직 미실현 상태이고 중간 snapshot 하나만 -1.31%였으므로, 가설을 반박하는 손실 사례로도 사용할 수 없다.

확정 실현 손실 0건, 확정 실현 수익 0건이므로 손실 대 수익 신호 구성 비교와 “BB 유무가 MACD+RSI 성과를 가른다”는 패턴의 라이브 검증은 **NOT TESTABLE**이다.

## 로깅 관문

현재 라이브 기록으로 가능한 것:

- candidate_id와 실제 live position 연결
- 주문 제출 시점 score, threshold, ratio 복원
- 활성 지표 조합 복원
- CE의 반올림 component 기여값 복원

현재 기록으로 불가능하거나 불완전한 것:

- 실제 buy fill timestamp
- 구형 snapshot의 component full precision
- intent/order journal의 fill 상태 자동 정합화
- 청산 체결과 진입 snapshot을 연결한 closed-trade history

따라서 결론은 “신호 구성 자체는 일부 복원되지만, 실현 성과 검증에는 아직 부족하다”이다. 향후 확인을 위해서는 fill event에 candidate_id와 full-precision component snapshot을 고정 저장하고, sell fill 시 같은 trade ID로 closed history를 남겨야 한다. 이번 작업에서는 코드 변경을 하지 않았다.

## 산출물

- `live_real_trade_signal_reconstruction_20260711_trades.csv`
- `live_real_trade_signal_reconstruction_20260711_signal_reconstruction.csv`
- `live_real_trade_signal_reconstruction_20260711_order_intents_part1.csv`
- `live_real_trade_signal_reconstruction_20260711_order_intents_part2.csv`
- `live_real_trade_signal_reconstruction_20260711_hypothesis_comparison.csv`
- `live_real_trade_signal_reconstruction_20260711_source_inventory.csv`
- `live_real_trade_signal_reconstruction_20260711_data_gaps.csv`
- `live_real_trade_signal_reconstruction_20260711_summary.csv`

이 결과는 실거래 1건, 실현 거래 0건에 대한 기록 감사이며 통계적 결론이 아니다.
