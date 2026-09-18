"""
Apply the RT execution recipe.

sil/vcan/eait_tx.py 의 레시피(can_stack_development.md §5.A, 468행 "이것이 Phase C 노드의
실행 조건이다")를 ROS2 노드에도 그대로 적용한다.

  1) 타이머 여유(timer slack) 1µs — 권한 불필요, 항상 적용. 기본 50µs 여유가 sleep 기반
     주기 편차의 대부분이었음(실측).
  2) CPU affinity — 격리 코어 배치. isolcpus 부팅에서는 명시하지 않으면 격리 코어에서
     절대 돌지 않는다(A-3).
  3) SCHED_FIFO + mlockall — 권한 필요(rtprio/memlock). 이 계정에 영구 부여하지 않는
     정책(2026-09-12 결정)이라 실패는 정상이며 경고만 남기고 계속 동작한다. 측정 시에는
     `sudo chrt -f <prio> sudo -u ailab ros2 run ...` 로 우선순위만 상속시켜 실행한다
     (설정 변경 없음).
"""
import ctypes
import os

PR_SET_TIMERSLACK = 29
MCL_CURRENT = 1
MCL_FUTURE = 2


def apply(cpus=None, rt_prio=0):
    """Apply timer-slack / CPU affinity / SCHED_FIFO+mlockall and return human-readable notes."""
    libc = ctypes.CDLL('libc.so.6', use_errno=True)
    notes = []
    if libc.prctl(PR_SET_TIMERSLACK, ctypes.c_ulong(1000), 0, 0, 0) == 0:
        notes.append('timer_slack=1µs')
    if cpus:
        try:
            os.sched_setaffinity(0, cpus)
            notes.append(f'cpu={sorted(cpus)}')
        except OSError as e:
            notes.append(f'⚠️ CPU 배치 실패({e.strerror})')
    if rt_prio:
        try:
            os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(rt_prio))
            notes.append(f'SCHED_FIFO {rt_prio}')
        except PermissionError:
            notes.append(
                '⚠️ SCHED_FIFO 권한 없음 (sudo chrt -f 로 상속 실행 — A-3/A-4, rtprio 영구 부여 안 함)'
            )
        if libc.mlockall(MCL_CURRENT | MCL_FUTURE) == 0:
            notes.append('mlockall')
        else:
            notes.append(f'⚠️ mlockall 실패({os.strerror(ctypes.get_errno())})')
    return notes


def parse_cpulist(txt):
    """Parse "8-11,14" into {8, 9, 10, 11, 14}; empty string -> empty set."""
    out = set()
    for part in txt.split(','):
        if not part:
            continue
        if '-' in part:
            a, b = part.split('-')
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out
