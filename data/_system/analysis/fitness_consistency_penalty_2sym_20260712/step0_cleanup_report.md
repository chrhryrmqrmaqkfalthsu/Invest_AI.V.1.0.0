# Step 0 — 사전 상태 및 보존 확인

## 작업 시점

- 기준 Git HEAD: `e4530ddd5afc4036bf396832d69ea4dfae41e5a2`
- 기존 floored 결과 경로: `data/_system/analysis/hybrid_group_test_2sym_floored_20260712/`
- 신규 결과 경로: `data/_system/analysis/fitness_consistency_penalty_2sym_20260712/`

## 보존 원칙

기존 floored 결과는 이동·삭제·수정하지 않는다. 이번 실험은 신규 디렉터리에만 출력한다.

사전 백업:

```text
backup/pre_fitness_consistency_penalty_2sym_20260712.tar.gz
backup/pre_fitness_consistency_penalty_2sym_20260712.manifest.sha256
```

백업에는 다음을 포함했다.

- floored grouped GA 및 실행기
- 기존 floored 결과 전체
- 기존 unfloored 결과 전체
- strict-AND 기준 산출물
- AAP·POWI 기준 거래 상세

## 라이브 상태

작업 시작 시 daemon:

```text
PID 494330
/home/g3000kkw/kingmaker/venv/bin/python .../live_candidate_slots.py daemon --interval 60
```

라이브 후보 풀·스위치·daemon·`.env`는 수정하지 않는다.

## 이번 변경 범위

모델 로직 변화는 fitness의 train↔stress 정밀도 일관성 페널티 한 곳뿐이다.

고정 유지 항목:

- 14개 feature와 4개 그룹
- 그룹 내 구간 통과 카운트
- 그룹 간 AND
- threshold floor/cap: G1/G2 2~3, G3/G4 2 고정
- population 100, generation 50, patience 15
- train_1/2/3
- rolling 목표일 청산
- early take profit OFF
- D-1 feature cutoff
- D0 gap·flow·orderbook 제외
