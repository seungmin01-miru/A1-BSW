"""02 LON-PROF 프로파일 생성기의 순수 로직 — yaml 설정 → 시간별 명령값(조향 핸들 °, 브레이크 %, 가속 %).

I/O(공유메모리·CAN)는 profile_gen.py 가 한다. 이 파일은 계산·검증만 해서 단위 시험·SIL 재현성 비교가 쉽다.
같은 설정이면 언제 돌려도 같은 명령열이 나온다(시각 = 주기 × 틱 번호, 실제 시계와 무관).

설정 파일(yaml) 예 — bsw_test/02_LON_profile/profiles/ 에 실사용 예시가 있다.

    name: LON-ACC-01                 # 테스트 ID (로그 이름용)
    vars: {acc: 10}                  # "$acc" 자리에 들어감. 현장에서 --var acc=15 로 바꿈
    limits:                          # 생성기 상한 — can_guard 클램프와 같은 값으로(이중)
      steer_deg: 15                  #   |핸들각| 상한
      brake_pct: 100
      acc_pct: 10
      steer_rate_deg_s: 120          #   핸들 속도 상한(어댑터 제한·액추에이터 약 125 °/s)
      max_speed_kph: 30              #   넘으면 즉시 끝내기 단계(가속 0 + 브레이크). 30 초과 설정 불가
    start: {steer: 0, brake: 30, acc: 0}   # Enter 누르기 전 대기 중 값(기본 = 이것)
    segments:
      - {type: set, brake: 0}              # 즉시 바꾸고 t 초 유지(t 생략 = 0)
      - {type: step, axis: acc, to: $acc, t: 10}
      - {type: ramp, axis: acc, to: 0, t: 2}           # 또는 rate: 5 (단위/s)
      - {type: trap, axis: acc, peak: 8, up: 2, hold: 3, down: 2}
      - {type: sine, axis: steer, amp: 30, freq: 0.2, cycles: 3}
      - {type: hold, t: 2}
      - {type: until_speed, kph: 20, timeout: 15}      # 차속 ≥ 20 까지 지금 값 유지(시간 초과 = 중단)
      # ── 2026-11-04 추가 ──
      - {type: speed, to: 10, rate: 2, t: 12}          # 폐루프: 목표 차속을 rate km/h/s 로 to 까지 → t 초까지 유지
                                                       #   (가속·브레이크는 SpeedController 가 정함, 조향 구간은 그 목표를 이어받음)
      - {type: until_dist, m: 20, timeout: 30}         # 이 구간 시작부터 후륜 적분 거리 ≥ m 까지 지금 값 유지
      - {type: until_heading, deg: 180, timeout: 60}   # 방위 변화(자전거 모델 추정) ≥ deg 까지
      - {type: until_stop, brake: 40, timeout: 10}     # 가속 0 + 브레이크 % → 차속 ≤ 0.3 km/h 1 s 까지
      - {type: uturn, deg: 150, kph: 5}                # 매크로: 정지 → 핸들 deg → 출발(kph 폐루프 또는 acc % 개루프)
                                                       #   → 방위 180° → 정지 → 핸들 0. 좌 = +deg, 우 = −deg
    course: {length_m: 50, decel_mps2: 1.5, delay_s: 0.3}   # (선택) 직선 코스 길이 — 이동거리 + 예측 정지거리 ≥ 길이면 끝내기
    vehicle: {wheelbase_m: 2.36, steer_ratio: 7.5}          # 방위 추정용(조향비 잠정, TODO-4)
    speed_ctrl: {kp: 2.0, ...}                              # (선택) SpeedController 파라미터 — 기본값 SPEED_CTRL_DEFAULTS
    end: {brake: 100, ramp_s: 2.5}         # 끝나면(정상·중단 모두): 가속 즉시 0, 조향 유지, 브레이크 ramp_s 동안 올림

실행 시 조향 오프셋(profile_gen --steer-offset, 핸들 °, 01 결과)은 모든 조향 명령에 더해진다. validate() 는 그 값을 넣고 검사.

축 이름: steer(핸들 °, 좌 +), brake(%), acc(%). CAN 단위 그대로(핸들각) — 타이어각 변환은 분석에서(CLAUDE.md 2.1).
"""
import math
from dataclasses import dataclass, field

AXES = ('steer', 'brake', 'acc')
HARD = {'steer_deg': 150.0, 'brake_pct': 100.0, 'acc_pct': 100.0, 'max_speed_kph': 30.0,
        'steer_rate_deg_s': 125.0}   # DBC 한계·공통 속도 상한(CLAUDE.md 1.4)·액추에이터 — 설정이 이걸 넘으면 거부
DEFAULT_LIMITS = {'steer_deg': 15.0, 'brake_pct': 100.0, 'acc_pct': 10.0, 'steer_rate_deg_s': 120.0,
                  'max_speed_kph': 10.0}
