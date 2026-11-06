"""`can_guard.run` 통합 시험 — python-can `virtual` 버스, 같은 프로세스(주 스레드에서 run, vcan·sudo 불필요).

reports/2026-11-05_bsw_update_report/scenarios.py 방식. 벽시계에 덜 민감하도록 시간을 재지 않고 **상태·프레임 수·이벤트**로 기다린다.
개발 PC 결과는 로직 확인용이고 지연 값 판정에 쓰지 않는다(CLAUDE.md). 요구사항 번호는 docs/race_flag_requirements.md.
"""
import csv
import os
import signal
import subprocess
import sys
import threading
import time
import uuid

import can
import pytest

import can_guard
import race_runtime
from protocol import Command, CommandChannel, EstopChannel, HeartbeatChannel, StatusChannel

GO, STOP, FINISH, SLOW_ON, SLOW_OFF = 0x80, 0x40, 0x20, 0x10, 0x08
HERE = os.path.dirname(os.path.abspath(__file__))
GUARD_DIR = os.path.dirname(HERE)
S = {'INIT': 0, 'ACTIVE': 1, 'HOLDING': 2, 'DEGRADED': 3, 'STOPPED': 4, 'ESTOP': 5}
P = {'PRE_GO': 0, 'RACING': 1, 'SLOW': 2, 'FINISHED': 3, 'PARKED': 4, 'SUSPENDED': 5}


def f200(steer):
    d = bytearray(8)
    d[0:2] = int(round(steer * 10)).to_bytes(2, 'little', signed=True)
    d[4] = 7
    return bytes(d)


def f201(kph):
    raw = int(round(kph * 10)) & 0xFFF
    return (raw | (raw << 24)).to_bytes(8, 'little')


def dec(d):
    return {'steer': int.from_bytes(d[0:2], 'little', signed=True) * 0.1, 'brake': int.from_bytes(d[2:4], 'little'),
            'acc': d[4], 'auto': d[5] & 7}


