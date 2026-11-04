#!/usr/bin/env python3
"""02 LON-PROF 프로파일 생성기 — yaml 한 장대로 can_guard 에 명령을 자동으로 넣는다(03·04·07 공용).

lift_cmd.py 와 같은 자리(가짜 제어 노드 + 가짜 인지 하트비트)에 선다: can_guard 의 CommandChannel 에 50 Hz 로 쓰고,
버스에는 **아무것도 보내지 않는다**(0x200/0x201/0x210 은 듣기만 — 차속 감시·로그용). lift_cmd 와 동시에 못 쓴다.

  # 1) 검사만(차 없이): 상한 위반·조향 속도·가속/브레이크 동시 여부 + 시간표 출력
  python3 bsw_test/common/scripts/profile_gen.py check bsw_test/02_LON_profile/profiles/acc_step.yaml --var acc=15

  # 2) 실행(bringup --no-adapter 뒤, lift_cmd 대신)
  python3 bsw_test/common/scripts/profile_gen.py run bsw_test/02_LON_profile/profiles/acc_step.yaml --var acc=15 \
      --run 20261105_LON-ACC-01_r4 --interface kvaser --channel 0

실행 흐름
  대기   start 값(기본 브레이크 30, 가속 0, auto 111)을 쓰면서 기다림.  Enter = 시작,  q = 취소
  실행   segments 순서대로.  아무 줄(Enter) / Ctrl+C = 중단 → 끝내기
         자동 중단: 차속 > limits.max_speed_kph, 0x201 0.3 s 이상 안 들어옴, until_speed 시간 초과
  끝내기 가속 즉시 0, 조향 유지, 브레이크 end.brake(기본 100) 까지 end.ramp_s 동안 → 유지
         q / Ctrl+C = 생성기 종료 → can_guard 가 50 ms 뒤 HOLDING(브레이크 그대로) → 2 s 뒤 STOPPED. 그다음 stop.sh.

남기는 것(--out, 기본 ~/a1_race_capture/track_latest):
  <run>_cmd.csv      매 주기 명령값 + 들은 차속·0x200·0x210 (02 판정: 0x210 과 1주기 안 일치)
  <run>_gen.log      이벤트(시작·중단 이유·끝)
  <run>_profile.yaml 실제 쓴 설정(vars 적용) — 같은 회차를 그대로 재현
  notes.txt          "<run> 시작 / 끝(이유)" 한 줄씩 (bringup 메모와 같은 파일)
"""
import argparse
import csv
import os
import queue
import select
import signal
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'safety', 'can_guard'))
sys.path.insert(0, os.path.join(REPO, 'tools', 'race_day'))
import profile_lib as pl  # noqa: E402

SPEED_STALE_S = 0.3
STATUS_EVERY_S = 0.5


def load_yaml(path):
    import yaml
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f)


class Listener:
    """버스 듣기만: 0x201 차속(후륜 좌우 평균), 0x200 조향·브레이크 위치, 0x210(can_guard 가 실제로 보낸 것)."""

    def __init__(self, bus):
        self.bus = bus
        self.lock = threading.Lock()
        self.speed = self.speed_t = None
        self.s200 = self.s200_t = None
        self.s210 = self.s210_t = None
        self.stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        from a1_proto import decode_0x200, decode_0x201, decode_0x210
        while not self.stop.is_set():
            m = self.bus.recv(timeout=0.1)
            if m is None or m.is_error_frame or len(m.data) < 8:
                continue
            now = time.monotonic()
            with self.lock:
                if m.arbitration_id == 0x201:
                    w = decode_0x201(m.data)
                    self.speed, self.speed_t = (w['left_kph'] + w['right_kph']) / 2, now
                elif m.arbitration_id == 0x200:
                    self.s200, self.s200_t = decode_0x200(m.data), now
                elif m.arbitration_id == 0x210:
                    self.s210, self.s210_t = decode_0x210(m.data), now

    def snap(self):
        now = time.monotonic()
        with self.lock:
            age = None if self.speed_t is None else now - self.speed_t
            return self.speed, age, self.s200, self.s210


