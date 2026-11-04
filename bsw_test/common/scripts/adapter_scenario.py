#!/usr/bin/env python3
"""adapter 경로 시나리오(실차·SIL): 목표 속도 + 타이어각 구간을 순서대로 → /control/target.

  --segments "0:5,20:10"   = 타이어 0° 로 5 s, 타이어 +20°(좌 최대) 로 10 s  (시간은 출발 뒤부터)
  --v-ref-kph 6            = 목표 차속(0x201 후륜 평균, PI)
끝나면 목표 −1.5 m/s² 로 정지 → 정지 후 조향 0 → 제동 유지(HOLD_END_S, 그사이 stop.sh).
안전: 차속 ≥ --vmax-stop 이면 즉시 −4 m/s²(브레이크 100 %).
기록: /control/test/event(JSON) — 단계 이벤트 + 0.2 s 마다 sample(v, v_ref, 목표 가속, 0x210, 0x200 조향).
"""
import argparse, json, os, sys, threading, time
import can
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from std_srvs.srv import SetBool
from a1_can_msgs.msg import ControllerOutput

sys.path.insert(0, os.path.expanduser('~/git/A1-BSW/tools/signal_audit'))
import dbc_lite  # noqa: E402

HOLD_A, STOP_A, ESTOP_A = -3.0, -1.5, -4.0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--segments', default='0:5,20:10')
    ap.add_argument('--v-ref-kph', type=float, default=6.0)
    ap.add_argument('--vmax-stop', type=float, default=10.0)
    ap.add_argument('--channel', default='kv0')
    ap.add_argument('--kp', type=float, default=0.8, help='m/s² per m/s')
    ap.add_argument('--ki', type=float, default=0.2)
    ap.add_argument('--a-ff', type=float, default=0.2, help='정속 유지 피드포워드 m/s²')
    ap.add_argument('--hold-end-s', type=float, default=5.0)
    a = ap.parse_args()
    segs = [(float(x.split(':')[0]), float(x.split(':')[1])) for x in a.segments.split(',')]

    db = dbc_lite.load(os.path.expanduser('~/git/A1-BSW/DBC/A1_dbc_fixed.dbc'))
    st = {'v': 0.0, 'steer_pos': None, 'brake_pos': None, 'acc_cmd': None, 'brk_cmd': None, 'steer_cmd': None}

    def rx():
        bus = can.Bus(channel=a.channel, interface='socketcan')
        while True:
            m = bus.recv(0.1)
            if m is None:
                continue
            if m.arbitration_id == 0x201:
                d = db[0x201].decode(m.data)
                st['v'] = (d['left_speed'] + d['right_speed']) / 2
            elif m.arbitration_id == 0x200:
                d = db[0x200].decode(m.data)
                st['brake_pos'], st['steer_pos'] = d['break_postion'], d['steer_postion']
            elif m.arbitration_id == 0x210:
                d = db[0x210].decode(m.data)
                st['acc_cmd'], st['brk_cmd'], st['steer_cmd'] = d['acc_command'], d['break_command'], d['steer_command']
    threading.Thread(target=rx, daemon=True).start()

    rclpy.init()
    node = Node('adapter_scenario')
    pub = node.create_publisher(ControllerOutput, '/control/target', 10)
    ev_pub = node.create_publisher(String, '/control/test/event', 10)
    tgt = {'a': HOLD_A, 'steer': 0.0, 'v_ref': 0.0, 'phase': 'init'}
    stop = threading.Event()

    def event(kind, **kw):
        d = {'t': time.time(), 'event': kind, 'phase': tgt['phase'], 'v_kph': round(st['v'], 2), 'v_ref_kph': tgt['v_ref'],
             'target_accel': round(tgt['a'], 3), 'target_tire_deg': tgt['steer'], 'steer_pos_wheel_deg': st['steer_pos'],
             'steer_cmd_0x210': st['steer_cmd'], 'acc_cmd_0x210': st['acc_cmd'], 'brake_cmd_0x210': st['brk_cmd'],
             'brake_pos': st['brake_pos'], **kw}
        ev_pub.publish(String(data=json.dumps(d, ensure_ascii=False)))
        if kind != 'sample':
            print(json.dumps(d, ensure_ascii=False), flush=True)
        return d

    def pub_loop():
        n = 0
        while not stop.is_set():
            msg = ControllerOutput()
            msg.header.stamp = node.get_clock().now().to_msg()
            msg.accel_mps2, msg.steer_tire_deg = float(tgt['a']), float(tgt['steer'])
            pub.publish(msg)
            n += 1
            if n % 10 == 0:
                event('sample')
            time.sleep(0.02)
    threading.Thread(target=pub_loop, daemon=True).start()

    def call(name):
        cli = node.create_client(SetBool, name)
        if not cli.wait_for_service(timeout_sec=3.0):
            raise SystemExit(f'{name} 없음')
        fut = cli.call_async(SetBool.Request(data=True))
        rclpy.spin_until_future_complete(node, fut, timeout_sec=3.0)
        r = fut.result()
        event('service', name=name, success=bool(r and r.success))
        if not (r and r.success):
            raise SystemExit(f'{name} 실패')

    integ = 0.0
    samples = []

    def ctrl_step(dt):
        nonlocal integ
        if st['v'] >= a.vmax_stop:
            tgt['a'] = ESTOP_A
            event('overspeed_brake')
            raise SystemExit('속도 상한')
        e = (tgt['v_ref'] - st['v']) / 3.6
        integ = max(-1.0, min(1.0, integ + e * dt))
        tgt['a'] = max(-1.5, min(0.8, a.a_ff + a.kp * e + a.ki * integ))

    try:
        time.sleep(1.0)
        call('/control/adapter/arm_steer')
        call('/control/adapter/arm_long')
        tgt['steer'] = segs[0][0]
        time.sleep(2.0)
        tgt['phase'], tgt['v_ref'] = 'launch', a.v_ref_kph
        event('start')
        t_launch = time.monotonic()
        t_move = None
        while t_move is None:
            ctrl_step(0.02)
            if st['v'] >= 0.3:
                t_move = time.monotonic()
                event('moved', lag_s=round(t_move - t_launch, 3))
            if time.monotonic() - t_launch > 8.0:
                raise SystemExit('8 s 안에 출발 못 함')
            time.sleep(0.02)
        t_seg = t_move
        for i, (deg, dur) in enumerate(segs):
            tgt['phase'], tgt['steer'] = f'seg{i}_tire{deg:+g}', deg
            event('segment', tire_deg=deg, dur_s=dur)
            while time.monotonic() - t_seg < dur:
                ctrl_step(0.02)
                samples.append((tgt['phase'], st['v'], st['steer_pos']))
                time.sleep(0.02)
            t_seg = time.monotonic()
        tgt['phase'], tgt['v_ref'], tgt['a'] = 'stop', 0.0, STOP_A
        v_b, t_b = st['v'], time.monotonic()
        while st['v'] > 0 and time.monotonic() - t_b < 10.0:
            if st['v'] >= a.vmax_stop:
                tgt['a'] = ESTOP_A
            time.sleep(0.02)
        tgt['a'] = HOLD_A
        event('stopped', v_at_brake_kph=round(v_b, 2), stop_s=round(time.monotonic() - t_b, 3))
        for ph in dict.fromkeys(p for p, _, _ in samples):
            vs = [v for p, v, _ in samples if p == ph]
            ss = [s for p, _, s in samples if p == ph and s is not None]
            tail = vs[len(vs) // 2:]
            event('segment_result', seg=ph, v_mean_2nd_half_kph=round(sum(tail) / len(tail), 2),
                  v_min_kph=round(min(tail), 2), v_max_kph=round(max(tail), 2),
                  steer_pos_mean_2nd_half=round(sum(ss[len(ss) // 2:]) / max(1, len(ss[len(ss) // 2:])), 1) if ss else None)
    finally:
        tgt['a'] = ESTOP_A if st['v'] > 0.5 else HOLD_A
        tgt['v_ref'] = 0.0
        t0 = time.monotonic()
        while st['v'] > 0 and time.monotonic() - t0 < 10.0:
            time.sleep(0.05)
        tgt['steer'] = 0.0
        tgt['phase'] = 'end_hold'
        event('end_hold_brake', hold_s=a.hold_end_s)
        time.sleep(a.hold_end_s)
        stop.set()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