EPS = 1e-6
MAX_STEER_OFFSET_DEG = 15.0          # 핸들 ° — 01 오프셋이 이보다 크면 측정·부호부터 의심
VEHICLE_DEFAULTS = {'wheelbase_m': 2.36, 'steer_ratio': 7.5}     # CLAUDE.md 2절 (조향비 잠정, TODO-4)
COURSE_DEFAULTS = {'length_m': 0.0, 'decel_mps2': 1.5, 'delay_s': 0.3}   # 감속도 잠정 — 04(a) 후 갱신
STOP_KPH = 0.3                       # until_stop / 세션 회차 사이 "정지" 판정
# SpeedController 기본값 — 전부 잠정(03(a) 계단 결과로 hold_* 와 kp 를 맞춘다). acc/brake % 는 0x210 단위.
SPEED_CTRL_DEFAULTS = {
    'kp': 2.0,                # 가속 %/(km/h)
    'ki': 0.6,                # 가속 %/(km/h·s)
    'i_max_pct': 8.0,         # 적분항 상한(±)
    'i_band_kph': 2.0,        # 오차가 이 안일 때만 적분(가속 중 와인드업 → 오버슈트 방지)
    'kp_brake': 6.0,          # 브레이크 %/(km/h) — 목표보다 brake_on_kph 넘게 빠를 때만
    'brake_on_kph': 1.5,      # 과속이 이만큼이면 브레이크 모드
    'brake_off_kph': 0.3,     # 과속이 이 밑으로 내려오면 가속 모드로
    'coast_decel_mps2': 0.3,  # 목표 감속이 이보다 크면(타력 감속으로 모자람) 브레이크 모드 — 잠정
    'stop_brake_pct': 40.0,   # 목표 0 이고 거의 섰을 때 붙잡는 브레이크
    'hold_kph': [0.0, 5.0, 10.0, 20.0, 30.0],   # 정속 유지 가속 % (피드포워드) — 03(a) 정상상태에서 맞춤
    'hold_pct': [0.0, 6.0, 8.0, 11.0, 14.0],
    'start_pct': 6.0,         # 정지에서 목표 > 0 일 때 최소 가속(불감대 넘기기) — 03(a) 불감대 + 1
    'accel_mps2': [0.1, 1.3], 'accel_pct': [0.0, 30.0],     # track.yaml accel_map (가속 피드포워드)
    'decel_mps2': [0.1, 1.0, 2.0, 3.0, 4.0], 'brake_pct': [0.0, 40.0, 70.0, 90.0, 100.0],   # track.yaml brake_map
    'acc_max_pct': 30.0,      # 제어기 상한(limits.acc_pct 와 작은 쪽)
    'brake_max_pct': 82.0,    # 82 % 이상은 위치 8.2 포화(CLAUDE.md 2절)
    'acc_slew_pct_s': 50.0,   # 가속 % 올림 변화율(내림은 즉시)
    'brake_slew_pct_s': 60.0, # 브레이크 % 올림 변화율(휠락 방지). 내림은 즉시
    'filter_s': 0.1,          # 차속 1차 저역통과 시정수
}


class ProfileError(ValueError):
    pass


@dataclass
class Prim:
    """하나의 기본 구간. base = 구간 시작 값(세 축), 한 축(axis)만 시간에 따라 변한다."""
    seg: int            # 설정 파일 segments 의 몇 번째(0부터)
    label: str
    kind: str           # const | ramp | sine | until_speed
    dur: float          # until_speed 는 timeout
    base: dict
    axis: str = None
    a: float = 0.0      # ramp 시작 / sine 중심
    b: float = 0.0      # ramp 끝
    amp: float = 0.0
    freq: float = 0.0
    kph: float = 0.0
    ctrl_a: float = None   # 폐루프 목표 차속 km/h (None = 개루프). speed 구간은 ctrl_a → ctrl_b 를 ctrl_rate 로
    ctrl_b: float = None
    ctrl_rate: float = 0.0
    m: float = 0.0          # until_dist 거리 / until_heading 거리 상한
    deg: float = 0.0        # until_heading 방위
    hold_s: float = 1.0     # until_stop 정지 유지
    lead_brake: float = 0.0  # until_heading: 다음 정지 브레이크 % — 서는 동안 더 돌 각도만큼 일찍 끝낸다(0 = 안 함)

    RUNTIME = ('until_speed', 'until_dist', 'until_heading', 'until_stop')

    @property
    def closed_loop(self):
        return self.ctrl_a is not None

    def target(self, tau):
        """폐루프 목표 (km/h, km/h/s). 개루프면 (None, 0)."""
        if self.ctrl_a is None:
            return None, 0.0
        if self.ctrl_b is None or self.ctrl_rate <= 0 or self.ctrl_a == self.ctrl_b:
            return (self.ctrl_b if self.ctrl_b is not None else self.ctrl_a), 0.0
        ramp_t = abs(self.ctrl_b - self.ctrl_a) / self.ctrl_rate
        if tau >= ramp_t:
            return self.ctrl_b, 0.0
        sgn = 1.0 if self.ctrl_b > self.ctrl_a else -1.0
        return self.ctrl_a + sgn * self.ctrl_rate * tau, sgn * self.ctrl_rate

    def value(self, tau):
        v = dict(self.base)
        if self.kind == 'ramp':
            r = 1.0 if self.dur <= 0 else min(max(tau / self.dur, 0.0), 1.0)
            v[self.axis] = self.a + (self.b - self.a) * r
        elif self.kind == 'sine':
            v[self.axis] = self.a + self.amp * math.sin(2 * math.pi * self.freq * min(tau, self.dur))
        return v

    def end_value(self):
        return self.value(self.dur)