def open_bus(a):
    import can
    if a.interface == 'socketcan':
        return can.Bus(channel=a.channel, interface='socketcan', receive_own_messages=False)
    import kvaser_compat   # lift_cmd 와 같은 이유로 핸들 1개 + 로컬 에코(can_guard 의 0x210 을 보려고)
    kvaser_compat.apply()
    return can.Bus(channel=int(a.channel), interface='kvaser', bitrate=a.bitrate, receive_own_messages=True,
                   single_handle=True)


def other_sender_running():
    """명령을 쓰는 다른 도구(lift_cmd·lift_tx·profile_gen run). pgrep -f 는 그 이름이 든 셸 명령줄까지 잡으므로
    /proc 의 argv 를 직접 본다: 실행 파일이 python 이고 그 스크립트 인자가 해당 파일인 프로세스만."""
    me = os.getpid()
    found = []
    for pid in filter(str.isdigit, os.listdir('/proc')):
        if int(pid) == me:
            continue
        try:
            with open(f'/proc/{pid}/cmdline', 'rb') as f:
                argv = [x.decode('utf-8', 'replace') for x in f.read().split(b'\0') if x]
        except OSError:
            continue
        if not argv or not os.path.basename(argv[0]).startswith('python'):
            continue
        scripts = [x for x in argv[1:3] if x.endswith('.py')]
        if not scripts:
            continue
        name = os.path.basename(scripts[0])
        if name in ('lift_cmd.py', 'lift_tx.py') or (name == 'profile_gen.py' and 'run' in argv):
            found.append(f'{pid} {" ".join(argv)}')
    return found


def cmd_check(a):
    prof = pl.build(load_yaml(a.profile), pl.parse_var_overrides(a.var))
    print(pl.summary(prof))
    errs = pl.validate(prof)
    if a.plan_csv:
        with open(a.plan_csv, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['tick', 't_s', 'seg', 'label', 'steer_cmd_deg', 'brake_cmd_pct', 'acc_cmd_pct'])
            for k, t, seg, lab, v in prof.samples():
                w.writerow([k, f'{t:.3f}', seg, lab, f'{v["steer"]:.3f}', f'{v["brake"]:.3f}', f'{v["acc"]:.3f}'])
        print(f'시간표 → {a.plan_csv}')
    if errs:
        print('❌ 상한 위반:')
        for e in errs:
            print('  ' + e)
        return 1
    print('✅ 검사 통과')
    return 0