class Rig:
    """가짜 차량·모델·대회 서버. 값은 시나리오가 속성으로 바꾼다. 뒷 스레드가 10 ms 마다 프레임·명령을 낸다."""

    def __init__(self, tmp_path, *extra, race=True, precheck=0.0, allow_missing=True):
        self.tag = uuid.uuid4().hex[:8]
        self.event_path = str(tmp_path / f'ev_{self.tag}.csv')
        self.status_name = 's' + self.tag
        argv = ['--interface', 'virtual', '--channel', 'r' + self.tag, '--precheck-s', str(precheck), '--quiet',
                '--cmd-shm', 'c' + self.tag, '--hb-shm', 'h' + self.tag, '--estop-shm', 'e' + self.tag,
                '--event-log', self.event_path]
        if race:
            argv += ['--race', '--status-shm', self.status_name]
            if allow_missing:
                argv.append('--speed-limit-allow-missing')
        self.argv = argv + list(extra)
        self.bus = can.Bus(channel='r' + self.tag, interface='virtual')
        # 차량
        self.steer = 0.0
        self.speed = 0.0
        self.limit_202 = None
        self.apply_203 = False
        # 모델(어댑터가 쓰는 Command)
        self.write_cmd = True
        self.beat = True
        self.auto = True
        self.m_steer, self.m_acc, self.m_brake = 0.0, 0.0, 0.0
        # 대회 서버
        self.sig124 = None
        self.res127 = None
        self._alive = 0
        # 기록
        self.tx = []
        self.tx203 = []
        self.done = threading.Event()
        self.rc = None
        self.error = None
        self._st = None

    def send(self, aid, data):
        self.bus.send(can.Message(arbitration_id=aid, data=bytes(data), is_extended_id=False))

    def send124(self, byte0, n=2, dlc=8):
        for _ in range(n):
            self.send(0x124, bytes([byte0]) + bytes(dlc - 1))

    def _background(self):
        cmd = hb = None
        self._channels = []
        while not self.done.is_set():
            if cmd is None:
                try:
                    cmd = CommandChannel.open('c' + self.tag)
                    hb = HeartbeatChannel.open('h' + self.tag)
                    self._channels = [cmd, hb]
                except (FileNotFoundError, ValueError):   # ValueError: can_guard 가 세그먼트를 막 만드는 순간(크기 0)
                    cmd = None
            elif self.write_cmd:
                try:
                    cmd.write(Command(steer_auto=self.auto, brake_auto=self.auto, acc_auto=self.auto,
                                      steer_cmd_deg=self.m_steer, acc_cmd_pct=self.m_acc, brake_cmd_pct=self.m_brake))
                    if self.beat:
                        hb.beat()
                except FileNotFoundError:
                    pass
            if hb is not None and self.beat and not self.write_cmd:
                hb.beat()
            self.send(0x200, f200(self.steer))
            self.send(0x201, f201(self.speed))
            if self.limit_202 is not None:
                self.send(0x202, int(round(self.limit_202 * 10)).to_bytes(2, 'little') + bytes(6))
            if self.sig124 is not None:
                self.send124(self.sig124, 1)
            if self.res127 is not None:
                self._alive = (self._alive + 1) & 0xFF
                self.send(0x127, bytes([0x80 if self.res127 else 0]) + bytes(6) + bytes([self._alive]))
            t_end = time.monotonic() + 0.01
            while True:
                m = self.bus.recv(timeout=max(0.0, t_end - time.monotonic()))
                if m is None:
                    break
                if m.arbitration_id == 0x210:
                    self.tx.append(dec(m.data))
                elif m.arbitration_id == 0x203:
                    raw = int.from_bytes(m.data[:2], 'little') & 0x3FF
                    self.tx203.append(raw)
                    if self.apply_203:
                        self.limit_202 = raw * 0.1
        for ch in self._channels:
            ch.close()

    def _script(self, script):
        try:
            if script is not None:
                script(self)
        except BaseException as e:   # noqa: BLE001 — 주 스레드에서 다시 던진다
            self.error = e
        finally:
            can_guard._stop_requested = True

    def run(self, script=None):
        can_guard._stop_requested = False
        bg = threading.Thread(target=self._background, daemon=True)
        th = threading.Thread(target=self._script, args=(script,), daemon=True)
        bg.start()
        th.start()
        try:
            self.rc = can_guard.run(can_guard.parse_args(self.argv))
        finally:
            self.done.set()
            can_guard._stop_requested = True
            th.join(timeout=20)
            bg.join(timeout=2)
            self.bus.shutdown()
            if self._st is not None:
                self._st.close()
        if self.error is not None:
            raise self.error
        return self.rc

    # --- 기다리기 ---------------------------------------------------------------------------------------------
    def status(self):
        if self._st is None:
            self._st = StatusChannel.open_or_none(self.status_name)
        return self._st.read() if self._st is not None else None

    def wait_status(self, pred, timeout=5.0, what=''):
        t_end = time.monotonic() + timeout
        last = None
        while time.monotonic() < t_end and not self.done.is_set():
            last = self.status() or last
            if last is not None and pred(last):
                return last
            time.sleep(0.005)
        raise AssertionError(f'상태 대기 실패 {what}: 마지막 {last}, rc={self.rc}')

    def wait_phase(self, name):
        return self.wait_status(lambda s: s.race_phase == P[name], what=name)

    def wait_guard(self, name):
        return self.wait_status(lambda s: s.guard_state == S[name], what=name)

    def wait_frames(self, n=10, timeout=5.0):
        """지금부터 0x210 이 n 개 더 나올 때까지."""
        start = len(self.tx)
        t_end = time.monotonic() + timeout
        while len(self.tx) < start + n:
            assert time.monotonic() < t_end, f'0x210 이 안 나온다: {len(self.tx) - start}/{n}'
            time.sleep(0.005)
        return start

    def wait_tx(self, pred, timeout=5.0, what=''):
        """지금부터 조건에 맞는 0x210 이 나올 때까지. 반환: 앞서 지나간 프레임 수."""
        start = len(self.tx)
        t_end = time.monotonic() + timeout
        i = start
        while time.monotonic() < t_end:
            while i < len(self.tx):
                if pred(self.tx[i]):
                    return i - start
                i += 1
            time.sleep(0.002)
        raise AssertionError(f'0x210 대기 실패 {what}: 마지막 {self.tx[-3:]}')

    def wait_event(self, name, timeout=5.0):
        t_end = time.monotonic() + timeout
        while time.monotonic() < t_end:
            if any(e[0] == name for e in self.events()):
                return
            time.sleep(0.005)
        raise AssertionError(f'이벤트 {name} 없음: {self.events()}')

    def events(self):
        if not os.path.exists(self.event_path):
            return []
        with open(self.event_path, newline='') as f:
            return [(r['event'], r['detail']) for r in csv.DictReader(f) if r.get('event')]

    def names(self):
        return [e[0] for e in self.events()]

    def arm_ready(self):
        """armed + PRE_GO + 0x124 가 0 임을 본 상태(해제 깃발 무장)로 만든다."""
        self.wait_guard('ACTIVE')
        self.send124(0x00, 2)
        self.wait_frames(3)

    def go(self):
        self.arm_ready()
        self.send124(GO, 2)
        self.wait_phase('RACING')


@pytest.fixture
def rig(tmp_path):
    saved = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGUSR1)}
    rigs = []

    def make(*extra, **kw):
        r = Rig(tmp_path, *extra, **kw)
        rigs.append(r)
        return r

    yield make
    for s, h in saved.items():
        signal.signal(s, h)


# === R-01 / R-02 ===================================================================================================

