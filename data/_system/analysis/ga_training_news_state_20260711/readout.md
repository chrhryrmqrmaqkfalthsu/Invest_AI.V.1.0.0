# GA 학습 시점의 뉴스 입력 상태

## 최종 판정

`TRAINED_WITH_LIVE_NEWS`

CE와 Event 과반 9종목의 최종 진입 룰북은 모두 `train_3(2024-07-01~2025-06-30)`에서 선택됐고, 해당 기간의 ticker sentiment CSV에는 실제 비제로 News/NewsTopics 입력이 존재했다.

다만 News는 연속적이지 않고 희소했다. 최종 룰북의 실제 학습 origin 기간 250거래일 중 비제로 뉴스 기여가 가능했던 날은 종목별 12~108일이었다.

CE는:

- raw ticker sentiment row: 18일
- News/NewsTopics가 D-1·7일 max-age 정책으로 비제로였던 거래일: 87/250일
- coverage: 34.8%
- `use_news_global=False`
- NewsTopics: 활성

따라서 CE가 News와 NewsTopics가 모두 0인 상태로 학습됐다는 가설은 성립하지 않는다. global News scalar는 꺼져 있었지만 NewsTopics는 실제 학습 입력으로 존재했다.

## 별도 핵심 발견

**Event 계수는 News가 죽어서 Event가 빈자리를 메우며 학습된 것이 아니다.**

Stage2와 Stage3 학습 runner는 모두:

```python
"use_llm_events": False
```

를 사용했다. `engine.learning.backtest::_lookup_signal_context()`는 이 경우 11개 Event flag를 전부 0으로 유지한다.

따라서 9종목 모두 학습 기간 Event 유효일은 0일이다. `event_response_*`, `event_strength_multiplier`, `use_event_block`은 GA fitness에 영향을 주지 못했고 historical Event 반응으로 식별되지 않았다.

즉 현재 높은 Event 계수의 성격은 다음과 같다.

- News 부재를 성과로 설명하며 커진 계수: 근거 없음
- historical Event 성과로 학습된 계수: 아님
- Event 입력이 0인 상태에서 선택압을 받지 않은 중립 gene: 코드상 확정

이 사실은 요청된 뉴스 상태 판정과 별개로 중요하다.

## 1. GA 뉴스 입력 경로

학습은 라이브와 같은 loader를 쓴다.

```text
prepare_ticker_context(ticker)
  -> load_ticker_sentiment(ticker)
  -> data/_system/ticker_sentiment/<TICKER>_daily.csv
  -> run_backtest(ticker_sentiment=...)
  -> D-1 이하 최신 row, max_age_days=7
  -> sentiment_avg + topic z-score
  -> evaluate_signal()
```

별도 학습 전용 뉴스 데이터셋은 확인되지 않았다.

`engine/strategies/learned_rulebook.py::_load_ticker_sentiment()`도 같은 loader를 사용하며 docstring에서 backtest와 같은 source라고 명시한다.

### `use_news_global`의 의미

`use_news_global=False`는 `weight_news_sentiment × sentiment_avg`만 0으로 만든다. NewsTopics 계산은 이 mask 바깥에 있어 독립적으로 작동한다.

따라서 CE처럼 `use_news_global=False`인 룰북도 topic feature가 존재하면 `weight_news_<topic>`을 학습에 사용한다.

## 2. 학습 시점과 CSV 상태

전수 updater는 2026-06-02에 실행됐다. 조사 대상 룰북의 학습 산출물은 2026-06-18~2026-06-29에 만들어졌다.

즉 조사 대상 CSV들은 학습 전에 존재했고, 현재 mtime도 모두 학습 시점보다 앞선 2026-06-02다. 이후 파일이 재작성되지 않았으므로 현재 파일 내용으로 당시 입력 coverage를 재현할 수 있다.

최종 개체 origin은 모두 `train_3`다.

| ticker | 학습 단계 | 학습 산출물 시각 | 비제로 News 거래일 | coverage |
|---|---|---|---:|---:|
| BMA | Stage3 | 2026-06-27 | 55/250 | 22.0% |
| BNTX | Stage3 | 2026-06-27 | 108/250 | 43.2% |
| BTBT | Stage3 | 2026-06-28 | 85/250 | 34.0% |
| CMC | Stage2 | 2026-06-18 | 73/250 | 29.2% |
| BWXT | Stage3 | 2026-06-28 | 74/250 | 29.6% |
| BMI | Stage3 | 2026-06-27 | 46/250 | 18.4% |
| BGC | Stage3 | 2026-06-27 | 52/250 | 20.8% |
| CE | Stage3 | 2026-06-29 | 87/250 | 34.8% |
| ACMR | Stage3 | 2026-06-24 | 12/250 | 4.8% |

