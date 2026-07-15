# Stage3 AAP 신규 feature 4종 개별 null-test v8

## 결론

노트북 연결을 먼저 확인한 뒤, 실제 8개 run은 모두 Windows 노트북 `DESKTOP-TO74AR2`에서 실행했다. VM `invest-bot`은 Dask scheduler/파일 회수/감사 기록 역할만 수행했다.

판정 요약:

| 후보 | 판정 | 요약 |
|---|---|---|
| `trend_chop20` | FEATURE_IS_NOISE | REAL은 train_2 pass가 +2였지만 all2가 1개뿐이고 trade<8 탈락률이 SHUFFLED보다 크게 나빠졌다. |
| `atr14_pct` | FEATURE_IS_NOISE | SHUFFLED에서 all3=1, all2=26이 나와 REAL보다 우세했다. |
| `range_pct_rank60` | FEATURE_IS_NOISE | SHUFFLED가 all2=57, train_2 pass=145로 REAL보다 크게 우세했다. |
| `rs_peer3_ret20` | FEATURE_AMBIGUOUS | REAL이 all2 7 vs 4, train_2 pass 82 vs 77로 소폭 우세했지만 trade<8 탈락률이 크게 악화되어 조합 단계로 바로 넘기기에는 약하다. |

이번 null-test 기준으로 **FEATURE_HAS_SIGNAL 후보는 없다.**

## STEP -1 — 실행 host 사전 확정

노트북 Dask worker 확인:

| 노드 | host | OS | threads | 역할 |
|---|---|---|---:|---|
| VM | `invest-bot` | Linux | 8 | scheduler/관찰/파일 회수 |
| 노트북 | `DESKTOP-TO74AR2` | Windows | 28 | 실제 8개 run 실행 |

실제 실행 방식:

- 노트북에 full git checkout은 없었고 `C:\kingmaker` 배포본만 있었다.
- 그래서 VM에서 최소 실행 번들을 `C:\kingmaker_nulltest_v8`로 전송했다.
- Dask는 노트북 연결 확인, staging 전송, notebook-local driver 실행, 산출물 회수에만 사용했다.
- 각 GA run은 노트북 로컬 `C:\dask310\Scripts\python.exe` subprocess에서 `--workers 28`로 실행했다.
- staging에는 `.env`를 전송하지 않았다.
- `yfinance.py`, `requests.py` fetch-disabled stub을 staging에만 두었다. 외부 fetch/regenerate는 비활성이고 stub은 호출 시 예외를 낸다.

## 코드 변경

추가 파일:

- `scripts/research/stage23_rework_20260713/scripts/research/run_stage3_aap_feature_nulltest_v8_host.py`
- `scripts/research/stage23_rework_20260713/scripts/research/run_stage3_aap_feature_nulltest_v8_fixed_host.py`
- Windows spawn private-module shim 파일들: `_aap_*`, `_stage3_*`, `_kingmaker_stage3_aggressive_original_20260706.py`

관련 commit:

| commit | 내용 |
|---|---|
| `7d49fd2` | 작업 전 기준점 백업 |
| `71ba4f1` | v8 동적 strict-AND runner 추가 |
| `9847a53` | notebook staging root 탐색 보강 |
| `e431191` | Windows spawn import shim 추가 |
| `47f8df1` | shim 밑줄 함수 재노출 |
| `c5f6484` | Stage3 private module shim 추가 |
| `ca1154d` | qualify-only 실행 보강 |

변경 범위:

- 기존 진입/청산·should_buy·legacy·fixed-sizing·EEC 항·거래수 factor·win_rate gate 골격은 변경하지 않았다.
- 실행별로 후보 feature 1개만 기존 strict-AND entry interval 목록에 6번째 feature로 편입했다.
- 새 feature가 strict-AND에 들어가므로 `entry_interval_break` 청산에 영향을 줄 수 있다. 이는 설계상 영향으로 기록만 했고 차단하지 않았다.
- null-test 목적에 맞춰 qualify 100/40×3-fold 이후 entry/exit/validate는 stub으로 skip했다.

## 신규 feature 계산 정의

공통: 후보 일별 raw series를 계산한 뒤 기존 5개 feature와 동일하게 D-5 trading-day shift를 적용했다.

| 후보 | 정의 |
|---|---|
| `trend_chop20` | `abs(Close.pct_change(20)) / rolling_sum(abs(ret1), 20)`. 0에 가까울수록 왕복/소음, 1에 가까울수록 일방향 추세. |
| `atr14_pct` | `ATR_pct` 컬럼 사용. 없으면 `ATR / Close * 100`. |
| `range_pct_rank60` | `(High-Low)/Close*100`의 60일 rolling percentile rank, `min_periods=20`. |
| `rs_peer3_ret20` | `AAP_ret20 - median(GPC_ret20, ORLY_ret20, AZO_ret20)`. 각 종목 close를 첫 유효일 100으로 rebasing한 뒤 20일 수익률을 계산. |

Peer cache:

`data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache/`

| ticker | SHA256 |
|---|---|
| AAP | `f3982ccac813b9932b58059afe30dc554b66964dd01e312fca771f7d96f719da` |
| GPC | `172528a07786e0e204ddeafb7570a185c465f13570ae19e50d6aecaa696bfeda` |
| ORLY | `e37d9b5da83d517e6e77ba0f2d55ef4f07869815b24e73cc4f7259df01f8d75a` |
| AZO | `42006c75744935719992fe0c423580541ba560e4ba67d3cd894d130690bd36d1` |

