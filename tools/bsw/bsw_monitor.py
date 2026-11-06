#!/usr/bin/env python3
"""BSW 상태판 — 연습 주행 때 봐야 할 수치를 한 창에 모은다. 표준 라이브러리(+tkinter)만 쓴다.

  python3 tools/bsw/bsw_monitor.py                    # 창(UI): kv0(미러 사본) + ~/a1_race_capture/bsw_latest 로그
  python3 tools/bsw/bsw_monitor.py --text             # 터미널 글자 화면(ssh 등 창을 못 띄울 때)
  python3 tools/bsw/bsw_monitor.py --channel vcan0    # SIL
  python3 tools/bsw/bsw_monitor.py --replay <CAN 로그(.zst 가능)> [--speed 5]   # 녹화 다시 보기(시험·복기)

bsw_up.sh 가 기본으로 띄운다(nice 10, 격리 코어 밖). 대회 본 주행처럼 필요 없으면 `bsw_up.sh --no-ui`.

**버스에 아무것도 보내지 않고, Kvaser 채널도 열지 않는다.** 미러가 만든 SocketCAN 사본(kv0)을 커널 raw 소켓으로 읽기만
한다 — Kvaser 핸들을 하나 더 만들면 11-03 처럼 송신이 깨질 위험이 있어서다(보고서 2026-11-05 5장).
커널 필터로 아래 5개 ID 만 받는다. 상태판이 멈추거나 느려도 vcan 은 이 소켓 몫만 버리고 미러·candump 는 영향받지 않는다.

보여 주는 것(5 Hz 갱신):
  can_guard  상태(INIT/ACTIVE/HOLDING/STOPPED/ESTOP, 로그의 마지막 상태 줄)·비상 정지·다른 0x210 송신자 이벤트
  차량       속도(0x201 평균 ÷ 0.958), 핸들 실제(0x200), 브레이크 위치, 차량 auto 비트
  우리 명령  0x210 핸들·브레이크 %·가속 %·auto 비트, 송신율(Hz) — 75 Hz 넘으면 "송신자 둘"
  앱         0x156 En, 0x157 ACC_Cmd·EPS_Cmd — En=1 이면 "앱이 쥠: 차량은 0x210 무시"
  수신       ID 별 수신율·마지막 수신 뒤 시간 — 0.2 s 넘게 안 오면 ❌
  경기       can_guard --race 일 때만 보임: 경기 상태·깃발 비트·RES·속도 상한·0x202 값·모델 정상 시간.
             판단하지 않고 StatusChannel(공유메모리, can_guard 가 씀)을 읽기만 한다. 다시 보기에는 --status-shm 을 줄 때만.
"""
import argparse
import atexit
import collections
import os
import socket
import struct
import subprocess
import sys
import threading
import time

FMT = '=IB3x8s'
SIZE = struct.calcsize(FMT)
WHEEL_SCALE = 0.958
IDS = {0x200: '0x200 차량 상태', 0x201: '0x201 바퀴', 0x210: '0x210 명령', 0x156: '0x156 앱 En', 0x157: '0x157 앱 명령'}
STALE_S = 0.2
TWO_SENDERS_HZ = 75
CAN_GUARD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'safety', 'can_guard')
RACE_STALE_S = 0.1     # protocol.STATUS_MAX_AGE_S_DEFAULT 와 같은 기준 — 넘으면 어댑터도 경기 규칙을 끈다


def s16(b, i):
    return int.from_bytes(b[i:i + 2], 'little', signed=True)


def u16(b, i):
    return int.from_bytes(b[i:i + 2], 'little')


def bits3(x):
    return f'{x & 1}{(x >> 1) & 1}{(x >> 2) & 1}'