@dataclass
class Profile:
    name: str
    period_s: float
    limits: dict
    start: dict
    end_brake: float
    end_ramp_s: float
    auto: dict
    prims: list = field(default_factory=list)
    vars: dict = field(default_factory=dict)
    steer_offset: float = 0.0          # 핸들 ° — 실행 때 모든 조향 명령에 더함(validate 도 반영)
    course: dict = None                # {length_m, decel_mps2, delay_s} 또는 None
    vehicle: dict = field(default_factory=lambda: dict(VEHICLE_DEFAULTS))
    speed_ctrl: dict = field(default_factory=lambda: dict(SPEED_CTRL_DEFAULTS))

    @property
    def needs_speed(self):
        """실행에 차속(0x201)이 반드시 필요한가 — 폐루프·런타임 구간·코스 제한."""
        return bool(self.course) or any(p.closed_loop or p.kind in Prim.RUNTIME for p in self.prims)

    @property
    def duration_s(self):
        """until_speed 를 timeout 끝까지 기다린다고 본 최대 길이."""
        return sum(p.dur for p in self.prims)

    def samples(self):
        """(틱, 시각, 구간 번호, 이름, 값) — 정해진 시간표(until_speed 는 timeout 까지 유지로 가정)."""
        out, k, t0 = [], 0, 0.0
        for p in self.prims:
            n = max(int(round(p.dur / self.period_s)), 0)
            for i in range(n):
                out.append((k, k * self.period_s, p.seg, p.label, p.value(i * self.period_s)))
                k += 1
            t0 += p.dur
        last = self.prims[-1].end_value() if self.prims else dict(self.start)
        out.append((k, k * self.period_s, self.prims[-1].seg if self.prims else -1, 'last', last))
        return out


def _num(x, vars_, what):
    if isinstance(x, str) and x.startswith('$'):
        key = x[1:]
        if key not in vars_:
            raise ProfileError(f'{what}: 변수 ${key} 가 vars 에 없음 (--var {key}=값)')
        x = vars_[key]
    try:
        return float(x)
    except (TypeError, ValueError):
        raise ProfileError(f'{what}: 숫자가 아님 ({x!r})') from None


def _axis(seg, i):
    ax = seg.get('axis')
    if ax not in AXES:
        raise ProfileError(f'segments[{i}]: axis 는 {AXES} 중 하나 (지금 {ax!r})')
    return ax


def parse_var_overrides(items):
    out = {}
    for it in items or ():
        if '=' not in it:
            raise ProfileError(f'--var 형식은 이름=값 ({it!r})')
        k, v = it.split('=', 1)
        out[k.strip()] = float(v)
    return out


