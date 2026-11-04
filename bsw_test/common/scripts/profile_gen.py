#!/usr/bin/env python3
"""02 LON-PROF 프로파일 생성기 — yaml 한 장대로 can_guard 에 명령을 자동으로 넣는다(03·04·07 공용).

lift_cmd.py 와 같은 자리(가짜 제어 노드 + 가짜 인지 하트비트)에 선다: can_guard 의 CommandChannel 에 50 Hz 로 쓰고,
버스에는 **아무것도 보내지 않는다**(0x200/0x201/0x210 은 듣기만 — 차속 감시·로그용). lift_cmd 와 동시에 못 쓴다.

  # 1) 검사만(차 없이): 상한 위반·조향 속도·가속/브레이크 동시 여부 + 시간표 출력
  python3 bsw_test/common/scripts/profile_gen.py check bsw_test/02_LON_profile/profiles/acc_step.yaml --var acc=15

  # 2) 실행(bringup --no-adapter 뒤, lift_cmd 대신)
  python3 bsw_test/common/scripts/profile_gen.py run bsw_test/02_LON_profile/profiles/acc_step.yaml --var acc=15 \
      --run 20261105_LON-ACC-01_r4 --interface kvaser --channel 0

  # 3) 한 세션 여러 회차 + 회차 사이 자율 U턴 (2026-11-04) — 50 m 코스 왕복, 재시작·사람 방향 전환 없음
  python3 bsw_test/common/scripts/profile_gen.py run $P/brake_step.yaml --var acc=15 --var v=10 \
      --sweep brake=20,40,60 --reps 3 --turn $P/uturn.yaml --turn-var deg=150 --turn-var acc=6 \
      --steer-offset 1.2 --run 20261104_LON-DEC-01 --interface kvaser --channel 0
  #   회차 이름 20261104_LON-DEC-01_r1 … _r9 (+ 사이 U턴 _r1_turn …). 회차마다 Enter 로 시작(안전 "비었음" 확인)

세션 흐름 (회차가 여러 개일 때)
  시험 r1 → 끝내기(브레이크 100) → 정지 1 s → U턴 대기 → Enter → U턴 → 정지 → r2 대기 → Enter → …
  회차 사이에도 계속 명령을 쓴다(브레이크 유지, auto 111) → can_guard 는 ACTIVE 그대로, stop.sh·재기동 없음.
  대기 중  Enter = 시작,  s = 이 회차 건너뛰기,  q = 생성기 종료
  세션 중단(남은 회차 안 함): 0x201 끊김, 차속 상한, can_guard 개입(ESTOP 등), 정지 안 함, Ctrl+C
  회차만 끝(다음 회차 대기): 프로파일 완료, 코스 끝 예측, until_* 시간 초과, 실행 중 Enter

실행 흐름 (회차 하나)
  대기   start 값(기본 브레이크 30, 가속 0, auto 111)을 쓰면서 기다림.  Enter = 시작,  q = 취소
  실행   segments 순서대로.  아무 줄(Enter) / Ctrl+C = 중단 → 끝내기
         자동 중단: 차속 > limits.max_speed_kph, 0x201 0.3 s 이상 안 들어옴, until_* 시간 초과,
                   코스(course.length_m): 이동거리 + 예측 정지거리 ≥ 코스 길이
         폐루프 구간(speed, uturn kph): SpeedController 가 0x201 후륜 차속으로 가속/브레이크 % 를 정함(--ctrl 파라미터)
         조향 오프셋(--steer-offset, 핸들 °): 모든 조향 명령에 더해서 씀 — 로그 steer_cmd_deg 는 더한 값
  끝내기 가속 즉시 0, 조향 유지, 브레이크 end.brake(기본 100) 까지 end.ramp_s 동안 → 유지
         q / Ctrl+C = 생성기 종료 → can_guard 가 50 ms 뒤 HOLDING(브레이크 그대로) → 2 s 뒤 STOPPED. 그다음 stop.sh.

남기는 것(--out, 기본 ~/a1_race_capture/track_latest). 회차가 여러 개면 <run> = 회차 이름(_r1, _r1_turn …):
  <run>_cmd.csv      매 주기 명령값 + 들은 차속·0x200·0x210 + 목표 차속·제어 모드·이동거리·방위 추정
                     (02 판정: 0x210 과 1주기 안 일치)
  <--run>_gen.log    이벤트(시작·중단 이유·끝) — 세션 하나에 한 파일
  <run>_profile.yaml 실제 쓴 설정(vars 적용, 적용한 speed_ctrl) — 같은 회차를 그대로 재현
  notes.txt          "<run> 시작 / 끝(이유)" 한 줄씩 (bringup 메모와 같은 파일)
"""
import argparse
import csv
import itertools
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

    def snap_full(self):
        """(차속, 나이, 0x200, 나이, 0x210, 나이) — 나이는 초, 없으면 None."""
        now = time.monotonic()
        with self.lock:
            ag = lambda t: None if t is None else now - t  # noqa: E731
            return self.speed, ag(self.speed_t), self.s200, ag(self.s200_t), self.s210, ag(self.s210_t)

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
    prof = pl.build(load_yaml(a.profile), pl.parse_var_overrides(a.var), a.steer_offset, load_ctrl(a.ctrl))
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