class State:
    """수신 프레임 저장(스레드 안전). now = 실시간이면 time.time(), 다시 보기면 로그 시각."""

    def __init__(self):
        self.lock = threading.Lock()
        self.last = {}                                   # id → (t, data)
        self.times = collections.defaultdict(collections.deque)   # id → 최근 1 s 수신 시각
        self.clock = None                                # 다시 보기: 마지막 로그 시각
        self.done = False

    def feed(self, t, cid, data):
        if cid not in IDS:
            return
        with self.lock:
            self.last[cid] = (t, data)
            q = self.times[cid]
            q.append(t)
            while q and q[0] < t - 1.0:
                q.popleft()

    def now(self):
        return self.clock if self.clock is not None else time.time()

    def snapshot(self):
        """화면에 필요한 값만 디코딩해 dict 로. 없는 신호는 None."""
        with self.lock:
            now = self.now()
            last = dict(self.last)
            rate = {}
            for i in IDS:
                q = self.times[i]
                while q and q[0] < now - 1.0:
                    q.popleft()
                rate[i] = len(q)
        v = {'now': now, 'speed': None, 'veh': None, 'cmd': None, 'app': None, 'rx': []}
        if 0x201 in last:
            b = last[0x201][1]
            r, lft = (u16(b, 0) & 0xFFF) * 0.1, (u16(b, 3) & 0xFFF) * 0.1   # DBC: right 0|12, left 24|12
            v['speed'] = ((r + lft) / 2 / WHEEL_SCALE, lft, r)
        if 0x200 in last:
            b = last[0x200][1]
            v['veh'] = (s16(b, 0) * 0.1, s16(b, 2), bits3(b[4]))      # 핸들 °, 브레이크 위치 %, auto
        if 0x210 in last:
            b = last[0x210][1]
            v['cmd'] = (s16(b, 0) * 0.1, u16(b, 2), b[4], bits3(b[5]), rate[0x210])
        if 0x156 in last:
            b = last[0x156][1]
            en = bool((b[0] & 1) or (b[2] & 1) or ((b[2] >> 6) & 1))
            acc = eps = None
            if 0x157 in last:
                c = last[0x157][1]
                acc, eps = u16(c, 3) * 0.01 - 10.23, s16(c, 0) * 0.1
            v['app'] = (en, acc, eps)
        for i in IDS:
            age = now - last[i][0] if i in last else None
            v['rx'].append((i, rate[i], age is not None and age <= STALE_S))
        return v


class GuardLog:
    """can_guard.log 마지막 상태 줄 + can_guard_events.csv 를 이어 읽기(파일 전체를 매번 읽지 않음)."""

    def __init__(self, d):
        self.d, self.off, self.ev = d, 0, collections.defaultdict(list)

    def poll(self):
        if not self.d:
            return '', self.ev
        st = ''
        try:
            with open(os.path.join(self.d, 'can_guard.log'), 'rb') as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 8000))
                lines = f.read().decode(errors='replace').splitlines()
            st = next((ln[12:] for ln in reversed(lines) if ln.startswith('[can_guard] ') and '차속' in ln), '')
        except OSError:
            pass
        try:
            with open(os.path.join(self.d, 'can_guard_events.csv'), 'rb') as f:
                f.seek(self.off)
                chunk = f.read()
            end = chunk.rfind(b'\n') + 1                 # 쓰는 중인 마지막 줄은 다음에
            self.off += end
            for ln in chunk[:end].decode(errors='replace').splitlines():
                p = ln.split(',', 3)
                if len(p) == 4:
                    self.ev[p[2]].append(p[3].strip('"'))
        except OSError:
            pass
        return st, self.ev


def guard_state(st, ev):
    """상태 = 이벤트 로그의 마지막 전이(즉시 기록) → 없으면 1 s 상태 줄의 첫 단어 → 없으면 '—'."""
    if ev.get('state'):
        return ev['state'][-1].split()[0].split('->')[-1]
    return st.split()[0] if st else '—'


def warnings(v, ev):
    w = []
    if v['app'] and v['app'][0]:
        w.append(('red', '앱이 쥠(En=1) — 차량은 0x210 무시. 차가 선 뒤에만 En 해제'))
    if v['cmd'] and v['cmd'][4] > TWO_SENDERS_HZ:
        w.append(('red', f'0x210 {v["cmd"][4]} Hz — 송신자 둘(원격 유닛?)'))
    if ev.get('foreign_0x210'):
        w.append(('red', f'다른 0x210 송신자 이벤트 {len(ev["foreign_0x210"])}줄 — 원격 유닛 끌 것'))
    if ev.get('estop_request'):
        w.append(('amber', f'비상 정지 {len(ev["estop_request"])}회 — 마지막: {ev["estop_request"][-1][:60]}'))
    if v['speed'] is None:
        w.append(('red', '0x201 없음 — 차속 모름'))
    if v['app'] is None:
        w.append(('amber', '0x156 없음 — 앱 비상 정지 경로 없음'))
    if v['cmd'] is None:
        w.append(('amber', '우리 0x210 송신 없음 (INIT silent 또는 disarm)'))
    return w


