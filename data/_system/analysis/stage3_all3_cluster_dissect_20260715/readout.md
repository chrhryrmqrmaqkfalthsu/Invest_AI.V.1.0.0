# LASR/ADPT all3 후보 클러스터 해부 — read-only

## 결론

판정: **ALL3_MIXED — AAP_IS_IDIOSYNCRATIC caveat 유지**

- **LASR all3 2개는 건강한 분산 신호가 아니다.** 두 후보는 전체 entry signal date 기준 Jaccard `0.974`로 사실상 같은 신호다. train_1/train_2 EEC가 `2.079/1.882`이고 최대 클러스터 비중이 `64.7%/62.5%`라 AAP에서 우려했던 몰빵 구조와 거의 같다. 특히 train_2는 최대 클러스터 제거 후 `8건 62.5%, +16.24p`에서 `3건 33.3%, -1.61p`로 무너진다.
- **ADPT all3 1개는 완전한 건강 분산은 아니지만, LASR식 몰빵 성과는 아니다.** train_1/train_3 EEC가 `2.564/2.941`로 낮고 최대 클러스터 비중도 `55.0%/40.0%`라 concentration caveat는 남는다. 하지만 최대 클러스터 제거 후 train_1, train_2, train_3 모두 성과가 유지되거나 오히려 개선된다.
- 따라서 “AAP가 유별나다”는 결론은 **부분 유지**하되, “다종목 all3가 건강한 분산 신호라 caveat 해제”는 **기각**한다.

## 실행/제약 확인

- 실행 host: `invest-bot`
- 작업 방식: read-only 결정론적 재생. GA/재학습 없음.
- 사용 데이터: `data/_system/analysis/stage3_multiticker_v5_probe_20260715/`의 LASR/ADPT all3 후보 rulebook + cross-fold 결과 + OHLCV cache.
- source/code 수정 없음.
- 저장된 rulebook JSON에는 entry-scope runtime marker `_active_ga_gene_scope="entry"`가 직렬화되지 않는다. 재생 시 이 marker를 복원해야 stored cross-fold의 trade_count와 일치한다. marker 복원 후 `MISMATCH=[]` 확인.

## 체결 기준 확인

동일 source의 결정론적 체결 기준:

- 진입: `t_plus_1_open`, 신호일 다음 거래일 open. `engine/learning/execution_mode_backtest.py:378-385`.
- ATR stop: 보유 중 당일 low가 stop_price 이하이면 체결. gap-down이면 당일 open, 아니면 stop_price. `engine/strategies/exit_simulator.py:245-249`, `409-420`.
- interval-break: 당일 strict interval fail 확인 후 다음 거래일 open 청산. `engine/strategies/exit_simulator.py:434-449`.
- max holding: provisional cap 도달 시 해당일 close. `engine/strategies/exit_simulator.py:463-471`.

따라서 당일 고가 매도 같은 look-ahead 체결은 없다.

## STEP 0 — 후보/데이터 검증

| ticker | all3 후보 수 | OHLCV SHA | D-5 feature SHA | rulebook file SHA | cross-fold file SHA |
|---|---:|---|---|---|---|
| LASR | 2 | `d9c89fb09b543d09ee7a08ca319e27a7950a580f47e9c9a1d22b9055362112a5` | `d5182e604d6102c3b9fc1da16c4a8e5bafe19fe6b921507fd6ab41c1b6b5c98a` | `90202b7f098b8b74d47625303874dee2bab05dc5100702133ebc4c3ed21272dc` | `15e4ef6fa90a637e6714478a5b1d847998349d8d8eb06419217f646d5f2e7701` |
| ADPT | 1 | `13fb9f982e8efa29e4ee6dd3ffb585d7fb5d578c3c5099e8c76409ca0ada9503` | `7ae6f2107da975d4c43c628705be74719f7203ee3469a3a205c23eaa2588ea60` | `b6e3ba9b55dcb0d2a1ca5fd5298a80db5ed30fbca66805198be31f77aeaf97f5` | `8b0135c4d1757179054ac5497e48270f1469e34cd10f46f93a3e28951a759a6d` |

all3 candidate object SHA:

| candidate | candidate_hash | rulebook object SHA | cross-fold object SHA |
|---|---|---|---|
| LASR_all3_1 | `deb35e5a3a60ad2731a33fe9e1378b162f798b89a8d710eb4505d1da37253e06` | `2b6034e1f89444eccf3d64551e193bda190253d909939c8369655171dfa4c6a6` | `0ee187b06e77eb83d0698ae7582bf819b5bfd3b0119a67463ec215fe9eb3a07e` |
| LASR_all3_2 | `ec2841e0fb1a9418dca1c961b2df1b6c337f18a8230a58abfdb04f4aaf47e852` | `a196c449f9fcfc441fd7ab98d39096c19acb13b484dbb76af48c6df72ec6889b` | `fcfa7346cec3bfcfbe4eb425c7b115f6e43fcbcf9d7c29b824ce38857e753d81` |
| ADPT_all3_1 | `3c950cfa5f239b1530cb312ad4b224b74ad1e7d8922d5dbe1c6e0146774231d6` | `d4cb840ec7a13769219227a79c4f420d7f34372c59b64354c55483bb9839d5c5` | `cf8615fe097218846c331db95a931ce95914ae27b3e9a7a600f99aaed3688d53` |