TURN_STEER_RATE = 60.0   # 회차 사이 조향 복귀(정지 상태) °/s
STALL_S = 5.0           # 가속 > 0 인데 이만큼 정지면 그 회차 끝내기
INTERVENE_TICKS = 5      # can_guard 가 명령과 다르게 보내는 것이 이만큼 연속이면 개입(ESTOP 등)으로 본다


def load_ctrl(path):
    """--ctrl 파일 → speed_ctrl dict (최상위가 speed_ctrl: 이든 그냥 키든)."""
    if not path:
        return None
    cfg = load_yaml(path) or {}
    return cfg.get('speed_ctrl', cfg)


class Item:
    """세션 안 한 회차(시험 또는 U턴)."""

    def __init__(self, kind, name, path, cfg, prof):
        self.kind, self.name, self.path, self.cfg, self.prof = kind, name, path, cfg, prof


def plan_items(a, ctrl):
    """--reps·--sweep·--turn → [Item]. 시험 회차 사이마다 U턴(있으면). 전부 미리 build·validate."""
    base_over = pl.parse_var_overrides(a.var)
    sweeps = []
    for sw in a.sweep:
        if '=' not in sw:
            raise pl.ProfileError(f'--sweep 형식은 이름=값1,값2,… ({sw!r})')
        k, vs = sw.split('=', 1)
        vals = [float(x) for x in vs.split(',') if x.strip()]
        if not vals:
            raise pl.ProfileError(f'--sweep {k}: 값 없음')
        sweeps.append([(k.strip(), v) for v in vals])
    combos = list(itertools.product(*sweeps)) if sweeps else [()]
    overs = []
    for combo in combos:
        for _ in range(a.reps):
            ov = dict(base_over)
            ov.update(dict(combo))
            overs.append(ov)
    multi = len(overs) > 1 or a.turn or a.first_rep is not None
    first = a.first_rep if a.first_rep is not None else 1
    cfg = load_yaml(a.profile)
    turn_cfg = load_yaml(a.turn) if a.turn else None
    items = []
    for j, ov in enumerate(overs):
        name = f'{a.run}_r{first + j}' if multi else a.run
        items.append(Item('test', name, a.profile, cfg, pl.build(cfg, ov, a.steer_offset, ctrl)))
        if turn_cfg is not None and j < len(overs) - 1:
            tp = pl.build(turn_cfg, pl.parse_var_overrides(a.turn_var), a.steer_offset, ctrl)
            items.append(Item('turn', f'{name}_turn', a.turn, turn_cfg, tp))
    return items