# ── 경기 상태(StatusChannel, 읽기 전용) ─────────────────
class RaceStatus:
    """can_guard 가 쓰는 StatusChannel 을 읽는다. name=None 이면 끔. 재시작한 can_guard 의 새 세그먼트는 1 s 마다 다시 열어 본다."""

    def __init__(self, name, can_guard_dir=CAN_GUARD_DIR):
        self.name, self.proto, self.ch, self.last, self.last_open = name, None, None, None, 0.0
        self.seen_race = False             # 한 번이라도 경기 모드를 봤나 — 그 뒤 사라지면 칸을 숨기지 않고 알린다
        if name is None:
            return
        if can_guard_dir not in sys.path:
            sys.path.insert(0, can_guard_dir)
        try:
            import protocol
            self.proto = protocol if hasattr(protocol, 'StatusChannel') else None
        except ImportError:
            self.proto = None

    def close(self):
        if self.ch is not None:
            self.ch.close()
        self.ch = None

    def _reopen(self):
        if time.monotonic() - self.last_open < 1.0:
            return
        self.last_open = time.monotonic()
        self.close()
        try:
            self.ch = self.proto.StatusChannel.open_or_none(self.name)
        except (ValueError, OSError):      # 만드는 도중이거나 크기가 다른 구버전 세그먼트
            self.ch = None

    def poll(self):
        """GuardStatus(나이는 age_s) 또는 None. 판단에 쓰지 않으므로 오래된 값도 돌려주고 화면에서 표시만 한다."""
        if self.proto is None:
            return None
        if self.ch is None or self.last is None or self.last.age_s > RACE_STALE_S:
            self._reopen()
        if self.ch is None:
            self.last = None
        else:
            try:
                self.last = self.ch.read(max_age_s=None)
            except self.proto.TornReadError:
                pass                       # 이번 주기 새 값 없음 — 직전 값을 그대로 보여 준다
        if self.last is not None and self.last.race_enabled:
            self.seen_race = True
        return self.last


def race_lines(st, seen_race=False):
    """화면 줄 목록. --race 아니면(또는 StatusChannel 없으면) 빈 목록 = 칸 숨김. [0] 이 머리 줄."""
    if st is None or not st.race_enabled:
        return ['경기 상태 없음 — StatusChannel 사라짐(can_guard 종료?) · 어댑터는 기존 규칙'] if seen_race else []
    phase = st.race_phase_name
    if phase == 'RACING' and st.slow_active:
        phase = 'SLOW'
    head = f'{phase}  (안전 축 {st.guard_state_name}'
    head += ' · 종방향 BSW)' if st.long_override else ')'
    if st.age_s > RACE_STALE_S:
        head = f'⚠️ 오래됨 {st.age_s:.1f} s(마지막 값) · 어댑터는 기존 규칙 — ' + head
    bit = lambda x: int(bool(x))           # noqa: E731
    age = '0x124 수신 없음' if st.race_signal_age_s < 0 else f'0x124 {st.race_signal_age_s:.2f} s 전'
    cap = '없음' if st.speed_cap_kph < 0 else ('정지 유지' if st.speed_cap_kph == 0 else f'{st.speed_cap_kph:.1f} km/h')
    lim = '수신 없음' if st.vehicle_speed_limit_kph < 0 else f'{st.vehicle_speed_limit_kph:.1f} km/h'
    return [head,
            f'깃발  Go {bit(st.sig_go)} · SlowOn {bit(st.sig_slow_on)} · SlowOff {bit(st.sig_slow_off)} · '
            f'Finish {bit(st.sig_finish)} · Stop {bit(st.sig_stop)}   ({age})',
            f'RES  {"작동 중" if st.res_active else "대기"} · alive {"정상" if st.res_alive_ok else "❌ 끊김"}',
            f'BSW 속도 상한 {cap}   ·   0x202 {lim}(차량 표시값, 30.0 = 원격 조종 제한)',
            f'모델 정상 {st.model_healthy_s:.1f} s']


