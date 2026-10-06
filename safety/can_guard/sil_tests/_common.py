"""P-1/P-2 공용: can_guard 를 vcan0 에 띄우고, 0x210 프레임을 시각과 함께 받아 두는 헬퍼.
2026-10-06 실차 프로토콜(DBC/A1_dbc_fixed.dbc)로 전환 — 이전 EAIT 0x156/0x157·Aliv_Cnt 검사는 git 기록."""
import os
import subprocess
import sys
import time

import can
import cantools

CAN_GUARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DBC = os.path.join(CAN_GUARD_DIR, '..', '..', 'DBC', 'A1_dbc_fixed.dbc')


def can_guard_cmd(cmd_shm, hb_shm, channel='vcan0', watchdog_t=0.05, period=0.02, extra_args=()):
    """can_guard.py 를 실행할 argv 리스트를 만든다(실행은 호출자가 함 — stderr 를 PIPE 로 받을지 파일로
    직접 보낼지는 용도에 따라 다르므로 여기서 결정하지 않는다).

    extra_args 에 --rt-priority(>0) 가 있으면 이 프로세스만 `sudo chrt` 로 감싼다 — 오케스트레이션
    스크립트(측정 관찰자·가짜 노드들 포함)를 통째로 chrt 로 감싸면 fork() 상속 때문에 걔들도 덩달아
    SCHED_FIFO 가 돼 측정이 오염된다(비격리 코어에서 동일 우선순위 프로세스들이 서로 경합).
    can_guard 프로세스 하나만 실제 배포 형태처럼 격리해야 깨끗하게 잰다."""
    # --precheck-s 0: 시작 전 1초 듣기(다른 0x210 송신자 거부)는 SIL 시간 측정에 잡음이라 끈다 —
    # 그 기능 자체는 tools/race_day/rehearse_lift_guard.sh 가 따로 확인한다.
    args = ['--channel', channel, '--cmd-shm', cmd_shm, '--hb-shm', hb_shm,
            '--watchdog-t', str(watchdog_t), '--period', str(period), '--precheck-s', '0', *extra_args]
    cmd = [sys.executable, os.path.join(CAN_GUARD_DIR, 'can_guard.py'), *args]
    extra_list = list(extra_args)
    if '--rt-priority' in extra_list:
        prio = extra_list[extra_list.index('--rt-priority') + 1]
        if int(prio) > 0:
            user = os.environ.get('SUDO_USER') or os.environ.get('USER') or 'ailab'
            cmd = ['sudo', 'chrt', '-f', str(prio), 'sudo', '-u', user] + cmd
    return cmd


def start_can_guard(cmd_shm, hb_shm, channel='vcan0', watchdog_t=0.05, period=0.02, extra_args=()):
    """can_guard.py 를 서브프로세스로 띄운다. stderr 는 파이프로 받아 상태 전이 로그를 관찰한다
    (짧은 P-1/P-2/A-3 시험용 — 8시간처럼 긴 실행은 파이프가 안 비워지면 print() 가 블록될 위험이 있어
    soak_8h.py 처럼 파일로 직접 리다이렉트해야 한다, can_guard_cmd() 를 직접 써서)."""
    return subprocess.Popen(
        can_guard_cmd(cmd_shm, hb_shm, channel, watchdog_t, period, extra_args),
        stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, bufsize=1,
    )


def start_fake_control_node(cmd_shm, period=0.02):
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
    """vcan0 의 0x210 프레임을 (monotonic 수신시각, 디코드값) 으로 쌓는다."""

    def __init__(self, channel='vcan0'):
        self.db = cantools.database.load_file(DBC)
        self.bus = can.Bus(channel=channel, interface='socketcan')
        self.frames = []   # (t_mono, arbitration_id, decoded dict)
        self._stop = False

    def run_for(self, seconds):
        t_end = time.monotonic() + seconds
        while time.monotonic() < t_end:
            msg = self.bus.recv(timeout=0.05)
            if msg is None or msg.arbitration_id != 0x210:
                continue
            try:
                decoded = self.db.decode_message(msg.arbitration_id, msg.data, decode_choices=False)
            except Exception:
                continue
            self.frames.append((time.monotonic(), msg.arbitration_id, decoded))

    def close(self):
        self.bus.shutdown()


def check_tx_continuous(frames, period, max_gap_factor=2.5):
    """0x210 이 끊김 없이 나갔는지 — 0x210 에는 alive counter 가 없어서(DBC 확인) 이전의 Aliv_Cnt 연속 검사
    대신 "어떤 간격도 목표 주기의 max_gap_factor 배를 넘지 않음"으로 본다. 반환: (연속 여부, 넘은 간격 목록)."""
    ts = [t for t, fid, _ in frames if fid == 0x210]
    gaps = [(a, b - a) for a, b in zip(ts, ts[1:]) if b - a > period * max_gap_factor]
    return len(gaps) == 0 and len(ts) > 1, gaps
