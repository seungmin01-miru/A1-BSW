"""서행(Slow) 구간 종방향 제어기 — 경기 깃발 계획서 6절. ROS·CAN 의존 없는 순수 계산(시계·I/O 없음).

D1: 서행 중에는 can_guard 가 가속·브레이크를 넘겨받는다(조향은 모델 값). 이 모듈은 차속만 보고 (가속 %, 브레이크 %) 를 낸다.

  서행 시작(reset 직후 첫 차속)
    ├─ 차속 > slow_brake_kph        → 진입 감속: 시작값에서 램프로 a_slow 의 감속표 % 까지. slow_target + 0.5 이하가 되면 아래로
    ├─ 정상 서행                     → PI(목표 slow_target_kph). 목표 가속도 → 가속표 % / 감속표 %
    └─ 안전 한계(항상 위에 덮어씀)
         차속 ≥ slow_acc_cut_kph     → 가속 0 (가속 중이면 지연만큼 앞당겨서: 차속 + 가속도 추정 × acc_cut_lookahead_s)
         차속 ≥ slow_brake_kph       → 브레이크를 hold_brake_ramp 로 올림, slow_acc_cut 아래로 내려오면 해제(히스테리시스)
    차속 None(0x201 0.1 s 초과, 브리프 A4) → 가속 0 + 진입 브레이크 시작값(이미 더 세게 밟고 있었으면 줄이지 않음)

왜 m/s² 로 PI 를 돌리나: 11-04 실차 6 km/h 추종(`bsw_test/common/scripts/adapter_scenario.py`, 평균 6.1~6.8 km/h)이 이 구조였다.
같은 파일의 kp 0.8·ki 0.2·ff 0.2 m/s² 를 잠정 초기값으로 쓴다.

⚠ 가속 % 표에는 출발 불감대가 있다(track.yaml accel_map, 11-04: 70 % 까지 출발 못 함, 80 % ≈ 0.13 m/s²). 표의 아래 끝이 75 % 라
불감대 위쪽은 가속 "켜짐 = 최소 75 %" 인 사실상 온·오프 제어다. 끄고 켜는 문턱이 같으면 50 Hz 로 0 ↔ 75 % 를 오가므로
`acc_on`(= 불감대 deadband_mps2)과 `acc_off_mps2` 사이에 히스테리시스를 둔다.

**잠정값:** a_slow·표·PI 는 전부 잠정이고 실차 서행 데이터로 고친다(`race_flag.yaml`, 계획서 7절). 정속 유지 % 는 아직 측정한 적이 없다.
"""
import math
from bisect import bisect_right
from dataclasses import dataclass, fields


def _interp(x, xs, ys):
    """구간 선형 보간. xs 는 오름차순, 범위 밖은 끝값으로 포화(외삽 안 함) — a1_control_adapter.conversion.interp 와 같은 규칙."""
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
    if not all(math.isfinite(v) for v in list(xs) + list(ys)):
        raise ValueError(f'{name}: NaN/inf 포함')
    if any(b <= a for a, b in zip(xs, xs[1:])):
        raise ValueError(f'{name}: x 가 엄격한 오름차순이 아님 {xs}')
    if any(not 0.0 <= y <= 100.0 for y in ys):
        raise ValueError(f'{name}: % 는 0~100 {ys}')


