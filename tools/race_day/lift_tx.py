#!/usr/bin/env python3
"""리프트 송신 시험 전용 도구 — 실차에 0x210(USER_control_command)을 20 ms 주기로 보내며 한 줄 명령으로 조작.

목적은 "우리가 보낸 0x210 이 차량에 제대로 먹히는지" 확인 하나뿐이다. can_guard(상태머신·RT 설정)를 대신하지
않는다 — **차량을 리프트에 올려 바퀴가 떠 있고, E-stop 을 누를 사람이 있을 때만** 쓴다.
절차: tools/race_day/VEHICLE_MANUAL.md "C. 리프트 송신 시험".

안전장치
  - 시작 전 점검: 인터페이스가 UP 이고 listen-only 가 아닌지, 2초 동안 들어서 **다른 장치가 0x210 을 보내고
    있으면 시작 거부**, 차량 메시지(0x200/0x201)가 하나도 없으면 시작 거부(엉뚱한 인터페이스·비트레이트 방지)
  - 시작 상태: auto 전부 0(수동), 명령 0
  - 입력 한계(기본 조향 ±30°, 브레이크 60 %, 가속 10 %)를 넘는 입력은 거부(자르지 않고 거부)
  - 자동 원위치: 가속 2초, 조향·브레이크 5초가 지나면 0 으로 돌아감 — 다시 입력하면 시간 연장
  - 가속과 브레이크 동시 금지: 브레이크를 걸면 가속은 0, 브레이크가 걸린 동안 가속 입력은 거부
  - 실행 중 다른 0x210 송신자가 나타나면 즉시 안전 종료
  - 종료(q / Ctrl+C / SIGTERM / 입력 끝 / 오류) 시 auto 0 + 명령 0 을 0.2초 보낸 뒤 종료
  - SIGKILL·전원 차단은 막을 수 없다 → 그때는 차량 자체 타임아웃 동작(이걸 관측하는 게 C6 시험)

  python3 tools/race_day/lift_tx.py --channel can0
  명령: auto on|off  /  auto steer|brake|acc on|off  /  steer <deg>  /  brake <%>  /  acc <%>  /  zero
        status  /  wait <초>(스크립트용)  /  help  /  q
"""
import argparse
import os
import select
import signal
import subprocess
import sys
import threading
import time

try:
    import can
except ImportError:
    print('[lift_tx] python-can 이 없습니다 — pip install --user python-can', file=sys.stderr)
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from a1_proto import (FRAME_CONTROL_COMMAND, FRAME_CONTROL_INFO, FRAME_WHEEL_INFO,  # noqa: E402
                      decode_0x200, decode_0x201, encode_0x210)

EXIT_OK, EXIT_PRECHECK_FOREIGN, EXIT_PRECHECK_NO_VEHICLE, EXIT_IFACE, EXIT_FOREIGN_RUNTIME, EXIT_TX_FAIL = 0, 3, 4, 5, 6, 7
AXES = ('steer', 'brake', 'acc')


class Logger:
    def __init__(self, path):
        self.f = open(path, 'a', buffering=1) if path else None
        self.lock = threading.Lock()

    def __call__(self, msg):
        line = f'{time.time():.6f} {time.strftime("%H:%M:%S")} {msg}'
        with self.lock:
            print(f'[lift_tx] {msg}', file=sys.stderr, flush=True)
            if self.f:
                self.f.write(line + '\n')


