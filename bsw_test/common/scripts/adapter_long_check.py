#!/usr/bin/env python3
"""adapter 경로 종방향 빠른 확인(실차·SIL 공용): /control/target → a1_control_adapter → can_guard → 0x210.

순서: 목표 제동(BRAKE_A) 1 s → arm_steer·arm_long → 단계마다 [목표 가속 a → v ≥ V_TARGET 또는 STEP_S → 목표 제동 → 정지]
안전: 차속 ≥ VMAX_STOP 이면 즉시 목표 −4 m/s²(브레이크 100 %). 끝나면 제동 목표를 HOLD_END_S 동안 계속 낸다
      (그사이 stop.sh — 먼저 disarm). 차속은 0x201(kv0 미러)에서 직접 읽는다.
측정값은 /control/test/event(std_msgs/String, JSON)로 내보내 bag(record.sh)에 남긴다.

  source /opt/ros/humble/setup.bash && source ~/git/A1-BSW/ros2_ws/install/setup.bash
  python3 adapter_long_check.py --accels 0.3,0.6 [--channel kv0]
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

BRAKE_A, ESTOP_A = -3.0, -4.0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--accels', default='0.3,0.6', help='목표 가속 m/s² 목록(단계마다 한 번 출발·정지)')
    ap.add_argument('--channel', default='kv0', help='0x201 을 읽을 socketcan 채널(실차 = kv0 미러, SIL = vcan0)')
    ap.add_argument('--v-target', type=float, default=3.0, help='km/h — 닿으면 제동')
    ap.add_argument('--vmax-stop', type=float, default=6.0, help='km/h — 넘으면 즉시 −4 m/s²')
    ap.add_argument('--step-s', type=float, default=5.0)
    ap.add_argument('--hold-end-s', type=float, default=20.0)
    ap.add_argument('--move-max-s', type=float, default=2.0, help='출발 후 이 시간 지나면 제동')
    ap.add_argument('--brake-a', type=float, default=-3.0, help='단계 끝 목표 감속(m/s², 음수)')
    ap.add_argument('--steer-tire-deg', type=float, default=0.0, help='시험 내내 줄 타이어각(+좌). 정지·제동 중에 먼저 돌려 둔다')
    a = ap.parse_args()
    accels = [float(x) for x in a.accels.split(',')]

    db = dbc_lite.load(os.path.expanduser('~/git/A1-BSW/DBC/A1_dbc_2_fixed.dbc'))
    st = {'v': 0.0, 'steer_pos': None, 'brake_pos_pct': None, 'acc_cmd': None, 'brk_cmd': None}

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
                st['brake_pos_pct'], st['steer_pos'] = d['break_postion'], d['steer_postion']
            elif m.arbitration_id == 0x210:
                d = db[0x210].decode(m.data)
                st['acc_cmd'], st['brk_cmd'] = d['acc_command'], d['break_command']
    threading.Thread(target=rx, daemon=True).start()

    rclpy.init()
    node = Node('adapter_long_check')
    pub = node.create_publisher(ControllerOutput, '/control/target', 10)
    ev_pub = node.create_publisher(String, '/control/test/event', 10)
    target = {'a': BRAKE_A}
    stop = threading.Event()

    def pub_loop():
        while not stop.is_set():
            msg = ControllerOutput()
            msg.header.stamp = node.get_clock().now().to_msg()
            msg.accel_mps2 = float(target['a'])
            msg.steer_tire_deg = float(a.steer_tire_deg)
            pub.publish(msg)
            time.sleep(0.02)
    threading.Thread(target=pub_loop, daemon=True).start()

    def event(kind, **kw):
        d = {'t': time.time(), 'event': kind, 'v_kph': round(st['v'], 2), 'brake_pos_pct': st['brake_pos_pct'], 'steer_pos_wheel_deg': st['steer_pos'], 'steer_tire_target': a.steer_tire_deg,
             'acc_cmd_0x210': st['acc_cmd'], 'brake_cmd_0x210': st['brk_cmd'], 'target_accel': target['a'], **kw}
        ev_pub.publish(String(data=json.dumps(d, ensure_ascii=False)))
        print(json.dumps(d, ensure_ascii=False), flush=True)

    def call(name, val=True):
        cli = node.create_client(SetBool, name)
        if not cli.wait_for_service(timeout_sec=3.0):
            raise SystemExit(f'{name} 없음 — bringup(어댑터 포함) 확인')
        fut = cli.call_async(SetBool.Request(data=val))
        rclpy.spin_until_future_complete(node, fut, timeout_sec=3.0)
        r = fut.result()
        event('service', name=name, success=bool(r and r.success), message=r.message if r else 'timeout')
        if not (r and r.success):
            raise SystemExit(f'{name} 실패')

    def guard():
        if st['v'] >= a.vmax_stop:
            target['a'] = ESTOP_A
            event('overspeed_brake')
            raise SystemExit('속도 상한')

    try:
        time.sleep(1.0)
        call('/control/adapter/arm_steer')
        call('/control/adapter/arm_long')
        time.sleep(3.0 if a.steer_tire_deg else 1.5)   # 조향 120 °/s 로 먼저 돌림
        event('armed_braking')
        for acc in accels:
            target['a'] = 0.0
            time.sleep(1.5)   # 데드밴드 → 가속 0·브레이크 0(브레이크 풀림)
            event('release', step=acc)
            target['a'] = acc
            t0 = time.monotonic()
            t_move = t_tgt = None
            event('accel_start', step=acc)
            while time.monotonic() - t0 < a.step_s:
                guard()
                if t_move is None and st['v'] >= 0.3:
                    t_move = time.monotonic()
                    event('moved', step=acc, lag_s=round(t_move - t0, 3))
                if st['v'] >= a.v_target:
                    t_tgt = time.monotonic()
                    break
                if t_move and time.monotonic() - t_move > a.move_max_s:
                    break
                time.sleep(0.02)
            v_b = st['v']
            target['a'] = a.brake_a
            t_b = time.monotonic()
            vs = []
            while st['v'] > 0 and time.monotonic() - t_b < 10.0:
                guard()
                vs.append((time.monotonic(), st['v']))
                time.sleep(0.02)
            dec = None
            hi = [x for x in vs if x[1] <= v_b * 0.8]; lo = [x for x in vs if x[1] <= v_b * 0.2]
            if hi and lo and lo[0][0] > hi[0][0]:
                dec = round((hi[0][1] - lo[0][1]) / 3.6 / (lo[0][0] - hi[0][0]), 3)
            target['a'] = BRAKE_A
            event('step_result', step=acc, moved=t_move is not None,
                  lag_s=round(t_move - t0, 3) if t_move else None,
                  to_v_target_s=round(t_tgt - t0, 3) if t_tgt else None,
                  v_at_brake_kph=round(v_b, 2), stop_s=round(time.monotonic() - t_b, 3), brake_target=a.brake_a, decel_80_20_mps2=dec,
                  mean_accel_mps2=round(v_b / 3.6 / (t_b - t_move), 3) if t_move and t_b > t_move else None)
            time.sleep(1.0)
    finally:
        target['a'] = ESTOP_A if st['v'] > 0 else BRAKE_A
        event('end_hold_brake', hold_s=a.hold_end_s)
        t_end = time.monotonic() + a.hold_end_s
        while time.monotonic() < t_end:
            time.sleep(0.2)
        stop.set()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