def test_pre_go_accel_zero_then_go_delivered_next_cycle(rig):
    """Go 전 모델 최대 가속 → 가속 0·조향 실제값(세부 보완 h). Go 확인 뒤 다음 주기에 모델 명령."""
    r = rig()
    r.steer, r.m_steer, r.m_acc = -12.0, 50.0, 100.0

    def script(r):
        r.arm_ready()
        r.send124(GO, 1)      # 1 프레임은 Go 가 아니다(D12)
        r.send124(0x00, 2)
        start = r.wait_frames(20)
        assert r.status().race_phase == P['PRE_GO']
        pre = r.tx[start:]
        assert all(f['acc'] == 0 and f['brake'] == 30 and abs(f['steer'] + 12.0) < 0.11 and f['auto'] == 7 for f in pre), pre[:3]

        r.send124(GO, 2)
        passed = r.wait_tx(lambda f: f['acc'] == 100, what='Go 뒤 모델 가속')
        assert passed <= 2, f'Go 뒤 {passed} 프레임 지나서야 전달(R-02)'
        assert r.tx[-1]['brake'] == 0 and abs(r.tx[-1]['steer'] - 50.0) < 0.11
        assert r.wait_phase('RACING').input_grace

    r.run(script)
    assert r.rc == 0
    assert 'race_internal_error' not in r.names()
    assert ('race_phase', 'PRE_GO->RACING go') in r.events()


def test_silent_before_arm(rig):
    """arm 전에는 기존 INIT 처럼 0x210 을 안 보낸다(auto 000 금지)."""
    r = rig()
    r.write_cmd = False

    def script(r):
        r.wait_status(lambda s: s.race_enabled)
        r.send124(0x00, 2)
        time.sleep(0.2)
        r.send124(GO, 2)
        r.wait_phase('RACING')
        time.sleep(0.1)
        assert r.tx == [], r.tx[:3]

    r.run(script)


def test_restart_with_old_go_held_is_ignored(rig):
    """A1: 재시작 직후 계속 켜진 옛 Go 로 출발하지 않는다. 0 을 본 뒤의 Go 만 받는다."""
    r = rig()
    r.sig124 = GO
    r.m_acc = 100.0

    def script(r):
        r.wait_guard('ACTIVE')
        start = r.wait_frames(40)
        assert r.status().race_phase == P['PRE_GO']
        assert all(f['acc'] == 0 for f in r.tx[start:])
        assert ('race_signal', 'go_level_at_start') in r.events()
        r.sig124 = 0x00
        r.wait_status(lambda s: not s.sig_go)
        r.sig124 = GO
        r.wait_phase('RACING')
        r.wait_tx(lambda f: f['acc'] == 100)

    r.run(script)


def test_precheck_frames_reach_decoder(rig):
    """세부 보완 b: 시작 전 점검 동안의 옛 Go 를 놓치지 않는다 + 점검 중 STOP 은 첫 주기에 반영."""
    r = rig(precheck=0.2)
    r.sig124 = GO
    r.m_acc = 100.0

    def script(r):
        r.wait_guard('ACTIVE')
        r.wait_frames(10)
        assert r.status().race_phase == P['PRE_GO']
        assert all(f['acc'] == 0 for f in r.tx)

    r.run(script)
    assert ('race_signal', 'go_level_at_start') in r.events()

    r2 = rig(precheck=0.2)
    r2.m_acc = 100.0
    r2.sig124 = STOP

    def script2(r):
        r.wait_phase('SUSPENDED')
        r.wait_guard('ESTOP')

    r2.run(script2)


def test_silence_arm_lets_first_go_through(rig):
    """A12: 깃발 있을 때만 송신하는 방식이면 0 프레임이 없다 → 조용함으로 무장, 첫 Go 로 출발."""
    r = rig()
    r.m_acc = 100.0

    def script(r):
        r.wait_guard('ACTIVE')
        r.wait_event('race_signal')    # silence_armed
        r.send124(GO, 2)
        r.wait_phase('RACING')
        r.wait_tx(lambda f: f['acc'] == 100)

    r.run(script)
    assert any(e[0] == 'race_signal' and 'silence_armed' in e[1] for e in r.events())


def test_short_race_frames_are_not_filtered(rig):
    """세부 보완 a: 대회 신호는 8바이트보다 짧아도 처리한다."""
    r = rig()

    def script(r):
        r.wait_guard('ACTIVE')
        r.send124(0x00, 2, dlc=1)
        r.send124(GO, 2, dlc=1)
        r.wait_phase('RACING')

    r.run(script)


# === R-06 RES =======================================================================================================