## STEP 1 — fold별 클러스터 구조

AAP 기준 비교: 사용자 지정 AAP v5 집중 baseline은 EEC `2.30/2.53/3.70`, 최대 클러스터 `60%/60%/37%`.

| candidate | fold | trades | non-dup events | EEC | max cluster share | cluster count | AAP 대비 |
|---|---:|---:|---:|---:|---:|---:|---|
| LASR_all3_1 | train_1 | 17 | 17 | 2.079 | 64.7% | 3 | AAP보다 나쁨 |
| LASR_all3_1 | train_2 | 8 | 8 | 1.882 | 62.5% | 2 | AAP보다 나쁨 |
| LASR_all3_1 | train_3 | 13 | 13 | 4.568 | 38.5% | 7 | EEC는 개선, max share는 비슷/약간 나쁨 |
| LASR_all3_2 | train_1 | 17 | 17 | 2.079 | 64.7% | 3 | AAP보다 나쁨 |
| LASR_all3_2 | train_2 | 8 | 8 | 1.882 | 62.5% | 2 | AAP보다 나쁨 |
| LASR_all3_2 | train_3 | 14 | 14 | 4.083 | 42.9% | 7 | EEC는 약간 개선, max share는 나쁨 |
| ADPT_all3_1 | train_1 | 20 | 20 | 2.564 | 55.0% | 4 | AAP와 비슷 |
| ADPT_all3_1 | train_2 | 16 | 16 | 5.565 | 25.0% | 8 | AAP보다 명확히 개선 |
| ADPT_all3_1 | train_3 | 10 | 10 | 2.941 | 40.0% | 3 | AAP와 비슷/약간 나쁨 |

핵심 클러스터:

| candidate | fold | cluster | period | trades | share | win-rate | total PnL | regime |
|---|---|---:|---|---:|---:|---:|---:|---|
| LASR_all3_1 | train_1 | C2 | 2022-10-11~2022-11-02 | 11 | 64.7% | 72.7% | +21.93 | down mid/low vol |
| LASR_all3_1 | train_2 | C2 | 2024-05-06~2024-05-10 | 5 | 62.5% | 80.0% | +17.85 | down/sideways low-mid vol |
| LASR_all3_1 | train_3 | C7 | 2025-05-01~2025-05-07 | 5 | 38.5% | 100.0% | +209.53 | strong_down low-mid vol |
| LASR_all3_2 | train_1 | C2 | 2022-10-11~2022-11-02 | 11 | 64.7% | 72.7% | +21.93 | same |
| LASR_all3_2 | train_2 | C2 | 2024-05-06~2024-05-10 | 5 | 62.5% | 80.0% | +17.85 | same |
| LASR_all3_2 | train_3 | C7 | 2025-04-30~2025-05-07 | 6 | 42.9% | 100.0% | +256.26 | strong_down low-mid vol |
| ADPT_all3_1 | train_1 | C4 | 2023-03-07~2023-04-04 | 11 | 55.0% | 45.5% | -18.69 | mixed sideways/up |
| ADPT_all3_1 | train_2 | C6/C7 | 2024-01-31~2024-04-05 | 4/4 | 25.0%/25.0% | 50.0%/100.0% | +6.05/+24.01 | down/high vol |
| ADPT_all3_1 | train_3 | C2 | 2025-03-21~2025-03-27 | 4 | 40.0% | 0.0% | -39.10 | strong_up high/mid vol |

## STEP 2 — 최대 클러스터 제거 후 성과

| candidate | fold | all n | all win-rate | all PnL | max cluster | removed n | removed win-rate | removed PnL | remain n | remain win-rate | remain PnL | remain losses | remain max loss |
|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| LASR_all3_1 | train_1 | 17 | 82.4% | +48.13 | C2 | 11 | 72.7% | +21.93 | 6 | 100.0% | +26.20 | 0 | +0.78 |
| LASR_all3_1 | train_2 | 8 | 62.5% | +16.24 | C2 | 5 | 80.0% | +17.85 | 3 | 33.3% | -1.61 | 2 | -2.44 |
| LASR_all3_1 | train_3 | 13 | 76.9% | +216.34 | C7 | 5 | 100.0% | +209.53 | 8 | 62.5% | +6.81 | 2 | -1.82 |
| LASR_all3_2 | train_1 | 17 | 82.4% | +48.13 | C2 | 11 | 72.7% | +21.93 | 6 | 100.0% | +26.20 | 0 | +0.78 |
| LASR_all3_2 | train_2 | 8 | 62.5% | +16.24 | C2 | 5 | 80.0% | +17.85 | 3 | 33.3% | -1.61 | 2 | -2.44 |
| LASR_all3_2 | train_3 | 14 | 78.6% | +263.07 | C7 | 6 | 100.0% | +256.26 | 8 | 62.5% | +6.81 | 2 | -1.82 |
| ADPT_all3_1 | train_1 | 20 | 60.0% | +50.56 | C4 | 11 | 45.5% | -18.69 | 9 | 77.8% | +69.25 | 2 | -4.19 |
| ADPT_all3_1 | train_2 | 16 | 81.2% | +84.38 | C6 | 4 | 50.0% | +6.05 | 12 | 91.7% | +78.33 | 1 | -1.94 |
| ADPT_all3_1 | train_3 | 10 | 60.0% | +24.51 | C2 | 4 | 0.0% | -39.10 | 6 | 100.0% | +63.62 | 0 | +3.93 |