def cmd_run(a):
    import yaml
    from protocol import Command, CommandChannel, HeartbeatChannel

    ctrl_params = load_ctrl(a.ctrl)
    items = plan_items(a, ctrl_params)
    bad = []
    for it in items:
        for e in pl.validate(it.prof):
            bad.append(f'{it.name}: {e}')
    print(pl.summary(items[0].prof), file=sys.stderr)
    if len(items) > 1:
        print(f'세션 {len(items)}회차: ' + ', '.join(f'{it.name}{"" if it.kind == "test" else "(U턴)"}'
                                                     f'{" " + str(it.prof.vars) if it.kind == "test" and it.prof.vars else ""}'
                                                     for it in items), file=sys.stderr)
        turns = [it for it in items if it.kind == 'turn']
        if turns:
            print('U턴 프로파일:\n' + pl.summary(turns[0].prof), file=sys.stderr)
    if bad:
        print('🛑 상한 위반 — 실행 안 함:\n  ' + '\n  '.join(bad[:30]), file=sys.stderr)
        return 2
    others = other_sender_running()
    if others:
        print('🛑 다른 명령 도구 실행 중(명령 쓰는 쪽이 둘이 됨):\n  ' + '\n  '.join(others), file=sys.stderr)
        return 2
    if not a.ctrl and any(p.closed_loop for it in items for p in it.prof.prims):
        print('⚠️⚠️ 폐루프 구간(speed / uturn kph)이 있는데 --ctrl 없음 → 속도 제어기 기본값(잠정, SIL 가짜 차량 기준)으로 돈다. '
              '현장 값이면 --ctrl (field_env: $CTRL) 붙여서 다시', file=sys.stderr)
    speed_check = not (a.no_speed_check or a.sil)
    needs_v = any(it.prof.needs_speed for it in items)
    if (speed_check or needs_v) and not a.channel:
        print('🛑 차속(0x201)이 필요 — --channel 지정 (차속 감시·폐루프·U턴·코스 제한)', file=sys.stderr)
        return 2

    out = os.path.expanduser(a.out)
    os.makedirs(out, exist_ok=True)
    for it in items:
        if os.path.exists(os.path.join(out, it.name + '_cmd.csv')):
            print(f'🛑 {it.name}_cmd.csv 이미 있음 — 회차 이름(--run / --first-rep)을 바꾸세요(덮어쓰지 않음)',
                  file=sys.stderr)
            return 2
    try:
        ch = CommandChannel.open(a.cmd_shm)
        hb = HeartbeatChannel.open(a.hb_shm)
    except FileNotFoundError:
        print('🛑 can_guard 공유메모리 없음 — bringup(--no-adapter) 먼저', file=sys.stderr)
        return 2

    for it in items:
        resolved = dict(it.cfg)
        resolved['vars'] = it.prof.vars
        with open(os.path.join(out, it.name + '_profile.yaml'), 'w', encoding='utf-8') as f:
            f.write(f'# {it.name} — {time.strftime("%F %T")} 계획 (원본: {os.path.abspath(it.path)}, '
                    f'조향 오프셋 {a.steer_offset:+g}°, ctrl {a.ctrl or "기본값(잠정)"})\n')
            yaml.safe_dump(resolved, f, allow_unicode=True, sort_keys=False)
            if it.prof.needs_speed:
                f.write('# speed_ctrl (실제 적용):\n')
                yaml.safe_dump({'speed_ctrl_applied': it.prof.speed_ctrl}, f, allow_unicode=True, sort_keys=False)
    glog = open(os.path.join(out, a.run + '_gen.log'), 'a', buffering=1)

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

    header = ['tick', 'mono_s', 'wall_s', 'phase', 't_prof_s', 'seg', 'label',
              'steer_cmd_deg', 'brake_cmd_pct', 'acc_cmd_pct', 'auto',
              'speed_kph', 'speed_age_s', 'steer_pos_deg', 'brake_pos', 'tx210_steer_deg', 'tx210_brake_pct',
              'tx210_acc_pct', 'tx210_auto',
              'steer_prof_deg', 'steer_offset_deg', 'v_ref_kph', 'ctrl_mode', 'dist_m', 'heading_deg', 'item']
    log(f'시작 {a.run}: {a.profile} {len(items)}회차 → {out}  (차속 감시 {"on" if speed_check else "OFF(SIL)"}, '
        f'조향 오프셋 {a.steer_offset:+g}°, ctrl {a.ctrl or "기본값(잠정)"})')

    S = {   # 회차 상태
        'idx': 0, 'phase': 'pre', 'pi': 0, 'k_in': 0, 'k_run': 0, 'end_from': None, 'end_k0': 0, 'end_reason': None,
        'fatal': False, 'dist_run': 0.0, 'dist_prim': 0.0, 'heading': 0.0, 'still_s': 0.0, 'ctrl': None,
        'intervene': 0, 'stall_s': 0.0, 'csvf': None, 'w': None, 'end_ramp_done_t': None, 'started_any': False,
    }
    cur = dict(items[0].prof.start)
    prev_cmd = dict(cur)
    tick = 0
    last_status = 0.0
    pre_hint = 0.0
    sil_quit_at = None
    rc = 0

    def item():
        return items[S['idx']]

    def open_item_csv():
        f = open(os.path.join(out, item().name + '_cmd.csv'), 'w', newline='', buffering=1)
        S['csvf'], S['w'] = f, csv.writer(f)
        S['w'].writerow(header)

    def close_item_csv():
        if S['csvf']:
            S['csvf'].close()
            S['csvf'] = S['w'] = None

    def reset_run_state():
        S.update(pi=0, k_in=0, k_run=0, end_from=None, end_reason=None, dist_run=0.0, dist_prim=0.0,
                 heading=0.0, still_s=0.0, ctrl=None, intervene=0, stall_s=0.0, end_ramp_done_t=None)

    def go_end(reason, value, fatal=False):
        prof = item().prof
        S.update(phase='end', end_from=dict(value), end_k0=tick, end_reason=reason)
        if fatal:
            S['fatal'] = True
        log(f'{item().name} 끝내기 단계: {reason} — 가속 0, 브레이크 → {prof.end_brake:g}% ({prof.end_ramp_s:g} s), '
            f'조향 {value["steer"] + prof.steer_offset:+.1f}° 유지. 이동 {S["dist_run"]:.1f} m'
            + ('  ⛔ 세션 중단(남은 회차 안 함) — q 로 종료' if fatal else ''))
        note(f'{item().name} 끝 ({reason}) t={S["k_run"] * prof.period_s:.2f}s 이동 {S["dist_run"]:.1f}m')

    open_item_csv()
    next_t = time.monotonic()
    try:
        while True:
            it = item()
            prof = it.prof
            period = prof.period_s
            auto = prof.auto
            auto_s = f'{int(auto["steer"])}{int(auto["brake"])}{int(auto["acc"])}'
            need_v = speed_check or prof.needs_speed
            now = time.monotonic()
            if listener:
                speed, age, s200, s200_age, s210, s210_age = listener.snap_full()
            else:
                speed = age = s200 = s200_age = s210 = s210_age = None
            speed_ok = speed is not None and age is not None and age <= SPEED_STALE_S
            phase = S['phase']
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
                        log(f'{it.name} 대기 중 종료 — 이 회차부터 실행 안 함')
                        rc = 0 if S['started_any'] else 1
                        raise StopIteration
                    if ev == 's' and S['idx'] > 0:
                        log(f'{it.name} 건너뜀(사용자 s)')
                        note(f'{it.name} 건너뜀')
                        S['phase'] = 'skip'
                        break
                    if ev == '':
                        if abs(cur['steer'] - prof.start['steer']) > 0.05:
                            log('⚠️ 조향 복귀 중 — 끝나면 다시 Enter')
                            continue
                        if need_v and not speed_ok:
                            log('⚠️ 0x201 차속이 안 보임 — 시작 거부(버스·Kvaser 확인)')
                            continue
                        if speed_ok and speed > prof.limits['max_speed_kph']:
                            log(f'⚠️ 차속 {speed:.1f} > 상한 — 시작 거부')
                            continue
                        if S['idx'] > 0 and speed_ok and speed > pl.STOP_KPH:
                            log(f'⚠️ 아직 움직임({speed:.1f} km/h) — 선 뒤 Enter')
                            continue
                        reset_run_state()
                        S['phase'] = 'run'
                        S['started_any'] = True
                        log(f'▶ {it.name} 실행 시작 ({S["idx"] + 1}/{len(items)}) {prof.vars if prof.vars else ""}')
                        note(f'{it.name} 시작 ({os.path.basename(it.path)} {prof.vars})')
                        break
                elif phase == 'run':
                    go_end('사용자 중단' if ev != 'SIGINT' else 'Ctrl+C 중단', cur, fatal=(ev == 'SIGINT'))
                    break
                else:
                    if ev in ('q', 'SIGINT', 'EOF'):
                        raise StopIteration
                    log('끝내기 단계 — 다음 회차는 정지 후 자동 대기, 생성기 종료는 q')
            phase = S['phase']
            if phase == 'skip':
                close_item_csv()
                S['idx'] += 1
                if S['idx'] >= len(items):
                    log('남은 회차 없음 — 끝내기 상태로 대기, q 로 종료')
                    S['idx'] = len(items) - 1
                    S['phase'] = 'end'
                    S['end_from'], S['end_k0'] = dict(cur), tick
                    S['fatal'] = True
                    open_item_csv()
                else:
                    S['phase'] = 'pre'
                    open_item_csv()
                continue
            # ── 이동거리·방위·정지 시간 ──
            if phase in ('run', 'end') and speed_ok:   # 끝내기(제동) 중 거리도 코스에 들어간다
                ds = speed / 3.6 * period
                S['dist_run'] += ds
                S['dist_prim'] += ds
                st_meas = (s200['steer_pos_deg'] if s200 and s200_age is not None and s200_age < 0.2
                           else cur['steer'] + prof.steer_offset)
                yr = pl.yaw_rate_deg_s(speed, st_meas - prof.steer_offset, prof.vehicle)
                sgn = 1.0 if cur['steer'] >= 0 else -1.0
                S['heading'] += sgn * yr * period
                S['yaw_rate'] = yr
            S['still_s'] = S['still_s'] + period if (speed_ok and speed <= pl.STOP_KPH) else 0.0
            # 가속을 주는데 안 움직임(가속 % 가 불감대 아래) — 시간 초과까지 기다리지 않는다
            S['stall_s'] = S['stall_s'] + period if (phase == 'run' and cur['acc'] > 0 and speed_ok
                                                     and speed <= pl.STOP_KPH) else 0.0
            # ── can_guard 개입 감지 (ESTOP 등: 명령보다 브레이크가 세거나 가속을 0 으로) ──
            if phase == 'run' and s210 is not None and s210_age is not None and s210_age < 0.1:
                hi_b = max(prev_cmd['brake'], cur['brake'])
                want_acc = min(prev_cmd['acc'], cur['acc'])
                if s210['brake_pct'] > hi_b + 5 or (want_acc > 1.0 and s210['acc_pct'] == 0):
                    S['intervene'] += 1
                else:
                    S['intervene'] = 0
            # ── 명령 계산 ──
            v_ref = None
            if phase == 'pre':
                if S['idx'] == 0:
                    cur = dict(prof.start)
                else:   # 회차 사이: 브레이크 유지, 정지 상태에서 조향을 다음 회차 시작값으로(60 °/s)
                    tgt = prof.start['steer']
                    stp = TURN_STEER_RATE * period
                    cur = {'steer': cur['steer'] + max(min(tgt - cur['steer'], stp), -stp),
                           'brake': max(cur['brake'], prof.start['brake']), 'acc': 0.0}
                if a.sil and events.empty() and abs(cur['steer'] - prof.start['steer']) <= 0.05 and (
                        S['idx'] == 0 or S['still_s'] >= 0.5) and (speed_ok or not need_v):
                    events.put('')   # SIL: 준비되면 Enter 대신
                if not a.sil and now - pre_hint > 5:
                    pre_hint = now
                    print(f'[gen] {it.name} 대기 ({S["idx"] + 1}/{len(items)}) 브레이크 {cur["brake"]:g}%, auto {auto_s}. '
                          f'안전 "비었음" 확인 후 Enter = 시작, q = 종료' + (', s = 이 회차 건너뛰기' if S['idx'] else '')
                          + (f'   차속 {speed:.1f} km/h' if speed_ok else '   차속 안 보임'), file=sys.stderr)
            elif phase == 'run':
                if need_v and not speed_ok:
                    go_end('0x201 차속 끊김', cur, fatal=True)
                elif speed_ok and speed > prof.limits['max_speed_kph']:
                    go_end(f'차속 {speed:.1f} km/h > 상한 {prof.limits["max_speed_kph"]:g}', cur, fatal=True)
                elif S['stall_s'] >= STALL_S:
                    go_end(f'{STALL_S:g} s 동안 출발 안 함(가속 {cur["acc"]:g} % — 불감대 아래?) → acc / start_pct 올려서 다시', cur)
                elif S['intervene'] >= INTERVENE_TICKS:
                    go_end(f'can_guard 개입(0x210 브레이크 {s210["brake_pct"]}% / 가속 {s210["acc_pct"]}% ≠ 명령) '
                           f'— ESTOP·HOLDING?', cur, fatal=True)
                elif prof.course and speed_ok and \
                        S['dist_run'] + pl.stop_distance_m(speed, prof.course, prof.end_ramp_s) >= prof.course['length_m']:
                    d_stop = pl.stop_distance_m(speed, prof.course, prof.end_ramp_s)
                    go_end(f'코스 끝 예측(이동 {S["dist_run"]:.1f} m + 정지 {d_stop:.1f} m ≥ {prof.course["length_m"]:g} m, '
                           f'{speed:.1f} km/h)', cur)
                else:
                    while S['pi'] < len(prof.prims):
                        p = prof.prims[S['pi']]
                        t_in = S['k_in'] * period
                        timed_out = t_in >= p.dur - 1e-9
                        if p.kind == 'until_speed':
                            if speed_ok and speed >= p.kph:
                                log(f'seg{p.seg} until_speed: 차속 {speed:.1f} ≥ {p.kph:g} — 다음 구간')
                            elif timed_out:
                                go_end(f'seg{p.seg} until_speed 시간 초과 ({p.dur:g} s 안에 {p.kph:g} km/h 못 감)', cur)
                                break
                            else:
                                break
                        elif p.kind == 'until_dist':
                            if S['dist_prim'] >= p.m:
                                log(f'seg{p.seg} until_dist: {S["dist_prim"]:.1f} m ≥ {p.m:g} — 다음 구간')
                            elif timed_out:
                                go_end(f'seg{p.seg} until_dist 시간 초과 ({S["dist_prim"]:.1f}/{p.m:g} m)', cur)
                                break
                            else:
                                break
                        elif p.kind == 'until_heading':
                            lead = pl.heading_lead_deg(speed if speed_ok else 0.0, S.get('yaw_rate', 0.0), p.lead_brake,
                                                       prof.speed_ctrl)
                            if S['heading'] + lead >= p.deg:
                                log(f'seg{p.seg} {p.label}: 방위 {S["heading"]:.0f}° + 정지 중 예측 {lead:.0f}° ≥ {p.deg:g} '
                                    f'(거리 {S["dist_prim"]:.1f} m) — 정지')
                            elif p.m > 0 and S['dist_prim'] > p.m:
                                go_end(f'seg{p.seg} {p.label} 거리 초과 {S["dist_prim"]:.1f} > {p.m:.0f} m '
                                       f'(방위 {S["heading"]:.0f}° — 조향비·조향 위치 확인)', cur)
                                break
                            elif timed_out:
                                go_end(f'seg{p.seg} {p.label} 시간 초과 (방위 {S["heading"]:.0f}/{p.deg:g}°)', cur)
                                break
                            else:
                                break
                        elif p.kind == 'until_stop':
                            if S['still_s'] >= p.hold_s:
                                log(f'seg{p.seg} {p.label}: 정지 — 다음 구간')
                            elif timed_out:
                                go_end(f'seg{p.seg} {p.label} 시간 초과 (정지 안 함, {speed if speed_ok else -1:.1f} km/h)',
                                       cur, fatal=True)
                                break
                            else:
                                break
                        elif S['k_in'] < int(round(p.dur / period)):
                            break
                        S['pi'] += 1
                        S['k_in'] = 0
                        S['dist_prim'] = 0.0
                        S['heading'] = 0.0
                    if S['phase'] == 'run':
                        if S['pi'] >= len(prof.prims):
                            last = prof.prims[-1]
                            go_end('프로파일 완료', cur if last.closed_loop else last.end_value())
                        else:
                            p = prof.prims[S['pi']]
                            t_in = S['k_in'] * period
                            val = p.value(t_in)
                            if p.closed_loop:
                                v_ref, a_ref = p.target(t_in)
                                if S['ctrl'] is None:
                                    S['ctrl'] = pl.SpeedController(prof.speed_ctrl, prof.limits['acc_pct'],
                                                                   prof.limits['brake_pct'])
                                    S['ctrl'].reset(speed)
                                val['acc'], val['brake'] = S['ctrl'].step(speed, v_ref, a_ref, period)
                            else:
                                S['ctrl'] = None
                            cur = val
                            S['k_in'] += 1
                            S['k_run'] += 1
            if S['phase'] == 'end':
                cur = pl.end_command(S['end_from'], prof.end_brake, prof.end_ramp_s, (tick - S['end_k0']) * period)
                ramp_done = (tick - S['end_k0']) * period >= prof.end_ramp_s
                last_item = S['idx'] >= len(items) - 1 or S['fatal']
                if ramp_done and S['end_ramp_done_t'] is None:
                    S['end_ramp_done_t'] = now
                if not last_item and ramp_done:
                    still = S['still_s'] >= 1.0 if speed_ok else (a.sil and now - S['end_ramp_done_t'] >= a.sil_hold_s)
                    if still:
                        log(f'{it.name} 정지 확인 → 다음 회차 {items[S["idx"] + 1].name} 대기')
                        close_item_csv()
                        S['idx'] += 1
                        S['phase'] = 'pre'
                        open_item_csv()
                        it = item()
                elif last_item and a.sil:
                    if sil_quit_at is None:
                        sil_quit_at = now + prof.end_ramp_s + a.sil_hold_s
                    elif now >= sil_quit_at:
                        raise StopIteration
            # ── 쓰기 ──
            prof = it.prof
            off = prof.steer_offset
            ch.write(Command(steer_auto=auto['steer'], brake_auto=auto['brake'], acc_auto=auto['acc'],
                             steer_cmd_deg=cur['steer'] + off, brake_cmd_pct=cur['brake'], acc_cmd_pct=cur['acc']))
            hb.beat()
            prev_cmd = dict(cur)
            ph = S['phase']
            p = prof.prims[min(S['pi'], len(prof.prims) - 1)]
            ctrl_mode = S['ctrl'].mode if (S['ctrl'] is not None and ph == 'run') else ''
            S['w'].writerow([tick, f'{now:.6f}', f'{time.time():.6f}', ph,
                             f'{S["k_run"] * period:.3f}' if ph != 'pre' else '', p.seg if ph == 'run' else '',
                             p.label if ph == 'run' else (S['end_reason'] or '' if ph == 'end' else ''),
                             f'{cur["steer"] + off:.3f}', f'{cur["brake"]:.3f}', f'{cur["acc"]:.3f}', auto_s,
                             f'{speed:.1f}' if speed is not None else '', f'{age:.3f}' if age is not None else '',
                             f'{s200["steer_pos_deg"]:.1f}' if s200 else '', f'{s200["brake_pos"]:.1f}' if s200 else '',
                             s210['steer_deg'] if s210 else '', s210['brake_pct'] if s210 else '',
                             s210['acc_pct'] if s210 else '',
                             f'{s210["steer_auto"]}{s210["brake_auto"]}{s210["acc_auto"]}' if s210 else '',
                             f'{cur["steer"]:.3f}', f'{off:g}', f'{v_ref:.2f}' if v_ref is not None else '', ctrl_mode,
                             f'{S["dist_run"]:.2f}', f'{S["heading"]:.1f}', S['idx']])
            if ph != 'pre' and now - last_status >= STATUS_EVERY_S and not a.sil:
                last_status = now
                tx = (f' | 0x210 조향 {s210["steer_deg"]:+.1f} 브 {s210["brake_pct"]} 가 {s210["acc_pct"]} '
                      f'auto {s210["steer_auto"]}{s210["brake_auto"]}{s210["acc_auto"]}') if s210 else ''
                spd = f'{speed:5.1f} km/h' if speed_ok else '  —  '
                lab = p.label if ph == 'run' else '끝내기'
                ref = f' 목표 {v_ref:4.1f}' if v_ref is not None else ''
                print(f'[gen] {it.name} {ph:<3} {S["k_run"] * period:6.2f}s {lab:<10} 명령 조향 {cur["steer"] + off:+6.1f}° '
                      f'브 {cur["brake"]:5.1f}% 가 {cur["acc"]:5.1f}% | 차속 {spd}{ref} 이동 {S["dist_run"]:5.1f} m'
                      + (f' 방위 {S["heading"]:4.0f}°' if p.kind == 'until_heading' and ph == 'run' else '') + tx,
                      file=sys.stderr)
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
        close_item_csv()
        ch.close()
        hb.close()
        if listener:
            listener.stop.set()
        if bus is not None:
            bus.shutdown()
        if S['started_any']:
            log(f'생성기 종료 (명령 {tick} 주기, 마지막 회차 {item().name}). can_guard → 50 ms 뒤 HOLDING(브레이크 유지) '
                f'→ 2 s 뒤 STOPPED. 다음: stop.sh (같은 세션 재사용 불가)')
        glog.close()
    return rc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check', help='검사·시간표 출력만 (차·can_guard 불필요)')
    c.add_argument('profile')
    c.add_argument('--var', action='append', default=[], help='이름=값 (yaml vars 덮어쓰기, 여러 번)')
    c.add_argument('--plan-csv', default='', help='시간표를 CSV 로 저장')
    c.add_argument('--steer-offset', type=float, default=0.0, help='조향 오프셋(핸들 °) 넣고 검사')
    c.add_argument('--ctrl', default='', help='속도 제어기 파라미터 yaml')
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
    r.add_argument('--reps', type=int, default=1, help='같은 설정 반복 횟수(한 세션). 회차 이름 = --run + _r<번호>')
    r.add_argument('--sweep', action='append', default=[],
                   help='이름=값1,값2,… — 값마다 --reps 번(여러 개면 모든 조합). 예 --sweep brake=20,40,60')
    r.add_argument('--first-rep', type=int, default=None, help='첫 회차 번호(기본 1) — 세션을 나눠 이어갈 때')
    r.add_argument('--turn', default='', help='시험 회차 사이에 넣을 U턴 프로파일(yaml), 예 profiles/uturn.yaml')
    r.add_argument('--turn-var', action='append', default=[], help='U턴 프로파일 vars 덮어쓰기')
    r.add_argument('--steer-offset', type=float, default=0.0, help='조향 오프셋(핸들 °, 01 결과) — 모든 조향 명령에 더함')
    r.add_argument('--ctrl', default='', help='속도 제어기 파라미터 yaml(현장에서 맞춘 값). 없으면 기본값(잠정)')
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