def test_res_latch_brake_after_release_and_steer_follow(rig):
    """RES: ESTOP 래치, 풀려도 브레이크 유지, 조향은 실제 핸들각을 따라감, auto 000 없음."""
    r = rig()
    r.m_acc, r.m_steer, r.speed, r.steer = 100.0, 40.0, 30.0, 40.0
    r.res127 = 0

    def script(r):
        r.go()
        r.wait_tx(lambda f: f['acc'] == 100)
        i0 = len(r.tx)
        r.res127 = 1
        r.wait_phase('SUSPENDED')
        r.wait_guard('ESTOP')
        r.wait_frames(3)
        r.steer = -7.0              # 차량이 RES 로 스스로 움직이는 동안
        r.wait_tx(lambda f: abs(f['steer'] + 7.0) < 0.11, what='RES 중 조향 follow')
        r.res127 = 0
        r.wait_status(lambda s: not s.res_active)
        r.speed = 0.0
        i1 = r.wait_frames(40)      # 풀린 뒤 0.8 s
        st = r.status()
        assert st.guard_state == S['ESTOP'] and st.race_phase == P['SUSPENDED'], st
        after = r.tx[i1:]
        assert all(f['acc'] == 0 and f['brake'] >= 30 and f['auto'] == 7 for f in after), after[:3]
        assert [f['brake'] for f in after] == sorted(f['brake'] for f in after), '브레이크가 줄었다'
        assert all(abs(f['steer'] + 7.0) < 0.11 for f in after), '풀린 뒤에는 마지막 실제값에 고정'
        assert all(f['auto'] == 7 for f in r.tx[i0:])
        r.send124(0x00, 2)
        r.send124(GO, 2)            # restart 모드: 새 Go 를 받지 않는다
        r.wait_frames(5)
        assert r.status().race_phase == P['SUSPENDED']

    r.run(script)
    names = r.names()
    assert ('res', 'start') in [(e[0], e[1].split()[0]) for e in r.events() if e[0] == 'res']
    assert ('res', 'end') in [(e[0], e[1].split()[0]) for e in r.events() if e[0] == 'res']
    assert any(e[0] == 'estop_request' and 'race_res reason=res' in e[1] for e in r.events())
    assert 'race_internal_error' not in names


def test_res_alive_stall_suspends(rig):
    """0x127 한 번 본 뒤 끊기면 SUSPENDED(S10)."""
    r = rig('--res-alive-timeout-s', '0.2')
    r.res127 = 0

    def script(r):
        r.go()
        r.res127 = None
        r.wait_phase('SUSPENDED')
        r.wait_guard('ESTOP')

    r.run(script)
    assert 'res_alive_lost' in r.names()


def test_go_reset_mode_resumes_only_when_stopped_and_new_go(rig):
    """resume_mode=go-reset 일 때만 ESTOP 해제 경로가 있다. 차가 서 있고 RES·STOP 이 꺼진 뒤 새 Go 여야 한다."""
    r = rig('--resume-mode', 'go-reset')
    r.m_acc, r.speed = 100.0, 20.0
    r.res127 = 0

    def script(r):
        r.go()
        r.res127 = 1
        r.wait_guard('ESTOP')
        r.res127 = 0
        r.wait_status(lambda s: not s.res_active)
        r.send124(0x00, 2)
        r.send124(GO, 2)            # 아직 달리는 중 — 받지 않는다
        r.wait_frames(5)
        assert r.status().race_phase == P['SUSPENDED']
        r.speed = 0.0
        r.wait_frames(5)
        r.send124(0x00, 2)
        r.wait_frames(3)
        r.send124(GO, 2)
        r.wait_phase('RACING')
        r.wait_guard('ACTIVE')
        r.wait_tx(lambda f: f['acc'] == 100)

    r.run(script)
    assert ('race_phase', 'SUSPENDED->RACING go-reset') in r.events()


# === auto 000 금지 ===================================================================================================

def test_parked_disarm_keeps_auto_and_brake(rig):
    """PARKED: 어댑터가 disarm 해도 auto 000·침묵 없이 브레이크 유지(A3, 13절)."""
    r = rig('--parked-after-s', '0.15')
    r.m_acc, r.speed = 50.0, 15.0

    def script(r):
        r.go()
        i0 = len(r.tx)
        r.send124(FINISH, 2)
        r.wait_phase('FINISHED')
        r.speed = 0.0
        r.wait_phase('PARKED')
        i1 = r.wait_frames(5)
        assert all(f['acc'] == 0 and f['brake'] == 60 and f['auto'] == 7 for f in r.tx[i1 + 2:]), r.tx[-3:]
        r.auto = False              # disarm(auto 000 명령)
        i2 = r.wait_frames(20)
        assert all(f['auto'] == 7 and f['acc'] == 0 and f['brake'] >= 60 for f in r.tx[i2 + 2:]), r.tx[-3:]
        r.write_cmd = False         # 어댑터 사망
        i3 = r.wait_frames(20)
        assert all(f['auto'] == 7 and f['brake'] >= 60 for f in r.tx[i3:])
        assert all(f['auto'] != 0 for f in r.tx[i0:])

    r.run(script)


def test_suspended_disarm_keeps_auto(rig):
    r = rig()
    r.m_acc, r.speed = 50.0, 15.0
    r.res127 = 0

    def script(r):
        r.go()
        r.res127 = 1
        r.wait_guard('ESTOP')
        r.res127 = 0
        r.auto = False
        i = r.wait_frames(20)
        assert all(f['auto'] == 7 and f['acc'] == 0 and f['brake'] >= 30 for f in r.tx[i:]), r.tx[-3:]

    r.run(script)