def build(cfg, var_overrides=None, steer_offset=0.0, speed_ctrl=None):
    """dict(yaml 읽은 것) → Profile. 형식 오류는 ProfileError."""
    if not isinstance(cfg, dict):
        raise ProfileError('설정 최상위는 사전(dict)이어야 함')
    vars_ = {k: float(v) for k, v in (cfg.get('vars') or {}).items()}
    for k, v in (var_overrides or {}).items():
        if k not in vars_:
            raise ProfileError(f'--var {k}: 설정 파일 vars 에 없는 이름 (오타?) — 있는 것: {sorted(vars_)}')
        vars_[k] = v
    limits = dict(DEFAULT_LIMITS)
    for k, v in (cfg.get('limits') or {}).items():
        if k not in DEFAULT_LIMITS:
            raise ProfileError(f'limits.{k}: 모르는 항목 — {sorted(DEFAULT_LIMITS)}')
        limits[k] = _num(v, vars_, f'limits.{k}')
    for k, hard in HARD.items():
        if not (0 < limits[k] <= hard):
            raise ProfileError(f'limits.{k} = {limits[k]:g} — 0 초과 {hard:g} 이하만 허용')
    period = _num(cfg.get('period_s', 0.02), vars_, 'period_s')
    if not (0.005 <= period <= 0.05):
        raise ProfileError('period_s 는 0.005~0.05 s (can_guard 워치독 50 ms 보다 짧게)')
    start = {'steer': 0.0, 'brake': 30.0, 'acc': 0.0}
    for k, v in (cfg.get('start') or {}).items():
        if k not in AXES:
            raise ProfileError(f'start.{k}: 축 이름은 {AXES}')
        start[k] = _num(v, vars_, f'start.{k}')
    if start['acc'] != 0:
        raise ProfileError('start.acc 는 0 이어야 함 (대기 중 가속 금지)')
    end = cfg.get('end') or {}
    end_brake = _num(end.get('brake', 100), vars_, 'end.brake')
    end_ramp = _num(end.get('ramp_s', 2.5), vars_, 'end.ramp_s')
    if end_brake < 30 or end_ramp < 0:
        raise ProfileError('end.brake ≥ 30, end.ramp_s ≥ 0')
    auto = {'steer': True, 'brake': True, 'acc': True}
    for k, v in (cfg.get('auto') or {}).items():
        if k not in AXES:
            raise ProfileError(f'auto.{k}: 축 이름은 {AXES}')
        auto[k] = bool(v)
    if not auto['brake']:
        raise ProfileError('auto.brake 는 true 여야 함 (끝내기 브레이크를 걸 수 있어야 함)')

    prof = Profile(name=str(cfg.get('name', 'profile')), period_s=period, limits=limits, start=start,
                   end_brake=end_brake, end_ramp_s=end_ramp, auto=auto, vars=vars_,
                   steer_offset=float(steer_offset))
    if abs(prof.steer_offset) > MAX_STEER_OFFSET_DEG:
        raise ProfileError(f'조향 오프셋 {steer_offset:+g}° — ±{MAX_STEER_OFFSET_DEG:g}° 이하만(핸들 °)')
    if cfg.get('course'):
        c = dict(COURSE_DEFAULTS)
        for k, v in cfg['course'].items():
            if k not in COURSE_DEFAULTS:
                raise ProfileError(f'course.{k}: 모르는 항목 — {sorted(COURSE_DEFAULTS)}')
            c[k] = _num(v, vars_, f'course.{k}')
        if not (5 <= c['length_m'] <= 300) or c['decel_mps2'] <= 0 or c['delay_s'] < 0:
            raise ProfileError('course: length_m 5~300, decel_mps2 > 0, delay_s ≥ 0')
        prof.course = c
    for k, v in (cfg.get('vehicle') or {}).items():
        if k not in VEHICLE_DEFAULTS:
            raise ProfileError(f'vehicle.{k}: 모르는 항목 — {sorted(VEHICLE_DEFAULTS)}')
        prof.vehicle[k] = _num(v, vars_, f'vehicle.{k}')
    sc = dict(SPEED_CTRL_DEFAULTS)
    sc.update(cfg.get('speed_ctrl') or {})
    sc.update(speed_ctrl or {})          # 현장에서 맞춘 파일(--ctrl)이 프로파일보다 우선
    prof.speed_ctrl = check_speed_ctrl(sc)
    cur = dict(start)
    ctrl = [None]   # 지금 폐루프 목표(km/h) — None = 개루프. 가속·브레이크를 직접 바꾸는 구간이 끊는다
    segs = cfg.get('segments') or []
    if not segs:
        raise ProfileError('segments 가 비어 있음')
    for i, s in enumerate(segs):
        if not isinstance(s, dict) or 'type' not in s:
            raise ProfileError(f'segments[{i}]: {{type: ...}} 형식이어야 함')
        typ = s['type']
        lab = s.get('label') or typ

        def n(key, default=None, _s=s, _i=i):
            if key not in _s:
                if default is None:
                    raise ProfileError(f'segments[{_i}] ({_s["type"]}): {key} 필요')
                return float(default)
            return _num(_s[key], vars_, f'segments[{_i}].{key}')

        def add(kind, dur, label=None, **kw):
            if dur < 0:
                raise ProfileError(f'segments[{i}]: 시간은 0 이상')
            if kw.get('axis') in ('acc', 'brake'):
                ctrl[0] = None
            if 'ctrl_a' not in kw and ctrl[0] is not None:
                kw['ctrl_a'] = ctrl[0]
            p = Prim(seg=i, label=label or lab, kind=kind, dur=dur, base=dict(cur), **kw)
            if kind == 'ramp':
                p.base[p.axis] = p.a
            prof.prims.append(p)
            return p.end_value()

        def set_open(**vals):
            """가속·브레이크를 직접 정함 → 폐루프 끝."""
            ctrl[0] = None
            cur.update(vals)

        def need_speed_le(kph, what):
            if not (0 <= kph <= limits['max_speed_kph'] - 1 + EPS):
                raise ProfileError(f'segments[{i}]: {what} {kph:g} km/h — 0 이상 max_speed_kph−1 '
                                   f'({limits["max_speed_kph"] - 1:g}) 이하')

        if typ == 'hold':
            cur = add('const', n('t'))
        elif typ in ('set', 'step'):
            if typ == 'step':
                ax = _axis(s, i)
                if ax != 'steer':
                    ctrl[0] = None
                cur[ax] = n('to')
            else:
                keys = [k for k in s if k in AXES]
                if not keys:
                    raise ProfileError(f'segments[{i}] (set): steer/brake/acc 중 하나 이상')
                if any(k != 'steer' for k in keys):
                    ctrl[0] = None
                for k in keys:
                    cur[k] = n(k)
            cur = add('const', n('t', 0))
        elif typ == 'ramp':
            ax = _axis(s, i)
            to = n('to')
            if 't' in s:
                dur = n('t')
            else:
                rate = n('rate')
                if rate <= 0:
                    raise ProfileError(f'segments[{i}]: rate > 0')
                dur = abs(to - cur[ax]) / rate
            cur = add('ramp', dur, axis=ax, a=cur[ax], b=to)
        elif typ == 'trap':
            ax = _axis(s, i)
            base = n('base', cur[ax])
            peak = n('peak')
            cur[ax] = base
            cur = add('ramp', n('up'), axis=ax, a=base, b=peak)
            cur = add('const', n('hold'))
            cur = add('ramp', n('down'), axis=ax, a=peak, b=base)
        elif typ == 'sine':
            ax = _axis(s, i)
            freq = n('freq')
            if freq <= 0:
                raise ProfileError(f'segments[{i}]: freq > 0')
            dur = n('cycles') / freq if 'cycles' in s else n('t')
            cur = add('sine', dur, axis=ax, a=n('offset', cur[ax]), amp=n('amp'), freq=freq)
        elif typ == 'until_speed':
            kph = n('kph')
            if not (0 < kph <= limits['max_speed_kph']):
                raise ProfileError(f'segments[{i}]: kph 는 0 초과 max_speed_kph({limits["max_speed_kph"]:g}) 이하')
            cur = add('until_speed', n('timeout'), kph=kph)
        elif typ == 'speed':
            to = n('to')
            need_speed_le(to, 'speed.to')
            a0 = ctrl[0] if ctrl[0] is not None else n('from', 0)
            rate = n('rate', 0)
            if rate < 0:
                raise ProfileError(f'segments[{i}]: rate ≥ 0 (0 = 계단)')
            ramp_t = abs(to - a0) / rate if rate > 0 else 0.0
            dur = n('t', ramp_t)
            if dur + EPS < ramp_t:
                raise ProfileError(f'segments[{i}]: t({dur:g}) < 램프 시간({ramp_t:g} s)')
            cur['acc'] = cur['brake'] = 0.0
            cur = add('const', dur, ctrl_a=a0, ctrl_b=to, ctrl_rate=rate)
            ctrl[0] = to
        elif typ == 'until_dist':
            m = n('m')
            if m <= 0:
                raise ProfileError(f'segments[{i}]: m > 0')
            cur = add('until_dist', n('timeout'), m=m)
        elif typ == 'until_heading':
            deg = n('deg')
            if not (0 < deg <= 360):
                raise ProfileError(f'segments[{i}]: deg 0~360')
            cur = add('until_heading', n('timeout'), deg=deg, m=n('max_m', 0))
        elif typ == 'until_stop':
            b = n('brake', 40)
            if not (30 <= b <= limits['brake_pct']):
                raise ProfileError(f'segments[{i}]: until_stop brake 30~limits.brake_pct')
            set_open(acc=0.0, brake=b)
            cur = add('until_stop', n('timeout', 10), hold_s=n('hold_s', 1.0))
        elif typ == 'uturn':
            deg = n('deg')
            if not (30 <= abs(deg) <= limits['steer_deg']):
                raise ProfileError(f'segments[{i}]: uturn |deg| 30~limits.steer_deg({limits["steer_deg"]:g})')
            # 오프셋을 더해도 상한 안: 끝까지(lock) 돌리는 게 목적이라 줄여서 맞춘다
            room = limits['steer_deg'] - (prof.steer_offset if deg > 0 else -prof.steer_offset)
            if abs(deg) > room:
                deg = math.copysign(room, deg)
            kph, acc = n('kph', 0), n('acc', 0)
            if (kph > 0) == (acc > 0):
                raise ProfileError(f'segments[{i}]: uturn 은 kph(폐루프) 와 acc(개루프 %) 중 하나만 > 0')
            if kph > 0:
                need_speed_le(kph, 'uturn.kph')
            heading = n('heading', 180)
            srate = n('steer_rate', 60)
            sb = n('stop_brake', 40)
            V = prof.vehicle
            r_est = V['wheelbase_m'] / math.tan(math.radians(abs(deg) / V['steer_ratio']))
            max_m = n('max_m', 1.6 * r_est * math.radians(heading))   # 조향비가 틀려도 여기서 멈춤
            to = n('timeout', 60)
            set_open(acc=0.0, brake=sb)
            cur = add('until_stop', 10, label='U턴정지', hold_s=0.5)
            cur = add('ramp', abs(deg - cur['steer']) / srate, label='U턴조향', axis='steer', a=cur['steer'], b=deg)
            cur = add('const', 1.0, label='U턴대기')
            if kph > 0:
                cur['acc'] = cur['brake'] = 0.0
                cur = add('until_heading', to, label='U턴', deg=heading, m=max_m, ctrl_a=kph, ctrl_b=kph, ctrl_rate=0,
                          lead_brake=sb)
            else:
                cur['brake'] = 0.0
                cur = add('const', 0.3, label='U턴브레이크풀기')
                cur['acc'] = acc
                cur = add('until_heading', to, label='U턴', deg=heading, m=max_m, lead_brake=sb)
            set_open(acc=0.0, brake=sb)
            cur = add('until_stop', 10, label='U턴정지', hold_s=0.5)
            cur = add('ramp', abs(cur['steer']) / srate, label='U턴복귀', axis='steer', a=cur['steer'], b=0.0)
        else:
            raise ProfileError(f'segments[{i}]: 모르는 type {typ!r} — hold/set/step/ramp/trap/sine/until_speed/'
                               f'speed/until_dist/until_heading/until_stop/uturn')
    return prof


