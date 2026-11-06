#!/usr/bin/env python3
"""경기 신호 전달 SIL 자동 판정 — fake_race_server → can_raw_bridge → race_state_publisher → /race/state (지시서 3절, WP1 관문).

  source /opt/ros/humble/setup.bash && source ros2_ws/install/setup.bash
  python3 safety/can_guard/sil_tests/r_race.py                                 # virtual: 브리지를 이 프로세스 스레드로(vcan 없는 PC)
  python3 safety/can_guard/sil_tests/r_race.py --interface socketcan --channel vcan0   # 대회 PC: 브리지도 별도 프로세스, vcan* 만
  python3 safety/can_guard/sil_tests/r_race.py --only S02,S09 --out /tmp/r
  python3 safety/can_guard/sil_tests/r_race.py --latency 200 --load             # 지연 측정(0x127 토글 200 회, 실제 버스 부하 흉내)

**차량·실제 CAN 에 닿지 않는다.** socketcan 은 `vcan*` 채널만 받고 그 밖은 종료 코드 2.
벽시계로 돈다. 개발 PC 결과는 로직 확인용이고 지연 판정은 대회 PC 로(CLAUDE.md). 합격: 지연 중앙값 ≤ 5 ms(지시서 3절).
결과(`--out`): summary.csv, latency.csv, <시나리오>_state.csv(시간에 따른 /race/state) — PlotJuggler 에 그대로 연다.
"""
import argparse
import csv
import glob
import math
import os
import statistics
import subprocess
import sys
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
GUARD_DIR = os.path.dirname(HERE)
SCEN_DIR = os.path.join(REPO, 'sil', 'vcan', 'race_scenarios')
sys.path[:0] = [GUARD_DIR, os.path.join(REPO, 'sil', 'vcan')]

import can  # noqa: E402
import fake_race_server as frs  # noqa: E402
import rclpy  # noqa: E402
from rclpy.executors import SingleThreadedExecutor  # noqa: E402
from rclpy.node import Node  # noqa: E402

from a1_can_bridge.can_raw_bridge import CanRawBridge  # noqa: E402
from a1_can_bridge.race_state_publisher import race_state_qos  # noqa: E402
from a1_can_msgs.msg import RaceState, WheelInfo  # noqa: E402
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy  # noqa: E402

BRIDGE_IDS = '0x200,0x201,0x210,0x124,0x125,0x126,0x127,0x128,0x202,0x156,0x157'   # launch 기본값과 같게
FLAGS = ('go', 'slow_on', 'slow_off', 'finish', 'stop')
EPS = 1e-6


class Recorder(Node):
    """/race/state 를 수신 시각과 함께 쌓는다."""

    def __init__(self):
        super().__init__('r_race_recorder')
        self.rows = []
        self.create_subscription(RaceState, '/race/state', self.on_state, race_state_qos())
        self.wheel_n, self.wheel_lag_max = 0, 0.0
        qos = QoSProfile(depth=100, reliability=ReliabilityPolicy.BEST_EFFORT, history=HistoryPolicy.KEEP_LAST)
        self.create_subscription(WheelInfo, '/control/status/wheel_info', self.on_wheel, qos)

    def on_state(self, m):
        self.rows.append((time.time(), m))

    def on_wheel(self, m):
        """디코더 밀림 확인 — 헤더(프레임 시각)와 도착 시각의 차."""
        self.wheel_n += 1
        self.wheel_lag_max = max(self.wheel_lag_max, time.time() - (m.header.stamp.sec + m.header.stamp.nanosec * 1e-9))


def _row_dict(t0, tr, m):
    d = {'t': tr - t0}
    for f in FLAGS:
        d[f'sig_{f}'] = int(getattr(m, f'sig_{f}'))
        d[f'seen_{f}'] = int(getattr(m, f'seen_{f}'))
    d.update(last_signal=m.last_signal, res_active=int(m.res_active), res_alive_ok=int(m.res_alive_ok),
             res_end=int(m.res_end), res_error=m.res_error, race_signal_age_s=m.race_signal_age_s,
             res_age_s=m.res_age_s, remote_app_active=int(m.remote_app_active),
             vehicle_speed_limit_kph=m.vehicle_speed_limit_kph, pit_lat_deg=m.pit_lat_deg, pit_lon_deg=m.pit_lon_deg)
    return d


