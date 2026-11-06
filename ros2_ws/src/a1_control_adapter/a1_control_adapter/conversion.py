"""제어기 출력(목표 가감속 m/s², 타이어각 °) → can_guard 명령(핸들각 °, 브레이크 %, 가속 %) 변환 — ROS 무관 순수 함수.

표(map)는 전부 config/track.yaml 에서 온다. 초기값은 10-07 리프트·10-08 주행 이미지에서 잡은 **잠정값**이고
(docs/a1_vehicle_characteristics.md), 트랙에서 tools/track_day/TRACK_MANUAL.md T2~T4 로 실측해 바꾼다.

  조향:   타이어각 → (steer_sign) → 표 보간 → 핸들각 → ±한계 클램프 → 속도 제한
  종방향: a > +불감대 → 가속 % 표, a < −불감대 → 브레이크 % 표(감속 크기 기준), 그 사이 → 둘 다 0
          deadband_off_mps2 를 주면 히스테리시스: 켜진 모드는 |a| < 이탈값 이 될 때까지 유지(표 보간은 끝값 포화)
          가속·브레이크는 동시에 0 이 아닐 수 없다(표 구조상 보장 + can_guard 정책과 같음)
표 밖 입력은 표 끝값으로 포화한다(외삽 안 함).
"""
import math
import os
from bisect import bisect_right
from dataclasses import dataclass, field


