# live_candidate_slots guard 자기 오탐 원인과 수정

## 원인 재현

기존 guard는 다음 부분 문자열 패턴을 사용했다.

```bash
PATTERN="data/_system/ops/live_candidate_slots.py daemon --interval 60"
pgrep -f "$PATTERN"
pkill -f "$PATTERN"
```

`-f`는 프로세스의 전체 명령줄을 부분 문자열로 검색한다. 따라서 실제 daemon이 아니더라도 다음 프로세스가 패턴을 포함하면 일치한다.

- guard를 실행하는 상위 shell 명령줄
- validation shell의 향후 명령 문자열
- pattern text를 인자로 받은 decoy 프로세스
- 광범위한 `pkill -f`와 같은 관리 명령

재현 절차:

1. 실제 daemon PID 474823을 정상 종료
2. 다음 decoy 실행

```text
python3 -c "sleep" "data/_system/ops/live_candidate_slots.py daemon --interval 60"
```

3. 기존 guard 실행

결과:

```text
old broad pgrep match=decoy/parent shell
exact daemon after guard=none
OLD_GUARD_FALSE_POSITIVE_REPRODUCED
```

즉 실제 daemon은 죽어 있었지만 guard는 `실행 중`으로 오판했다.

## 채택 방식

`pgrep/pkill -f`를 완전히 제거했다.

새 guard는 `/proc/<pid>/cmdline`의 NUL-separated argv를 읽어 다음 5개 인자와 완전히 동일한 경우만 daemon으로 인정한다.

```text
/home/g3000kkw/kingmaker/venv/bin/python
/home/g3000kkw/kingmaker/data/_system/ops/live_candidate_slots.py
daemon
--interval
60
```

부분 문자열, 정규식, shell command text를 사용하지 않는다.

## PID file

새 daemon 시작 시:

```text
data/_system/live_candidate_slots_daemon.pid
```

에 `$!`를 저장한다.

PID file은 빠른 후보로 사용하지만 신뢰를 전제하지 않는다. `/proc/<pid>/cmdline` exact argv 검증을 통과해야만 실제 daemon으로 인정한다. PID file이 없거나 stale이어도 `/proc` 전체에서 exact argv를 검색한다.

## 종료·재시작

기존:

```bash
pkill -f "$PATTERN"
```

대신 exact argv를 만족한 PID만 TERM 후 필요 시 KILL한다. 따라서 pattern text를 포함한 guard, shell, decoy를 종료하지 않는다.

## 동시 실행 방지

실제 crontab의 기존 외부 lock은 변경하지 않았다.

```text
/usr/bin/flock -n /tmp/kingmaker_live_candidate_slots_guard.lock
```

매분 및 reboot 두 경로 모두 같은 lock을 사용한다.

## daemon 인자

실행 인자는 변경하지 않았다.

```text
live_candidate_slots.py daemon --interval 60
```