@dataclass(frozen=True)
class SlowParams:
    """숫자 근거는 계획서 6·10절. 표 기본값 = track.yaml(2026-11-06 기준) 복사, 전부 잠정."""
    # 서행 속도 단계(D8, 1단계). CLI --slow-target-kph 등
    slow_target_kph: float = 8.0
    slow_acc_cut_kph: float = 8.8
    slow_brake_kph: float = 9.3
    entry_exit_margin_kph: float = 0.5      # 차속 ≤ target + 이 값이면 진입 감속 끝(6.1)
    # 진입 감속(race_flag.yaml)
    a_slow_mps2: float = 2.0
    entry_brake_start_pct: float = 30.0     # 시작값이자 차속 None 때의 값(A4). 휠락 방지로 ESTOP 프로파일과 같게 시작
    entry_brake_ramp_pct_s: float = 40.0    # ESTOP 프로파일과 같은 램프(30 → 40 %/s)
    # PI(race_flag.yaml). 단위: 오차 m/s → 목표 가속도 m/s²
    kp: float = 0.8
    ki: float = 0.2
    ff_mps2: float = 0.2
    i_max_mps2: float = 0.3                 # 적분 항 절대값 상한(anti-windup)
    a_max_mps2: float = 0.8                 # PI 출력 상한 — adapter_scenario.py(11-04) 와 같음
    a_min_mps2: float = -1.5                # PI 출력 하한 — 〃
    deadband_mps2: float = 0.1              # track.yaml long_deadband_mps2. |a| 가 이 안이면 둘 다 0
    acc_off_mps2: float = 0.05              # 가속을 켠 뒤에는 a 가 이 아래로 내려와야 끔(떨림 방지 히스테리시스)
    # 가속 차단 앞당김: 끈 뒤에도 차는 지연만큼 더 가속한다. 차속 + 가속도 추정 × 이 시간 ≥ slow_acc_cut 이면 미리 끈다.
    # 0.7 s = 11-04 출발 지연 실측 0.64~0.74 s(quick_lon.md). 주행 중 지연은 미측정이라 보수적으로 같은 값을 쓴다(잠정). 0 = 끔
    acc_cut_lookahead_s: float = 0.7
    accel_est_tau_s: float = 0.2            # 가속도 추정 1차 필터 시정수
    # 변환표(track.yaml accel_map / brake_map 복사)
    accel_map_mps2: tuple = (0.1, 0.13, 0.57)
    accel_map_pct: tuple = (75.0, 80.0, 100.0)
    decel_map_mps2: tuple = (0.1, 1.0, 2.0, 3.0, 4.0)
    decel_map_pct: tuple = (0.0, 40.0, 70.0, 90.0, 100.0)
    # 출력 상한. 브레이크는 차량 위치가 82 % 에서 포화(11-03)하므로 그 위는 보내도 소용없다
    acc_max_pct: float = 100.0
    brake_max_pct: float = 82.0
    # 차속 ≥ slow_brake_kph 때의 hold_brake_ramp(계획서 8.2 정지 확정과 같은 값, can_guard --hold-brake-*)
    hold_brake_start_pct: float = 30.0
    hold_brake_ramp_pct_s: float = 20.0
    hold_brake_max_pct: float = 60.0

    def __post_init__(self):
        if not 0.0 < self.slow_target_kph < self.slow_acc_cut_kph < self.slow_brake_kph < 10.0:
            raise ValueError(f'target < acc_cut < brake < 10 km/h 이어야 함: '
                             f'{self.slow_target_kph}/{self.slow_acc_cut_kph}/{self.slow_brake_kph}')
        if self.slow_target_kph + self.entry_exit_margin_kph > self.slow_brake_kph:
            raise ValueError('target + entry_exit_margin 이 slow_brake 를 넘으면 진입 감속이 안 끝날 수 있음')
        _check_table('accel_map', self.accel_map_mps2, self.accel_map_pct)
        _check_table('decel_map', self.decel_map_mps2, self.decel_map_pct)
        if not (self.a_slow_mps2 > 0 and self.kp >= 0 and self.ki >= 0 and self.i_max_mps2 >= 0):
            raise ValueError('a_slow > 0, kp·ki·i_max ≥ 0 이어야 함')
        if not self.a_min_mps2 < 0 < self.a_max_mps2:
            raise ValueError('a_min < 0 < a_max 이어야 함')
        if self.acc_cut_lookahead_s < 0 or self.accel_est_tau_s <= 0:
            raise ValueError('acc_cut_lookahead_s ≥ 0, accel_est_tau_s > 0 이어야 함')
        if not 0 <= self.acc_off_mps2 <= self.deadband_mps2:
            raise ValueError('0 ≤ acc_off ≤ deadband 이어야 함')
        if not (0 < self.acc_max_pct <= 100 and 0 < self.brake_max_pct <= 100):
            raise ValueError('출력 상한은 0~100 %')
        if not 0 <= self.hold_brake_start_pct <= self.hold_brake_max_pct <= 100 or self.hold_brake_ramp_pct_s < 0:
            raise ValueError('hold_brake_ramp 값이 이상함')
        if not 0 <= self.entry_brake_start_pct <= 100 or self.entry_brake_ramp_pct_s < 0:
            raise ValueError('진입 브레이크 값이 이상함')

    @classmethod
    def from_yaml_dict(cls, d, **overrides):
        """`race_flag.yaml` 을 읽은 dict → SlowParams. overrides 는 CLI 값(`slow_target_kph=...` 등).

        학습값(a_slow·두 표)은 빠지면 ValueError — 성적에 직결되는 값을 기본값으로 조용히 채우지 않는다.
        `slow_pi` 는 선택(없으면 잠정 기본). 그 안의 모르는 키는 오타로 보고 거부. 최상위의 다른 키(slow_off_ramp_pct_s 등)는 무시."""
        missing = [k for k in ('a_slow', 'accel_map', 'brake_map') if k not in d]
        if missing:
            raise ValueError(f'race_flag.yaml 에 필수 키 없음: {missing}')
        kw = {
            'a_slow_mps2': float(d['a_slow']),
            'accel_map_mps2': tuple(float(v) for v in d['accel_map']['accel_mps2']),
            'accel_map_pct': tuple(float(v) for v in d['accel_map']['acc_pct']),
            'decel_map_mps2': tuple(float(v) for v in d['brake_map']['decel_mps2']),
            'decel_map_pct': tuple(float(v) for v in d['brake_map']['brake_pct']),
        }
        allowed = {f.name for f in fields(cls)}
        for key, val in (d.get('slow_pi') or {}).items():
            name = 'ff_mps2' if key == 'ff' else key
            if name not in allowed:
                raise ValueError(f'race_flag.yaml slow_pi: 모르는 키 {key!r}')
            kw[name] = float(val)
        kw.update(overrides)
        return cls(**kw)


