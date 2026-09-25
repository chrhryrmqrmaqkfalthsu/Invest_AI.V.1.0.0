# Stage 2 / Stage 3 원본 재구성 보고

## 고정 작업 디렉터리

```text
scripts/research/stage23_rework_20260713/
```

이 디렉터리는 정식 Stage 2/3 원본을 다시 복사한 독립 작업 기준점이다.

## 삭제 전 임시 복구 백업

삭제 전에 전체 대상을 저장소 밖 `/tmp`에 임시 백업했다.

```text
/tmp/stage23_cleanup_pre_20260713T002028Z.tar.gz
SHA-256:
874d712ee585244c6d97e01842fb604c2d8127c31f6541fa8cdd35cea5c5a47d
크기: 462,216,339 bytes
검증: PASS
```

이 임시 복구본은 Git 산출물에 포함하지 않았다.

## 삭제 결과

삭제 전 대상 총 용량:

```text
674,409,601 bytes
```

삭제한 최상위 대상:

```text
74개
```

삭제 범위:

```text
scripts/research/redesign_workspace_20260712/
docs/redesign/strict_and_interval_redesign_20260712.md
data/_system/analysis/*20260712*/
data/_system/analysis/*20260713*/
backup/*20260712*
backup/*20260713*
scripts/research/tmp/check_market_fetch_7y.py
```

명시됐지만 삭제 전에 존재하지 않았던 경로:

```text
data/_system/analysis/official_stage2_2sym_20260712/
```

삭제 후 잔존 검사:

```text
날짜형 analysis 디렉터리 잔존: 0
날짜형 backup 경로 잔존: 0
redesign_workspace_20260712 잔존: 0
strict_and_interval 설계문서 잔존: 0
check_market_fetch_7y.py 잔존: 0
```

전체 삭제 목록은 다음 파일에 기록했다.

```text
deleted_targets.txt
```

## 복사 범위

누락된 동적 import나 package marker가 생기지 않도록 Git이 추적하는 다음 정식 원본을 구조 그대로 복사했다.

```text
engine/ 전체 추적 소스
config/ 전체 추적 파일
scripts/research/run_stage2.py
scripts/research/run_stage3_aggressive.py
scripts/research/run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001
scripts/research/run_stage23_batch.py
```

복사 파일 수:

```text
engine + config: 162개
Stage 2/3 진입 스크립트: 4개
총: 166개
```

전체 상대경로 목록은 다음 파일에 기록했다.

```text
copied_files.txt
```

## 전체 SHA 검증

삭제·복사 전에 166개 정식 원본의 파일별 SHA-256을 기록했다.

복사 후 다음 세 집합을 비교했다.

```text
사전 정식 원본 SHA
복사 후 정식 원본 SHA
새 작업 디렉터리 복사본 SHA
```

결과:

```text
정식 원본 변경: 0/166
복사본 불일치: 0/166
복사 누락: 0/166
Python 구문 오류: 0
```

파일 경로와 SHA를 정렬해 계산한 aggregate SHA:

```text
정식 원본:
466b1bb08f03ce6aa2432f16430fa35062222978e47ff618dcc5df760294c17d

복사본:
466b1bb08f03ce6aa2432f16430fa35062222978e47ff618dcc5df760294c17d
```

핵심 파일별 대조표는 다음 파일에 기록했다.

```text
core_sha256_comparison.csv
```

## 핵심 원본 SHA-256

```text
scripts/research/run_stage2.py
9a83b1490b669176fbfdd50d6ce48c1fbdfdd9fa1c6525d91ed83af82c70165c

scripts/research/run_stage3_aggressive.py
8f275ca52745b6b9f92d56e0e24d8043ccef8644b5c5d996217b9c6226e701c0

scripts/research/run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001
bc3e191a449d6b67980dd0884a2a510acf9ecda719a8b12e76b0e8178de33004

scripts/research/run_stage23_batch.py
2718a3e12eb8eea71011543efa41d435850402aeaa70c5517d5eded9761a00e3

engine/strategies/rulebook.py
c7b2892f410cd1b25b8090fe26b2b6daaa0aa4bfeaa28555cf4c8b6d12cb15dc

engine/strategies/evaluator.py
d7ce157564c3311d95ba73de79f41dfad3d7d1134727dd8a5fa776487cd83584

engine/learning/genetic.py
89611d799fdca69d7a8e149898f5652f7e4ef5d020349f567919a548bf4361ad

engine/learning/execution_mode_backtest.py
efd0a9edea250efaa6b70163bd5d44b5695098be74c485b0cb78643a559bcae0

engine/pipeline/stage2_gate.py
b3018f9323fb7f0194990ce726979841b9db5c5a852711dac3fb7a1d3357f15a

engine/pipeline/stage3_gate.py
6447efa706edfe9c31c92e652ca504da1c8f16215d79f0eee3352f37520fc36a

engine/pipeline/context.py
33740d9032eb838716070b603d39b13fb87e6e883f7ec46583b16038ec34d74d

engine/pipeline/exit_gene.py
79a2d8d22557defd6692af917707febb9472fb8e4e8eb0fb820b285c317d9073

engine/pipeline/topn_survivor.py
d457da84972078087dceec6260dad66273229bfb657c152a9725f6fb07cbc306
```

## 보호 파일

작업 전 보호값:

```text
.env
da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce

market_history.csv
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38

market_history_v2.csv
b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611
```

세 파일은 삭제·복사 대상에 포함하지 않았다.

## 실행 상태

```text
GA 실행: 0건
학습 실행: 0건
백테스트 실행: 0건
정식 원본 수정: 0건
daemon PID 494330 유지
```

## 다음 작업 기준

향후 CE·BOIL 합산 방식 수정은 반드시 다음 복사본 내부에서만 시작한다.

```text
scripts/research/stage23_rework_20260713/
```

정식 최상위 `engine/`, `config/`, `scripts/research/run_stage2.py`, `scripts/research/run_stage3_aggressive.py`는 계속 read-only로 유지한다.