def test_pre_go_gap_and_disarm(rig):
    """A2·A3·D11: PRE_GO 끊김·인지 끊김에도 STOPPED 로 가지 않고, disarm 해도 브레이크를 유지하고, 돌아오면 그대로 출발한다."""
    r = rig('--stopped-after', '0.1', '--perception-lost-stopped-after', '0.1', '--perception-timeout', '0.1')
    r.m_acc = 100.0

    def script(r):
        r.arm_ready()
        r.write_cmd = False
        r.wait_guard('HOLDING')
        i = r.wait_frames(20)
        assert r.status().guard_state == S['HOLDING']
        assert all(f['acc'] == 0 and f['auto'] == 7 and f['brake'] >= 30 for f in r.tx[i:])
        r.write_cmd = True
        r.wait_guard('ACTIVE')
        assert r.wait_status(lambda s: s.model_healthy_s > 0.1).model_healthy_s > 0.1
        r.beat = False
        r.wait_guard('DEGRADED')
        i = r.wait_frames(15)
        assert r.status().guard_state == S['DEGRADED']
        assert all(f['acc'] == 0 for f in r.tx[i:])
        r.beat = True
        r.wait_guard('ACTIVE')
        r.auto = False
        r.wait_status(lambda s: s.model_healthy_s == 0.0)
        i = r.wait_frames(15)
        assert all(f['auto'] == 7 and f['acc'] == 0 and f['brake'] == 30 for f in r.tx[i:]), r.tx[-3:]
        r.auto = True
        r.wait_status(lambda s: s.model_healthy_s > 0.0)
        r.send124(GO, 2)
        r.wait_phase('RACING')
        r.wait_tx(lambda f: f['acc'] == 100)

    r.run(script)


# === 서행 ===========================================================================================================

def test_slow_ignores_model_acceleration(rig):
    """서행 중 모델 큰 가속 무시(D1), 8 km/h 대에서 이벤트가 쏟아지지 않음(세부 보완 g), Slow Off 램프."""
    r = rig()
    r.m_acc, r.speed = 100.0, 30.0

    def script(r):
        r.go()
        r.send124(SLOW_ON, 2)
        st = r.wait_status(lambda s: s.long_override)
        assert st.race_phase == P['SLOW'] and st.speed_target_kph == 8.0 and st.input_grace
        i = r.wait_frames(5)
        assert all(f['acc'] == 0 and f['brake'] >= 30 for f in r.tx[i + 1:]), r.tx[-3:]
        r.speed = 8.9               # 가속 차단 위
        i = r.wait_frames(5)
        assert all(f['acc'] == 0 for f in r.tx[i + 1:]), r.tx[-3:]
        r.speed = 7.0
        r.wait_tx(lambda f: f['brake'] == 0 and 0 < f['acc'] < 100, what='PI 출력')
        r.speed = 9.6               # 브레이크 문턱 위
        r.wait_tx(lambda f: f['acc'] == 0 and f['brake'] >= 30, what='과속 브레이크')
        for _ in range(20):         # 문턱 근처 흔들림
            r.speed = 8.7
            time.sleep(0.02)
            r.speed = 8.9
            time.sleep(0.02)
        r.speed = 8.0
        names = r.names()
        assert names.count('override_on') == 1 and names.count('override_off') == 0
        assert names.count('input_clamped') <= 2, [e for e in r.events() if e[0] == 'input_clamped']
        assert r.status().race_phase == P['SLOW']

        r.send124(SLOW_OFF, 2)
        r.wait_status(lambda s: not s.long_override and s.input_grace)
        passed = r.wait_tx(lambda f: f['acc'] == 100, what='램프 끝')
        assert passed >= 20, f'램프가 너무 빠름({passed} 프레임)'

    r.run(script)
    assert 'slow_on' in r.names() and 'slow_off' in r.names() and 'override_off' in r.names()


def test_infinite_ramp_yaml(rig, tmp_path):
    """`slow_off_ramp_pct_s: .inf` = 램프 없음."""
    src = open(os.path.join(GUARD_DIR, 'race_flag.yaml'), encoding='utf-8').read()
    assert 'slow_off_ramp_pct_s: 50.0' in src
    y = tmp_path / 'flag.yaml'
    y.write_text(src.replace('slow_off_ramp_pct_s: 50.0', 'slow_off_ramp_pct_s: .inf'), encoding='utf-8')
    r = rig('--race-flag-yaml', str(y))
    r.m_acc, r.speed = 100.0, 8.0

    def script(r):
        r.go()
        r.send124(SLOW_ON, 2)
        r.wait_status(lambda s: s.long_override)
        r.send124(0x00, 2)
        r.wait_frames(3)
        r.send124(SLOW_OFF, 2)
        passed = r.wait_tx(lambda f: f['acc'] == 100)
        assert passed <= 3, passed

    r.run(script)
    assert r.rc == 0 and 'race_internal_error' not in r.names()


@pytest.mark.parametrize('bad', ['slow_off_ramp_pct_s: 0.0', 'slow_off_ramp_pct_s: -1', 'slow_off_ramp_pct_s: .nan'])
def test_bad_ramp_yaml_refuses_to_start(rig, tmp_path, bad):
    src = open(os.path.join(GUARD_DIR, 'race_flag.yaml'), encoding='utf-8').read()
    y = tmp_path / 'flag.yaml'
    y.write_text(src.replace('slow_off_ramp_pct_s: 50.0', bad), encoding='utf-8')
    r = rig('--race-flag-yaml', str(y))
    assert r.run() == can_guard.EXIT_RACE_CONFIG
    assert r.tx == []