def interp(x, xs, ys):
    """구간 선형 보간. xs 는 오름차순, 범위 밖은 끝값으로 포화."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect_right(xs, x)
    x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def _check_table(name, xs, ys):
    if len(xs) < 2 or len(xs) != len(ys):
        raise ValueError(f'{name}: 점이 2개 이상이고 x·y 길이가 같아야 함 ({len(xs)} vs {len(ys)})')
    if any(b <= a for a, b in zip(xs, xs[1:])):
        raise ValueError(f'{name}: x 가 엄격한 오름차순이 아님 {xs}')
    if not all(math.isfinite(v) for v in list(xs) + list(ys)):
        raise ValueError(f'{name}: NaN/inf 포함')


@dataclass
class SteerMap:
    """타이어각 ↔ 핸들각 표. 좌우 비대칭이면 음수 쪽 점을 따로 넣는다(예: tire [-20,-10,0,10,20])."""
    tire_deg: list
    wheel_deg: list
    sign: float = 1.0           # 제어기 + 좌회전 ↔ CAN + 방향이 반대면 −1 (T1 에서 확정)
    wheel_limit_deg: float = 150.0

    def __post_init__(self):
        _check_table('steer_map', self.tire_deg, self.wheel_deg)
        if any(b < a for a, b in zip(self.wheel_deg, self.wheel_deg[1:])):
            raise ValueError('steer_map: 핸들각이 타이어각에 대해 단조 증가가 아님')
        if self.sign not in (1.0, -1.0):
            raise ValueError('steer_sign 은 1 또는 -1')

    def to_wheel(self, tire_deg):
        w = interp(self.sign * tire_deg, self.tire_deg, self.wheel_deg)
        return max(-self.wheel_limit_deg, min(self.wheel_limit_deg, w))

    def to_tire(self, wheel_deg):
        """역변환(0x200 실측 핸들각 → 타이어각, 상태 표시·제어기 피드백용)."""
        return self.sign * interp(wheel_deg, self.wheel_deg, self.tire_deg)


def _next_mode(mode, a, on, off):
    """가감속 모드 전이(+1 가속, −1 브레이크, 0 없음). off 가 None 이면 상태 없는 불감대."""
    if a > on:
        return 1
    if a < -on:
        return -1
    if off is None or abs(a) < off or a * mode < 0:
        return 0                            # 이탈값 안쪽, 또는 모드 반대쪽 값
    return mode


@dataclass
class LongMap:
    """목표 가감속 → (가속 %, 브레이크 %). 감속 표는 감속 **크기**(양수 m/s²)로 쓴다.

    deadband_off_mps2 를 주면 to_pedals 가 상태를 가진다(히스테리시스, 2026-11-04 bsw5 on/off 전환 분당 161회 해소):
      진입 |a| > deadband_mps2, 이탈 |a| < deadband_off_mps2, 그 사이는 이전 모드 유지.
    None 이면 상태 없는 예전 동작(|a| ≤ deadband → 0).
    """
    accel_mps2: list
    acc_pct: list
    decel_mps2: list
    brake_pct: list
    deadband_mps2: float = 0.1
    acc_max_pct: float = 30.0
    brake_max_pct: float = 100.0
    deadband_off_mps2: float = None
    _mode: int = field(default=0, init=False, repr=False)   # +1 가속, −1 브레이크, 0 없음

    def __post_init__(self):
        off = self.deadband_off_mps2
        if off is not None and not 0 <= off <= self.deadband_mps2:
            raise ValueError(f'deadband_off_mps2 는 0~deadband_mps2: {off}')
        _check_table('accel_map', self.accel_mps2, self.acc_pct)
        _check_table('brake_map', self.decel_mps2, self.brake_pct)
        for name, ys in (('acc_pct', self.acc_pct), ('brake_pct', self.brake_pct)):
            if min(ys) < 0 or max(ys) > 100:
                raise ValueError(f'{name}: 0~100 % 밖 값')
        if not (0 <= self.acc_max_pct <= 100 and 0 <= self.brake_max_pct <= 100):
            raise ValueError('acc_max_pct/brake_max_pct 는 0~100')

    def reset(self):
        """히스테리시스 모드를 지운다(종방향 arm 순간 — 옛 입력의 모드로 출발하지 않게)."""
        self._mode = 0

    def to_pedals(self, accel_mps2):
        self._mode = _next_mode(self._mode, accel_mps2, self.deadband_mps2, self.deadband_off_mps2)
        if self._mode > 0:
            return min(interp(accel_mps2, self.accel_mps2, self.acc_pct), self.acc_max_pct), 0.0
        if self._mode < 0:
            return 0.0, min(interp(-accel_mps2, self.decel_mps2, self.brake_pct), self.brake_max_pct)
        return 0.0, 0.0


class LongMap2D:
    """속도 구간별 목표 가감속 → 페달 %. 지시서 2.2절 `long_map`(config/long_map.yaml).

    표: 행 = 속도(km/h), 열 = 가감속(m/s²), 값 = 페달 %(양수 가속, 음수 브레이크, None = 미측정).
    - 이중선형 보간. 표 밖은 끝값 고정 + `last_clamped`.
    - None 칸은 같은 쪽(가속/브레이크)의 가장 가까운 측정 칸 값을 쓰고, 그 칸이 보간에 쓰이면 `last_extrapolated`.
    - 보간은 가속 쪽·브레이크 쪽을 따로 한다. 가장 작은 |a| 칸 아래는 그 칸 값으로 포화한다(0 열과 섞지 않음) —
      불감대 위 75 % 와 0 을 이으면 차가 안 움직이는 % 가 나오고, 1차원 표(LongMap)와도 달라진다.
    - 불감대·히스테리시스는 LongMap 과 같다. 가속·브레이크는 동시에 0 이 아닐 수 없다.
    - 차속 < launch_speed_kph 이고 가속 모드면 가속 % 하한 launch_min_acc_pct(출발 불감대).
    """

    def __init__(self, cfg, acc_max_pct=100.0, brake_max_pct=100.0):
        self.version = cfg.get('version')
        self.source = cfg.get('source')
        if self.version is None or not self.source:
            raise ValueError('long_map: version·source 는 필수')
        self.speeds = [float(x) for x in cfg['speed_bins_kph']]
        self.accels = [float(x) for x in cfg['accel_bins_mps2']]
        _check_table('long_map.speed_bins', self.speeds, self.speeds)
        _check_table('long_map.accel_bins', self.accels, self.accels)
        self.deadband = float(cfg.get('deadband_mps2', 0.1))
        off = cfg.get('deadband_off_mps2', 0.03)
        self.deadband_off = None if off is None else float(off)
        if self.deadband_off is not None and not 0 <= self.deadband_off <= self.deadband:
            raise ValueError(f'long_map: deadband_off_mps2 는 0~deadband_mps2: {off}')
        self.launch_min = float(cfg.get('launch_min_acc_pct', 0.0))
        self.launch_speed = float(cfg.get('launch_speed_kph', 0.0))
        self.acc_max = float(acc_max_pct)
        self.brake_max = float(brake_max_pct)
        table = cfg['pedal_pct']
        if set(float(k) for k in table) != set(self.speeds):
            raise ValueError('long_map: pedal_pct 행이 speed_bins_kph 와 다름')
        rows = {float(k): v for k, v in table.items()}
        self.raw = [self._check_row(sp, rows[sp]) for sp in self.speeds]
        self.grid, self.filled = self._fill()
        self._pos = [j for j, a in enumerate(self.accels) if a > 0]
        self._neg = [j for j, a in reversed(list(enumerate(self.accels))) if a < 0]
        self._mode = 0
        self.last_clamped = self.last_extrapolated = False

    def _check_row(self, speed, row):
        if len(row) != len(self.accels):
            raise ValueError(f'long_map: {speed:g} km/h 행 길이 {len(row)} ≠ 가속도 칸 {len(self.accels)}')
        prev = None
        for a, v in zip(self.accels, row):
            if v is None:
                continue
            v = float(v)
            if not (math.isfinite(v) and -100.0 <= v <= 100.0):
                raise ValueError(f'long_map: {speed:g} km/h, {a:g} m/s²: 값 {v} 가 ±100 % 밖')
            if (a > 0 and v < 0) or (a < 0 and v > 0) or (a == 0 and v != 0):
                raise ValueError(f'long_map: {speed:g} km/h, {a:g} m/s²: 부호가 틀림 {v}')
            if prev is not None and v < prev:
                raise ValueError(f'long_map: {speed:g} km/h 행이 가속도에 대해 단조 증가가 아님({a:g} m/s²)')
            prev = v
        return [None if v is None else float(v) for v in row]

    def _fill(self):
        """None 칸 → 같은 쪽 가장 가까운 측정 칸. 한쪽에 측정이 하나도 없으면 표를 받지 않는다."""
        grid = [list(r) for r in self.raw]
        filled = [[False] * len(self.accels) for _ in self.speeds]
        for side in (1, -1):
            cols = [j for j, a in enumerate(self.accels) if a * side > 0]
            known = [(i, j) for i in range(len(self.speeds)) for j in cols if self.raw[i][j] is not None]
            if not known:
                raise ValueError(f'long_map: {"가속" if side > 0 else "브레이크"} 쪽에 측정 칸이 없음')
            for i in range(len(self.speeds)):
                for j in cols:
                    if self.raw[i][j] is None:
                        ki, kj = min(known, key=lambda c: (abs(c[0] - i) + abs(c[1] - j), abs(c[0] - i), c[0]))
                        grid[i][j] = self.raw[ki][kj]
                        filled[i][j] = True
        return grid, filled

    def reset(self):
        """히스테리시스 모드를 지운다(종방향 arm 순간)."""
        self._mode = 0

    def _bilinear(self, speed, mag, cols):
        xs = self.speeds
        clamped = speed < xs[0] or speed > xs[-1]
        s = min(max(speed, xs[0]), xs[-1])
        i1 = min(max(bisect_right(xs, s), 1), len(xs) - 1)
        i0 = i1 - 1
        w = (s - xs[i0]) / (xs[i1] - xs[i0])
        mags = [abs(self.accels[j]) for j in cols]
        clamped = clamped or mag > mags[-1]
        m = min(max(mag, mags[0]), mags[-1])
        k1 = min(max(bisect_right(mags, m), 1), len(mags) - 1)
        k0 = max(k1 - 1, 0)
        u = (m - mags[k0]) / (mags[k1] - mags[k0]) if k1 > k0 else 0.0
        used = [(i0, k0, (1 - w) * (1 - u)), (i0, k1, (1 - w) * u), (i1, k0, w * (1 - u)), (i1, k1, w * u)]
        val = sum(abs(self.grid[i][cols[k]]) * wt for i, k, wt in used)
        extrap = any(wt > 1e-9 and self.filled[i][cols[k]] for i, k, wt in used)
        return val, clamped, extrap

    def to_pedals(self, accel_mps2, speed_kph=0.0):
        """(가속 %, 브레이크 %). speed_kph = 현재 차속(0x201 평균)."""
        self._mode = _next_mode(self._mode, accel_mps2, self.deadband, self.deadband_off)
        self.last_clamped = self.last_extrapolated = False
        if self._mode == 0:
            return 0.0, 0.0
        if self._mode > 0:
            val, self.last_clamped, self.last_extrapolated = self._bilinear(speed_kph, abs(accel_mps2), self._pos)
            if speed_kph < self.launch_speed:
                val = max(val, self.launch_min)
            return min(val, self.acc_max), 0.0
        val, self.last_clamped, self.last_extrapolated = self._bilinear(speed_kph, abs(accel_mps2), self._neg)
        return 0.0, min(val, self.brake_max)


def resolve_map_path(path, can_guard_dir):
    """long_map_file 경로. 상대 경로는 저장소 루트(can_guard_dir/../..) 기준. 빈 값 = 2차원 표 안 씀."""
    if not path:
        return ''
    p = os.path.expanduser(path)
    if os.path.isabs(p):
        return p
    return os.path.normpath(os.path.join(os.path.expanduser(can_guard_dir), '..', '..', p))


def wheel_speed_kph(left_kph, right_kph):
    """0x201 좌우 평균 — 후륜 엔코더, 부호 없음."""
    return (float(left_kph) + float(right_kph)) / 2.0


@dataclass
class InputLimits:
    """이 범위를 벗어난 입력은 '제어기 고장'으로 보고 버린다(클램프 아님). 정상 범위는 표가 포화로 처리."""
    accel_abs_mps2: float = 15.0
    steer_tire_abs_deg: float = 45.0


def validate(accel_mps2, steer_tire_deg, lim):
    """문제 없으면 None, 있으면 사유 문자열."""
    if not (math.isfinite(accel_mps2) and math.isfinite(steer_tire_deg)):
        return f'NaN/inf 입력 (accel={accel_mps2}, steer={steer_tire_deg})'
    if abs(accel_mps2) > lim.accel_abs_mps2:
        return f'가감속 {accel_mps2:.2f} m/s² 가 입력 한계 ±{lim.accel_abs_mps2:g} 밖'
    if abs(steer_tire_deg) > lim.steer_tire_abs_deg:
        return f'타이어각 {steer_tire_deg:.2f}° 가 입력 한계 ±{lim.steer_tire_abs_deg:g} 밖'
    return None


class SlewLimiter:
    """핸들각 속도 제한 [deg/s]. rate <= 0 이면 통과. reset(x) 로 현재 위치에서 다시 시작(arm 순간 튐 방지)."""

    def __init__(self, rate_per_s):
        self.rate = rate_per_s
        self.y = None

    def reset(self, y=None):
        self.y = y

    def step(self, target, dt):
        if self.rate <= 0 or self.y is None or dt <= 0:
            self.y = target
            return target
        d = self.rate * dt
        self.y = max(self.y - d, min(self.y + d, target))
        return self.y
