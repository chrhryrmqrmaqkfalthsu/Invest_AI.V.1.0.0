# Dask 노트북 워커 Stage23 engine 배포 및 scatter 검증 readout

- 작업일: 2026-07-14
- scheduler: `tcp://localhost:8786`
- Windows Python: `C:\dask310\Scripts\python.exe`
- Windows 배포 경로: `C:\kingmaker`
- Linux 비교 worker 격리 경로: `/tmp/kingmaker_stage23_rework_20260713`
- 최종 상태: **PASS**
- 정식 Dask GA 재학습: 미실행

## 1. 원인 분석

Windows 노트북 worker에는 NumPy 2.2.6과 pandas 2.3.3이 설치돼 있었지만 프로젝트 소스와 project root가 없어 `engine` import가 실패했다.

```text
C:\kingmaker: 없음
engine: ModuleNotFoundError
```

초기 환경:

| 환경 | Python | OS | NumPy | pandas | engine |
|---|---|---|---|---|---|
| Linux worker | 3.10.12 | Linux | 2.2.6 | 2.3.3 | VM root에서 import 가능 |
| Windows worker | 3.10.11 | Windows | 2.2.6 | 2.3.3 | import 불가 |

Stage23 fitness 핵심 경로를 실제 import trace로 확인한 결과 `engine.adapters`, `engine.core`, `engine.market`, `engine.pipeline`, `engine.strategies`, `engine.learning`의 여러 간접 의존성이 로드된다. 일부 파일만 선별할 경우 재귀 import 누락 가능성이 있어 Stage23 작업 트리의 **전체 `engine` 패키지**를 배포했다.

## 2. 최종 bundle

최종 launcher 재배포 bundle:

```text
SHA-256: a1a7bcfcf7714b1d65b4a172d4c048f32356fc0e617116a2bb4ba8848f0e2c64
크기: 746496 bytes
manifest 프로젝트 파일 수: 191
engine Python 파일 수: 159
```

ZIP 컨테이너 SHA는 생성 시각 메타데이터를 포함하므로 재생성할 때 달라질 수 있다. 신뢰 기준은 worker에서 재검증한 내부 파일별 SHA manifest와 아래 policy SHA다.

```text
config/policy.yaml
b08882c16424743dfe1072424a06d8676d0129ae8c1b1d04beb68f6e35d6012b
```

포함:

- `scripts/research/stage23_rework_20260713/engine/` 전체
- `config/policy.yaml`
- `worker_preload.py`
- 순수 Python vendor `loguru`
- 순수 Python vendor `python-dotenv`

제외:

- `.env`와 secret
- `market_history.csv`, `market_history_v2.csv`
- 가격·뉴스·결과 데이터
- cache와 `__pycache__`

bundle 정적 검사에서 `.env`와 시장 데이터 파일이 포함되지 않았음을 확인했다.

## 3. Windows 호환 처리

Vendored Loguru는 Windows에서 선택 패키지 `win32_setctime`을 import한다. Windows worker에는 해당 패키지가 없어 첫 key import가 다음 오류로 실패했다.

```text
ModuleNotFoundError: No module named 'win32_setctime'
```

학습 계산은 Windows 파일 생성시각 변경에 의존하지 않으므로 다음 compatibility module을 추가했다.

```text
engine/_win32_setctime_compat.py
```

- `SUPPORTED = False`
- `setctime()`은 no-op
- Windows에서 실제 패키지가 없을 때만 `engine/__init__.py`가 compatibility module을 등록
- Linux 동작에는 영향 없음

## 4. 배포 방식

VM에서 bundle ZIP bytes를 메모리에 만들고 Dask task 인자로 전달했다. Worker는 VM 프로젝트 파일을 직접 읽지 않는다.

Windows 첫 배포 후 Loguru가 `C:\kingmaker\data\logs` 파일을 열어 둔 상태라 root rename이 거부됐다.

```text
PermissionError: C:\kingmaker -> C:\.kingmaker.previous-...
```

최종 live-safe 배포 절차:

1. worker 임시 디렉터리에 ZIP 해제
2. 모든 내부 파일 SHA 검증
3. 기존 project root에 파일 단위 in-place sync
4. 최종 root에서 파일 SHA 재검증
5. `engine.*` module cache 제거
6. project root와 `vendor`를 `sys.path` 선두에 추가
7. 핵심 fitness module 재-import

최종 결과:

| Worker | OS | 경로 | 상태 |
|---|---|---|---|
| `tcp://127.0.0.1:46481` | Linux | `/tmp/kingmaker_stage23_rework_20260713` | `DEPLOYED_IN_PLACE` |
| `tcp://127.0.0.1:60941` | Windows | `C:\kingmaker` | `DEPLOYED_IN_PLACE` |

두 worker 모두 임시 tree와 최종 root에서 191개 manifest 프로젝트 파일 검증을 통과했다.

## 5. sys.path와 preload

현재 worker에는 `Client.run`으로 다음 경로가 주입됐다.

```text
Windows:
C:\kingmaker
C:\kingmaker\vendor

Linux:
/tmp/kingmaker_stage23_rework_20260713
/tmp/kingmaker_stage23_rework_20260713/vendor
```