# === R-07 복구 (계획서 8.2) ===========================================================================================

def test_recover_short_gap_coasts_then_active(rig):
    """0.1 s 끊김: 복구 구간은 관성 주행(가속·브레이크 0), 저속이어도 STOPPED 로 가지 않고 ACTIVE 로 돌아온다."""
    r = rig('--adapter-hold-after-s', '0', '--track-min-radius-m', '200', '--stopped-after', '0.05')
    r.m_acc, r.m_steer, r.speed, r.steer = 40.0, 10.0, 1.0, 10.0   # 1 km/h: 예전 규칙이면 즉시 STOPPED

    def script(r):
        r.go()
        r.wait_tx(lambda f: f['acc'] == 40)
        i0 = len(r.tx)
        r.write_cmd = False
        r.wait_guard('HOLDING')
        time.sleep(0.05)
        r.write_cmd = True
        r.wait_guard('ACTIVE')
        r.wait_event('recover')
        r.wait_tx(lambda f: f['acc'] == 40)
        seg = r.tx[i0:]
        assert any(f['acc'] == 0 and f['brake'] == 0 for f in seg), '복구 구간 프레임이 없다'
        assert all(f['brake'] == 0 for f in seg), '복구 구간에 브레이크'
        assert all(abs(f['steer'] - 10.0) < 0.11 and f['auto'] == 7 for f in seg)

    r.run(script)
    assert 'commit' not in r.names()
    assert not any(e[0] == 'state' and 'STOPPED' in e[1] for e in r.events())


def test_gap_over_budget_commits_then_stopped(rig):
    """0.6 s 끊김(R_min 모름 → 약 0.2 s 초과): 정지 확정, 입력이 돌아와도 ACTIVE 로 안 감, 3 km/h 이하에서 STOPPED."""
    r = rig('--adapter-hold-after-s', '0')
    r.m_acc, r.m_steer, r.speed, r.steer = 40.0, 10.0, 30.0, 10.0

    def script(r):
        r.go()
        r.wait_tx(lambda f: f['acc'] == 40)
        i0 = len(r.tx)
        r.write_cmd = False
        r.wait_guard('HOLDING')
        r.wait_event('commit')
        time.sleep(0.6)
        seg = r.tx[i0:]
        assert any(f['acc'] == 0 and f['brake'] == 0 for f in seg), '확정 전 관성 구간 없음'
        assert seg[-1]['brake'] >= 30 and seg[-1]['acc'] == 0
        r.write_cmd = True
        i1 = r.wait_frames(10)
        assert r.status().guard_state == S['HOLDING'], '확정 뒤 입력 재개로 ACTIVE 로 가면 안 됨'
        assert all(f['acc'] == 0 for f in r.tx[i1:])
        r.speed = 2.0
        r.wait_guard('STOPPED')
        i2 = r.wait_frames(5)
        assert all(f['auto'] == 7 and f['acc'] == 0 and f['brake'] >= 30 for f in r.tx[i2:])

    r.run(script)
    assert 'recover' not in r.names()


def test_default_hold_after_makes_any_visible_gap_commit(rig):
    """★ 기본값의 실제 동작 고정(open item): hold_after 0.10 + R_min 모름이면, can_guard 가 볼 수 있는 끊김은 곧 예산 초과다
    (감지 순간 끊김 0.05 + 0.10 = 0.15 s, 입력이 돌아와 3주기를 채우면 0.2 s 를 넘는다)."""
    r = rig()
    r.m_acc, r.speed = 40.0, 30.0

    def script(r):
        r.go()
        r.write_cmd = False
        r.wait_guard('HOLDING')
        time.sleep(0.05)
        r.write_cmd = True
        r.wait_event('commit')

    r.run(script)


def test_stopped_after_is_ceiling_after_commit(rig):
    """--stopped-after 는 committed 이후 상한(달리는 중에도 그 시간 뒤 STOPPED)."""
    r = rig('--adapter-hold-after-s', '0', '--stopped-after', '0.2')
    r.m_acc, r.speed = 40.0, 30.0

    def script(r):
        r.go()
        r.write_cmd = False
        r.wait_event('commit')
        r.wait_guard('STOPPED')

    r.run(script)


# === 0x202 / 0x203 (WP4, A5) =========================================================================================

def test_tx_off_default_30kph_passes_and_never_sends(rig):
    """A5: 0x202 = 30.0 은 통과 + 기록. 송신 기본 꺼짐 — Slow On/Off 에도 0x203 이 안 나간다."""
    r = rig(precheck=0.2, allow_missing=False)
    r.limit_202 = 30.0
    r.m_acc = 10.0

    def script(r):
        r.go()
        r.send124(SLOW_ON, 2)
        r.wait_status(lambda s: s.long_override)
        st = r.wait_status(lambda s: s.vehicle_speed_limit_kph == 30.0)
        assert st.speed_limit_target_kph == -1.0
        r.send124(SLOW_OFF, 2)
        r.wait_status(lambda s: not s.long_override)
        r.wait_frames(10)
        assert r.tx203 == []

    assert r.run(script) == 0
    assert any(e[0] == 'speed_limit_startup' and 'observed=30.0' in e[1] for e in r.events())


