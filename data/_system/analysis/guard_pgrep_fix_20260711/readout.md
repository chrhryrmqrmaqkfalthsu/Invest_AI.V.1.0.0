# live_candidate_slots_guard.sh pgrep 자기 오탐 수정

## 최종 판정

수정 완료. 세 가지 필수 시나리오 모두 통과했다.

## 변경 파일

```text
scripts/live_candidate_slots_guard.sh
```

Guard 이외 운영 코드는 수정하지 않았다.

## 기존 문제

기존 로직:

```bash
PATTERN="data/_system/ops/live_candidate_slots.py daemon --interval 60"
pgrep -f "$PATTERN"
pkill -f "$PATTERN"
```

`pgrep -f`는 전체 명령줄의 부분 문자열을 검색한다. 실제 daemon이 없어도 guard의 상위 shell이나 decoy 프로세스가 패턴을 포함하면 실행 중으로 오판할 수 있었다.

실제 재현:

```text
real daemon stopped
pattern text를 argv에 넣은 decoy 실행
old guard 실행
exact daemon after old guard=none
OLD_GUARD_FALSE_POSITIVE_REPRODUCED
```

## 수정 방식

`pgrep/pkill`을 제거하고 `/proc/<pid>/cmdline` exact argv 검증으로 교체했다.

Daemon으로 인정하는 유일한 argv:

```text
/home/g3000kkw/kingmaker/venv/bin/python
/home/g3000kkw/kingmaker/data/_system/ops/live_candidate_slots.py
daemon
--interval
60
```

선택 이유:

- guard 자기 명령줄과 일치하지 않음
- decoy의 부분 문자열과 일치하지 않음
- 상위 shell의 command text와 일치하지 않음
- Python 경로·script 경로·subcommand·interval까지 모두 고정
- 과잉 매칭과 과소 매칭을 동시에 방지

PID file:

```text
data/_system/live_candidate_slots_daemon.pid
```

새 daemon 시작 시 PID를 저장한다. PID file 값도 `/proc` exact argv 검증을 통과해야 유효하다. PID file이 없거나 stale이면 `/proc` 전체를 exact scan한다.

종료도 broad `pkill` 대신 exact daemon PID에만 TERM/KILL을 보낸다.

## 시나리오 검증

### A. Daemon 생존

```text
before PID=477527
after PID=477527
exact daemon count=1
```

결과:

```text
중복 기동 없음
SCENARIO_A_PASS
```

### B. Daemon 사망

```text
old PID=477527 종료
before guard exact daemon=none
new PID=478988
pidfile=478988
exact daemon count=1
```

결과:

```text
사망 감지 및 재기동 정상
SCENARIO_B_PASS
```

### C. Guard 실행 중 + decoy 존재 + daemon 없음

기존 broad pattern과 일치하는 프로세스:

```text
parent shell
pattern-text decoy PID 479021
validation command
```

검증:

```text
broad matches=479014|479021|479028
exact daemon before=none
new exact daemon PID=479037
pidfile=479037
exact daemon count=1
```

결과:

```text
guard/self/parent/decoy 오탐 없이 재기동
SCENARIO_C_PASS
```

## 최종 안전 상태

```text
current daemon PID=479037
exact daemon count=1
pidfile=479037
Alpaca open orders=0
upstream_gate_enforcement=BLOCK
integrated_gate symbol=removed
```

현재 daemon은 최신 코드로 기동됐다.

## Lock 유지

Crontab은 변경하지 않았다.

```text
* * * * * /usr/bin/flock -n /tmp/kingmaker_live_candidate_slots_guard.lock ...
@reboot /usr/bin/flock -n /tmp/kingmaker_live_candidate_slots_guard.lock ...
```

동시 guard 실행 방지는 그대로 유지된다.

## Open order 확인

기존 guard 스크립트 내부에는 broker open-order 검사 로직이 없었다. 이번 수정에서 daemon 생존 판정과 무관한 신규 차단 정책은 추가하지 않았다.

대신 모든 실제 daemon 종료·재기동 검증 전후에 Alpaca API로 open order 0건을 확인했다.

## 테스트 및 검증

```text
bash -n scripts/live_candidate_slots_guard.sh
```

통과.

세 시나리오와 최종 단일 PID 확인 결과는 `scenario_validation.csv`에 기록했다.

## 롤백

이 구현 커밋을 revert하면 기존 `pgrep -f` 방식으로 돌아간다. 다만 기존 방식은 실제 오탐이 재현됐으므로 롤백은 권장하지 않는다.