ACMR은 매우 희소하지만 0은 아니다. 따라서 요청된 dead/live 이분법에서는 9종목 모두 live다.

## 3. 학습 당시 코드 확인

현재 코드만 본 것이 아니다.

CMC Stage2 artifact가 기록한 code commit:

```text
12e4503978adb23823da77c2e2bd1dc1f63cbcca
```

해당 commit의 `scripts/research/run_stage2.py::base_kwargs()`에도 `use_llm_events=False`가 존재한다.

Stage3 학습 실행 전 commit `9312510`과 그 이전 `6d519f9`, `a5dd0fa`의 `base_backtest_kwargs()`에도 동일하게 `use_llm_events=False`가 존재한다.

따라서 Event 강제 0은 사후 코드 변경이 아니라 실제 학습 당시 조건이었다.

## 4. CE News 계수와 Event 계수

CE global News:

- `use_news_global=False`
- `weight_news_sentiment=1.00943826`
- 이 weight는 mask가 false이므로 fitness에 직접 영향을 주지 못함

CE NewsTopics:

- 15개 topic weight 모두 비제로
- topic weight 절대값 합: 24.0754
- 최대 절대값: 3.0
- `news_block_cap=4.17148930`
- 실제 train_3 비제로 입력: 87거래일

CE Event:

- 11개 response 절대값 합: 8.74497
- 최대 절대값: `geopolitical=-1.93125`
- `event_strength_multiplier=2.33874`
- 실제 학습 Event 입력: 0거래일

따라서 CE의 News 계수가 0이거나 미학습 기본값으로 고정됐다는 증거는 없다. NewsTopics는 실제 데이터와 함께 GA 표현 공간에 있었다.

반대로 Event 계수는 크기와 관계없이 학습 데이터로 검증되지 않았다.

## 5. Event 과반 9종목 패턴

공통점:

- 9/9: 15개 NewsTopics weight가 모두 비제로
- 9/9: origin train_3에 비제로 News/NewsTopics 입력 존재
- 9/9: Event 학습 유효일 0
- 9/9: `use_event_block=True`
- 9/9: Event response 및 multiplier가 라이브에서 비제로 효과를 낼 수 있음

차이:

- global News 활성: BNTX, CMC, BMI, BGC 4종목
- global News 비활성, NewsTopics만 활성: BMA, BTBT, BWXT, CE, ACMR 5종목
- News coverage는 ACMR 4.8%부터 BNTX 43.2%까지 편차가 큼

따라서 공통 패턴은 `News 계수 죽음 + Event 계수 큼`이 아니다.

실제 공통 패턴은:

```text
희소하지만 살아 있는 News/NewsTopics
+
학습 중 항상 0이어서 식별되지 않은 Event genes
+
라이브에서는 Event genes 활성화
```

이다.

## 대응 규모에 대한 코드 근거 기반 해석

updater 복구는 현재 라이브에서 0이 된 News/NewsTopics를 학습 당시와 같은 입력 구조로 되돌리는 데 필요하다.

그러나 updater 복구만으로 Event 편중 문제가 완전히 해소된다고 단정할 수 없다. Event 계수는 학습 당시 검증된 적이 없기 때문이다.

동일한 `use_llm_events=False` 설정으로 재학습해도 Event 계수 식별 문제는 그대로 남는다. 이는 재학습 필요 여부와 별개로, 현재 Event 계수를 학습된 반응계수로 신뢰할 근거가 없다는 뜻이다.

## 확인 불가 및 제한

- 각 뉴스 feature가 개별 진입 거래의 fitness를 얼마나 변화시켰는지에 대한 gene별 인과 기여도: 별도 ablation 미실행으로 확인 불가
- News coverage가 더 높았다면 어떤 룰북이 선택됐을지: 재학습 금지 조건으로 확인 불가
- Event를 historical flag와 함께 학습했을 때 계수가 어떻게 달라질지: 확인 불가

## 산출물

- `data/_system/analysis/ga_training_news_state_20260711/ga_news_input_path.md`
- `data/_system/analysis/ga_training_news_state_20260711/training_time_news_coverage.csv`
- `data/_system/analysis/ga_training_news_state_20260711/ce_news_event_coefficients.csv`
- `data/_system/analysis/ga_training_news_state_20260711/event_majority_news_event_pattern.csv`
- `data/_system/analysis/ga_training_news_state_20260711/readout.md`

운영 코드·설정·재학습 변경: 0건