# ── 판정: 시나리오별. rows = [(t, RaceState)], t 는 시나리오 시작 기준 s ─────────────────────
def _last(rows):
    return rows[-1][1]


def _any(rows, fn, t0=-1.0, t1=1e9):
    return any(fn(m) for t, m in rows if t0 <= t <= t1)


def _all(rows, fn, t0=-1.0, t1=1e9):
    picked = [m for t, m in rows if t0 <= t <= t1]
    return bool(picked) and all(fn(m) for m in picked)


def _first_t(rows, fn, t0=-1.0):
    return next((t for t, m in rows if t >= t0 and fn(m)), None)


def j_s02(r):
    return [('seen_go 전 false', _all(r, lambda m: not m.seen_go, 0.0, 1.9)),
            ('Go 뒤 seen_go·sig_go', _last(r).seen_go and _last(r).sig_go),
            ('last_signal=GO', _last(r).last_signal == 'GO'),
            ('약 2 s 에 올라감', abs((_first_t(r, lambda m: m.seen_go) or -9) - 2.0) < 0.1)]


def j_ignored(r):
    return [('seen_go false', not _any(r, lambda m: m.seen_go)), ('sig_go false', not _any(r, lambda m: m.sig_go)),
            ('last_signal=NONE', _last(r).last_signal == 'NONE')]


def j_s04(r):
    return [('seen_go true', _last(r).seen_go), ('sig_go 다시 false', not _last(r).sig_go),
            ('last_signal=GO', _last(r).last_signal == 'GO')]


def j_s09(r):
    on = _first_t(r, lambda m: m.res_active)
    off = _first_t(r, lambda m: not m.res_active, 8.0)
    return [('7 s 에 res_active', on is not None and abs(on - 7.0) < 0.05),
            ('10 s 에 해제', off is not None and abs(off - 10.0) < 0.05),
            ('그 사이 유지', _all(r, lambda m: m.res_active, 7.1, 9.9)),
            ('끝까지 alive 정상', _all(r, lambda m: m.res_alive_ok, 1.0))]


def j_s09b(r):
    n = sum(1 for _, m in r if m.res_active)
    return [('1 프레임도 발행됨', n >= 1), ('곧바로 풀림', not _last(r).res_active),
            ('깃발 처리에 영향 없음', _last(r).seen_go)]


def j_s10(r):
    t = _first_t(r, lambda m: not m.res_alive_ok, 2.0)
    return [('0.5 s 뒤 alive 나쁨', t is not None and abs(t - 5.5) < 0.1),
            ('res_age_s 증가', _last(r).res_age_s > 4.0), ('RES 작동 아님', not _last(r).res_active)]


def j_s10b(r):
    t = _first_t(r, lambda m: not m.res_alive_ok, 2.0)
    return [('0.5 s 뒤 alive 나쁨', t is not None and abs(t - 5.5) < 0.1),
            ('프레임은 계속 옴(age 작음)', _all(r, lambda m: m.res_age_s < 0.1, 5.6, 9.9))]


def j_s11a(r):
    return [('sig_go 유지', _last(r).sig_go and _last(r).seen_go),
            ('race_signal_age_s 증가', _last(r).race_signal_age_s > 4.0),
            ('끊기기 전 age 작음', _all(r, lambda m: m.race_signal_age_s < 0.1, 1.0, 4.9))]


def j_x01(r):
    before = [m for t, m in r if t < 1.9]
    return [('처음엔 NaN', bool(before) and all(math.isnan(m.pit_lat_deg) for m in before)),
            ('좌표 도착', abs(_last(r).pit_lat_deg - 37.0) < 1e-6 and abs(_last(r).pit_lon_deg - 127.0) < 1e-6)]


JUDGE = {'S02_go': j_s02, 'S03_go_pulse_1frame': j_ignored, 'S03b_go_flicker': j_ignored,
         'S04_go_pulse_2frames': j_s04, 'S09_res_then_release': j_s09, 'S09b_res_pulse_1frame': j_s09b,
         'S10_res_alive_stop': j_s10, 'S10b_res_alive_freeze': j_s10b, 'S11a_race_signal_lost_default': j_s11a,
         'X01_pit_repair_record_only': j_x01}