class LineReader:
    """stdin 줄 읽기(타임아웃 있음). `select` + `sys.stdin.readline()` 조합은 여러 줄이 한꺼번에 들어오면(붙여넣기,
    스크립트) 두 번째 줄부터 Python 버퍼에 갇혀 다음 입력이 올 때까지 실행되지 않는다(2026-10-06 리허설에서 발견) —
    그래서 fd 에서 직접 읽어 자체 버퍼로 줄을 나눈다. readline(): 줄(str), 입력 끝 '', 타임아웃 None."""

    def __init__(self, fd):
        self.fd = fd
        self.buf = b''
        self.eof = False

    def readline(self, timeout):
        while True:
            if b'\n' in self.buf:
                line, self.buf = self.buf.split(b'\n', 1)
                return line.decode('utf-8', 'replace') + '\n'
            if self.eof:
                line, self.buf = self.buf, b''
                return line.decode('utf-8', 'replace')
            ready, _, _ = select.select([self.fd], [], [], timeout)
            if not ready:
                return None
            chunk = os.read(self.fd, 4096)
            if chunk:
                self.buf += chunk
            else:
                self.eof = True


class Command:
    """현재 명령 + 축별 설정 시각(자동 원위치용). TX 스레드와 입력 스레드가 같이 쓰므로 잠금."""

    def __init__(self, hold_s):
        self.lock = threading.Lock()
        self.hold_s = hold_s
        self.auto = {a: False for a in AXES}
        self.val = {a: 0.0 for a in AXES}
        self.set_at = {a: None for a in AXES}

    def set(self, axis, value):
        with self.lock:
            self.val[axis] = float(value)
            self.set_at[axis] = time.monotonic() if value != 0 else None

    def set_auto(self, axes, on):
        with self.lock:
            for a in axes:
                self.auto[a] = bool(on)

    def safe(self):
        with self.lock:
            for a in AXES:
                self.auto[a] = False
                self.val[a] = 0.0
                self.set_at[a] = None

    def snapshot(self, now):
        """(steer, brake, acc, auto×3), 이번에 자동 원위치된 축 목록."""
        expired = []
        with self.lock:
            for a in AXES:
                t = self.set_at[a]
                if t is not None and now - t > self.hold_s[a]:
                    self.val[a] = 0.0
                    self.set_at[a] = None
                    expired.append(a)
            snap = (self.val['steer'], self.val['brake'], self.val['acc'],
                    self.auto['steer'], self.auto['brake'], self.auto['acc'])
        return snap, expired


class Status:
    def __init__(self):
        self.lock = threading.Lock()
        self.info = None
        self.wheel = None
        self.info_t = self.wheel_t = None
        self.n_info = self.n_wheel = 0


def iface_problem(channel):
    """보낼 수 없는 상태면 이유 문자열, 괜찮으면 None. `ip` 출력만 읽는다(sudo 불필요, 아무것도 안 바꿈)."""
    try:
        out = subprocess.run(['ip', '-details', 'link', 'show', channel],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError) as e:
        return f'ip 명령 실패: {e}'
    return iface_problem_from_ip_output(out, channel)


def iface_problem_from_ip_output(out, channel):
    if not out.strip():
        return f'{channel} 인터페이스가 없습니다'
    if 'LISTEN-ONLY' in out:
        return (f'{channel} 이 listen-only 입니다 — 송신하려면: sudo ip link set {channel} down && '
                f'sudo ip link set {channel} type can bitrate <확정값> listen-only off && sudo ip link set {channel} up')
    first = out.splitlines()[0]
    if ',UP' not in first and '<UP' not in first:
        return f'{channel} 이 DOWN 상태입니다'
    return None


def precheck(bus, seconds, log):
    """seconds 동안 듣기만 한다. (0x210 개수, 0x200 개수, 0x201 개수, ID별 개수)."""
    counts = {}
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        m = bus.recv(timeout=0.05)
        if m is None or m.is_error_frame:
            continue
        counts[m.arbitration_id] = counts.get(m.arbitration_id, 0) + 1
    summary = ', '.join(f'0x{k:03x}×{v}' for k, v in sorted(counts.items())) or '(아무것도 없음)'
    log(f'시작 전 점검 {seconds:.1f}초 수신: {summary}')
    return counts.get(FRAME_CONTROL_COMMAND, 0), counts.get(FRAME_CONTROL_INFO, 0), counts.get(FRAME_WHEEL_INFO, 0)