def cmd_run(a):
    import yaml
    from protocol import Command, CommandChannel, HeartbeatChannel

    cfg = load_yaml(a.profile)
    prof = pl.build(cfg, pl.parse_var_overrides(a.var))
    errs = pl.validate(prof)
    print(pl.summary(prof), file=sys.stderr)
    if errs:
        print('🛑 상한 위반 — 실행 안 함:\n  ' + '\n  '.join(errs), file=sys.stderr)
        return 2
    others = other_sender_running()
    if others:
        print('🛑 다른 명령 도구 실행 중(명령 쓰는 쪽이 둘이 됨):\n  ' + '\n  '.join(others), file=sys.stderr)
        return 2
    speed_check = not (a.no_speed_check or a.sil)
    if speed_check and not a.channel:
        print('🛑 차속 감시에 --channel 필요 (SIL 에서만 --no-speed-check)', file=sys.stderr)
        return 2

    out = os.path.expanduser(a.out)
    os.makedirs(out, exist_ok=True)
    base = os.path.join(out, a.run)
    if os.path.exists(base + '_cmd.csv'):
        print(f'🛑 {base}_cmd.csv 이미 있음 — 회차 이름(--run)을 바꾸세요(덮어쓰지 않음)', file=sys.stderr)
        return 2
    try:
        ch = CommandChannel.open(a.cmd_shm)
        hb = HeartbeatChannel.open(a.hb_shm)
    except FileNotFoundError:
        print('🛑 can_guard 공유메모리 없음 — bringup(--no-adapter) 먼저', file=sys.stderr)
        return 2

    resolved = dict(cfg)
    resolved['vars'] = prof.vars
    with open(base + '_profile.yaml', 'w', encoding='utf-8') as f:
        f.write(f'# {a.run} — {time.strftime("%F %T")} 실행 시 실제 설정 (원본: {os.path.abspath(a.profile)})\n')
        yaml.safe_dump(resolved, f, allow_unicode=True, sort_keys=False)
    glog = open(base + '_gen.log', 'a', buffering=1)

    def log(msg):
        line = f'{time.time():.6f} {time.strftime("%H:%M:%S")} {msg}'
        print(f'[gen] {msg}', file=sys.stderr, flush=True)
        glog.write(line + '\n')

    def note(msg):
        with open(os.path.join(out, 'notes.txt'), 'a') as f:
            f.write(f'{time.time():.6f} {time.strftime("%H:%M:%S")} {msg}\n')

    bus = listener = None
    if a.channel:
        bus = open_bus(a)
        listener = Listener(bus)

    events = queue.Queue()

    def on_sigint(*_):
        events.put('SIGINT')
    signal.signal(signal.SIGINT, on_sigint)
    signal.signal(signal.SIGTERM, on_sigint)

    def stdin_loop():
        fd = sys.stdin.fileno()
        buf = b''
        while True:
            try:
                r, _, _ = select.select([fd], [], [], 0.2)
            except (OSError, ValueError):
                return
            if not r:
                continue
            chunk = os.read(fd, 1024)
            if not chunk:
                events.put('EOF')
                return
            buf += chunk
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                events.put(line.decode('utf-8', 'replace').strip())
    if not a.sil:
        threading.Thread(target=stdin_loop, daemon=True).start()

    auto = prof.auto
    period = prof.period_s
    csvf = open(base + '_cmd.csv', 'w', newline='', buffering=1)
    w = csv.writer(csvf)
    w.writerow(['tick', 'mono_s', 'wall_s', 'phase', 't_prof_s', 'seg', 'label',
                'steer_cmd_deg', 'brake_cmd_pct', 'acc_cmd_pct', 'auto',
                'speed_kph', 'speed_age_s', 'steer_pos_deg', 'brake_pos', 'tx210_steer_deg', 'tx210_brake_pct',
                'tx210_acc_pct', 'tx210_auto'])
    auto_s = f'{int(auto["steer"])}{int(auto["brake"])}{int(auto["acc"])}'
    log(f'시작 {a.run}: {a.profile} vars={prof.vars} → {out}  (차속 감시 {"on" if speed_check else "OFF(SIL)"})')

    phase = 'pre'
    pi = 0            # 지금 구간
    k_in = 0          # 구간 안 틱
    k_run = 0         # 실행 단계 틱(시간표 시각 = k_run × period)
    end_from = end_k0 = None
    end_reason = None
    cur = dict(prof.start)
    tick = 0
    last_status = 0.0
    pre_hint = 0.0
    sil_quit_at = None
    rc = 0
    if a.sil:
        events.put('')   # 바로 시작

    def go_end(reason, value):
        nonlocal phase, end_from, end_k0, end_reason
        phase, end_from, end_k0, end_reason = 'end', dict(value), tick, reason
        log(f'끝내기 단계: {reason} — 가속 0, 브레이크 → {prof.end_brake:g}% ({prof.end_ramp_s:g} s), 조향 {value["steer"]:+.1f}° 유지. '
            f'q 또는 Ctrl+C 로 생성기 종료')
        note(f'{a.run} 끝 ({reason}) t={k_run * period:.2f}s')

    next_t = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            speed, age, s200, s210 = listener.snap() if listener else (None, None, None, None)
            speed_ok = speed is not None and age is not None and age <= SPEED_STALE_S
            # ── 입력 ──
            evs = []
            while True:
                try:
                    evs.append(events.get_nowait())
                except queue.Empty:
                    break
            for ev in evs:
                if phase == 'pre':
                    if ev in ('q', 'SIGINT', 'EOF'):
                        log('대기 중 취소 — 실행 안 함')
                        rc = 1
                        raise StopIteration
                    if ev == '':
                        if speed_check and not speed_ok:
                            log('⚠️ 0x201 차속이 안 보임 — 시작 거부(버스·Kvaser 확인)')
                            continue
                        if speed_check and speed > prof.limits['max_speed_kph']:
                            log(f'⚠️ 차속 {speed:.1f} > 상한 — 시작 거부')
                            continue
                        phase = 'run'
                        log('▶ 실행 시작')
                        note(f'{a.run} 시작 ({os.path.basename(a.profile)} {prof.vars})')
                elif phase == 'run':
                    go_end('사용자 중단' if ev != 'SIGINT' else 'Ctrl+C 중단', cur)
                    break
                else:
                    if ev in ('q', 'SIGINT', 'EOF'):
                        raise StopIteration
                    log('끝내기 단계 — 끝내려면 q')
            # ── 명령 계산 ──
            if phase == 'pre':
                cur = dict(prof.start)
                if not a.sil and now - pre_hint > 5:
                    pre_hint = now
                    print(f'[gen] 대기 중 (브레이크 {cur["brake"]:g}%, auto {auto_s}). 안전 "비었음" 확인 후 Enter = 시작, q = 취소'
                          + (f'   차속 {speed:.1f} km/h' if speed_ok else '   차속 안 보임'), file=sys.stderr)
            elif phase == 'run':
                if speed_check and not speed_ok:
                    go_end('0x201 차속 끊김', cur)
                elif speed_check and speed > prof.limits['max_speed_kph']:
                    go_end(f'차속 {speed:.1f} km/h > 상한 {prof.limits["max_speed_kph"]:g}', cur)
                else:
                    # 구간 넘기기(길이 0 구간은 건너뜀)
                    while pi < len(prof.prims):
                        p = prof.prims[pi]
                        if p.kind == 'until_speed':
                            if speed_ok and speed >= p.kph:
                                log(f'seg{p.seg} until_speed: 차속 {speed:.1f} ≥ {p.kph:g} — 다음 구간')
                            elif k_in * period >= p.dur - 1e-9:
                                go_end(f'seg{p.seg} until_speed 시간 초과 ({p.dur:g} s 안에 {p.kph:g} km/h 못 감)', cur)
                                break
                            else:
                                break
                        elif k_in < int(round(p.dur / period)):
                            break
                        pi += 1
                        k_in = 0
                    if phase == 'run':
                        if pi >= len(prof.prims):
                            go_end('프로파일 완료', prof.prims[-1].end_value())
                        else:
                            cur = prof.prims[pi].value(k_in * period)
                            k_in += 1
                            k_run += 1
            if phase == 'end':
                cur = pl.end_command(end_from, prof.end_brake, prof.end_ramp_s, (tick - end_k0) * period)
                if a.sil:
                    if sil_quit_at is None:
                        sil_quit_at = now + prof.end_ramp_s + a.sil_hold_s
                    elif now >= sil_quit_at:
                        raise StopIteration
            # ── 쓰기 ──
            ch.write(Command(steer_auto=auto['steer'], brake_auto=auto['brake'], acc_auto=auto['acc'],
                             steer_cmd_deg=cur['steer'], brake_cmd_pct=cur['brake'], acc_cmd_pct=cur['acc']))
            hb.beat()
            p = prof.prims[min(pi, len(prof.prims) - 1)]
            w.writerow([tick, f'{now:.6f}', f'{time.time():.6f}', phase,
                        f'{k_run * period:.3f}' if phase != 'pre' else '', p.seg if phase == 'run' else '',
                        p.label if phase == 'run' else (end_reason or ''),
                        f'{cur["steer"]:.3f}', f'{cur["brake"]:.3f}', f'{cur["acc"]:.3f}', auto_s,
                        f'{speed:.1f}' if speed is not None else '', f'{age:.3f}' if age is not None else '',
                        f'{s200["steer_pos_deg"]:.1f}' if s200 else '', f'{s200["brake_pos"]:.1f}' if s200 else '',
                        s210['steer_deg'] if s210 else '', s210['brake_pct'] if s210 else '',
                        s210['acc_pct'] if s210 else '',
                        f'{s210["steer_auto"]}{s210["brake_auto"]}{s210["acc_auto"]}' if s210 else ''])
            if phase != 'pre' and now - last_status >= STATUS_EVERY_S and not a.sil:
                last_status = now
                tx = (f' | 0x210 조향 {s210["steer_deg"]:+.1f} 브 {s210["brake_pct"]} 가 {s210["acc_pct"]} '
                      f'auto {s210["steer_auto"]}{s210["brake_auto"]}{s210["acc_auto"]}') if s210 else ''
                spd = f'{speed:5.1f} km/h' if speed_ok else '  —  '
                lab = p.label if phase == 'run' else '끝내기'
                print(f'[gen] {phase:<3} {k_run * period:6.2f}s {lab:<10} 명령 조향 {cur["steer"]:+6.1f}° '
                      f'브 {cur["brake"]:5.1f}% 가 {cur["acc"]:5.1f}% | 차속 {spd}{tx}', file=sys.stderr)
            tick += 1
            next_t += period
            d = next_t - time.monotonic()
            if d > 0:
                time.sleep(d)
            else:
                next_t = time.monotonic()
    except StopIteration:
        pass
    finally:
        csvf.close()
        ch.close()
        hb.close()
        if listener:
            listener.stop.set()
        if bus is not None:
            bus.shutdown()
        if phase != 'pre':
            log(f'생성기 종료 (명령 {tick} 주기). can_guard → 50 ms 뒤 HOLDING(브레이크 유지) → 2 s 뒤 STOPPED. '
                f'다음: stop.sh (같은 세션 재사용 불가)')
        glog.close()
    return rc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check', help='검사·시간표 출력만 (차·can_guard 불필요)')
    c.add_argument('profile')
    c.add_argument('--var', action='append', default=[], help='이름=값 (yaml vars 덮어쓰기, 여러 번)')
    c.add_argument('--plan-csv', default='', help='시간표를 CSV 로 저장')
    r = sub.add_parser('run', help='can_guard 에 명령 넣기')
    r.add_argument('profile')
    r.add_argument('--var', action='append', default=[])
    r.add_argument('--run', required=True, help='회차 이름, 예 20261105_LON-ACC-01_r4 (파일 이름에 씀)')
    r.add_argument('--out', default='~/a1_race_capture/track_latest')
    r.add_argument('--interface', default='socketcan', choices=('socketcan', 'kvaser'))
    r.add_argument('--channel', default='', help='듣기용 (kvaser 면 채널 번호 0). 송신 안 함')
    r.add_argument('--bitrate', type=int, default=500000)
    r.add_argument('--cmd-shm', default='a1_can_guard_cmd')
    r.add_argument('--hb-shm', default='a1_can_guard_perception_hb')
    r.add_argument('--no-speed-check', action='store_true', help='SIL 전용 — 실차 금지')
    r.add_argument('--sil', action='store_true', help='SIL 전용: 바로 시작, 끝내기 후 자동 종료, 입력 안 받음')
    r.add_argument('--sil-hold-s', type=float, default=0.5)
    a = ap.parse_args()
    try:
        return cmd_check(a) if a.cmd == 'check' else cmd_run(a)
    except pl.ProfileError as e:
        print(f'🛑 설정 오류: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