def check_speed_ctrl(sc):
    unknown = set(sc) - set(SPEED_CTRL_DEFAULTS)
    if unknown:
        raise ProfileError(f'speed_ctrl: 모르는 항목 {sorted(unknown)}')
    out = {}
    for k, v in sc.items():
        if isinstance(SPEED_CTRL_DEFAULTS[k], list):
            out[k] = [float(x) for x in v]
        else:
            out[k] = float(v)
    for xs, ys in (('hold_kph', 'hold_pct'), ('accel_mps2', 'accel_pct'), ('decel_mps2', 'brake_pct')):
        if len(out[xs]) != len(out[ys]) or len(out[xs]) < 2 or any(b <= a for a, b in zip(out[xs], out[xs][1:])):
            raise ProfileError(f'speed_ctrl.{xs}/{ys}: 길이 같고 2개 이상, {xs} 증가 순')
    if not (0 < out['acc_max_pct'] <= 100 and 0 < out['brake_max_pct'] <= 100 and out['stop_brake_pct'] >= 30):
        raise ProfileError('speed_ctrl: acc_max_pct·brake_max_pct 0~100, stop_brake_pct ≥ 30')
    if out['brake_off_kph'] >= out['brake_on_kph']:
        raise ProfileError('speed_ctrl: brake_off_kph < brake_on_kph (히스테리시스)')
    return out


