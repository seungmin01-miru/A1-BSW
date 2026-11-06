#!/usr/bin/env python3
"""엔진룸 SIL(지시서 WP2·WP3 관문) — vcan0 에 가짜 차량(--plant real) + 실제 can_guard·브리지·디코더·어댑터를 띄워 판정한다.

  source /opt/ros/humble/setup.bash && source ros2_ws/install/setup.bash
  python3 bsw_test/09_ENGINE_ROOM/scripts/sil_engine_room.py --out <폴더>              # 전부
  python3 bsw_test/09_ENGINE_ROOM/scripts/sil_engine_room.py --only map,fit,circle --out <폴더>

  map     속도별 표: 시험용 표로 가속 % 가 속도에 따라 바뀌는지, 미측정 칸 표시, 차속 끊김 표시 (WP2)
  fit     adapter_long_check --plan → CAN 로그 + 가짜 GNSS → fit_long_map: v1 표는 급변으로 차단, 측정값을 표로 삼으면 통과 (WP3)
  circle  circle_test → fit_circle: 가짜 차량 조향비를 되찾는지 (WP3)

**차량·실제 CAN 에 닿지 않는다**(vcan* 만). 가짜 차량 숫자는 잠정이고 판정에 쓰지 않는다 — 도구가 맞게 도는지만 본다.
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import threading
import time
import uuid

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
CG = os.path.join(REPO, 'safety', 'can_guard')
sys.path[:0] = [os.path.join(CG, 'sil_tests'), CG, os.path.join(REPO, 'ros2_ws', 'src', 'a1_control_adapter')]

from _common import can_guard_cmd, wait_for_shm  # noqa: E402
from a1_control_adapter.conversion import LongMap2D  # noqa: E402

FAKE = os.path.join(REPO, 'sil', 'vcan', 'fake_a1_vehicle.py')
TRACK = os.path.join(REPO, 'ros2_ws', 'src', 'a1_control_adapter', 'config', 'track.yaml')
LONG_CHECK = os.path.join(REPO, 'bsw_test', 'common', 'scripts', 'adapter_long_check.py')
CIRCLE = os.path.join(REPO, 'bsw_test', 'common', 'scripts', 'circle_test.py')
FIT_LONG = os.path.join(REPO, 'tools', 'engine_room', 'fit_long_map.py')
FIT_CIRCLE = os.path.join(REPO, 'tools', 'engine_room', 'fit_circle.py')
LIMITS = ('--steer-limit-deg', '150', '--brake-limit-pct', '100', '--acc-limit-pct', '100', '--quiet')

# 시험용 표: 같은 요청(0.3 m/s²)에 속도마다 다른 %. 30 km/h 이상은 미측정.
TEST_MAP = dict(version='sil', source='sil_engine_room.py 시험용', speed_bins_kph=[0, 10, 20, 30], accel_bins_mps2=[-3, -1, 0, 0.3, 0.6],
                pedal_pct={0: [-90, -40, 0, 78, 90], 10: [-90, -40, 0, 84, 92], 20: [-90, -40, 0, 92, 96], 30: [None, None, 0, None, None]},
                deadband_mps2=0.1, deadband_off_mps2=0.03, launch_min_acc_pct=75, launch_speed_kph=3.0)


class Stack:
    """가짜 차량 + can_guard + (선택) 브리지·디코더·어댑터 + candump."""

    def __init__(self, a, d, adapter=True, map_file=None, fake_args=()):
        self.d, self.a = d, a
        os.makedirs(d, exist_ok=True)
        tag = uuid.uuid4().hex[:8]
        self.cmd_shm, self.hb_shm = f'er_cmd_{tag}', f'er_hb_{tag}'
        self.gnss = os.path.join(d, 'gnss.csv')
        self.can_log = os.path.join(d, 'candump.log')
        self.ev = os.path.join(d, 'can_guard_events.csv')
        self.p = {}
        self.env = dict(os.environ, ROS_DOMAIN_ID=str(a.domain))
        self.p['candump'] = subprocess.Popen(['candump', '-L', a.channel], stdout=open(self.can_log, 'w'), stderr=subprocess.DEVNULL)
        self.p['fake'] = subprocess.Popen([sys.executable, FAKE, '--channel', a.channel, '--physics', '--plant', 'real', '--gnss-csv', self.gnss, *fake_args],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.5)
        self.p['guard'] = subprocess.Popen(can_guard_cmd(self.cmd_shm, self.hb_shm, a.channel, 0.05, 0.02, (*LIMITS, '--event-log', self.ev)),
                                           stdout=subprocess.DEVNULL, stderr=open(os.path.join(d, 'can_guard.log'), 'w'))
        if not (wait_for_shm(self.cmd_shm) and wait_for_shm(self.hb_shm)):
            self.close()
            raise RuntimeError('can_guard 공유메모리 안 생김')
        if adapter:
            lg = open(os.path.join(d, 'nodes.log'), 'w')
            self.p['bridge'] = subprocess.Popen(
                [sys.executable, '-m', 'a1_can_bridge.can_raw_bridge', '--ros-args', '-p', f'channel:={a.channel}',
                 '-p', 'ids:=0x200,0x201,0x210'], env=self.env, stdout=lg, stderr=lg)
            self.p['decoder'] = subprocess.Popen([sys.executable, '-m', 'a1_can_bridge.a1_status_decoder'], env=self.env, stdout=lg, stderr=lg)
            argv = [sys.executable, '-m', 'a1_control_adapter.cmd_adapter', '--ros-args', '--params-file', TRACK, '-p', f'can_guard_dir:={CG}',
                    '-p', f'cmd_shm:={self.cmd_shm}', '-p', f'hb_shm:={self.hb_shm}']
            if map_file:
                argv += ['-p', f'long_map_file:={map_file}']
            self.p['adapter'] = subprocess.Popen(argv, env=self.env, stdout=lg, stderr=lg)
            time.sleep(2.0)

    def stop(self, name):
        p = self.p.pop(name, None)
        if p:
            p.send_signal(2)
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()

    def close(self):
        for n in ('adapter', 'decoder', 'bridge', 'guard', 'fake', 'candump'):
            self.stop(n)


def t_map(a, out):
    """WP2: 같은 요청에 속도별로 다른 %, 미측정 칸 표시, 차속 끊김."""
    d = os.path.join(out, 'map')
    os.makedirs(d, exist_ok=True)
    mp = os.path.join(d, 'test_map.yaml')
    with open(mp, 'w', encoding='utf-8') as f:
        yaml.safe_dump({'long_map': TEST_MAP}, f, allow_unicode=True)
    st = Stack(a, d, map_file=mp)
    import rclpy
    from a1_can_msgs.msg import AdapterStatus, ControllerOutput
    from std_srvs.srv import SetBool
    os.environ['ROS_DOMAIN_ID'] = str(a.domain)
    rclpy.init()
    node = rclpy.create_node('sil_map')
    pub = node.create_publisher(ControllerOutput, '/control/target', 10)
    rows = []

    def on_st(m):
        rows.append((time.time(), m.speed_used_kph, m.in_accel_mps2, m.out_acc_pct, m.out_brake_pct, m.extrapolated, m.speed_stale, m.long_map_version, m.long_map_clamped))
    node.create_subscription(AdapterStatus, '/control/adapter/status', on_st, 10)
    target = {'a': -1.0}
    stop = threading.Event()

    def pub_loop():
        while not stop.is_set():
            m = ControllerOutput()
            m.accel_mps2 = float(target['a'])
            pub.publish(m)
            time.sleep(0.05)     # 모델 실제 주기 20 Hz
    threading.Thread(target=pub_loop, daemon=True).start()

    def wait(sec):
        end = time.time() + sec
        while time.time() < end:
            rclpy.spin_once(node, timeout_sec=0.02)

    def speed_now():
        return rows[-1][1] if rows else 0.0
    try:
        wait(1.5)
        for name in ('/control/adapter/arm_steer', '/control/adapter/arm_long'):
            cli = node.create_client(SetBool, name)
            cli.wait_for_service(timeout_sec=5.0)
            fut = cli.call_async(SetBool.Request(data=True))
            rclpy.spin_until_future_complete(node, fut, timeout_sec=5.0)
            if not (fut.result() and fut.result().success):
                raise RuntimeError(f'{name} 실패: {fut.result()}')
        target['a'] = 0.3
        end = time.time() + 40
        while speed_now() < 27 and time.time() < end:
            wait(0.2)
        target['a'] = -3.0
        wait(6.0)
        n_before = len(rows)
        st.stop('fake')             # 0x201 끊김
        wait(1.0)
        after = rows[n_before:]
    finally:
        stop.set()
        node.destroy_node()
        rclpy.shutdown()
        st.close()
    with open(os.path.join(d, 'status.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['t', 'speed_used_kph', 'in_accel_mps2', 'out_acc_pct', 'out_brake_pct', 'extrapolated', 'speed_stale'])
        t0 = rows[0][0]
        w.writerows((round(r[0] - t0, 3), round(r[1], 2), r[2], round(r[3], 2), round(r[4], 2), int(r[5]), int(r[6])) for r in rows)
    model = LongMap2D(TEST_MAP)
    req = [r for r in rows if r[2] == 0.3 and r[3] > 0]
    res = []
    bad = []
    for r in req:
        model.reset()
        exp = model.to_pedals(0.3, r[1])[0]
        if abs(exp - r[3]) > 1.5:
            bad.append((r[1], r[3], exp))
    res.append(('어댑터 출력 = 표 이중선형(차속 사용)', bool(req) and len(bad) <= 2, f'표본 {len(req)}, 1.5 % 넘게 다름 {len(bad)}'))
    at = lambda lo, hi: [r[3] for r in req if lo <= r[1] < hi]    # noqa: E731
    lo_v, hi_v = at(4, 6), at(19, 21)
    res.append(('같은 요청에 속도별로 다른 %', bool(lo_v and hi_v) and sum(hi_v) / len(hi_v) - sum(lo_v) / len(lo_v) > 8,
                f'5 km/h {sum(lo_v) / max(len(lo_v), 1):.1f} %, 20 km/h {sum(hi_v) / max(len(hi_v), 1):.1f} %'))
    ex = [r for r in req if r[1] > 21]
    res.append(('미측정 칸 쓰면 extrapolated', bool(ex) and all(r[5] for r in ex) and not any(r[5] for r in req if r[1] < 19), f'20 km/h 위 {len(ex)} 표본'))
    res.append(('표 버전 표시', all(r[7] == 'sil' for r in rows), rows[-1][7]))
    res.append(('가속·브레이크 동시 0건', not any(r[3] > 0 and r[4] > 0 for r in rows), f'{len(rows)} 표본'))
    late = after[len(after) // 2:]
    res.append(('0x201 끊기면 speed_stale, 마지막 값 유지', bool(late) and all(r[6] for r in late), f'끊긴 뒤 {len(late)} 표본, 차속 {late[-1][1] if late else 0:.1f} km/h'))
    return res


PLAN_STEPS = [(0.13, 2.5), (0.3, 2.5), (0.57, 2.5), (-0.5, 2.0), (-1.0, 2.0), (-2.0, 1.5), (-3.0, 1.2)]


def t_fit(a, out):
    d = os.path.join(out, 'fit')
    st = Stack(a, d)
    plan = ','.join(f'14:{x}:{t}' for _ in range(4) for x, t in PLAN_STEPS)
    env = dict(st.env)
    try:
        lg = open(os.path.join(d, 'long_check.log'), 'w')
        rc = subprocess.call([sys.executable, LONG_CHECK, '--plan', plan, '--channel', a.channel, '--vmax-stop', '45', '--hold-end-s', '1'],
                             env=env, stdout=lg, stderr=lg, timeout=420)
    finally:
        st.close()
    res = [('adapter_long_check --plan 완주', rc == 0, f'rc {rc}')]
    t0 = time.monotonic()
    r1 = subprocess.run([sys.executable, FIT_LONG, '--can', st.can_log, '--gnss-csv', st.gnss, '--events', st.ev, '--out', os.path.join(d, 'v1')], capture_output=True, text=True)
    took = time.monotonic() - t0
    res.append(('v1 표(복사본)는 급변으로 차단(종료 3)', r1.returncode == 3 and '급변' in open(os.path.join(d, 'v1', 'UPDATE_BLOCKED.md'), encoding='utf-8').read(), r1.stdout.split('\n')[0][:60]))
    res.append(('표·그래프 1 분 안에(지시서 현장 목표)', took < 60, f'{took:.1f} s'))
    # 사람이 측정값을 승인한 표를 기존 표로 삼으면 통과해야 한다
    with open(os.path.join(REPO, 'ros2_ws', 'src', 'a1_control_adapter', 'config', 'long_map.yaml'), encoding='utf-8') as f:
        cfg = yaml.safe_load(f)['long_map']
    with open(os.path.join(d, 'v1', 'cells.csv')) as f:
        cells = [r for r in csv.DictReader(f) if r['used'] == '1']
    for r in cells:
        cfg['pedal_pct'][int(float(r['speed_kph']))][cfg['accel_bins_mps2'].index(float(r['accel_mps2']))] = float(r['pedal_mean'])
    for k, row in cfg['pedal_pct'].items():
        used = {cfg['accel_bins_mps2'].index(float(r['accel_mps2'])) for r in cells if int(float(r['speed_kph'])) == k}
        if used:
            cfg['pedal_pct'][k] = [v if (j in used or cfg['accel_bins_mps2'][j] == 0) else None for j, v in enumerate(row)]
    cur = os.path.join(d, 'approved_map.yaml')
    with open(cur, 'w', encoding='utf-8') as f:
        yaml.safe_dump({'long_map': cfg}, f, allow_unicode=True)
    r2 = subprocess.run([sys.executable, FIT_LONG, '--can', st.can_log, '--gnss-csv', st.gnss, '--events', st.ev, '--current', cur, '--out', os.path.join(d, 'v2')], capture_output=True, text=True)
    res.append(('측정값을 기존 표로 삼으면 통과·추천 파일', r2.returncode == 0 and os.path.exists(os.path.join(d, 'v2', 'long_map_recommended.yaml')), r2.stdout.split('\n')[0][:60]))
    if r2.returncode == 0:
        with open(os.path.join(d, 'v2', 'long_map_recommended.yaml'), encoding='utf-8') as f:
            LongMap2D(yaml.safe_load(f)['long_map'])
        res.append(('추천 표를 어댑터 검사가 받음', True, 'LongMap2D 통과'))
    return res


def t_circle(a, out, ratio=10.7):
    d = os.path.join(out, 'circle')
    st = Stack(a, d, adapter=False, fake_args=('--steer-ratio', str(ratio)))
    runs = os.path.join(d, 'runs')
    try:
        lg = open(os.path.join(d, 'circle_test.log'), 'w')
        rc = subprocess.call([sys.executable, CIRCLE, '--run', 'sil', '--handles', '75,150', '--laps', '1.1', '--kph', '5', '--out', runs, '--interface', 'socketcan',
                              '--channel', a.channel, '--cmd-shm', st.cmd_shm, '--hb-shm', st.hb_shm, '--sil'], stdout=lg, stderr=lg, timeout=600)
    finally:
        st.close()
    res = [('circle_test 완주(회차 4)', rc == 0, f'rc {rc}')]
    r = subprocess.run([sys.executable, FIT_CIRCLE, '--run-dir', runs, '--gnss-csv', st.gnss, '--out', os.path.join(d, 'fit')], capture_output=True, text=True)
    res.append(('fit_circle 통과', r.returncode == 0, r.stdout.split('\n')[2][:50] if r.stdout else r.stderr[:80]))
    path = os.path.join(d, 'fit', 'circle_fit.csv')
    rows = list(csv.DictReader(open(path))) if os.path.exists(path) else []
    errs = [abs(float(x['ratio']) - ratio) for x in rows]
    res.append((f'가짜 차량 조향비 {ratio} 를 되찾음(±0.5)', len(rows) == 4 and max(errs) < 0.5, ', '.join(f"{float(x['ratio']):.2f}" for x in rows)))
    res.append(('좌우 부호(좌 +)', all((float(x['handle_cmd_deg']) > 0) == (float(x['tire_deg']) > 0) for x in rows) and bool(rows), ''))
    speeds = [float(x['speed_kph']) for x in rows]
    res.append(('저속 유지 평균(≤ 8 km/h)', bool(speeds) and max(speeds) <= 8.0, ', '.join(f'{v:.1f}' for v in speeds)))
    peak = 0.0
    for f in os.listdir(runs):
        if f.endswith('_cmd.csv'):
            peak = max([peak] + [float(x['speed_kph']) for x in csv.DictReader(open(os.path.join(runs, f))) if x['speed_kph'] and x['phase'] == 'run'])
    res.append(('순간 최고도 상한 8 km/h 아래(세션 중단 안 남)', peak < 8.0, f'{peak:.1f} km/h'))
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--channel', default='vcan0')
    ap.add_argument('--only', default='map,fit,circle')
    ap.add_argument('--out', required=True)
    ap.add_argument('--domain', type=int, default=88)
    a = ap.parse_args()
    if not a.channel.startswith('vcan'):
        print('vcan* 채널만 받는다(실차 금지)', file=sys.stderr)
        return 2
    os.makedirs(a.out, exist_ok=True)
    fns = {'map': t_map, 'fit': t_fit, 'circle': t_circle}
    allres = {}
    rc = 0
    for k in a.only.split(','):
        print(f'▶ {k}', flush=True)
        try:
            res = fns[k](a, a.out)
        except Exception as e:   # noqa: BLE001 — 판정 결과로 남긴다
            res = [(f'{k} 실행', False, f'{type(e).__name__}: {e}')]
        allres[k] = res
        for what, ok, detail in res:
            print(f'  {"PASS" if ok else "FAIL"}  {what}  {detail}', flush=True)
            rc |= 0 if ok else 1
    with open(os.path.join(a.out, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump({k: [dict(what=w, ok=bool(o), detail=d) for w, o, d in v] for k, v in allres.items()}, f, ensure_ascii=False, indent=1)
    print('전체:', 'PASS' if rc == 0 else 'FAIL')
    return rc


if __name__ == '__main__':
    sys.exit(main())
