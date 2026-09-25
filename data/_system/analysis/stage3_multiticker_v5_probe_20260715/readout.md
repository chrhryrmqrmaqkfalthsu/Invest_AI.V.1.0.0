# Stage3 다종목 v5 재학습 probe — AAP 유별성 검증

## 결론

판정: **AAP_IS_IDIOSYNCRATIC_WITH_EEC_CAVEAT**

엄밀한 분기 기준인 “4종목 중 하나라도 all3≥1이면 AAP 개별 문제”는 충족됐다. LASR에서 all3=2, ADPT에서 all3=1이 나왔다. 따라서 AAP all3=0은 파이프라인이 어떤 종목에서도 all3를 못 만드는 구조적 한계라기보다, AAP 구간/종목 특성의 영향이 크다.

다만 caveat가 있다. LASR/ADPT의 all3 후보는 일부 fold에서 EEC가 2~3 수준으로 낮다. 즉 “완전히 건전한 분산 all3”라고 보기는 어렵다. 그래도 동일 v5 조건 target=6/floor=0.5에서 all3 자체가 나온 점은 STRUCTURAL_LIMIT_CONFIRMED를 기각하기에 충분하다.

## 실행 사실

- 실행 host: `DESKTOP-TO74AR2` Windows 노트북
- 관찰/파일 회수/커밋 host: `invest-bot`
- 실행 방식: Dask로 노트북 연결 확인 후, 노트북 로컬 subprocess에서 각 ticker별 28 workers 실행
- seed: `2026071401`
- qualify: population 100 / generations 40 × train_1·train_2·train_3
- EEC: target `6.0`, floor `0.5`, cluster gap `8` trading days
- 실행 범위: qualify-only probe. all3가 나오면 원래 full v5 runner는 entry/exit로 넘어가지만, 본 지시의 판정 지표는 qualify의 all3/all2/fold pass/fold-best이므로 downstream entry/exit는 output driver에서 stub 처리했다.
- source 변경: 없음. `scripts/research/stage23_rework_20260713/` source diff 0 bytes.
- runtime patch: v5 runner가 AAP 전용이고 `--ticker`가 없어서, 산출물 폴더의 notebook driver가 런타임에만 `TICKER`와 pkl cache loader를 주입했다. 원본 source는 수정하지 않았다.

## STEP 0 — 데이터 검증

주 cache:

`data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache/`

| ticker | SHA256 | rows | 기간 | OHLCV 완비 | null OHLCV | duplicate date | fold rows train_1/2/3 | 기존 5 feature D-5 |
|---|---|---:|---|---:|---:|---:|---:|---:|
| LASR | `d9c89fb09b543d09ee7a08ca319e27a7950a580f47e9c9a1d22b9055362112a5` | 1527 | 2020-05-18~2026-06-15 | YES | 0 | 0 | 251/250/250 | OK |
| BTBT | `c555db59f58a41c50f4c3f6771f030585883a28bf454c22866a579b30ee5af9e` | 1527 | 2020-05-18~2026-06-15 | YES | 0 | 0 | 251/250/250 | OK |
| ADPT | `13fb9f982e8efa29e4ee6dd3ffb585d7fb5d578c3c5099e8c76409ca0ada9503` | 1527 | 2020-05-18~2026-06-15 | YES | 0 | 0 | 251/250/250 | OK |
| FIX | `9a6e3aa4fe099242f649ed9a2c36b6c1e7b2a49dc804442007e0e92d888579b5` | 1527 | 2020-05-18~2026-06-15 | YES | 0 | 0 | 251/250/250 | OK |

기존 5개 feature는 `ma_trend`, `macd_hist`, `rsi`, `bb_position`, `volume_ratio`다. 네 종목 모두 fold별 D-5 finite count가 train_1 251, train_2 250, train_3 250으로 정상이다.

## STEP 1 — Host 확인

Dask scheduler `tcp://localhost:8786` 기준:

| worker | host | OS | threads |
|---|---|---|---:|
| VM worker | `invest-bot` | Linux | 8 |
| notebook worker | `DESKTOP-TO74AR2` | Windows | 28 |

실제 GA run은 모두 `DESKTOP-TO74AR2`에서 수행했다. run별 host는 각 `*_launch_driver.json` 및 통합 `launch_command.json`에 기록했다.

## STEP A/B — 코드 변경 없음 / 정적 검증

| 검증 | 결과 |
|---|---:|
| source 수정 | 없음 |
| source diff bytes | 0 |
| py_compile | PASS |
| mutation-helper AST SHA | `aab7163f9194cf5f989ad01973e8d2967dad48be53f7d52ee09747eea502077d` |
| v5 EEC target/floor 최종 output 확인 | 6.0 / 0.5 |
| auto-fetch/regenerate | disabled / fail-closed |

주의: 현재 source 파일 `execution_mode_backtest_eec_v5.py` 자체 상수는 이전 v6 실험 때문에 target=4/floor=0.7로 남아 있었다. 이 지시서는 v5 target=6/floor=0.5를 요구하므로, source는 건드리지 않고 환경변수와 런타임 모듈 상수로 target=6/floor=0.5를 강제했다. 최종 산출물의 `qualify_cross_fold_matrix.jsonl`과 per-run `launch_command.json`에서 모두 target=6.0/floor=0.5를 확인했다.

## AAP v5 대비 다종목 결과

