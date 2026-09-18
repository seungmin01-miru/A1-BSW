"""RT 실행 환경 적용 — sil/vcan/eait_tx.py 의 apply_rt_setup 과 같은 레시피를, can_guard 전용으로
독립 구현한다(§5.D "최소 의존" 원칙 — 이 디렉터리는 이 저장소의 다른 어떤 부분도 import 하지 않는다).
A-3(격리 코어+SCHED_FIFO+mlockall+timer slack)는 can_stack_development.md §5.C 재측정으로 raw SocketCAN
경로에서 효과가 실측된 바로 그 레시피다 — can_guard 가 그 경로를 쓴다.
"""
import ctypes
import os

PR_SET_TIMERSLACK = 29
MCL_CURRENT = 1
MCL_FUTURE = 2


def apply(cpus=None, rt_prio=0):
    """timer slack(항상) → cpu affinity(주어지면) → SCHED_FIFO+mlockall(rt_prio>0 이면, 권한 없으면 경고만).
    반환값은 사람이 읽을 로그 줄 리스트 — 실패해도 예외를 던지지 않는다(권한 없는 게 정상 운영 모드다,
    이 계정에 rtprio 를 영구로 주지 않기로 한 프로젝트 결정 때문 — 측정/운용 시엔 `sudo chrt -f` 로 상속)."""
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
            notes.append('⚠️ SCHED_FIFO 권한 없음 (sudo chrt -f 로 상속 실행 — rtprio 영구 부여 안 함)')
        if libc.mlockall(MCL_CURRENT | MCL_FUTURE) == 0:
            notes.append('mlockall')
        else:
            notes.append(f'⚠️ mlockall 실패({os.strerror(ctypes.get_errno())})')
    return notes


def parse_cpulist(txt):
    """"8" 또는 "8-11,14" → {8, 9, 10, 11, 14}. 빈 문자열/None → None(제한 없음)."""
    if not txt:
        return None
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