# ── 입력 ────────────────────────────────────────────────
def replay_frames(path, speed):
    txt = subprocess.run(['zstd', '-dc', path], capture_output=True, text=True, check=True).stdout \
        if path.endswith('.zst') else open(path).read()
    t0 = w0 = None
    for ln in txt.splitlines():
        p = ln.split()
        if len(p) < 3 or '#' not in p[2]:
            continue
        t = float(p[0][1:-1])
        i, d = p[2].split('#', 1)
        if t0 is None:
            t0, w0 = t, time.monotonic()
        lag = (t - t0) / speed - (time.monotonic() - w0)
        if lag > 0:
            time.sleep(lag)
        yield t, int(i, 16), bytes.fromhex(d)


def open_socket(channel):
    sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    mask = socket.CAN_SFF_MASK | socket.CAN_EFF_FLAG | socket.CAN_RTR_FLAG
    sock.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FILTER,
                    b''.join(struct.pack('=II', i, mask) for i in IDS))       # 5개 ID 만(커널에서 거름)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    sock.bind((channel,))
    return sock


def start_reader(a, s):
    """수신 스레드 시작. 반환: 화면에 쓸 입력 이름."""
    if a.replay:
        def run():
            for t, cid, data in replay_frames(a.replay, a.speed):
                s.clock = t
                s.feed(t, cid, data)
            s.done = True
        threading.Thread(target=run, daemon=True).start()
        return f'다시 보기 {os.path.basename(a.replay)} ×{a.speed:g}'
    try:
        sock = open_socket(a.channel)
    except OSError as e:
        sys.exit(f'{a.channel} 열기 실패({e}) — bsw_up 으로 미러(kv0)가 떠 있는지 확인')

    def run():
        while True:
            cid, dlc, data = struct.unpack(FMT, sock.recv(SIZE))
            s.feed(time.time(), cid & socket.CAN_SFF_MASK, data[:dlc])
    threading.Thread(target=run, daemon=True).start()
    return a.channel


# ── 터미널 화면 ──────────────────────────────────────────
def render_text(v, st, ev, d, src, race=()):
    out = []
    w = out.append
    w(f'── BSW 상태판  {time.strftime("%H:%M:%S")}  입력 {src}  세션 {d or "-"}')
    state = guard_state(st, ev)
    w(f'can_guard   {state:8s} {st.split(None, 1)[-1][:110] if st else "(로그 없음)"}')
    if ev.get('state'):
        w(f'  마지막 상태 전이: {ev["state"][-1][:90]}')
    for lvl, msg in warnings(v, ev):
        w(f'  {"❌" if lvl == "red" else "⚠️"} {msg}')
    if race:
        w(f'경기        {race[0]}')
        for ln in race[1:]:
            w(f'            {ln}')
    w('')
    if v['speed']:
        w(f'차량 속도   {v["speed"][0]:6.1f} km/h   (좌 {v["speed"][1]:.1f} 우 {v["speed"][2]:.1f}, ÷{WHEEL_SCALE})')
    if v['veh']:
        w(f'차량 상태   핸들 {v["veh"][0]:+7.1f}°  브레이크 위치 {v["veh"][1]:3.0f} %  auto {v["veh"][2]}')
    if v['cmd']:
        c = v['cmd']
        w(f'우리 0x210  핸들 {c[0]:+7.1f}°  브레이크 {c[1]:3d} %  가속 {c[2]:3d} %  auto {c[3]}  {c[4]} Hz')
    if v['app']:
        en, acc, eps = v['app']
        w(f'앱          En {int(en)}  ' + (f'ACC_Cmd {acc:+.2f} m/s²  EPS_Cmd {eps:+.1f}°' if acc is not None else ''))
    w('')
    w('수신        ' + '  '.join(f'0x{i:03X} {r:3d}/s {"✅" if ok else "❌"}' for i, r, ok in v['rx']))
    return '\n'.join(out)