# ── 실행 ──────────────────────────────────────────────────────────────
class Rig:
    """publisher(별도 프로세스) + 브리지(virtual = 스레드, socketcan = 프로세스) + 기록 노드."""

    def __init__(self, a, ids=BRIDGE_IDS, decoder=False):
        self.a, self.procs = a, []
        self.channel = a.channel or f'r_{uuid.uuid4().hex[:8]}'
        self.bus = can.Bus(channel=self.channel, interface=a.interface)
        env = dict(os.environ)
        pkg = '-p', f'can_guard_dir:={GUARD_DIR}'
        self.procs.append(subprocess.Popen(
            [sys.executable, '-m', 'a1_can_bridge.race_state_publisher', '--ros-args', *pkg, '--log-level', 'warn'],
            env=env, stderr=subprocess.DEVNULL))
        self.publisher = self.procs[-1]
        self.decoder = None
        if decoder:
            self.procs.append(subprocess.Popen(
                [sys.executable, '-m', 'a1_can_bridge.a1_status_decoder', '--ros-args', '--log-level', 'error'],
                env=env, stderr=subprocess.DEVNULL))
            self.decoder = self.procs[-1]
        self.bridge = None
        if a.interface == 'socketcan':
            self.procs.append(subprocess.Popen(
                [sys.executable, '-m', 'a1_can_bridge.can_raw_bridge', '--ros-args', '-p', f'channel:={self.channel}',
                 '-p', 'interface:=socketcan', '-p', f'ids:={ids}', '--log-level', 'warn'],
                env=env, stderr=subprocess.DEVNULL))
        else:
            self.bridge = _BridgeOverride(self.channel, ids)
            self.bridge_th = threading.Thread(target=self.bridge.spin_recv, daemon=True)
            self.bridge_th.start()
        self.rec = Recorder()
        self.ex = SingleThreadedExecutor()
        self.ex.add_node(self.rec)
        self.ex_th = threading.Thread(target=self.ex.spin, daemon=True)
        self.ex_th.start()
        t_end = time.time() + 15.0
        while not self.rec.rows and time.time() < t_end:
            time.sleep(0.05)
        if not self.rec.rows:
            raise RuntimeError('/race/state 가 안 나옴 — race_state_publisher 확인')
        time.sleep(0.5)     # DDS 연결(브리지 → publisher)이 잡힐 시간

    def close(self):
        for p in self.procs:
            p.terminate()
        for p in self.procs:
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
        if self.bridge is not None:
            self.bridge.running = False
            self.bridge_th.join(timeout=2)
            self.bridge._bus.shutdown()
            self.bridge.destroy_node()
        self.ex.shutdown()
        self.rec.destroy_node()
        self.bus.shutdown()


class _BridgeOverride(CanRawBridge):
    """실제 CanRawBridge 를 virtual 버스로(파라미터만 다름). 같은 프로세스 스레드에서 돈다."""

    def __init__(self, channel, ids):
        self._ov = (channel, ids)
        super().__init__()

    def declare_parameter(self, name, value=None, *a, **k):
        channel, ids = self._ov
        over = {'channel': channel, 'interface': 'virtual', 'ids': ids}
        return super().declare_parameter(name, over.get(name, value), *a, **k)

    def spin_recv(self):
        """실제 루프와 같은 publish_frame. 전역 executor 를 쓰지 않는다(이 프로세스에 노드가 여럿이다)."""
        self.running = True
        while self.running:
            msg = self._bus.recv(timeout=0.05)
            if msg is not None:
                self.publish_frame(msg)


def run_scenario(a, path):
    sc = frs.load_scenario(path)
    rig = Rig(a)
    try:
        srv = frs.RaceServer(rig.bus, sc)
        rig.rec.rows.clear()
        t0 = time.time()
        srv.start(t0)
        while not srv.finished(time.time()):
            srv.step(time.time())
            time.sleep(0.002)
        time.sleep(0.2)
        rows = [(tr - t0, m) for tr, m in rig.rec.rows]
    finally:
        rig.close()
    checks = JUDGE[sc['name']](rows)
    if a.out:
        with open(os.path.join(a.out, f"{sc['name']}_state.csv"), 'w', newline='') as f:
            w = None
            for tr, m in rows:
                d = _row_dict(0.0, tr, m)
                if w is None:
                    w = csv.DictWriter(f, list(d))
                    w.writeheader()
                w.writerow(d)
    return sc['name'], checks, len(rows)


