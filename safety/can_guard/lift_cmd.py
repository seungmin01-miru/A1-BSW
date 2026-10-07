#!/usr/bin/env python3
"""리프트 송신 시험용 명령 도구 — can_guard 앞에 서는 **가짜 제어 노드 + 가짜 인지 프로세스**.

진짜 ROS2 제어 노드·인지 프로세스가 아직 없으므로, 사람이 한 줄 명령으로 can_guard 의 CommandChannel 에 명령을
쓰고(50 Hz), HeartbeatChannel 에 하트비트도 대신 보낸다. 차량으로 나가는 프레임은 전부 can_guard 가 만든다 —
이 도구는 버스에 **아무것도 보내지 않는다**(상태 표시용으로 듣기만 함).

  python3 safety/can_guard/can_guard.py --channel can0 --steer-limit-deg 15 --brake-limit-pct 60 \
      --acc-limit-pct 10 --status-interval-s 1          # 터미널 1 (먼저)
  python3 safety/can_guard/lift_cmd.py --channel can0     # 터미널 2

명령(입력 한계·자동 원위치·브레이크/가속 인터록은 tools/race_day/lift_tx.py 와 같은 코드):
  auto on|off  /  auto steer|brake|acc on|off  /  steer <deg>  /  brake <%>  /  acc <%>  /  zero
  perception on|off   — 하트비트 끊기(off) → can_guard DEGRADED → 2초 뒤 STOPPED 시험
  status  /  wait <초>  /  help  /  q

이중 한계: 이 도구의 입력 한계(기본 30° / 60 % / 10 %) + can_guard 의 운용 한계(--*-limit-*) — 둘 다 같은 값으로.
종료(q / Ctrl+C / 입력 끝): 명령을 멈춘다 → can_guard 가 50 ms 뒤 HOLDING(조향 고정·가속 0·브레이크 램프)
→ 2초 뒤 STOPPED. 즉 이 도구를 끄는 것 자체가 P-1(제어 노드 죽음) 시험이다. 수동으로 돌려놓으려면 끄기 전에
`auto off` 를 먼저 입력해 can_guard 가 auto 0 을 보내게 한다.
"""
import argparse
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'race_day'))
import lift_tx  # noqa: E402  (입력 처리·한계·자동 원위치 로직 재사용)
from protocol import Command, CommandChannel, HeartbeatChannel  # noqa: E402
from protocol import HEARTBEAT_SHM_NAME_DEFAULT, SHM_NAME_DEFAULT  # noqa: E402


def writer_loop(ch, hb, cmd, perception_on, period_s, stop, log):
    next_t = time.monotonic()
    while not stop.is_set():
        (steer, brake, acc, sa, ba, aa), expired = cmd.snapshot(time.monotonic())
        for a in expired:
            log(f'자동 원위치: {a} → 0 (유지 시간 {cmd.hold_s[a]:.0f}초 경과)')
        ch.write(Command(steer_auto=sa, brake_auto=ba, acc_auto=aa,
                         steer_cmd_deg=steer, brake_cmd_pct=brake, acc_cmd_pct=acc))
        if perception_on.is_set():
            hb.beat()
        next_t += period_s
        d = next_t - time.monotonic()
        if d > 0:
            time.sleep(d)
        else:
            next_t = time.monotonic()


