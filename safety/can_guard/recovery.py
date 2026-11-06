"""짧은 끊김 복구 판정 — 경기 깃발 계획서 8절(D3). ROS·CAN 의존 없는 순수 계산(시각은 호출자가 넘긴다).

8.1 이탈 예산: 조향이 고정된 동안 옆으로 벗어날 수 있는 최악 거리

    d(t_gap) = ½ · a_err · (t_gap + delay)²      delay 0.25 s = 조향 무반응 0.175 s + 재보정 여유
    a_err    = min(alat_max, v² / R_min)         alat_max 3.0 m/s² = 관측 최대 횡가속(10-08) 근처. R_min 모르면 alat_max
    예산      = 0.30 m

    ★ R_min 모름 → 약 0.20 s(속도 무관). R_min 20 m·9 km/h → 약 1.1 s.

8.2 단계: NORMAL → (끊김) → RECOVER(관성 주행) → (신선한 입력 3주기 연속) → NORMAL
                                         └→ (이탈 > 예산) → COMMITTED(정지 확정, 래치)
    실제 조향 고정·브레이크는 state_machine / command_policy 가 한다. 이 모듈은 판정만 낸다.
8.3 저속 재개: COMMITTED 에서 조건 5개를 전부 만족할 때만 NORMAL 로 돌아간다(`--resume-low-speed`, 기본 off).

⚠ 8.3 조건 1 은 계획서 문구 그대로 구현했다("끊김 시작부터 지금까지의 최악 이탈 ≤ 예산, 끊김 동안 최고 속도로 계산"). 이 값은 시간이 갈수록
커지기만 하므로 **이탈 예산을 넘어 확정된 경우에는 절대 재개되지 않는다.** `commit()` 으로 예산과 다른 이유로 확정했을 때만 재개될 수 있다.
안전한 쪽이라 그대로 두고 미결로 올렸다(해석을 지어내지 않음).
"""
import math
from dataclasses import dataclass, field

PHASE_NORMAL = 'NORMAL'
PHASE_RECOVER = 'RECOVER'
PHASE_COMMITTED = 'COMMITTED'


@dataclass(frozen=True)
class RecoveryParams:
    budget_m: float = 0.30              # --recover-budget-m (확정, D3)
    delay_s: float = 0.25               # --recover-delay-s (잠정)
    alat_max_mps2: float = 3.0          # --recover-alat-max (잠정)
    min_radius_m: float = None          # --track-min-radius-m. None = 모름 → a_err = alat_max
    fresh_frames: int = 3               # 신선한 입력 3주기(60 ms) 연속이면 복구(8.2)
    resume_low_speed: bool = False      # --resume-low-speed (기본 off, 8.3)
    resume_healthy_s: float = 0.5       # 조건 2
    resume_max: int = 2                 # 조건 4: 재개 횟수 상한(세 번째 끊김은 무조건 정지)
    resume_cap_kph: float = 20.0        # 조건 5
    resume_cap_s: float = 3.0           # 조건 5

    def __post_init__(self):
        if not (self.budget_m > 0 and self.delay_s >= 0 and self.alat_max_mps2 > 0 and self.fresh_frames >= 1):
            raise ValueError(f'복구 파라미터 값이 이상함: {self}')
        if self.min_radius_m is not None and not self.min_radius_m > 0:
            raise ValueError(f'min_radius_m 은 None 또는 양수: {self.min_radius_m}')


def a_err_mps2(speed_kph, params=RecoveryParams()):
    """조향 고정 중 가정할 횡가속. R_min 이나 차속을 모르면 상한(보수적)."""
    if params.min_radius_m is None or speed_kph is None:
        return params.alat_max_mps2
    v = max(float(speed_kph), 0.0) / 3.6
    return min(params.alat_max_mps2, v * v / params.min_radius_m)


def worst_deviation_m(t_gap_s, speed_kph, params=RecoveryParams()):
    """끊김 t_gap_s 동안의 최악 이탈 거리(m)."""
    return 0.5 * a_err_mps2(speed_kph, params) * (max(t_gap_s, 0.0) + params.delay_s) ** 2


def budget_gap_s(speed_kph, params=RecoveryParams()):
    """예산 안에서 허용되는 끊김 시간(s). a_err = 0(차가 섬)이면 inf. delay 만으로 예산을 넘으면 0."""
    a = a_err_mps2(speed_kph, params)
    if a <= 0.0:
        return math.inf
    return max(math.sqrt(2.0 * params.budget_m / a) - params.delay_s, 0.0)


def resume_blockers(deviation_m, healthy_s, estop, res_active, suspended, resumes_used, params=RecoveryParams()):
    """8.3 조건 중 만족 못 한 것의 이름 목록(빈 목록 = 전부 만족). 조건 5(재개 뒤 상한)는 재개 뒤에 RecoveryTracker 가 건다."""
    out = []
    if not deviation_m <= params.budget_m:
        out.append('deviation')
    if not healthy_s >= params.resume_healthy_s:
        out.append('model_not_healthy')
    if estop or res_active or suspended:
        out.append('estop_res_suspended')
    if not resumes_used < params.resume_max:
        out.append('resume_count')
    return out