## 정적 검증

| 검증 | 결과 |
|---|---:|
| py_compile | PASS |
| 4 후보 × REAL/SHUFFLED verify-only | PASS |
| 새 feature D-5 series SHA 기록 | PASS |
| peer basket GPC/ORLY/AZO cache 존재·SHA 기록 | PASS |
| Rulebook extra interval roundtrip | PASS |
| legacy bitwise without dynamic fields | PASS |
| mutation helper AST SHA | `aab7163f9194cf5f989ad01973e8d2967dad48be53f7d52ee09747eea502077d` |
| entry_interval_break 영향 | 기록됨 |

## REAL vs SHUFFLED 비교표

### trend_chop20

| Metric | REAL | SHUFFLED |
|---|---:|---:|
| all3/all2/all1/all0 | 0/1/230/69 | 0/11/219/70 |
| train_2 pass | 82 | 80 |
| train_2 trade<8 탈락률 | 32.33% | 1.33% |
| fold-best fitness | 0.846 / 1.520 / 1.384 | 0.808 / 0.427 / 1.580 |
| fold-best EEC | 4.455 / 5.818 / 6.368 | 5.400 / 3.400 / 6.400 |
| 판정 | FEATURE_IS_NOISE | - |

해석: REAL train_2 pass는 +2지만 all2와 trade<8 병목이 SHUFFLED보다 나쁘다.

### atr14_pct

| Metric | REAL | SHUFFLED |
|---|---:|---:|
| all3/all2/all1/all0 | 0/2/257/41 | 1/26/229/44 |
| train_2 pass | 83 | 111 |
| train_2 trade<8 탈락률 | 3.00% | 29.67% |
| fold-best fitness | 0.849 / 1.381 / 1.743 | 0.806 / 1.365 / 1.711 |
| fold-best EEC | 5.114 / 3.657 / 4.500 | 4.840 / 4.568 / 7.118 |
| 판정 | FEATURE_IS_NOISE | - |

해석: REAL은 trade<8 탈락률은 낮지만 SHUFFLED가 all3=1, all2=26, train_2 pass=111로 명확히 우세하다.

### range_pct_rank60

| Metric | REAL | SHUFFLED |
|---|---:|---:|
| all3/all2/all1/all0 | 0/3/238/59 | 0/57/194/49 |
| train_2 pass | 87 | 145 |
| train_2 trade<8 탈락률 | 1.33% | 1.67% |
| fold-best fitness | 0.912 / 1.823 / 1.697 | 0.874 / 1.493 / 1.620 |
| fold-best EEC | 5.452 / 6.000 / 5.444 | 4.945 / 5.944 / 5.452 |
| 판정 | FEATURE_IS_NOISE | - |

해석: REAL fold-best fitness는 좋지만, candidate population 기준 핵심 지표인 all2와 train_2 pass가 SHUFFLED보다 훨씬 약하다.

### rs_peer3_ret20

| Metric | REAL | SHUFFLED |
|---|---:|---:|
| all3/all2/all1/all0 | 0/7/234/59 | 0/4/238/58 |
| train_2 pass | 82 | 77 |
| train_2 trade<8 탈락률 | 28.00% | 0.67% |
| fold-best fitness | 0.843 / 1.414 / 1.711 | 0.863 / 1.287 / 1.691 |
| fold-best EEC | 3.723 / 5.488 / 6.533 | 6.261 / 4.000 / 4.455 |
| 판정 | FEATURE_AMBIGUOUS | - |

해석: REAL이 all2와 train_2 pass를 소폭 올렸지만, trade<8 병목이 크게 나빠졌고 all3는 없다. 단독 feature로 조합 단계 진입은 보류한다.

## 후보별 최종 판정

| 후보 | 코드 | 근거 |
|---|---|---|
| trend_chop20 | FEATURE_IS_NOISE | REAL all2 1 < SHUFFLED 11, trade<8 탈락률 32.33%로 악화. |
| atr14_pct | FEATURE_IS_NOISE | SHUFFLED가 all3 1개를 만들고 train_2 pass도 더 높음. |
| range_pct_rank60 | FEATURE_IS_NOISE | SHUFFLED all2 57, train_2 pass 145로 REAL보다 우세. |
| rs_peer3_ret20 | FEATURE_AMBIGUOUS | REAL all2 7 vs 4, train_2 pass 82 vs 77이나 trade<8 탈락률 28.00%로 악화. |

다음 단계 권고:

1. 이번 4개 단일 후보는 조합 단계로 바로 넘기지 않는다.
2. `rs_peer3_ret20`만 약한 보류 후보로 남긴다. 단, 다음에는 trade-count support를 보존하는 방식의 domain/interval 제약 또는 더 안정적인 peer-relative 변형을 먼저 검토해야 한다.
3. `range_pct_rank60`은 개별 feature로는 noise 판정이지만 fold-best fitness는 좋았다. 다만 SHUFFLED가 population 지표에서 너무 강해 단독 채택 근거는 없다.

## 보호파일 / daemon

시작·종료 SHA 동일:

| 파일 | SHA256 |
|---|---|
| `.env` | `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` |
| `data/_system/market_history.csv` | `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38` |
| `data/_system/market_history_v2.csv` | `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611` |

- daemon PID `494330` 유지 확인.
- final git 상태와 readout SHA는 `SHA256SUMS.txt`에 기록한다.