Worker 재시작용 preload:

```text
Windows: C:\kingmaker\worker_preload.py
Linux:   /tmp/kingmaker_stage23_rework_20260713/worker_preload.py
```

Windows worker 시작 명령에는 다음 옵션을 추가한다.

```text
--preload C:\kingmaker\worker_preload.py
```

## 6. 요청한 `client.run` import 검증

다음 형태의 검증을 두 worker에서 실행했다.

```python
lambda: (
    sys.path.insert(0, OS별_배포_경로),
    __import__('engine').__file__,
)
```

결과:

```text
tcp://127.0.0.1:46481
/tmp/kingmaker_stage23_rework_20260713/engine/__init__.py

tcp://127.0.0.1:60941
C:\kingmaker\engine\__init__.py
```

두 worker 모두 PASS했다. 다음 핵심 import도 전부 각 배포 root에서 성공했다.

- `engine.core.config`
- `engine.core.logger`
- `engine.strategies.rulebook`
- `engine.learning.backtest`
- `engine.learning.execution_mode_backtest`
- `engine.learning.genetic`
- `engine.learning.genetic_parallel`

## 7. Fitness 데이터 계약

정식 entry fitness는 다음 형태다.

```text
run_entry_backtest_period(rulebook, ctx, start, end)
```

평가 중 데이터는 `ctx["df"]`와 `base_backtest_kwargs(ctx)`가 반환하는 객체에서 받는다. 시장 snapshot 파일 직접 읽기는 client/parent의 context 준비와 manifest preflight에서만 발생한다. 준비된 `ctx`를 함수 인자로 주면 candidate fitness가 worker 로컬 CSV를 열지 않는다.

따라서 core backtest 함수 변경은 필요하지 않았다.

```text
context 준비: client/parent
전달: Client.scatter
fitness 입력: 함수 인자 ctx
worker local market file read: false
```

## 8. 시장 데이터 scatter 검증

VM root snapshot:

```text
파일: data/_system/market_history.csv
SHA-256: 35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38
bytes: 276656
rows: 1759
```

VM에서 원본 bytes와 DataFrame을 payload로 만들고 worker별로 `Client.scatter`했다. 각 worker가 bytes SHA를 다시 계산했다.

| Worker | SHA 일치 | DataFrame | 행 수 | 로컬 파일 읽기 |
|---|---|---|---:|---|
| Linux | true | true | 1759 | false |
| Windows | true | true | 1759 | false |

시장 파일 자체는 Windows에 복사하지 않았다.

## 9. 시행 중 실패와 해결

### 실패 1 — Loguru 선택 의존성

```text
ModuleNotFoundError: No module named 'win32_setctime'
```

해결: Windows-only compatibility module을 engine bootstrap에 추가.

### 실패 2 — Task graph 역직렬화

```text
Error during deserialization of the task graph
```

원인: launcher가 로컬 research module 이름 참조로 함수를 직렬화해 scheduler가 해당 module을 찾지 못함.

해결: live installer task를 실행 script의 `__main__`에 정의해 구현을 값으로 전송.

### 실패 3 — Windows root rename 거부

```text
PermissionError: C:\kingmaker -> C:\.kingmaker.previous-...
```

원인: import된 Loguru가 root 하위 로그 파일을 열고 있음.

해결: temp tree 검증 후 파일 단위 in-place sync.

### 검증 호출 실수

최종 상태 확인 중 `Client.run`에 lambda positional argument를 잘못 전달해 다음 오류가 한 번 발생했다.

```text
TypeError: <lambda>() missing 1 required positional argument: 'root_map'
```

배포 상태 문제는 아니었으며 OS별 경로를 lambda 내부에서 선택하도록 고쳐 두 worker PASS를 재확인했다.

## 10. 배포 도구

- `scripts/research/dask_worker_preload.py`
- `scripts/research/deploy_dask_worker_bundle.py`
- `scripts/research/deploy_dask_worker_bundle_live.py`
- `scripts/research/deploy_dask_worker_bundle_launch.py`
- `engine/_win32_setctime_compat.py`

기본 실행:

```text
python3 scripts/research/deploy_dask_worker_bundle_launch.py --scheduler tcp://localhost:8786
```

기본 launcher 재실행 검증도 PASS했다.

## 11. 남은 재현성 경고

Import와 scatter는 해결됐지만 Python patch 차이는 남아 있다.

```text
VM/client/scheduler/Linux worker: Python 3.10.12
Windows worker:                  Python 3.10.11
```

두 worker의 계산 라이브러리는 일치한다.

```text
NumPy 2.2.6
pandas 2.3.3
```

Worker 실행 가능성은 확보됐지만 bit-identical GA 재현성은 별도의 VM-vs-Dask 정식 실행과 hash 대조로 확인해야 한다. 이번 작업에서는 Dask GA를 재학습하지 않았다.

## 12. 보호 대상

`.env`와 시장 원본은 수정하지 않았고 daemon PID 494330은 유지됐다.

```text
.env
 da8173082d40ef3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce

market_history.csv
 35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38

market_history_v2.csv
 b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611
```