승부처 해석:

- LASR train_2: 최대 클러스터 C2를 제거하면 `62.5%, +16.24`가 `33.3%, -1.61`로 붕괴한다. AAP train_2에서 보였던 구조와 가장 유사하다.
- LASR train_3: win-rate는 62.5%로 남지만, PnL은 `+216.34/+263.07`에서 `+6.81`로 거의 전부 사라진다. 강한 2025-05 반등 클러스터가 성과를 지배한다.
- ADPT: 최대 클러스터가 오히려 나쁜 클러스터인 fold가 많다. 제거 후 train_1 `+69.25`, train_2 `+78.33`, train_3 `+63.62`로 성과가 유지/개선된다. ADPT는 LASR식 “좋은 클러스터 몰빵”은 아니다.

## STEP 3 — 분산 실체와 후보 간 중복

LASR 두 후보 간 entry signal date overlap:

| candidate A | candidate B | dates A | dates B | intersection | union | Jaccard |
|---|---|---:|---:|---:|---:|---:|
| LASR_all3_1 | LASR_all3_2 | 38 | 39 | 38 | 39 | 0.974 |

즉 LASR all3 2개는 서로 독립 후보가 아니라 거의 같은 국면을 공유한다.

대표 timeline:

| candidate | fold | timeline summary |
|---|---|---|
| LASR_all3_1 | train_1 | 2022-07 C1 down/low_vol wins; 2022-10~11 C2 down/mid-low_vol 11 trades; 2023-05 C3 sideways/down mid_vol |
| LASR_all3_1 | train_2 | 2023-11 C1 sideways high/mid vol poor; 2024-05 C2 down/sideways low-mid vol dominates win |
| LASR_all3_1 | train_3 | 2024-07~2025-03 scattered small trades, then 2025-05 C7 strong_down rebound dominates PnL |
| LASR_all3_2 | all folds | 거의 LASR_all3_1과 동일. train_3에 2025-04-30이 추가되어 C7이 6건으로 커짐 |
| ADPT_all3_1 | train_1 | 2023-01 C3 strong_up/low_vol good, 2023-03~04 C4 mixed but negative |
| ADPT_all3_1 | train_2 | 여러 strong_down 구간에 분산. C6/C7/C8 중 C7/C8가 성과, C6은 약함 |
| ADPT_all3_1 | train_3 | 2024-10 C1 good, 2025-03 C2 strong_up/high_vol bad, 2025-04~05 C3 good |

## STEP 4 — 판정

| candidate | 판정 | 근거 |
|---|---|---|
| LASR_all3_1 | ALL3_IS_CONCENTRATED_REDUX | train_1/2 EEC가 AAP보다 낮고 max cluster가 64.7%/62.5%. train_2는 max cluster 제거 후 음수 PnL. train_3도 PnL 대부분이 2025-05 C7에서 발생. |
| LASR_all3_2 | ALL3_IS_CONCENTRATED_REDUX | LASR_all3_1과 거의 동일, Jaccard 0.974. train_3 C7이 42.9%, +256.26p로 성과 지배. |
| ADPT_all3_1 | ALL3_MIXED_ROBUST_BUT_NOT_HEALTHY | train_2는 EEC 5.565/max 25.0%로 건강. train_1/3은 EEC 2.564/2.941로 낮아 caveat 유지. 다만 max cluster 제거 후 성과는 유지/개선되어 LASR식 몰빵은 아님. |

종합: **ALL3_MIXED**. LASR는 “종목만 바뀐 몰빵”으로 보이며, ADPT는 일부 fold가 견고하지만 전체 fold 기준 건강 분산 조건 `전 fold EEC ≥4 + max cluster 제거 후 유지`를 만족하지 못한다. 따라서 다종목 v5의 AAP_IS_IDIOSYNCRATIC 판정은 구조적 불가능 기각 근거로는 유효하지만, caveat는 해제하지 않는다.

## 산출물

- `readout.md`: 본 보고서
- `trade_level_compact.md`: 후보별·fold별 compact trade-level 로그
- `SHA256SUMS.txt`: SHA 및 보호파일/daemon/git audit

## 보호파일 / daemon

시작·종료 SHA 동일:

| 파일 | SHA256 |
|---|---|
| `.env` | `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` |
| `data/_system/market_history.csv` | `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38` |
| `data/_system/market_history_v2.csv` | `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611` |

- daemon PID `494330` 유지 확인.
- 산출 전 backup commit: `b80cc97`.