def rx_loop(bus, status, last_tx, stop):
    """버스를 듣기만 한다 — 차량 0x200/0x201 과 can_guard 가 실제로 보낸 0x210 을 상태 표시용으로."""
    from a1_proto import decode_0x200, decode_0x201, decode_0x210
    while not stop.is_set():
        m = bus.recv(timeout=0.1)
        if m is None or m.is_error_frame or len(m.data) < 8:
            continue
        with status.lock:
            if m.arbitration_id == 0x200:
                status.info, status.info_t = decode_0x200(m.data), time.monotonic()
                status.n_info += 1
            elif m.arbitration_id == 0x201:
                status.wheel, status.wheel_t = decode_0x201(m.data), time.monotonic()
                status.n_wheel += 1
            elif m.arbitration_id == 0x210:
                last_tx['d'], last_tx['t'] = decode_0x210(m.data), time.monotonic()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--channel', default='', help='상태 표시용으로 들을 CAN 채널(비우면 표시 없음). 송신은 안 함')
    ap.add_argument('--interface', default='socketcan', choices=('socketcan', 'kvaser'),
                    help='can_guard 와 같은 값으로(kvaser 면 --channel 은 CANlib 채널 번호)')
    ap.add_argument('--bitrate', type=int, default=500000, help='kvaser 에서만 사용 — can_guard 와 같은 값')
    ap.add_argument('--cmd-shm', default=SHM_NAME_DEFAULT)
    ap.add_argument('--hb-shm', default=HEARTBEAT_SHM_NAME_DEFAULT)
    ap.add_argument('--period-ms', type=float, default=20.0)
    ap.add_argument('--steer-limit-deg', type=float, default=15.0,
                    help='±deg. 기본 15 = 2026-10-07 리프트에서 시험한 최대(raw 150)')
    ap.add_argument('--brake-limit-pct', type=float, default=60.0)
    ap.add_argument('--acc-limit-pct', type=float, default=10.0)
    ap.add_argument('--acc-hold-s', type=float, default=2.0)
    ap.add_argument('--steer-hold-s', type=float, default=5.0)
    ap.add_argument('--brake-hold-s', type=float, default=5.0)
    ap.add_argument('--no-perception', action='store_true', help='하트비트를 처음부터 보내지 않음')
    ap.add_argument('--log', default=None, help='기록 파일 (기본: ./lift_cmd_<시각>.log)')
    a = ap.parse_args()

    log = lift_tx.Logger(a.log or f'lift_cmd_{time.strftime("%Y%m%d_%H%M%S")}.log')
    try:
        ch = CommandChannel.open(a.cmd_shm)
        hb = HeartbeatChannel.open(a.hb_shm)
    except FileNotFoundError:
        log('🛑 can_guard 의 공유메모리가 없습니다 — can_guard.py 를 먼저 실행하세요')
        return 2
    lim = {'steer': (-a.steer_limit_deg, a.steer_limit_deg), 'brake': (0, a.brake_limit_pct),
           'acc': (0, a.acc_limit_pct)}
    cmd = lift_tx.Command({'steer': a.steer_hold_s, 'brake': a.brake_hold_s, 'acc': a.acc_hold_s})
    status = lift_tx.Status()
    last_tx = {'d': None, 't': None}
    perception_on = threading.Event()
    if not a.no_perception:
        perception_on.set()
    stop = threading.Event()
    period = a.period_ms / 1000.0

    bus = None
    if a.channel:
        import can
        if a.interface == 'socketcan':
            bus = can.Bus(channel=a.channel, interface='socketcan', receive_own_messages=False)
        else:
            # Kvaser: 같은 채널의 다른 핸들(can_guard)이 보낸 0x210 을 보려면 로컬 TX 에코를 켜야 한다
            # (python-can kvaser 의 receive_own_messages). 이 도구는 송신하지 않는다.
            import kvaser_compat
            kvaser_compat.apply()
            bus = can.Bus(channel=a.channel, interface='kvaser', bitrate=a.bitrate, receive_own_messages=True)
        threading.Thread(target=rx_loop, args=(bus, status, last_tx, stop), daemon=True).start()
    w = threading.Thread(target=writer_loop, args=(ch, hb, cmd, perception_on, period, stop, log), daemon=True)
    w.start()
    log(f'시작: {a.cmd_shm}/{a.hb_shm} 에 {a.period_ms:g} ms 로 씀, 하트비트 {"on" if perception_on.is_set() else "off"}, '
        f'한계 조향±{a.steer_limit_deg:g}° 브레이크≤{a.brake_limit_pct:g}% 가속≤{a.acc_limit_pct:g}%. auto 0·명령 0')

    def show_status():
        lift_tx.print_status(cmd, status, log)
        if last_tx['d']:
            d = last_tx['d']
            log(f'can_guard 가 보낸 0x210({time.monotonic() - last_tx["t"]:.2f}초 전): 조향 {d["steer_deg"]:+.1f}°(raw {d["steer_raw"]:+d}) '
                f'브레이크 {d["brake_pct"]}% 가속 {d["acc_pct"]}% auto {d["steer_auto"]}{d["brake_auto"]}{d["acc_auto"]}')
        elif bus:
            log('can_guard 의 0x210: 아직 안 보임(can_guard 실행 중인지 확인)')
        log(f'하트비트: {"on" if perception_on.is_set() else "off"}')

    interactive = sys.stdin.isatty()
    try:
        prompt_shown = False
        reader = lift_tx.LineReader(sys.stdin.fileno())   # 여러 줄 붙여넣기에도 안전한 줄 읽기
        while True:
            if interactive and not prompt_shown:
                sys.stderr.write('lift_cmd> ')
                sys.stderr.flush()
                prompt_shown = True
            line = reader.readline(0.2)
            if line is None:
                continue
            prompt_shown = False
            if not line:
                break
            p = line.strip().split()
            if p[:1] == ['perception'] and len(p) == 2 and p[1] in ('on', 'off'):
                (perception_on.set if p[1] == 'on' else perception_on.clear)()
                log(f'하트비트 {p[1]}' + (' — can_guard 가 0.5초 뒤 DEGRADED, 2초 더 지나면 STOPPED'
                                         if p[1] == 'off' else ''))
                continue
            if p[:1] == ['status']:
                show_status()
                continue
            if p[:1] == ['help']:
                log(__doc__.split('명령(')[1].split('이중 한계')[0].strip())
                continue
            if lift_tx.handle_line(line, cmd, status, lim, log):
                break
    except KeyboardInterrupt:
        print(file=sys.stderr)
        log('Ctrl+C')
    finally:
        stop.set()
        w.join(timeout=1.0)
        ch.close()
        hb.close()
        if bus is not None:
            bus.shutdown()
        log('종료 — 명령 쓰기를 멈췄다. can_guard 는 50 ms 뒤 HOLDING, 2초 뒤 STOPPED 로 간다(재시작해야 풀림)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