def tx_loop(bus, cmd, status, period_s, stop, result, log):
    sent = errs = consec = late = 0
    next_t = time.monotonic()
    while not stop.is_set():
        now = time.monotonic()
        (steer, brake, acc, sa, ba, aa), expired = cmd.snapshot(now)
        for a in expired:
            log(f'자동 원위치: {a} → 0 (유지 시간 {cmd.hold_s[a]:.0f}초 경과)')
        data = encode_0x210(steer, brake, acc, sa, ba, aa)
        try:
            bus.send(can.Message(arbitration_id=FRAME_CONTROL_COMMAND, data=data, is_extended_id=False),
                     timeout=period_s / 2)
            sent += 1
            consec = 0
        except can.CanError as e:
            errs += 1
            consec += 1
            if consec in (1, 10):
                log(f'⚠️ 송신 실패 {consec}회 연속: {e}')
            if consec >= int(1.0 / period_s):
                log('🛑 1초 동안 송신이 전부 실패 — 버스 문제(bus-off·케이블·비트레이트). 종료합니다')
                result['code'] = EXIT_TX_FAIL
                stop.set()
                break
        # 수신 드레인(논블로킹, 한 주기 최대 32개)
        for _ in range(32):
            m = bus.recv(timeout=0)
            if m is None:
                break
            if m.is_error_frame:
                continue
            if m.arbitration_id == FRAME_CONTROL_INFO and len(m.data) == 8:
                with status.lock:
                    status.info, status.info_t = decode_0x200(m.data), time.monotonic()
                    status.n_info += 1
            elif m.arbitration_id == FRAME_WHEEL_INFO and len(m.data) == 8:
                with status.lock:
                    status.wheel, status.wheel_t = decode_0x201(m.data), time.monotonic()
                    status.n_wheel += 1
            elif m.arbitration_id == FRAME_CONTROL_COMMAND:
                # 우리 소켓은 자기 송신을 받지 않으므로(receive_own_messages=False) 이건 다른 송신자다
                log(f'🛑 다른 장치의 0x210 감지({bytes(m.data).hex()}) — 명령 충돌, 안전 종료합니다')
                result['code'] = EXIT_FOREIGN_RUNTIME
                stop.set()
                break
        next_t += period_s
        delay = next_t - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            late += 1
            if delay < -period_s:
                next_t = time.monotonic()
    result.update(sent=sent, errs=errs, late=late)


def send_safe_frames(bus, period_s, n, log):
    data = encode_0x210(0, 0, 0, False, False, False)
    ok = 0
    for _ in range(n):
        try:
            bus.send(can.Message(arbitration_id=FRAME_CONTROL_COMMAND, data=data, is_extended_id=False),
                     timeout=period_s / 2)
            ok += 1
        except can.CanError:
            pass
        time.sleep(period_s)
    log(f'안전 종료 프레임(auto 0, 명령 0) {ok}/{n}개 송신')


def print_status(cmd, status, log):
    (steer, brake, acc, sa, ba, aa), _ = cmd.snapshot(time.monotonic())
    log(f'명령: steer={steer:+.0f}° brake={brake:.0f}% acc={acc:.0f}% auto(S/B/A)={int(sa)}{int(ba)}{int(aa)}')
    now = time.monotonic()
    with status.lock:
        i, w, it, wt, ni, nw = status.info, status.wheel, status.info_t, status.wheel_t, status.n_info, status.n_wheel
    if i:
        log(f'차량 0x200({now - it:.2f}초 전, 누적 {ni}): 조향 위치 {i["steer_pos_deg"]:+.1f}°  브레이크 위치 '
            f'{i["brake_pos"]:.1f}  auto(S/B/A)={i["steer_auto"]}{i["brake_auto"]}{i["acc_auto"]}  '
            f'카운터 {i["steer_cnt"]}/{i["brake_cnt"]}/{i["acc_cnt"]}')
    else:
        log('차량 0x200: 아직 못 받음')
    if w:
        log(f'차량 0x201({now - wt:.2f}초 전, 누적 {nw}): 우 {w["right_kph"]:.1f} km/h {w["right_rpm"]} rpm / '
            f'좌 {w["left_kph"]:.1f} km/h {w["left_rpm"]} rpm')
    else:
        log('차량 0x201: 아직 못 받음')


