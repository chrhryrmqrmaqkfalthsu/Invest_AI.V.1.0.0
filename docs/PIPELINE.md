# Kingmaker 새 학습 파이프라인 설계

목적: 거름망 → 롤링 검증 → 통학습 → 메타데이터 부착으로 이어지는 단일 파이프라인의 기준을 고정한다.

## 1. 원칙

새 오케스트레이터는 `engine/pipeline/`에 둔다. 기존 상위 경로인 `engine.learning.learner.learn`, `scripts/_true_wf_grade.py`, `scripts/screening/_bulk_swing_worker.py`, `scripts/screening/bulk_swing_diagnostic.py`는 직접 호출하지 않는다. 대신 검증된 하위 부품만 호출한다.

재사용 부품:

```text
run_backtest
run_ga / GAConfig
run_ensemble_backtest
ExitPolicy 경유 백테스트 청산
feature_lag
build_metadata / compute_member_hash
FEATURE_LAG_METADATA
```

## 2. 4단계 구조

```text
Stage 1. 거름망
  ADV $25M 필터 + 데이터 충분성 + 가벼운 GA 진단

Stage 2. 롤링 검증
  2023 / 2024 / 2025 OOS 평가
  종목 점수 산정

Stage 3. 통학습
  2020-2025 전체 6년 학습
  자격 개체 보존

Stage 4. 메타데이터 부착
  모든 산출물에 _meta 부착
```

staged gating을 적용한다. 거름망 탈락 종목은 롤링 검증을 돌리지 않고, rolling 점수 컷오프 미달 종목은 통학습을 돌리지 않는다.

## 3. 거름망

입력은 ticker와 pipeline config다. 처리 흐름은 `get_adapter(ticker)` → `adapter.load_history(years=6)` → 최근 252거래일 ADV 계산 → ADV 필터 → sentiment CSV 유무 기록 → light GA diagnostic이다.

확정 기준:

```text
min_adv_usd = 25,000,000
adv_lookback_days = 252
ADV >= $100M          liquidity_weight = 1.00
$25M <= ADV < $100M   liquidity_weight = 0.90
ADV < $25M            제외
```

sentiment 데이터 유무는 기록하지만 하드 필터로 쓰지 않는다.

## 4. 롤링 검증과 종목 점수

종목 점수는 rolling OOS 결과로만 계산한다. 통학습 성과는 종목 간 비교에 쓰지 않는다.

고정 split:

```text
2023 평가: train 2020-2022 → test 2023
2024 평가: train 2020-2023 → test 2024
2025 평가: train 2020-2024 → test 2025
```

연도별 PASS 기준:

```text
oos_trades >= 5
oos_win_rate > 50%
oos_expectancy_pct > 1.0%
oos_profit_factor > 1.2
```

종목 점수:

```text
consistency_score: 0-60
  PASS 3/3 = 60
  PASS 2/3 = 40
  PASS 1/3 = 20
  PASS 0/3 = 0, 제외

quality_score: 0-40
  통과 연도들의 평균 expectancy와 profit_factor 기반
  초기 공식은 잠정값으로 둔다.

stock_score = (consistency_score + quality_score) × liquidity_weight
```

quality 공식과 stock_score cutoff는 2패스로 확정한다. Pass 1에서는 rolling 결과와 raw metrics를 모두 저장하고, Pass 2에서 분포를 보고 cutoff와 scaling을 확정한다.

## 5. 통학습과 개체 점수

통과 종목만 2020-2025 전체 6년 데이터로 통학습한다.

```text
train = 2020-2025
purpose = 실전 개체 생성 및 종목 내 상대 줄세우기
```

6년 통학습 성과는 OOS가 아니므로 종목 간 비교에는 쓰지 않는다.

잠정 최소 자격:

```text
trade_count >= 10
expectancy_pct > 0
profit_factor > 1.0
```

자격 미달 개체도 버리지 않고 `qualified=false`로 raw metrics와 함께 보존한다. 그래야 분포 분석과 공식 재계산이 가능하다.

## 6. 메타데이터

모든 산출물에는 `_meta`를 붙인다. `build_metadata()`를 호출하고, feature lag는 `FEATURE_LAG_METADATA`를 넘긴다.

필수 항목:

```text
run_id, created_at, source, ticker, fitness_mode,
data_start, data_end, train_period, test_period, oos_periods,
ga, rulebook_hash, member_hash, validation, feature_lag
```

feature lag 실제 적용값:

```json
{
  "ticker_sentiment_days": 1,
  "market_events_days": 1,
  "max_age_days": 7
}
```

## 7. 산출물 디렉터리

새 파이프라인은 기존 라이브 산출물을 자동으로 덮지 않는다.

표준 저장 위치:

```text
data/_system/pipeline/v1/runs/{run_id}/
  manifest.json
  progress.json
  summary.csv
  errors.jsonl
  tickers/
    NVDA/
      screening.json
      rolling_validation.json
      full_training.json
      final.json
      members.jsonl
      logs.txt
```

전역 인덱스:

```text
data/_system/pipeline/v1/latest_run.json
data/_system/pipeline/v1/qualified_tickers.csv
data/_system/pipeline/v1/qualified_members.jsonl
```

promote 후보:

```text
data/_system/pipeline/v1/promoted/
  parameters/
    NVDA.json
  members/
    NVDA_members.jsonl
```

## 8. 종목별 상태 enum과 재개

상태 enum:

```text
PENDING
SCREENING_DONE
SCREENING_FAILED
ROLLING_DONE
ROLLING_CUTOFF_FAILED
FULL_TRAIN_DONE
ERROR
```

진척 파일에는 `run_id`, `config_hash`, `completed`, `failed`, `skipped`, `running`을 기록한다. 재시작 시 `running`은 비우고, `final.json`이 있는 종목은 완료로 본다. `config_hash`가 다르면 resume을 막거나 force 옵션을 요구한다.

## 9. promote 분리

현재 라이브는 `data/symbols/{ticker}/parameters.json`을 읽는다. 새 pipeline 결과를 바로 `data/symbols`에 쓰면 검증 전 개체가 실거래 경로에 들어갈 수 있다.

확정 정책:

```text
pipeline run 결과는 data/_system/pipeline/v1/runs/{run_id}/에만 저장한다.
data/symbols는 자동으로 덮지 않는다.
별도 promote 단계에서만 라이브 산출물로 승격한다.
```

초기 promote는 종목별 best member 1개를 기존 `parameters.json` 형식으로 export하고, 이후 live ensemble runner가 members.jsonl 또는 live registry를 직접 읽도록 확장한다.

## 10. 시간 전략

1136개 종목을 모두 full training까지 보내면 계산 시간이 과도하다. 따라서 staged gating이 필수다.

권장 순서:

```text
1. ADV/data prefilter 전체 실행
2. screening light GA 전체 실행
3. screening 통과 종목만 rolling validation
4. rolling 결과 분포 확인
5. stock_score cutoff 확정
6. cutoff 통과 종목만 full training
7. member_score 분포 확인
8. promote 후보 확정
```
