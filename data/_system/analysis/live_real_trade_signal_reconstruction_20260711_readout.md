# 라이브 실거래 손실 진입 신호 구성 재구성

## 결론

현재 보존된 `alpaca_live` 실거래 자료만으로는 **과거 재실행에서 관찰된 실패 패턴이 라이브에서도 반복됐다고 확인할 수 없다.**

확정 가능한 라이브 매수 체결은 CE 1건뿐이고 아직 청산되지 않았다. 확인 시점 미실현 수익률은 -1.31%였지만, 진입 신호는 `MACD+RSI+BB+Event` 4개 발화, ratio 3.151로 복원됐다. 따라서 과거 가설의 핵심인 `MACD+RSI` 단독·BB 부재·소수 발화·threshold 턱걸이 패턴과는 반대다.

즉 현재 관찰은 다음과 같다.

- 라이브 확정 청산 거래: 0건
- 라이브 확정 매수 체결: 1건(CE, 미청산)
- CE 진입 구성: `MACD+RSI+BB+Event`
- BB 존재: 예
- 양수 발화 수: 4개
- score / threshold / ratio: 8.3632 / 2.6542 / 3.1510
- 확인 시점 미실현 pnl: -1.31%
- 과거 실패 가설과의 일치: 아니오
- 통계적 판단 가능 여부: 불가

## 실거래 범위 확정

`data/_system/real_dashboard_trades_history.json`은 `account_source=alpaca_live`, `isolated=true`인 실거래 전용 청산 기록이지만 거래가 0건이다.

`data/_system/real_dashboard_manual_buy_intent.json`에는 직접 실계좌 주문 의도 8건이 있으나, 로컬에 저장된 주문 상태는 모두 `pending_new`, `filled_shares=0`, `filled_avg_price=0`이다. 제출 기록만으로는 체결을 확정하지 않았다.

`data/_system/real_dashboard_alpaca_exit_orders.json`에는 `account_source=alpaca_live`인 CE 포지션 스냅샷이 있으며, candidate_id가 매수 의도와 일치하고 보유 수량 13.270723주와 평균단가 48.5715가 기록돼 있다. 이 자료로 CE 매수 체결 1건만 확정했다.

`trade_log.csv`, `positions.json`은 별도 대시보드 자료에서 `alpaca_paper`로 식별되어 실거래 표본에서 제외했다. 계정 유형이 남지 않은 과거 broker snapshot도 보수적으로 제외했다.

## CE 진입 신호 복원

CE 주문 제출 후보 스냅샷 시각은 `2026-07-08T14:27:15.330072+00:00`이고 주문 제출 시각은 약 0.23초 뒤다. 실제 체결 시각은 보존되지 않았다.

후보 스냅샷과 reasons에서 다음 값을 복원했다.

| 항목 | 값 |
|---|---:|
| score | 8.363246 |
| raw score | 8.363246 |
| threshold | 2.654187 |
| ratio | 3.150964 |
| market adjustment | 1.0 |
| MA | 0.00 |
| MACD | 1.17 |
| RSI | 1.73 |
| BB | 0.85 |
| Volume | 0.00 |
| News | 0.00 |
| NewsTopics | 0.00 |
| Event | 4.62 |

활성 지표 값은 로그 reasons에 두 자리로 반올림돼 있고, score·threshold·ratio는 원 정밀도로 남아 있다. 비활성 지표의 0은 명시적 component dict가 아니라 reasons 부재에서 추론했다. 따라서 분류는 `actual_live_log_reconstruction`이지만 component 정밀도에는 이 제한이 있다.

## 과거 가설과 대조

과거 신뢰 높은 CE 재실행에서는 다음 6건이 대비됐다.

- `MACD+RSI`, BB 없음: 3건, 평균 -9.59%, 승률 0%
- `MACD+RSI+BB`: 3건, 평균 +6.27%, 승률 100%

라이브 CE는 `MACD+RSI+BB+Event`다. BB가 존재하고, 발화 수가 4개이며, ratio도 1.15 이하 턱걸이가 아니다. 따라서 현재 미실현 하락을 과거의 `MACD+RSI` 단독 실패 패턴 재현으로 해석할 근거가 없다.

또한 아직 청산되지 않았으므로 이 건을 손실 거래로 분류하면 안 된다. 미실현 -1.31%는 특정 시점의 평가손익일 뿐 최종 성과가 아니다.

## 판정

이번 조사에서 관문은 부분적으로 통과했다. CE 1건은 진입 당시 신호 구성을 실제 라이브 후보 로그에서 복원할 수 있었다. 그러나 확정 청산 손실 거래가 0건이어서 손실 대 수익의 신호 구성 비교는 수행할 수 없다.

따라서 현재 판정은 다음과 같다.

> 라이브에서 같은 패턴으로 무너졌다는 증거는 없다. 확인 가능한 유일한 라이브 체결은 오히려 BB가 포함된 다중 확증 진입이다. 다만 청산 표본이 없어 과거 가설을 지지하거나 반박할 통계적 근거도 없다.

## 산출물

- `live_real_trade_signal_reconstruction_20260711_source_inventory.csv`
- `live_real_trade_signal_reconstruction_20260711_trades.csv`
- `live_real_trade_signal_reconstruction_20260711_signal_reconstruction.csv`
- `live_real_trade_signal_reconstruction_20260711_readout.md`

운영 소스·설정·주문·상태 파일은 변경하지 않았다.