def _load_frames():
    """11-04 실제 버스에 있던 것 + 대회 신호 20 ms. (id, 주기 s, 8바이트). 0x210 은 우리 송신."""
    z = bytes(8)
    return [(0x200, 0.021, z), (0x201, 0.021, z), (0x210, 0.020, z),
            (0x156, 0.010, z), (0x157, 0.010, z), (0x202, 0.020, bytes.fromhex('2C01000000000000')),
            (0x128, 0.020, z), (0x124, 0.020, z), (0x125, 0.020, z), (0x126, 0.020, z),
            (0x710, 0.020, z), (0x711, 0.020, z), (0x712, 0.020, z)]


def run_latency(a, n, load, ids=BRIDGE_IDS, decoder=False, label='latency'):
    """0x127 RES 를 100 ms 마다 뒤집어 프레임 송신 → /race/state 수신 지연을 잰다."""
    rig = Rig(a, ids=ids, decoder=decoder)
    lat = []
    sent = rx0 = 0
    try:
        frames = _load_frames() if load else []
        nxt = [time.time() + i * 0.001 for i in range(len(frames))]
        rig.rec.rows.clear()
        rig.rec.wheel_n, rig.rec.wheel_lag_max = 0, 0.0
        alive, on = 0, False
        t_tog = time.time() + 0.2
        t_res = time.time()
        k = 0
        cpu0 = _cpu(rig)
        t_start = time.time()
        while k < n:
            now = time.time()
            for i, (cid, per, data) in enumerate(frames):
                if now >= nxt[i]:
                    rig.bus.send(can.Message(arbitration_id=cid, data=data, is_extended_id=False))
                    nxt[i] += per
                    sent += 1
            if now >= t_res:
                toggle = now >= t_tog
                if toggle:
                    on = not on
                    t_tog = now + 0.1
                alive = (alive + 1) & 0xFF
                t_send = time.time()
                rig.bus.send(can.Message(arbitration_id=0x127, data=frs.encode_res(on, alive), is_extended_id=False))
                t_res = now + 0.02
                sent += 1
                if toggle:
                    want, before = on, len(rig.rec.rows)
                    t_lim = t_send + 0.2
                    while time.time() < t_lim:
                        hit = next((tr for tr, m in rig.rec.rows[before:] if m.res_active == want), None)
                        if hit is not None:
                            lat.append((hit - t_send) * 1e3)
                            break
                        for i, (cid, per, data) in enumerate(frames):
                            if time.time() >= nxt[i]:
                                rig.bus.send(can.Message(arbitration_id=cid, data=data, is_extended_id=False))
                                nxt[i] += per
                                sent += 1
                        time.sleep(0.0002)
                    else:
                        lat.append(float('inf'))
                    k += 1
            time.sleep(0.0003)
        dur = time.time() - t_start
        cpu = {k_: (v - cpu0[k_]) / dur * 100 for k_, v in _cpu(rig).items()}
        wheel_hz, wheel_lag = rig.rec.wheel_n / dur, rig.rec.wheel_lag_max
    finally:
        rig.close()
    finite = sorted(x for x in lat if math.isfinite(x))
    return {'label': label, 'n': len(lat), 'lost': len(lat) - len(finite),
            'median_ms': statistics.median(finite), 'p99_ms': finite[int(0.99 * (len(finite) - 1))],
            'max_ms': finite[-1], 'bus_fps': sent / dur, 'pub_cpu_pct': cpu.get('publisher', float('nan')),
            'dec_cpu_pct': cpu.get('decoder', float('nan')), 'wheel_hz': wheel_hz, 'wheel_lag_ms': wheel_lag * 1e3,
            'samples': lat}