def interp(x, xs, ys):
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for j in range(1, len(xs)):
        if x <= xs[j]:
            r = (x - xs[j - 1]) / (xs[j] - xs[j - 1])
            return ys[j - 1] + (ys[j] - ys[j - 1]) * r
    return ys[-1]


class SpeedController:
    """후륜 차속(0x201) 피드백 + 변환표 피드포워드 → (가속 %, 브레이크 %). 둘은 절대 동시에 > 0 이 아니다.

    가속 모드: 정속 유지 %(hold 표) + 가속 피드포워드(accel_map) + PI. 정지에서 출발할 땐 start_pct 이상.
    브레이크 모드: 과속 > brake_on_kph 이거나 목표 0. 감속 피드포워드(brake_map) + P, 목표 0 이고 거의 서면 stop_brake_pct.
    순수 계산(시계·I/O 없음) — 같은 입력열이면 같은 출력열."""

    def __init__(self, params, acc_limit, brake_limit):
        self.p = params
        self.acc_max = min(params['acc_max_pct'], acc_limit)
        self.brake_max = min(params['brake_max_pct'], brake_limit)
        self.reset()

    def reset(self, v0=None):
        self.mode = 'acc'
        self.i = 0.0
        self.acc = 0.0
        self.brake = 0.0
        self.v = v0

    def step(self, v_meas, v_ref, a_ref, dt):
        p = self.p
        if self.v is None:
            self.v = v_meas
        k = 1.0 if p['filter_s'] <= 0 else min(dt / p['filter_s'], 1.0)
        self.v += (v_meas - self.v) * k
        v = self.v
        e = v_ref - v
        stop_target = v_ref <= 0.05
        decel_ramp = a_ref / 3.6 < -p['coast_decel_mps2']
        if self.mode == 'acc' and (stop_target or e < -p['brake_on_kph'] or (decel_ramp and e < p['brake_off_kph'])):
            self.mode, self.i = 'brake', 0.0
        elif self.mode == 'brake' and not stop_target and (
                (not decel_ramp and e > -p['brake_off_kph']) or e > p['brake_on_kph']):
            self.mode = 'acc'
        if self.mode == 'acc':
            ff = interp(v_ref, p['hold_kph'], p['hold_pct'])
            if a_ref > 0:
                ff += max(interp(a_ref / 3.6, p['accel_mps2'], p['accel_pct']) - p['accel_pct'][0], 0.0)
            u_p = ff + p['kp'] * e
            u = u_p + self.i
            sat_hi, sat_lo = u >= self.acc_max, u <= 0
            if abs(e) <= p['i_band_kph'] and not ((sat_hi and e > 0) or (sat_lo and e < 0)):   # 조건부 적분
                self.i = min(max(self.i + p['ki'] * e * dt, -p['i_max_pct']), p['i_max_pct'])
            u = min(max(u_p + self.i, 0.0), self.acc_max)
            if v < 0.5 and v_ref > 0.5:
                u = max(u, min(p['start_pct'], self.acc_max))
            # 올릴 때만 변화율 제한(급출발 방지), 내릴 때는 즉시 — 대칭 제한은 목표 근처에서 못 줄여 오버슈트
            self.acc = min(u, self.acc + p['acc_slew_pct_s'] * dt)
            self.brake = 0.0
        else:
            self.acc = 0.0
            if decel_ramp:   # 감속 램프: 피드포워드 ± P (목표보다 느리면 덜 밟음)
                b = interp(-a_ref / 3.6, p['decel_mps2'], p['brake_pct']) + p['kp_brake'] * (-e)
            else:            # 과속·정지: 넘친 만큼만
                b = p['kp_brake'] * max(-e - p['brake_off_kph'], 0.0)
            if stop_target and v < 1.0:
                b = max(b, p['stop_brake_pct'])
            b = min(max(b, 0.0), self.brake_max)
            if b > self.brake:
                b = min(b, self.brake + p['brake_slew_pct_s'] * dt)
            self.brake = b
        # 0x210 은 정수 % — 여기서 반올림(반올림 방식 차이로 로그와 버스가 어긋나지 않게). 내부 상태는 소수 유지
        return float(int(self.acc + 0.5)), float(int(self.brake + 0.5))