def run_text(a, s, g, d, src, rs):
    t_end = time.monotonic() + a.once if a.once else None
    try:
        while True:
            time.sleep(0.2)
            fin = (t_end and time.monotonic() >= t_end) or s.done
            st, ev = g.poll()
            txt = render_text(s.snapshot(), st, ev, d, src, race_lines(rs.poll(), rs.seen_race))
            if fin or a.once:
                if fin:
                    print(txt)
                    return
                continue
            print('\x1b[H\x1b[2J' + txt, flush=True)
    except KeyboardInterrupt:
        pass


# ── 창(UI) ──────────────────────────────────────────────
STATE_COLOR = {'ACTIVE': '#1f9d55', 'INIT': '#6b7280', 'HOLDING': '#d97706', 'STOPPED': '#ea580c',
               'ESTOP': '#dc2626'}
C = {'bg': '#eef0f3', 'card': '#ffffff', 'ink': '#111827', 'muted': '#6b7280', 'line': '#d1d5db',
     'ok': '#1f9d55', 'bad': '#dc2626', 'amber': '#d97706', 'okbg': '#e7f6ee', 'badbg': '#fde8e8'}


def run_gui(a, s, g, d, src, rs):
    import tkinter as tk
    from tkinter import font as tkfont

    root = tk.Tk()
    root.title('BSW 상태판')
    root.configure(bg=C['bg'])
    root.minsize(820, 560)                             # 높이는 경고 줄 수에 맞춰 늘어남
    fams = set(tkfont.families())
    fam = next((f for f in ('Noto Sans CJK KR', 'NanumGothic', 'Noto Sans KR', 'Noto Sans CJK JP') if f in fams),
               'TkDefaultFont')
    F = lambda size, w='normal': (fam, size, w)       # noqa: E731
    mono = next((f for f in ('DejaVu Sans Mono', 'Ubuntu Mono') if f in fams), 'TkFixedFont')
    M = lambda size, w='normal': (mono, size, w)      # noqa: E731
    cache = {}

    def put(lbl, key, **kw):
        """바뀐 것만 다시 그린다."""
        if cache.get(key) != kw:
            cache[key] = kw
            lbl.config(**kw)

    def card(parent, title):
        f = tk.Frame(parent, bg=C['card'], highlightbackground=C['line'], highlightthickness=1, padx=12, pady=8)
        tk.Label(f, text=title, bg=C['card'], fg=C['muted'], font=F(10)).pack(anchor='w')
        return f

    # 머리줄
    top = tk.Frame(root, bg=C['bg'], padx=12, pady=8)
    top.pack(fill='x')
    tk.Label(top, text='BSW 상태판', bg=C['bg'], fg=C['ink'], font=F(14, 'bold')).pack(side='left')
    hdr = tk.Label(top, bg=C['bg'], fg=C['muted'], font=F(10))
    hdr.pack(side='right')

    body = tk.Frame(root, bg=C['bg'], padx=12)
    body.pack(fill='both', expand=True)
    body.columnconfigure(0, weight=1)
    body.columnconfigure(1, weight=1)

    # can_guard 상태 + 차속
    c1 = card(body, 'can_guard 상태')
    c1.grid(row=0, column=0, sticky='nsew', padx=(0, 6), pady=6)
    pill = tk.Label(c1, font=F(26, 'bold'), fg='white', padx=14, pady=2)
    pill.pack(anchor='w', pady=(4, 2))
    stline = tk.Label(c1, bg=C['card'], fg=C['muted'], font=F(9), justify='left', wraplength=360, anchor='w')
    stline.pack(anchor='w', fill='x')

    c2 = card(body, '차량 속도 (0x201 후륜 평균 ÷ 0.958)')
    c2.grid(row=0, column=1, sticky='nsew', padx=(6, 0), pady=6)
    spd = tk.Label(c2, bg=C['card'], fg=C['ink'], font=M(40, 'bold'))
    spd.pack(anchor='w')
    spd2 = tk.Label(c2, bg=C['card'], fg=C['muted'], font=F(10))
    spd2.pack(anchor='w')

    # 경고
    wbox = tk.Label(body, font=F(12, 'bold'), justify='left', anchor='w', padx=12, pady=8)
    wbox.grid(row=1, column=0, columnspan=2, sticky='ew', pady=6)

    # 명령 vs 실제
    c3 = card(body, '우리 명령(0x210) vs 차량 실제(0x200)')
    c3.grid(row=2, column=0, columnspan=2, sticky='nsew', pady=6)
    tab = tk.Frame(c3, bg=C['card'])
    tab.pack(fill='x', pady=(4, 0))
    heads = ('', '명령', '실제')
    rows = ('핸들 °', '브레이크 %', '가속 %', 'auto (조향·브레이크·가속)')
    for j, h in enumerate(heads):
        tk.Label(tab, text=h, bg=C['card'], fg=C['muted'], font=F(10)).grid(row=0, column=j, sticky='w', padx=(0, 28))
    cells = {}
    for r, name in enumerate(rows, 1):
        tk.Label(tab, text=name, bg=C['card'], fg=C['ink'], font=F(11)).grid(row=r, column=0, sticky='w', padx=(0, 28))
        for j in (1, 2):
            lb = tk.Label(tab, bg=C['card'], fg=C['ink'], font=M(15, 'bold'), width=9, anchor='w')
            lb.grid(row=r, column=j, sticky='w', padx=(0, 28))
            cells[r, j] = lb
    hz = tk.Label(c3, bg=C['card'], fg=C['muted'], font=F(10))
    hz.pack(anchor='w', pady=(4, 0))

    # 앱 + 수신
    c4 = card(body, '앱 (원격 인수 0x156/0x157)')
    c4.grid(row=3, column=0, sticky='nsew', padx=(0, 6), pady=6)
    app = tk.Label(c4, bg=C['card'], fg=C['ink'], font=F(12), justify='left', anchor='w')
    app.pack(anchor='w', fill='x')

    c5 = card(body, '수신 (1 s 동안 프레임 수, 0.2 s 넘게 끊기면 빨강)')
    c5.grid(row=3, column=1, sticky='nsew', padx=(6, 0), pady=6)
    rxf = tk.Frame(c5, bg=C['card'])
    rxf.pack(anchor='w', pady=(4, 0))
    rxl = {}
    for k, i in enumerate(IDS):
        lb = tk.Label(rxf, font=M(10, 'bold'), width=7, padx=4, pady=4)
        lb.grid(row=0, column=k, padx=(0, 4))
        rxl[i] = lb

    # 경기 상태 — can_guard --race 일 때만 보인다
    c6 = card(body, '경기 상태 (StatusChannel, 읽기 전용)')
    c6.grid(row=4, column=0, columnspan=2, sticky='nsew', pady=6)
    race_head = tk.Label(c6, bg=C['card'], fg=C['ink'], font=F(14, 'bold'), justify='left', anchor='w')
    race_head.pack(anchor='w', fill='x')
    race_body = tk.Label(c6, bg=C['card'], fg=C['ink'], font=F(11), justify='left', anchor='w')
    race_body.pack(anchor='w', fill='x')
    c6.grid_remove()

    t_end = time.monotonic() + a.once if a.once else None

    def tick():
        v = s.snapshot()
        st, ev = g.poll()
        state = guard_state(st, ev)
        put(hdr, 'hdr', text=f'{time.strftime("%H:%M:%S")}   입력 {src}   세션 {os.path.basename(d) or "-"}')
        put(pill, 'pill', text=state, bg=STATE_COLOR.get(state, '#9ca3af'))
        last_tr = ev['state'][-1] if ev.get('state') else ''
        brief = ' · '.join(p.strip() for p in st.split('|')[2:]) if st else ''   # 차속 · 송신오류(명령·차량은 아래 표)
        put(stline, 'st', text=(brief or '(can_guard 로그 없음)')
            + (f'\n마지막 전이: {last_tr[:100]}' if last_tr else ''))
        if v['speed']:
            put(spd, 'spd', text=f'{v["speed"][0]:5.1f} km/h', fg=C['ink'])
            put(spd2, 'spd2', text=f'좌 {v["speed"][1]:.1f} · 우 {v["speed"][2]:.1f} km/h (원값)')
        else:
            put(spd, 'spd', text='  —  km/h', fg=C['muted'])
            put(spd2, 'spd2', text='0x201 없음')
        ws = warnings(v, ev)
        if ws:
            red = any(lvl == 'red' for lvl, _ in ws)
            put(wbox, 'w', text='\n'.join(('❌ ' if lvl == 'red' else '⚠️ ') + m for lvl, m in ws),
                bg=C['badbg'] if red else '#fef3c7', fg=C['bad'] if red else '#92400e')
        else:
            put(wbox, 'w', text='✅ 경고 없음', bg=C['okbg'], fg=C['ok'])
        c, vh = v['cmd'], v['veh']
        vals = {(1, 1): f'{c[0]:+.1f}' if c else '—', (1, 2): f'{vh[0]:+.1f}' if vh else '—',
                (2, 1): f'{c[1]}' if c else '—', (2, 2): f'{vh[1]:.0f}' if vh else '—',
                (3, 1): f'{c[2]}' if c else '—', (3, 2): '',
                (4, 1): c[3] if c else '—', (4, 2): vh[2] if vh else '—'}
        for k, txt in vals.items():
            put(cells[k], k, text=txt)
        put(hz, 'hz', text=(f'0x210 송신 {c[4]} Hz (정상 50)' if c else '0x210 송신 없음 — INIT silent 또는 disarm')
            + '   · 브레이크 실제 = 위치 × 10 (82 % 이상에서 포화)',
            fg=C['bad'] if c and c[4] > TWO_SENDERS_HZ else C['muted'])
        if v['app']:
            en, acc, eps = v['app']
            put(app, 'app', text=(f'En {int(en)}' + ('  — 앱이 쥠' if en else '  — 대기')
                                  + (f'\nACC_Cmd {acc:+.2f} m/s²   EPS_Cmd {eps:+.1f}°' if acc is not None else '')),
                fg=C['bad'] if en else C['ink'])
        else:
            put(app, 'app', text='0x156 없음 — 앱 비상 정지 경로 없음', fg=C['amber'])
        for i, r, ok in v['rx']:
            put(rxl[i], ('rx', i), text=f'0x{i:03X}\n{r}/s', bg=C['okbg'] if ok else C['badbg'],
                fg=C['ok'] if ok else C['bad'])
        race = race_lines(rs.poll(), rs.seen_race)
        if race:
            c6.grid()
            put(race_head, 'race_head', text=race[0])
            put(race_body, 'race_body', text='\n'.join(race[1:]))
        else:
            c6.grid_remove()
        if (t_end and time.monotonic() >= t_end) or (a.replay and s.done):
            if a.shot:                                 # 시험용 창 캡처(xwd 형식, 실차에서는 안 씀)
                root.update()
                subprocess.run(['xwd', '-id', str(int(root.wm_frame(), 16)), '-out', a.shot], check=False)
            root.destroy()
            return
        root.after(200, tick)

    root.after(200, tick)
    root.mainloop()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--channel', default='kv0')
    ap.add_argument('--session', default=os.path.expanduser('~/a1_race_capture/bsw_latest'))
    ap.add_argument('--replay')
    ap.add_argument('--speed', type=float, default=1.0)
    ap.add_argument('--text', action='store_true', help='터미널 글자 화면')
    ap.add_argument('--once', type=float, default=0.0, help='이만큼(s) 받고 끝(글자 화면이면 한 번 출력, 시험용)')
    ap.add_argument('--status-shm', help='StatusChannel 이름(기본: a1_can_guard_status, 다시 보기에서는 끔)')
    ap.add_argument('--shot', help=argparse.SUPPRESS)          # 시험용: 끝날 때 창 캡처(ImageMagick import)
    a = ap.parse_args()
    d = os.path.realpath(a.session) if os.path.isdir(a.session) else ''
    s = State()
    g = GuardLog(d)
    src = start_reader(a, s)
    rs = RaceStatus(a.status_shm or (None if a.replay else 'a1_can_guard_status'))
    atexit.register(rs.close)
    gui = not a.text
    if gui:
        try:
            import tkinter  # noqa: F401
            if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
                raise RuntimeError('화면 없음(DISPLAY)')
        except (ImportError, RuntimeError) as e:
            print(f'창을 띄울 수 없음({e}) — 글자 화면으로. tkinter 가 없으면: sudo apt install python3-tk', file=sys.stderr)
            gui = False
    (run_gui if gui else run_text)(a, s, g, d, src, rs)


if __name__ == '__main__':
    main()