def test_residual_slow_limit_refuses_with_exit_5(rig):
    """A5: 잔류 서행 제한(0 < 값 ≤ slow_brake)은 시작 거부. 0x210 은 한 프레임도 안 나간다."""
    r = rig(precheck=0.2, allow_missing=False)
    r.limit_202 = 9.3
    r.m_acc = 50.0
    assert r.run(lambda r: r.done.wait(5)) == can_guard.EXIT_SPEED_LIMIT == 5
    assert r.tx == [] and r.tx203 == []


def test_missing_0x202_refuses_unless_allowed(rig):
    r = rig(precheck=0.15, allow_missing=False)
    assert r.run(lambda r: r.done.wait(5)) == 5
    r2 = rig(precheck=0.15, allow_missing=True)

    def script(r):
        r.wait_status(lambda s: s.race_enabled)

    assert r2.run(script) == 0


def test_tx_on_corrects_at_startup_and_follows_slow(rig):
    """tx on: 시작 때 고치고 3프레임 반영, Slow On → 93, Slow Off → 해제값. 인코더 raw 와 관리자 raw 일치."""
    r = rig('--speed-limit-tx', '--speed-limit-release-kph', '100', precheck=0.15, allow_missing=False)
    r.limit_202 = 9.3
    r.apply_203 = True
    r.m_acc = 10.0

    def script(r):
        r.go()
        assert r.tx203 and r.tx203[0] == 1000
        r.send124(SLOW_ON, 2)
        r.wait_status(lambda s: s.speed_limit_target_kph == 9.3)
        r.wait_status(lambda s: s.vehicle_speed_limit_kph == 9.3)
        r.wait_frames(5)
        assert 93 in r.tx203
        r.send124(SLOW_OFF, 2)
        r.wait_status(lambda s: s.vehicle_speed_limit_kph == 100.0)
        r.wait_frames(5)
        assert r.tx203[-1] == 1000

    assert r.run(script) == 0
    names = r.names()
    assert names.count('speed_limit_confirm') >= 3 and 'speed_limit_tx' in names
    assert 'speed_limit_encode_mismatch' not in names


def test_tx_on_startup_timeout_refuses(rig):
    r = rig('--speed-limit-tx', '--speed-limit-release-kph', '100', '--speed-limit-startup-timeout', '0.2',
            precheck=0.15, allow_missing=False)
    r.limit_202 = 9.3
    assert r.run(lambda r: r.done.wait(5)) == 5
    assert r.tx == [] and r.tx203 and all(v == 1000 for v in r.tx203)


def test_0x203_send_failure_does_not_block_0x210(rig, monkeypatch):
    """세부 보완 e: 0x203 송신 실패가 0x210 을 막지 않는다."""
    real_send = can.interfaces.virtual.VirtualBus.send

    def flaky(self, msg, timeout=None):
        if msg.arbitration_id == 0x203:
            raise can.CanOperationError('막힘')
        return real_send(self, msg, timeout)

    monkeypatch.setattr(can.interfaces.virtual.VirtualBus, 'send', flaky)
    r = rig('--speed-limit-tx', '--speed-limit-release-kph', '100', precheck=0.1, allow_missing=False)
    r.limit_202 = 100.0
    r.m_acc = 10.0

    def script(r):
        r.go()
        r.send124(SLOW_ON, 2)
        r.wait_status(lambda s: s.speed_limit_target_kph == 9.3)
        r.wait_frames(10)

    assert r.run(script) == 0
    assert 'speed_limit_tx_fail' in r.names()
    assert 'race_internal_error' not in r.names()


def test_foreign_0x203_logged(rig):
    r = rig()

    def script(r):
        r.wait_status(lambda s: s.race_enabled)
        r.send(0x203, bytes(8))
        r.wait_event('foreign_0x203')

    r.run(script)


# === 예외 =============================================================================================================

@pytest.mark.parametrize('target', ['next_race_state', 'apply_race_envelope'])
def test_internal_error_goes_estop_and_loop_survives(rig, monkeypatch, target):
    """세부 보완 f: race 계산 예외 → 이벤트 + SUSPENDED + ESTOP. 메인 루프는 계속 돌고 auto 000 이 없다. 다시 풀리지 않는다."""
    real = getattr(race_runtime, target)
    armed = {'on': False}

    def boom(*a, **kw):
        if armed['on']:
            raise RuntimeError('시험용 예외')
        return real(*a, **kw)

    monkeypatch.setattr(race_runtime, target, boom)
    r = rig()
    r.m_acc, r.speed = 50.0, 20.0

    def script(r):
        r.go()
        r.wait_tx(lambda f: f['acc'] == 50)
        armed['on'] = True
        r.wait_phase('SUSPENDED')
        r.wait_guard('ESTOP')
        i = r.wait_frames(25)
        assert all(f['acc'] == 0 and f['brake'] >= 30 and f['auto'] == 7 for f in r.tx[i:]), r.tx[-3:]
        armed['on'] = False
        r.speed = 0.0
        r.wait_frames(10)
        assert r.status().guard_state == S['ESTOP']

    r.run(script)
    errs = [e for e in r.events() if e[0] == 'race_internal_error']
    assert len(errs) == 1 and 'RuntimeError' in errs[0][1]