def stop_distance_m(v_kph, course, end_ramp_s):
    """끝내기(가속 0 + 브레이크 end_ramp_s 동안 올림) 시작부터 정지까지 예측 거리 — 보수적.
    지연 + 램프 절반 동안은 그 속도로 간다고 보고, 그다음 decel_mps2 로 선다."""
    v = max(v_kph, 0.0) / 3.6
    return v * (course['delay_s'] + end_ramp_s / 2.0) + v * v / (2.0 * course['decel_mps2'])


def heading_lead_deg(v_kph, yaw_rate, brake_pct, speed_ctrl, delay_s=0.3):
    """브레이크 brake_pct 로 서는 동안 더 돌 방위(°) ★ — 지연 동안 그대로 + 감속 동안 선형으로 줄어든다고 봄.
    감속도는 speed_ctrl 의 brake_map(잠정)에서."""
    if brake_pct <= 0 or v_kph <= 0:
        return 0.0
    dec = max(interp(brake_pct, speed_ctrl['brake_pct'], speed_ctrl['decel_mps2']), 0.3)
    return abs(yaw_rate) * (delay_s + (v_kph / 3.6) / dec / 2.0)


def yaw_rate_deg_s(v_kph, steer_handle_deg, vehicle):
    """자전거 모델 방위 변화율(°/s, 좌 +). 타이어각 = 핸들각 / 조향비(잠정)."""
    tire = math.radians(steer_handle_deg / vehicle['steer_ratio'])
    return math.degrees((v_kph / 3.6) * math.tan(tire) / vehicle['wheelbase_m'])


