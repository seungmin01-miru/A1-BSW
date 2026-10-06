"""P-1/P-2 공용: can_guard 를 vcan0 에 띄우고, 0x156/0x157 프레임을 시각과 함께 받아 두는 헬퍼."""
import os
import subprocess
import sys
import time

import can
import cantools

CAN_GUARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DBC = os.path.join(CAN_GUARD_DIR, '..', '..', 'DBC', 'EAIT_CAN(AVANTE_CN7).dbc')


def can_guard_cmd(cmd_shm, hb_shm, channel='vcan0', watchdog_t=0.05, period=0.01, extra_args=()):
    """can_guard.py 를 실행할 argv 리스트를 만든다(실행은 호출자가 함 — stderr 를 PIPE 로 받을지 파일로
    직접 보낼지는 용도에 따라 다르므로 여기서 결정하지 않는다).

    extra_args 에 --rt-priority(>0) 가 있으면 이 프로세스만 `sudo chrt` 로 감싼다 — 오케스트레이션
    스크립트(측정 관찰자·가짜 노드들 포함)를 통째로 chrt 로 감싸면 fork() 상속 때문에 걔들도 덩달아
    SCHED_FIFO 가 돼 측정이 오염된다(비격리 코어에서 동일 우선순위 프로세스들이 서로 경합).
    can_guard 프로세스 하나만 실제 배포 형태처럼 격리해야 깨끗하게 잰다."""
    args = ['--channel', channel, '--cmd-shm', cmd_shm, '--hb-shm', hb_shm,
            '--watchdog-t', str(watchdog_t), '--period', str(period), *extra_args]
    cmd = [sys.executable, os.path.join(CAN_GUARD_DIR, 'can_guard.py'), *args]
    extra_list = list(extra_args)
    if '--rt-priority' in extra_list:
        prio = extra_list[extra_list.index('--rt-priority') + 1]
        if int(prio) > 0:
            user = os.environ.get('SUDO_USER') or os.environ.get('USER') or 'ailab'
            cmd = ['sudo', 'chrt', '-f', str(prio), 'sudo', '-u', user] + cmd
    return cmd


def start_can_guard(cmd_shm, hb_shm, channel='vcan0', watchdog_t=0.05, period=0.01, extra_args=()):
    """can_guard.py 를 서브프로세스로 띄운다. stderr 는 파이프로 받아 상태 전이 로그를 관찰한다
    (짧은 P-1/P-2/A-3 시험용 — 8시간처럼 긴 실행은 파이프가 안 비워지면 print() 가 블록될 위험이 있어
    soak_8h.py 처럼 파일로 직접 리다이렉트해야 한다, can_guard_cmd() 를 직접 써서)."""
    return subprocess.Popen(
        can_guard_cmd(cmd_shm, hb_shm, channel, watchdog_t, period, extra_args),
        stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, bufsize=1,
    )


def start_fake_control_node(cmd_shm, period=0.01):
    return subprocess.Popen(
        [sys.executable, os.path.join(CAN_GUARD_DIR, 'sil_tests', 'fake_control_node.py'),
         '--cmd-shm', cmd_shm, '--period', str(period)],
        stderr=subprocess.DEVNULL,
    )


def start_fake_perception(hb_shm, period=0.05):
    return subprocess.Popen(
        [sys.executable, os.path.join(CAN_GUARD_DIR, 'sil_tests', 'fake_perception.py'),
         '--hb-shm', hb_shm, '--period', str(period)],
        stderr=subprocess.DEVNULL,
    )


def wait_for_shm(name, timeout=2.0):
    """can_guard 가 세그먼트를 만들 때까지 대기(먼저 떠야 하니 약간 시간이 걸린다)."""
    from multiprocessing import resource_tracker, shared_memory
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end:
        try:
            shm = shared_memory.SharedMemory(name=name, create=False)
            # protocol.py 의 _unregister_from_tracker 와 같은 이유 — 이 스크립트는 소유자가 아니고,
            # can_guard(소유자)가 나중에 unlink 하면 이 프로세스 종료 시 resource_tracker 가
            # "추적하던 걸 못 지웠다" 는 무해한 경고를 찍는다. 등록에서 미리 빼 둔다.
            try:
                resource_tracker.unregister(shm._name, 'shared_memory')
            except Exception:
                pass
            shm.close()
            return True
        except FileNotFoundError:
            time.sleep(0.01)
    return False


class FrameRecorder:
    """vcan0 의 0x156/0x157 프레임을 (monotonic 수신시각, 디코드값) 으로 쌓는다. 별도 스레드로 돈다."""

    def __init__(self, channel='vcan0'):
        self.db = cantools.database.load_file(DBC)
        self.bus = can.Bus(channel=channel, interface='socketcan')
        self.frames = []   # (t_mono, arbitration_id, decoded dict)
        self._stop = False

    def run_for(self, seconds):
        t_end = time.monotonic() + seconds
        while time.monotonic() < t_end:
            msg = self.bus.recv(timeout=0.05)
            if msg is None or msg.arbitration_id not in (0x156, 0x157):
                continue
            try:
                decoded = self.db.decode_message(msg.arbitration_id, msg.data, decode_choices=False)
            except Exception:
                continue
            self.frames.append((time.monotonic(), msg.arbitration_id, decoded))

    def close(self):
        self.bus.shutdown()


def check_alive_cnt_continuous(frames):
    """0x156 프레임들의 Aliv_Cnt 가 (재부팅 없이) 정확히 +1 씩(255→0 롤오버 포함) 이어지는지.
    반환: (연속 여부, 끊긴 지점 목록)."""
    seq = [(t, d['Aliv_Cnt']) for t, fid, d in frames if fid == 0x156]
    gaps = []
    for i in range(1, len(seq)):
        prev = seq[i - 1][1]
        cur = seq[i][1]
        if cur != (prev + 1) % 256:
            gaps.append((seq[i - 1], seq[i]))
    return len(gaps) == 0, gaps
