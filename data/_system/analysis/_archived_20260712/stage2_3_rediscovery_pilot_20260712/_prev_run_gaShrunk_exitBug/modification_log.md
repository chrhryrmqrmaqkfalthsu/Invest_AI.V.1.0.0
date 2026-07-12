# Step 2 — 복사본 직접 수정 내역

## 수정 원칙

Step 1에서 원본과 SHA-256이 **39/39 일치**한 `scripts/research/rolling_rediscovery/upstream_snapshot/` 트리를 실제 실행 작업본으로 사용했다. 이 디렉터리는 보존용 박제가 아니라 무손실 복사 직후 직접 수정한 rolling 실행 베이스다. 원본 `scripts/research/run_stage2_path_filter.py`, `scripts/research/run_stage2.py`, `scripts/research/run_stage3_aggressive.py`, `engine/` 파일은 수정하지 않았다.

별도 독립 runner를 새로 만들지 않았다. 실행 진입점은 복사된 `upstream_snapshot/scripts/research/run_stage2_path_filter.py`이고, 이 파일이 같은 복사 트리의 `scripts/research/run_stage2.py`를 호출한다. orchestration은 같은 복사 트리의 `engine/learning/genetic.py`와 `engine/learning/execution_mode_backtest.py`를 사용한다. 복사된 stage3 진입점도 동일 rolling orchestration을 호출한다.

`build_stage3_live_pool.py`는 학습·개체 생성 의존성이 아니라 검증 완료 개체를 라이브 후보 풀로 내보내는 배포 도구이며, 이번 지시의 라이브 변경 금지 대상이므로 복사·호출·수정하지 않았다.

## 파일별 변경

### `upstream_snapshot/scripts/research/run_stage2_path_filter.py`

- 기존 monkey-patch형 path-filter 진입점을 rolling 파일럿 CLI 진입점으로 직접 수정했다.
- 같은 복사 트리의 `scripts.research.run_stage2.main`을 호출하도록 연결했다.
- frozen OHLC가 6년 스냅샷으로 2020-06-08부터 시작하는 사실을 반영해, **2020년 안에 저장 이력이 시작되고 OOS가 존재하는 종목**을 표본 후보로 인정하도록 복사본의 선정 의존성을 직접 보정했다.
- 현 라이브 10종목은 반드시 포함하고, 실제 최초 저장일은 `symbol_list.csv`에 보존한다. 2020-01~05 구간은 `NOT_STORED`이며 합성 복구하지 않았다.
- `multiprocessing` spawn 안전을 위해 `if __name__ == "__main__"`와 `freeze_support()`를 적용했다.

### `upstream_snapshot/scripts/research/run_stage2.py`

- 기존 stage2 orchestration의 유니버스→학습→regime 검증→survivor→백테스트 흐름을 50종목 파일럿 orchestration으로 직접 수정했다.
- 현 라이브 후보 10개를 고정 포함하고 frozen OHLC 표본에서 seed `20260712`로 40개를 결정적 무작위 선정한다.
- 저장된 2020년 가용일 이후 모든 거래일을 보유 상태와 무관한 독립 후보로 평가한다.
- feature는 D-5~D-1 path_filter 12개로 제한하며 `STK_gap_d0`, `ETF_gap_d0`, flow, order_book를 사용하지 않는다.
- 라벨은 [추정] D0 open 대비 D+1~D+2 high 최대값이 +3% 이상인지로 구성한다. D0/future 가격은 feature가 아니라 라벨·체결 성과 계산에만 사용한다.
- train만 GA에 사용하고 stress(저장 시작~2022-06-30), OOS(2025-07-01~)는 검증 전용 이중 게이트로 사용한다.
- 종목별 seed를 SHA-256으로 고정하고 `multiprocessing` spawn `Pool(6)`로 50종목을 독립 병렬 실행한다.
- worker별 입력 CSV와 결과 JSON을 `_worker_tmp/`에 분리해 파일 경쟁을 방지한다.
- 요청된 CSV와 `readout.md`, `pilot_summary.json`을 병합 생성한다.

### `upstream_snapshot/scripts/research/run_stage3_aggressive.py`

- 기존 stage3 qualify→entry→exit→validate wrapper를 같은 복사 작업본의 rolling orchestration 진입점으로 직접 수정했다.
- 기존 stage3 exit-gene의 `max_holding_days` 기반 청산은 rolling 원칙과 충돌하므로 호출하지 않는다.
- stage2와 stage3가 동일한 양방향 interval gene, strict AND, train-only GA, stress/OOS 이중 게이트, 동일 임계선 rolling 진입·청산 구조를 공유하도록 연결했다.
- 수정 전 stage3 wrapper와 동적 원본 orchestration 백업, `exit_gene.py`, `stage3_gate.py`는 각각 무손실 복사·SHA 대조를 완료했다.

### `upstream_snapshot/engine/learning/genetic.py`

- 기존 수치·카테고리 rulebook GA를 12개 bilateral interval gene GA로 직접 수정했다.
- 각 gene은 정규화 train 범위의 `[low, high]` 두 경계를 모두 가진다.
- 최소 폭은 [추정] 정규화 범위의 10%이며 미달 개체를 탈락시킨다.
- 사실상 무제한인 폭 98% 이상 gene이 2개를 초과하면 노이즈 개체로 탈락시킨다.
- 통과 mask는 `np.all((x >= low) & (x <= high), axis=1)`의 전 지표 AND이며 가중합·합산·다른 지표 상쇄 경로가 없다.
- 상한 학습 실패를 non-finite upper로 명시하고, 해당 feature의 train 성공 거래 최댓값 또는 최소폭 경계로만 상한을 대입한다. 다른 gene은 변경하지 않는다.
- fitness는 2일 +3% 라벨 정밀도 중심이며 [추정] train 최소 표본 `max(20, 2%)` 게이트를 선행한다.

### `upstream_snapshot/engine/learning/execution_mode_backtest.py`

- 인위적 `max_holding_days` 청산을 제거한 daily rolling score 백테스트로 직접 수정했다.
- 점수는 strict-AND 통과 train 표본의 +3% 정밀도이며, 미통과일은 0이다.
- 동일 임계선을 진입·유지·청산에 사용한다: `score >= threshold`면 진입/유지, 미만이면 미진입/청산.
- 구간 말의 평가용 mark-to-market 외 보유일 상한은 없다.
- 비교군으로 flat-only 고정 2거래일 보유 백테스트를 구현했다.
- 임계선 crossing, 1일 휩쏘, 20/60/252세션 장기보유 위험을 측정한다.

## 파일럿 게이트의 [추정] 값

- gene 최소폭: 정규화 train 범위의 10%
- train 표본 게이트: `max(20, train 행의 2%)`
- stress/OOS 표본 게이트: `max(8, 검증 행의 1.5%)`
- 진입·청산 임계선: `max(45%, train 양성률 + 8%p)`, 최대 80%
- 검증 정밀도 하한: `max(30%, regime 양성률 + 3%p, train 정밀도 - 15%p)`
- 거래 비용: 왕복 10bp

이 값들은 파일럿 분포 확인 후 확정 대상이며 라이브 설정에 반영되지 않는다. 파일럿 결과 stress 통과 4개, OOS 통과 5개였지만 교집합이 0개였으므로 임계값을 사후 완화하지 않고 `PILOT_FAIL`로 유지했다.