def validate(prof):
    """시간표 전체를 주기마다 훑어 상한 위반을 모은다. 빈 리스트 = 통과."""
    L = prof.limits
    errs = []
    prev = None
    pts = [(-1, -prof.period_s, -1, 'start', prof.start)] + prof.samples()
    for k, t, seg, lab, v in pts:
        where = f't={t:.2f}s seg{seg}({lab})'
        st = v['steer'] + prof.steer_offset
        if abs(st) > L['steer_deg'] + EPS:
            errs.append(f'{where}: 조향 {st:+.1f}° (오프셋 {prof.steer_offset:+g} 포함) > ±{L["steer_deg"]:g}')
        if not (-EPS <= v['brake'] <= L['brake_pct'] + EPS):
            errs.append(f'{where}: 브레이크 {v["brake"]:.1f}% 범위 밖(0~{L["brake_pct"]:g})')
        if not (-EPS <= v['acc'] <= L['acc_pct'] + EPS):
            errs.append(f'{where}: 가속 {v["acc"]:.1f}% 범위 밖(0~{L["acc_pct"]:g})')
        if v['acc'] > EPS and v['brake'] > EPS:
            errs.append(f'{where}: 가속 {v["acc"]:.1f}% 와 브레이크 {v["brake"]:.1f}% 동시 (브레이크 먼저 0 으로)')
        for ax in AXES:
            if not prof.auto[ax] and abs(v[ax]) > EPS:
                errs.append(f'{where}: auto.{ax}=false 인데 {ax} 명령 {v[ax]:g}')
        if prev is not None:
            rate = abs(v['steer'] - prev['steer']) / prof.period_s
            if rate > L['steer_rate_deg_s'] + 1e-3:
                errs.append(f'{where}: 조향 속도 {rate:.0f} °/s > {L["steer_rate_deg_s"]:g} (계단 대신 ramp/rate 로)')
        prev = v
        if len(errs) >= 20:
            errs.append('… (20건에서 멈춤)')
            break
    # 사인 진폭 × 2πf 도 따로 알려 준다(샘플 사이 최대 속도)
    for p in prof.prims:
        if p.kind == 'sine' and p.axis == 'steer':
            peak = abs(p.amp) * 2 * math.pi * p.freq
            if peak > L['steer_rate_deg_s'] + 1e-3:
                errs.append(f'seg{p.seg}(sine): 진폭×2πf = {peak:.0f} °/s > {L["steer_rate_deg_s"]:g}')
    return errs


def end_command(cur, end_brake, end_ramp_s, tau):
    """끝내기 단계 값: 가속 0, 조향 유지, 브레이크 cur → end_brake 를 end_ramp_s 동안(이미 더 세면 그대로)."""
    b0 = cur['brake']
    if end_ramp_s <= 0 or b0 >= end_brake:
        b = max(b0, end_brake)
    else:
        b = b0 + (end_brake - b0) * min(tau / end_ramp_s, 1.0)
    return {'steer': cur['steer'], 'brake': b, 'acc': 0.0}


def summary(prof):
    mx = {ax: max(abs(s[4][ax]) for s in prof.samples()) for ax in AXES}
    lines = [f'프로파일 {prof.name}: 최대 {prof.duration_s:.1f} s, 주기 {prof.period_s * 1e3:g} ms, 구간 {len(prof.prims)}개',
             f'  상한  조향 ±{prof.limits["steer_deg"]:g}°  브레이크 {prof.limits["brake_pct"]:g}%  가속 {prof.limits["acc_pct"]:g}%  '
             f'조향속도 {prof.limits["steer_rate_deg_s"]:g}°/s  차속 {prof.limits["max_speed_kph"]:g} km/h',
             f'  최대  조향 {mx["steer"]:.1f}°  브레이크 {mx["brake"]:.1f}%  가속 {mx["acc"]:.1f}%',
             f'  대기 {prof.start}  끝내기 브레이크 {prof.end_brake:g}% / {prof.end_ramp_s:g} s'
             + (f'  vars {prof.vars}' if prof.vars else '')
             + (f'\n  조향 오프셋 {prof.steer_offset:+g}° (핸들, 모든 조향 명령에 더함)' if prof.steer_offset else '')
             + (f'\n  코스 {prof.course["length_m"]:g} m — 이동 + 예측 정지거리(감속 {prof.course["decel_mps2"]:g} m/s² 잠정, '
                f'지연 {prof.course["delay_s"]:g} s) ≥ 코스면 끝내기' if prof.course else '')]
    t = 0.0
    for p in prof.prims:
        desc = {'const': '유지', 'ramp': f'{p.axis} {p.a:g}→{p.b:g}', 'sine': f'{p.axis} {p.a:g}±{p.amp:g} {p.freq:g} Hz',
                'until_speed': f'차속 ≥ {p.kph:g} km/h 까지(최대)',
                'until_dist': f'거리 ≥ {p.m:g} m 까지(최대)',
                'until_heading': f'방위 ≥ {p.deg:g}° 까지(최대, ≤ {p.m:.0f} m)',
                'until_stop': f'정지 {p.hold_s:g} s 까지(최대)'}[p.kind]
        if p.closed_loop:
            desc += (f' [폐루프 {p.ctrl_a:g}→{p.ctrl_b:g} km/h]' if p.ctrl_b is not None and p.ctrl_b != p.ctrl_a
                     else f' [폐루프 {p.ctrl_a:g} km/h]')
        lines.append(f'  {t:6.2f}~{t + p.dur:6.2f} s  seg{p.seg} {p.label:<11} {desc:<26} '
                     f'시작 조향 {p.base["steer"]:+.1f} 브레이크 {p.base["brake"]:.0f} 가속 {p.base["acc"]:.0f}')
        t += p.dur
    return '\n'.join(lines)