# 디버그·기록용 모드 이름
MODE_IDLE = 'IDLE'              # reset 뒤 차속을 아직 못 받음
MODE_ENTRY = 'ENTRY'            # 진입 감속
MODE_PI = 'PI'                  # 정상 서행
MODE_OVERSPEED = 'OVERSPEED'    # 정상 서행 중 slow_brake_kph 초과 → 8.8 아래까지 브레이크
MODE_NO_SPEED = 'NO_SPEED'      # 차속 None


class SlowController:
    """`acc_pct, brake_pct = step(speed_kph_or_None, dt_s)`. 둘은 절대 동시에 > 0 이 아니다.

    slow_on 이벤트(또는 override 시작) 때 `reset()` 후 매 주기 호출한다. 내부 상태는 소수 그대로, 정수 반올림은
    `tx_encode` 가 한다. 같은 입력열이면 같은 출력열이다."""

    def __init__(self, params=None):
        self.p = params if params is not None else SlowParams()
        self._entry_target_pct = min(_interp(self.p.a_slow_mps2, self.p.decel_map_mps2, self.p.decel_map_pct),
                                     self.p.brake_max_pct)
        self.reset()

    def reset(self):
        self.mode = MODE_IDLE
        self._started = False       # 첫 차속을 보고 진입 감속 여부를 정했는가
        self._entry = False
        self._entry_brake = 0.0
        self._overspeed = False
        self._hold_brake = 0.0
        self._integ = 0.0
        self._acc_on = False
        self._v_prev = None         # 가속도 추정용 직전 차속(km/h)
        self._accel_est = 0.0       # m/s²
        self._last_brake = 0.0
        self._last_brake_protective = False   # 마지막 브레이크가 진입/과속 때문인가(차속 None 때 줄이지 않을 값)

    @property
    def integ_mps2(self):
        """적분 항(시험·기록용)."""
        return self._integ

    def step(self, speed_kph, dt_s):
        p = self.p
        dt = max(float(dt_s), 0.0)

        if speed_kph is None or not math.isfinite(speed_kph):
            # A4: 차속을 모르면 가속 0 + 진입 브레이크 시작값. 적분·램프는 멈춘다(모르는 동안 쌓지 않음)
            brake = p.entry_brake_start_pct
            if self._last_brake_protective:
                brake = max(brake, self._last_brake)
            brake = min(brake, p.brake_max_pct)
            self.mode = MODE_NO_SPEED
            self._acc_on = False
            self._v_prev = None         # 끊긴 구간을 건너뛰어 미분하지 않음
            self._last_brake = brake
            self._last_brake_protective = True
            return 0.0, brake

        v = max(float(speed_kph), 0.0)   # 0x201 은 부호가 없다
        if self._v_prev is not None and dt > 0:
            k = min(dt / p.accel_est_tau_s, 1.0)
            self._accel_est += ((v - self._v_prev) / 3.6 / dt - self._accel_est) * k
        self._v_prev = v
        if not self._started:
            self._started = True
            self._entry = v > p.slow_brake_kph

        if self._entry and v <= p.slow_target_kph + p.entry_exit_margin_kph:
            self._entry = False
            self._entry_brake = 0.0
            self._integ = 0.0           # 브레이크 → PI 전환은 적분 0 에서 시작

        if v >= p.slow_brake_kph:
            self._overspeed = True
        elif v < p.slow_acc_cut_kph:
            self._overspeed = False
            self._hold_brake = 0.0

        acc, brake = 0.0, 0.0
        if self._entry:
            self._acc_on = False
            if self._entry_brake > 0:
                self._entry_brake = min(self._entry_target_pct, self._entry_brake + p.entry_brake_ramp_pct_s * dt)
            else:
                self._entry_brake = min(p.entry_brake_start_pct, self._entry_target_pct)
            brake = self._entry_brake
            self.mode = MODE_ENTRY
        else:
            acc, brake = self._pi(v, dt)
            self.mode = MODE_PI

        # --- 안전 한계: 위 결과에 항상 덮어쓴다 ---
        v_ahead = v + max(self._accel_est, 0.0) * 3.6 * p.acc_cut_lookahead_s
        if v_ahead >= p.slow_acc_cut_kph:
            acc = 0.0
            self._acc_on = False
        if self._overspeed:
            self._hold_brake = (min(p.hold_brake_max_pct, self._hold_brake + p.hold_brake_ramp_pct_s * dt)
                                if self._hold_brake > 0 else p.hold_brake_start_pct)
            brake = max(brake, self._hold_brake)
            acc = 0.0
            self._acc_on = False
            if not self._entry:
                self.mode = MODE_OVERSPEED

        brake = min(brake, p.brake_max_pct)
        acc = min(acc, p.acc_max_pct)
        if brake > 0.0:
            acc = 0.0                   # 동시 양수 금지(표 구조상 안 나오지만 마지막에 한 번 더)
        self._last_brake = brake
        self._last_brake_protective = self._entry or self._overspeed
        return acc, brake

    def _pi(self, v_kph, dt):
        p = self.p
        err = (p.slow_target_kph - v_kph) / 3.6
        a_raw = p.ff_mps2 + p.kp * err + self._integ
        sat_hi, sat_lo = a_raw >= p.a_max_mps2, a_raw <= p.a_min_mps2
        # anti-windup: 포화 방향이면 쌓지 않음 + 안전 한계가 잡는 동안(과속)도 쌓지 않음 + 절대값 상한
        if not ((sat_hi and err > 0) or (sat_lo and err < 0) or self._overspeed):
            self._integ = min(max(self._integ + p.ki * err * dt, -p.i_max_mps2), p.i_max_mps2)
        a = min(max(p.ff_mps2 + p.kp * err + self._integ, p.a_min_mps2), p.a_max_mps2)

        if a > p.deadband_mps2:
            self._acc_on = True
        elif a < p.acc_off_mps2:
            self._acc_on = False
        if self._acc_on:
            return _interp(a, p.accel_map_mps2, p.accel_map_pct), 0.0
        if a < -p.deadband_mps2:
            return 0.0, _interp(-a, p.decel_map_mps2, p.decel_map_pct)
        return 0.0, 0.0