def handle_line(line, cmd, status, lim, log):
    """한 줄 명령 처리. 종료 요청이면 True."""
    p = line.strip().split()
    if not p or p[0].startswith('#'):
        return False
    c = p[0].lower()
    try:
        if c in ('q', 'quit', 'exit'):
            return True
        if c == 'help':
            log(__doc__.split('명령:')[1].strip())
        elif c == 'status':
            print_status(cmd, status, log)
        elif c == 'wait' and len(p) == 2:
            time.sleep(float(p[1]))
        elif c == 'zero':
            for a in AXES:
                cmd.set(a, 0)
            log('명령 전부 0 (auto 상태는 그대로)')
        elif c == 'auto' and len(p) == 2 and p[1] in ('on', 'off'):
            cmd.set_auto(AXES, p[1] == 'on')
            log(f'auto 전 축 {p[1]}')
        elif c == 'auto' and len(p) == 3 and p[1] in AXES and p[2] in ('on', 'off'):
            cmd.set_auto([p[1]], p[2] == 'on')
            log(f'auto {p[1]} {p[2]}')
        elif c in AXES and len(p) == 2:
            v = float(p[1])
            lo, hi = lim[c]
            if not lo <= v <= hi:
                log(f'❌ 거부: {c} {v:g} 는 한계 [{lo:g}, {hi:g}] 밖 (값을 자르지 않고 무시)')
                return False
            if c == 'acc' and v > 0 and cmd.snapshot(time.monotonic())[0][1] > 0:
                log('❌ 거부: 브레이크가 걸린 동안은 가속 불가 — 먼저 brake 0')
                return False
            if c == 'brake' and v > 0:
                cmd.set('acc', 0)
            cmd.set(c, v)
            auto_on = cmd.snapshot(time.monotonic())[0][3 + AXES.index(c)]
            note = '' if auto_on else '  (⚠️ 이 축 auto off — 차량이 무시함)'
            log(f'{c} = {v:g}{note}')
        else:
            log(f'❓ 모르는 명령: {line.strip()}  (help)')
    except ValueError as e:
        log(f'❌ 입력 오류: {e}')
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--channel', required=True)
    ap.add_argument('--period-ms', type=float, default=20.0, help='0x210 송신 주기 (9/17 실측 21.5 ms)')
    ap.add_argument('--steer-limit-deg', type=float, default=30.0)
    ap.add_argument('--brake-limit-pct', type=float, default=60.0)
    ap.add_argument('--acc-limit-pct', type=float, default=10.0)
    ap.add_argument('--acc-hold-s', type=float, default=2.0)
    ap.add_argument('--steer-hold-s', type=float, default=5.0)
    ap.add_argument('--brake-hold-s', type=float, default=5.0)
    ap.add_argument('--precheck-s', type=float, default=2.0)
    ap.add_argument('--allow-no-vehicle', action='store_true',
                    help='차량 메시지(0x200/0x201)가 없어도 시작(리허설 전용, 실차에서 쓰지 말 것)')
    ap.add_argument('--log', default=None, help='기록 파일 (기본: ./lift_tx_<시각>.log)')
    a = ap.parse_args()

    if a.steer_limit_deg > 150 or a.brake_limit_pct > 100 or a.acc_limit_pct > 100:
        ap.error('한계가 DBC 범위를 넘습니다')
    log = Logger(a.log or f'lift_tx_{time.strftime("%Y%m%d_%H%M%S")}.log')
    period = a.period_ms / 1000.0
    lim = {'steer': (-a.steer_limit_deg, a.steer_limit_deg), 'brake': (0, a.brake_limit_pct),
           'acc': (0, a.acc_limit_pct)}
    log(f'시작: channel={a.channel} 주기={a.period_ms:g}ms 한계 조향±{a.steer_limit_deg:g}° '
        f'브레이크≤{a.brake_limit_pct:g}% 가속≤{a.acc_limit_pct:g}% 원위치 가속{a.acc_hold_s:g}s/'
        f'조향{a.steer_hold_s:g}s/브레이크{a.brake_hold_s:g}s')

    prob = iface_problem(a.channel)
    if prob:
        log(f'🛑 {prob}')
        return EXIT_IFACE
    try:
        bus = can.Bus(channel=a.channel, interface='socketcan', receive_own_messages=False)
    except Exception as e:  # noqa: BLE001
        log(f'🛑 {a.channel} 열기 실패: {e}')
        return EXIT_IFACE

    try:
        n210, n200, n201 = precheck(bus, a.precheck_s, log)
        if n210:
            log(f'🛑 다른 장치가 이미 0x210 을 보내고 있습니다({n210}개) — 원격조종 등 그 장치를 끄고 다시 실행')
            return EXIT_PRECHECK_FOREIGN
        if n200 == 0 and n201 == 0 and not a.allow_no_vehicle:
            log('🛑 차량 메시지(0x200/0x201)가 없습니다 — 인터페이스·비트레이트·배선 확인')
            return EXIT_PRECHECK_NO_VEHICLE

        cmd = Command({'steer': a.steer_hold_s, 'brake': a.brake_hold_s, 'acc': a.acc_hold_s})
        status = Status()
        stop = threading.Event()
        result = {'code': EXIT_OK}
        tx = threading.Thread(target=tx_loop, args=(bus, cmd, status, period, stop, result, log), daemon=True)

        def on_term(signum, frame):
            raise SystemExit(EXIT_OK)
        signal.signal(signal.SIGTERM, on_term)

        tx.start()
        log('송신 시작 — auto 0, 명령 0. "help" 로 명령 목록')
        interactive = sys.stdin.isatty()
        try:
            prompt_shown = False
            reader = LineReader(sys.stdin.fileno())
            while not stop.is_set():
                # 0.2초마다 깨어나 TX 스레드의 정지(송신 실패·다른 송신자)를 바로 반영한다
                if interactive and not prompt_shown:
                    sys.stderr.write('lift_tx> ')
                    sys.stderr.flush()
                    prompt_shown = True
                line = reader.readline(0.2)
                if line is None:
                    continue
                prompt_shown = False
                if not line:
                    break
                if stop.is_set():
                    break
                if handle_line(line, cmd, status, lim, log):
                    break
        except KeyboardInterrupt:
            print(file=sys.stderr)
            log('Ctrl+C')
        finally:
            # 안전 종료: TX 스레드가 살아 있으면 0.2초 동안 auto 0·명령 0 을 그 스레드가 보내게 하고, 죽었으면 직접 보낸다
            cmd.safe()
            if tx.is_alive() and not stop.is_set():
                time.sleep(10 * period)
                stop.set()
                tx.join(timeout=1.0)
                log('안전 종료: auto 0, 명령 0 을 0.2초간 송신 후 정지')
            else:
                stop.set()
                tx.join(timeout=1.0)
                if result.get('code') == EXIT_FOREIGN_RUNTIME:
                    # 다른 송신자(원격조종 등)가 차를 제어 중일 수 있다 — 우리가 auto 0 을 섞으면 그쪽 제어를
                    # 방해하므로 아무것도 더 보내지 않고 즉시 빠진다
                    log('다른 송신자가 있어 안전 종료 프레임도 보내지 않고 즉시 정지')
                elif result.get('code') != EXIT_TX_FAIL:
                    send_safe_frames(bus, period, 10, log)
            log(f'종료 — 송신 {result.get("sent", 0)}개, 실패 {result.get("errs", 0)}개, '
                f'주기 지각 {result.get("late", 0)}회')
        return result['code']
    finally:
        bus.shutdown()


if __name__ == '__main__':
    sys.exit(main())
