# Step 0 — λ 스윕 사전 상태

- 기준 Git HEAD: `076450afa05986ee43e2046eaf26f7c30c123ee1`
- 기존 λ=0.5 결과: `data/_system/analysis/fitness_consistency_penalty_2sym_20260712/`
- 신규 출력: `data/_system/analysis/fitness_lambda_sweep_2sym_20260712/`
- 기존 λ=0.5 및 λ=0 floored 결과는 이동·삭제·수정하지 않는다.

사전 백업:

```text
backup/pre_fitness_lambda_sweep_2sym_20260712.tar.gz
backup/pre_fitness_lambda_sweep_2sym_20260712.manifest.sha256
```

이번 실험에서 바뀌는 모델 선택 값은 런타임 `CONSISTENCY_LAMBDA`뿐이다.

```text
λ=0.2
λ=0.3
```

다음은 직전 λ=0.5와 동일하게 유지한다.

- consistency gap 정의
- precision weight 220
- Stress 사용 범위: fitness scorer only
- feature·gene domain·G3 floor·fallback: train only
- 14개 지표와 4그룹
- threshold floor/cap
- population 100, generation 50, patience 15
- rolling 목표일, TP OFF
- 6개 병렬 worker 상한