| ticker | all3/all2/all1/all0 | qualified | fold pass train_1/2/3 | train_2 trade<8 탈락률 | train_2 win-gate 병목률 |
|---|---:|---:|---:|---:|---:|
| AAP v5 | 0/4/245/51 | false | 90/80/83 | 2.67% | 69.33% |
| LASR | 2/149/114/35 | true | 165/91/162 | 3.00% | 65.33% |
| BTBT | 0/9/270/21 | false | 102/95/91 | 0.33% | 68.00% |
| ADPT | 1/24/248/27 | true | 117/76/106 | 27.33% | 46.67% |
| FIX | 0/51/219/30 | false | 91/82/148 | 1.67% | 35.00% |

핵심: LASR와 ADPT가 all3를 생성했다. AAP만 아니라 다른 ticker에서도 all3=0이 보편적으로 반복되는 구조는 아니다.

## fold-best 비교

### fold-best fitness

| ticker | train_1 | train_2 | train_3 |
|---|---:|---:|---:|
| AAP v5 | 0.873 | 1.504 | 1.445 |
| LASR | 1.703 | 1.122 | 3.857 |
| BTBT | 4.094 | 2.640 | 1.380 |
| ADPT | 1.275 | 1.557 | 2.764 |
| FIX | 1.747 | 1.743 | 2.181 |

### fold-best EEC

| ticker | train_1 | train_2 | train_3 |
|---|---:|---:|---:|
| AAP v5 | 4.955 | 6.211 | 8.048 |
| LASR | 5.882 | 2.439 | 5.556 |
| BTBT | 4.587 | 5.786 | 6.545 |
| ADPT | 5.333 | 6.564 | 6.452 |
| FIX | 6.564 | 6.564 | 8.696 |

### fold-best 최대 클러스터 비중

| ticker | train_1 | train_2 | train_3 |
|---|---:|---:|---:|
| AAP v5 | 23.8% | 19.0% | 23.1% |
| LASR | 20.0% | 45.0% | 30.0% |
| BTBT | 29.4% | 22.2% | 25.0% |
| ADPT | 25.0% | 21.1% | 25.0% |
| FIX | 21.1% | 21.1% | 20.0% |

### fold-best win-rate

| ticker | train_1 | train_2 | train_3 |
|---|---:|---:|---:|
| AAP v5 | 95.24% | 80.95% | 100.00% |
| LASR | 78.57% | 85.00% | 85.00% |
| BTBT | 88.24% | 88.89% | 100.00% |
| ADPT | 75.00% | 73.68% | 87.10% |
| FIX | 78.95% | 73.68% | 93.33% |

### fold-best trade count

| ticker | train_1 | train_2 | train_3 |
|---|---:|---:|---:|
| AAP v5 | 21 | 21 | 13 |
| LASR | 14 | 20 | 20 |
| BTBT | 17 | 18 | 12 |
| ADPT | 24 | 19 | 31 |
| FIX | 19 | 19 | 30 |

## all3 후보의 EEC caveat

LASR와 ADPT는 all3를 만들었지만, all3 후보의 fold별 EEC는 일부 fold에서 낮다.

| ticker | all3 수 | all3 후보 fold별 EEC |
|---|---:|---|
| LASR | 2 | `[2.079, 1.882, 4.568]`, `[2.079, 1.882, 4.083]` |
| ADPT | 1 | `[2.564, 5.565, 2.941]` |

따라서 “분산이 완전히 깨끗한 all3”라고 보기는 어렵다. 그래도 동일 v5 조건에서 all3 자체가 나왔으므로 AAP all3=0을 순수 파이프라인 구조 한계로 보기는 어렵다.

## 판정

| 판정 코드 | 적용 여부 | 근거 |
|---|---:|---|
| AAP_IS_IDIOSYNCRATIC | 채택, caveat 포함 | LASR all3=2, ADPT all3=1. pipeline은 all3를 만들 수 있음. |
| STRUCTURAL_LIMIT_CONFIRMED | 기각 | 4종목 모두 all3=0이 아님. |
| MIXED | 부분적 보조 해석 | 종목별 all2/all3 분포와 EEC 품질은 상이함. LASR/ADPT all3는 concentration caveat 존재. |

최종 해석: AAP all3=0은 파이프라인이 본질적으로 all3를 못 만드는 구조적 한계라기보다, AAP 종목/구간/신호 공간의 개별 난점이다. 다만 all3가 나온 종목도 EEC 품질이 완전히 깨끗하지 않으므로, 다음 단계에서 feature 확장 또는 gate 재설계를 할 때 “all3 수”와 “all3 EEC 품질”을 분리해서 봐야 한다.

## 산출물 구조

- `LASR/`, `BTBT/`, `ADPT/`, `FIX/`: 종목별 v5 qualify 산출물
- `logs/`: 노트북 stdout 로그
- `*_launch_driver.json`: 종목별 실제 실행 command/env/host
- `driver_status.json`: 노트북 driver 완료 상태
- `run_one_ticker_v5.py`, `driver_multiticker_v5.py`: 산출물 폴더 안의 runtime driver. 원본 source가 아니라 실행 재현용 기록이다.
- `launch_command.json`: 통합 실행 기록
- `SHA256SUMS.txt`: SHA 및 보호파일/daemon/git audit

## 보호파일 / daemon

시작·종료 SHA 동일:

| 파일 | SHA256 |
|---|---|
| `.env` | `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` |
| `data/_system/market_history.csv` | `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38` |
| `data/_system/market_history_v2.csv` | `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611` |

- daemon PID `494330` 유지 확인.
- final git 상태와 SHA는 `SHA256SUMS.txt`에 기록한다.