# === CLI · off ========================================================================================================

@pytest.mark.parametrize('argv', [
    ['--race', '--stopped-mode', 'manual'],
    ['--race', '--auto-off-mode', 'send'],
    ['--race', '--init-mode', 'legacy'],
    ['--speed-limit-tx', '--speed-limit-release-kph', '100'],
    ['--race', '--speed-limit-tx'],
    ['--race', '--race-stop-action', 'nope'],
])
def test_cli_rejects_dangerous_combinations(argv):
    with pytest.raises(SystemExit) as e:
        can_guard.parse_args(argv)
    assert e.value.code == 2


def test_cli_defaults_match_plan():
    a = can_guard.parse_args([])
    assert a.race is False and a.speed_limit_tx is False and a.speed_limit_release_kph is None
    assert (a.race_confirm_frames, a.race_stop_action, a.race_signal_timeout_s, a.resume_mode) == (2, 'estop', 0.0, 'restart')
    assert (a.res_alive_timeout_s, a.pre_go_brake_pct) == (0.5, 30.0)
    assert (a.slow_target_kph, a.slow_acc_cut_kph, a.slow_brake_kph, a.finish_cap_kph) == (8.0, 8.8, 9.3, 20.0)
    assert (a.parked_speed_kph, a.parked_after_s, a.parked_brake_pct, a.input_grace_s) == (0.5, 5.0, 60.0, 1.0)
    assert (a.recover_budget_m, a.recover_delay_s, a.recover_alat_max, a.track_min_radius_m) == (0.30, 0.25, 3.0, None)
    assert a.resume_low_speed is False and a.adapter_hold_after_s == 0.10
    assert (a.speed_limit_practice_kph, a.speed_limit_fast_period, a.speed_limit_keep_period) == (30.0, 0.02, 0.1)
    assert (a.speed_limit_confirm_frames, a.speed_limit_mismatch_s, a.speed_limit_release_note_s,
            a.speed_limit_startup_timeout) == (3, 0.3, 1.0, 2.0)
    assert os.path.samefile(a.race_flag_yaml, os.path.join(GUARD_DIR, 'race_flag.yaml'))


def test_race_off_has_no_status_no_race_events_and_ignores_flags(rig):
    """--race off: 새 공유메모리·새 프레임 처리·새 이벤트 없음. 깃발이 와도 명령은 그대로 나간다."""
    r = rig(race=False)
    r.m_acc, r.m_steer, r.steer = 40.0, 10.0, -5.0
    r.sig124 = STOP
    r.res127 = 1
    r.limit_202 = 9.3

    def script(r):
        r.wait_tx(lambda f: f['acc'] == 40, what='명령 통과')
        i = r.wait_frames(20)
        assert all(f['acc'] == 40 and abs(f['steer'] - 10.0) < 0.11 and f['brake'] == 0 and f['auto'] == 7 for f in r.tx[i:])
        assert StatusChannel.open_or_none(r.status_name) is None

    assert r.run(script) == 0
    assert r.tx203 == []
    assert set(r.names()) <= {'start', 'state', 'stop', 'estop_request'}


def test_race_off_does_not_import_race_modules():
    code = ("import sys; sys.path.insert(0, %r); import can_guard; a = can_guard.parse_args([]); assert not a.race; "
            "assert not {'race_runtime', 'race_state', 'race_signal', 'race_policy', 'speed_ctrl', 'yaml'} & set(sys.modules), sorted(sys.modules)"
            % GUARD_DIR)
    subprocess.run([sys.executable, '-c', code], check=True)


def test_estop_shm_reset_does_not_clear_race_latch(rig):
    """estop.py 해제 요청이 와도 SUSPENDED 동안은 원천 race_res 가 켜져 있어 ESTOP 이 풀리지 않는다."""
    r = rig()
    r.speed = 0.0
    r.res127 = 0

    def script(r):
        r.go()
        r.res127 = 1
        r.wait_guard('ESTOP')
        r.res127 = 0
        r.wait_status(lambda s: not s.res_active)
        ch = EstopChannel.open('e' + r.tag)
        ch.trigger('test')      # reset_seq 는 can_guard 가 값을 한 번 본 뒤의 변화만 해제 요청으로 친다
        r.wait_frames(3)
        ch.reset('test')
        ch.close()
        r.wait_event('estop_reset')
        r.wait_frames(5)
        assert r.status().guard_state == S['ESTOP']

    r.run(script)
    assert any(e[0] == 'estop_reset' and e[1].startswith('refused') for e in r.events())