def _cpu(rig):
    """프로세스별 누적 CPU 초."""
    out = {}
    tick = os.sysconf('SC_CLK_TCK')
    for name, p in (('publisher', rig.publisher), ('decoder', rig.decoder)):
        if p is None:
            continue
        try:
            with open(f'/proc/{p.pid}/stat') as f:
                st = f.read().rsplit(')', 1)[1].split()
            out[name] = (int(st[11]) + int(st[12])) / tick
        except OSError:
            pass
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--interface', default='virtual', choices=('virtual', 'socketcan'))
    ap.add_argument('--channel', default='', help='socketcan 이면 vcan* 필수')
    ap.add_argument('--only', default='', help='쉼표로 시나리오 이름 앞부분(S02,S09)')
    ap.add_argument('--out', default='')
    ap.add_argument('--latency', type=int, default=0, help='지연 측정 토글 횟수(0 = 안 함)')
    ap.add_argument('--load', action='store_true', help='지연 측정 때 실제 버스 부하(11-04 + 대회 신호)')
    ap.add_argument('--no-scenarios', action='store_true')
    ap.add_argument('--ids', default=BRIDGE_IDS, help='지연 측정 때 브리지 커널 필터')
    ap.add_argument('--label', default='')
    ap.add_argument('--domain', type=int, default=0, help='ROS_DOMAIN_ID, 0 = 프로세스 번호로 정함')
    a = ap.parse_args()
    if a.interface == 'socketcan' and not a.channel.startswith('vcan'):
        print('socketcan 은 vcan* 채널만 받는다(실차 금지)', file=sys.stderr)
        return 2
    os.environ['ROS_DOMAIN_ID'] = str(a.domain or 60 + os.getpid() % 40)
    os.environ['RCUTILS_LOGGING_MIN_SEVERITY'] = 'WARN'
    if a.out:
        os.makedirs(a.out, exist_ok=True)
    rclpy.init()
    rc = 0
    summary = []
    try:
        if not a.no_scenarios:
            only = [x for x in a.only.split(',') if x]
            for path in sorted(glob.glob(os.path.join(SCEN_DIR, '*.yaml'))):
                base = os.path.basename(path)
                if only and not any(base.startswith(x) for x in only):
                    continue
                name, checks, n = run_scenario(a, path)
                ok = all(c for _, c in checks)
                rc |= 0 if ok else 1
                print(f'{"PASS" if ok else "FAIL"}  {name}  ({n} 발행)')
                for what, c in checks:
                    print(f'   {"ok " if c else "NG "} {what}')
                summary.append({'scenario': name, 'pass': int(ok), 'published': n,
                                'checks': '; '.join(f'{w}={"ok" if c else "NG"}' for w, c in checks)})
        if a.latency:
            r = run_latency(a, a.latency, a.load, ids=a.ids, decoder=a.load, label=a.label or ('load' if a.load else 'idle'))
            lat_ok = r['median_ms'] <= 5.0 and r['lost'] == 0
            rc |= 0 if lat_ok else 1
            print(f"{'PASS' if lat_ok else 'FAIL'}  지연 {r['label']}: 중앙 {r['median_ms']:.2f} ms, p99 {r['p99_ms']:.2f}, "
                  f"최대 {r['max_ms']:.2f}, 유실 {r['lost']}/{r['n']}, 버스 {r['bus_fps']:.0f} 프레임/s, "
                  f"CPU publisher {r['pub_cpu_pct']:.1f} % decoder {r['dec_cpu_pct']:.1f} %, "
                  f"0x201→wheel_info {r['wheel_hz']:.1f} Hz 최대 지연 {r['wheel_lag_ms']:.1f} ms")
            if a.out:
                with open(os.path.join(a.out, f"latency_{r['label']}.csv"), 'w', newline='') as f:
                    w = csv.writer(f)
                    w.writerow(['t_index', 'latency_ms'])
                    w.writerows(enumerate(r['samples']))
                summary.append({'scenario': f"latency_{r['label']}", 'pass': int(lat_ok), 'published': r['n'],
                                'checks': f"median={r['median_ms']:.2f}ms p99={r['p99_ms']:.2f}ms max={r['max_ms']:.2f}ms "
                                          f"lost={r['lost']} bus={r['bus_fps']:.0f}fps pub_cpu={r['pub_cpu_pct']:.1f}% "
                                          f"dec_cpu={r['dec_cpu_pct']:.1f}% wheel={r['wheel_hz']:.1f}Hz "
                                          f"wheel_lag_max={r['wheel_lag_ms']:.1f}ms"})
        if a.out and summary:
            with open(os.path.join(a.out, 'summary.csv'), 'w', newline='') as f:
                w = csv.DictWriter(f, ['scenario', 'pass', 'published', 'checks'])
                w.writeheader()
                w.writerows(summary)
    finally:
        rclpy.try_shutdown()
    return rc


if __name__ == '__main__':
    sys.exit(main())
