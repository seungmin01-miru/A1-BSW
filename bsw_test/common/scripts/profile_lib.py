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
    end: {brake: 100, ramp_s: 2.5}         # 끝나면(정상·중단 모두): 가속 즉시 0, 조향 유지, 브레이크 ramp_s 동안 올림

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


def build(cfg, var_overrides=None):
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
                   end_brake=end_brake, end_ramp_s=end_ramp, auto=auto, vars=vars_)
    cur = dict(start)
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

        def add(kind, dur, **kw):
            if dur < 0:
                raise ProfileError(f'segments[{i}]: 시간은 0 이상')
            p = Prim(seg=i, label=lab, kind=kind, dur=dur, base=dict(cur), **kw)
            if kind == 'ramp':
                p.base[p.axis] = p.a
            prof.prims.append(p)
            return p.end_value()

        if typ == 'hold':
            cur = add('const', n('t'))
        elif typ in ('set', 'step'):
            if typ == 'step':
                ax = _axis(s, i)
                cur[ax] = n('to')
            else:
                keys = [k for k in s if k in AXES]
                if not keys:
                    raise ProfileError(f'segments[{i}] (set): steer/brake/acc 중 하나 이상')
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
        else:
            raise ProfileError(f'segments[{i}]: 모르는 type {typ!r} — hold/set/step/ramp/trap/sine/until_speed')
    return prof


def validate(prof):
    """시간표 전체를 주기마다 훑어 상한 위반을 모은다. 빈 리스트 = 통과."""
    L = prof.limits
    errs = []
    prev = None
    pts = [(-1, -prof.period_s, -1, 'start', prof.start)] + prof.samples()
    for k, t, seg, lab, v in pts:
        where = f't={t:.2f}s seg{seg}({lab})'
        if abs(v['steer']) > L['steer_deg'] + EPS:
            errs.append(f'{where}: 조향 {v["steer"]:+.1f}° > ±{L["steer_deg"]:g}')
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
             + (f'  vars {prof.vars}' if prof.vars else '')]
    t = 0.0
    for p in prof.prims:
        desc = {'const': '유지', 'ramp': f'{p.axis} {p.a:g}→{p.b:g}', 'sine': f'{p.axis} {p.a:g}±{p.amp:g} {p.freq:g} Hz',
                'until_speed': f'차속 ≥ {p.kph:g} km/h 까지(최대)'}[p.kind]
        lines.append(f'  {t:6.2f}~{t + p.dur:6.2f} s  seg{p.seg} {p.label:<11} {desc:<26} '
                     f'시작 조향 {p.base["steer"]:+.1f} 브레이크 {p.base["brake"]:.0f} 가속 {p.base["acc"]:.0f}')
        t += p.dur
    return '\n'.join(lines)