@dataclass
class RecoveryDecision:
    phase: str
    gap_s: float = 0.0                  # 끊김 시작부터 지금까지(NORMAL 이면 0)
    deviation_m: float = 0.0
    budget_s: float = math.inf          # 지금 최고 속도 기준 허용 끊김 시간
    events: list = field(default_factory=list)   # [(이름, detail 문자열)] — can_guard 의 ev() 형식


class RecoveryTracker:
    """매 주기 `step()` 한 번. 시각·끊김 시간은 호출자가 준다(can_guard 가 `cmd_age + hold_after_s` 로 계산, 8.2).

      d = tr.step(now, fresh=..., gap_s=cmd_age + hold_after, speed_kph=v)
      d.phase 가 RECOVER 면 관성 주행, COMMITTED 면 정지 확정
      tr.try_resume(now, healthy_s, estop, res_active, suspended)  # COMMITTED 일 때만 의미 있음. True 면 NORMAL
      tr.speed_cap_kph(now)                                       # 재개 뒤 3 s 동안 20, 그 밖에는 None
    """

    def __init__(self, params=None):
        self.p = params if params is not None else RecoveryParams()
        self.phase = PHASE_NORMAL
        self.resumes_used = 0
        self._gap_start = None
        self._peak_kph = None
        self._fresh_n = 0
        self._cap_until = None
        self._events = []

    def _update_peak(self, speed_kph):
        if speed_kph is not None and (self._peak_kph is None or speed_kph > self._peak_kph):
            self._peak_kph = speed_kph

    def _elapsed(self, now):
        return now - self._gap_start if self._gap_start is not None else 0.0

    def deviation_m(self, now):
        """끊김 시작부터 지금까지의 최악 이탈(끊김 동안 최고 속도 기준). 끊김이 아니면 0."""
        if self._gap_start is None:
            return 0.0
        return worst_deviation_m(self._elapsed(now), self._peak_kph, self.p)

    def step(self, now, fresh, gap_s, speed_kph):
        """fresh: 이번 주기 모델 입력이 신선한가. gap_s: 끊긴 시간(fresh=False 일 때 끊김 시작 시각 계산에 씀)."""
        if self.phase == PHASE_NORMAL and not fresh:
            self.phase = PHASE_RECOVER
            self._gap_start = now - max(gap_s, 0.0)
            self._peak_kph = None
            self._fresh_n = 0

        if self.phase != PHASE_NORMAL:
            self._update_peak(speed_kph)

        if self.phase == PHASE_RECOVER:
            # 복구 확인 3주기 동안에도 조향은 아직 고정이다 → 시간은 계속 센다. 입력이 돌아와도 예산을 넘으면 확정
            d = self.deviation_m(now)
            if d > self.p.budget_m:
                self.commit(now, f'deviation={d:.3f}m')
            elif fresh:
                self._fresh_n += 1
                if self._fresh_n >= self.p.fresh_frames:
                    self._events.append(('recover', f'gap={self._elapsed(now):.3f}s dev={d:.3f}m'))
                    self._to_normal()
            else:
                self._fresh_n = 0

        events, self._events = self._events, []
        return RecoveryDecision(self.phase, self._elapsed(now), self.deviation_m(now),
                                budget_gap_s(self._peak_kph, self.p) if self._gap_start is not None else math.inf,
                                events)

    def commit(self, now, reason='external'):
        """정지 확정(래치). 이미 확정이면 아무것도 안 함."""
        if self.phase == PHASE_COMMITTED:
            return
        if self._gap_start is None:
            self._gap_start = now
        self.phase = PHASE_COMMITTED
        self._events.append(('commit', f'{reason} gap={self._elapsed(now):.3f}s'))

    def try_resume(self, now, healthy_s, estop=False, res_active=False, suspended=False):
        """8.3 저속 재개. 옵션이 꺼져 있거나 COMMITTED 가 아니면 False."""
        if not self.p.resume_low_speed or self.phase != PHASE_COMMITTED:
            return False
        if resume_blockers(self.deviation_m(now), healthy_s, estop, res_active, suspended,
                           self.resumes_used, self.p):
            return False
        self.resumes_used += 1
        self._cap_until = now + self.p.resume_cap_s
        self._events.append(('resume', f'n={self.resumes_used} gap={self._elapsed(now):.3f}s'))
        self._to_normal()
        return True

    def speed_cap_kph(self, now):
        """재개 뒤 상한(조건 5). 없으면 None."""
        if self._cap_until is not None and now < self._cap_until:
            return self.p.resume_cap_kph
        return None

    def _to_normal(self):
        self.phase = PHASE_NORMAL
        self._gap_start = None
        self._peak_kph = None
        self._fresh_n = 0
