"""0x203 속도 제한 관리자 — 경기 깃발 계획서 9절(D10, D14), 브리프 A5. ROS·CAN 의존 없는 순수 상태기계(시각은 호출자가 넘긴다).

두 스위치(9.1):
  읽기 점검         항상 켬 — 0x202 를 읽어 기대값과 비교
  --speed-limit-tx  기본 off(KIAPI 허락 전) — off 면 **아무것도 보내지 않는다**. 켜면 아래 값 바꾸기 흐름

값 바꾸기(9.3): 목표값 → 0x203 20 ms 송신 → 0x202 에 목표값 3프레임 연속 = 반영 완료 → 100 ms 보험 송신 + 매 프레임 감시
                0x202 가 목표와 0.3 s 넘게 다름 → 다시 20 ms 송신 + speed_limit_mismatch
                해제 1 s 안에 반영 안 됨 → speed_limit_release_slow(**기록용**, D14 — 재시도는 계속, 동작에 영향 없음)
비교는 raw(0.1 km/h 단위) 정수로 한다. 부동소수 비교를 하지 않는다.

**0x202 = 30.0 은 자율 속도 상한이 아니다**(A5, 사용자 확인 2026-11-06: 30 km/h 제한은 원격 조종에만 걸린다). 그래서 해제값 미설정일 때
시작 점검은 "잔류 서행 제한"(0 < 값 ≤ slow_brake_kph)만 거부하고, 그 밖의 값은 기록만 하고 통과한다. 해제값은 지어내지 않는다.

사용:
    r = startup_check(observed_kph, params)         # 시작 전 점검. r.action 이 'refuse' 면 r.exit_code 로 종료
    mgr = SpeedLimitManager(params)
    if r.action == 'correct':  mgr.start(now); 루프에서 mgr.startup_result(now) 가 True/False 될 때까지 tick·on_0x202
    매 주기:  mgr.on_0x202(now, kph) (수신마다) / mgr.on_foreign_0x203(now, data_hex) (우리 소켓은 자기 송신을 안 받는다)
             out = mgr.tick(now);  out.tx_kph 가 None 이 아니면 tx_encode.encode_speed_limit_command(out.tx_kph) 를 보낸다(인코딩은 호출자 몫)
             out.events = [(이름, detail 문자열)] — can_guard 의 ev() 형식
    Slow On / Slow Off:  mgr.slow_on(now) / mgr.slow_off(now)
"""
from dataclasses import dataclass, field

EXIT_SPEED_LIMIT = 5        # 시작 거부 종료 코드(계획서 9.2). can_guard EXIT_FOREIGN_SENDER=3, EXIT_NO_ESTOP_SOURCE=4 다음

RAW_PER_KPH = 10            # 0x202/0x203 은 0.1 km/h 단위
RAW_MAX = 1000              # 100.0 km/h(encode_speed_limit_command 와 같은 클램프)

ACTION_PASS = 'pass'
ACTION_REFUSE = 'refuse'
ACTION_CORRECT = 'correct'  # tx on: 0x203 으로 고치고 최대 2 s 기다린다

STATE_IDLE = 'IDLE'         # 목표 없음
STATE_CHANGING = 'CHANGING'  # 20 ms 송신, 반영 대기
STATE_CONFIRMED = 'CONFIRMED'  # 100 ms 보험 송신, 감시

_PERIOD_TOL_S = 0.001       # 호출 주기 떨림 허용 — 0.0199 s 때문에 한 주기를 건너뛰지 않게
_FOREIGN_LOG_PERIOD_S = 1.0  # foreign_0x210 과 같은 1 초에 한 줄


def kph_to_raw(kph):
    """km/h → raw(0.1 km/h 단위 정수). 0~100 km/h 로 클램프."""
    return max(0, min(RAW_MAX, int(round(float(kph) * RAW_PER_KPH))))


def raw_to_kph(raw):
    return raw / RAW_PER_KPH


@dataclass(frozen=True)
class SpeedLimitParams:
    tx_enabled: bool = False            # --speed-limit-tx. 기본 off(시험으로 고정)
    release_kph: float = None           # --speed-limit-release-kph. 미정(업체 확인 대기) — 지어내지 않는다
    practice_kph: float = 30.0          # --speed-limit-practice-kph
    slow_brake_kph: float = 9.3         # --slow-brake-kph. 서행 때 0x203 값이자 잔류 제한 판정 경계
    allow_missing: bool = False         # --speed-limit-allow-missing
    fast_period_s: float = 0.02         # --speed-limit-fast-period
    keep_period_s: float = 0.1          # --speed-limit-keep-period
    confirm_frames: int = 3             # --speed-limit-confirm-frames
    mismatch_s: float = 0.3             # --speed-limit-mismatch-s
    release_note_s: float = 1.0         # --speed-limit-release-note-s (기록용, D14)
    startup_timeout_s: float = 2.0      # --speed-limit-startup-timeout

    def __post_init__(self):
        if not (self.fast_period_s > 0 and self.keep_period_s > 0 and self.confirm_frames >= 1
                and self.mismatch_s > 0 and self.release_note_s > 0 and self.startup_timeout_s > 0):
            raise ValueError(f'속도 제한 파라미터 값이 이상함: {self}')


@dataclass(frozen=True)
class StartupResult:
    action: str                 # ACTION_*
    expected_kph: float         # 고치거나 기다릴 값. 해제값 미설정이면 None
    exit_code: int              # 0 또는 EXIT_SPEED_LIMIT
    message: str
    events: tuple               # ((이름, detail), ...)


def startup_check(observed_kph, params, race_mode=True):
    """시작 전 점검(9.2 + A5). observed_kph: 점검 시간 안에 읽은 0x202 값(km/h), 한 번도 못 봤으면 None.
    race_mode: True = 경기(기대값 = 해제값), False = 연습(기대값 = practice 값)."""
    def done(action, expected, message):
        detail = (f'observed={observed_kph} expected={expected} tx={"on" if params.tx_enabled else "off"} '
                  f'race={race_mode} action={action}')
        code = EXIT_SPEED_LIMIT if action == ACTION_REFUSE else 0
        return StartupResult(action, expected, code, message, (('speed_limit_startup', detail),))

    if params.tx_enabled and params.release_kph is None:
        return done(ACTION_REFUSE, None, '--speed-limit-tx 를 켰는데 --speed-limit-release-kph 가 없음 — 해제값은 지어내지 않는다')

    expected = params.release_kph if race_mode else params.practice_kph

    if observed_kph is None:
        if race_mode and not params.allow_missing:
            return done(ACTION_REFUSE, expected, '0x202 가 안 보임 (--speed-limit-allow-missing 으로만 통과)')
        return done(ACTION_PASS, expected, '0x202 가 안 보임 — 통과')

    raw = kph_to_raw(observed_kph)
    if expected is None:    # A5: 경기 모드 + 해제값 미설정(tx off)
        if 0 < raw <= kph_to_raw(params.slow_brake_kph):
            return done(ACTION_REFUSE, None, f'차량에 {raw_to_kph(raw):g} km/h 제한이 남아 있음(서행 제한 잔류 의심) '
                                             '(--speed-limit-tx on 으로 해제 필요)')
        return done(ACTION_PASS, None, f'0x202 = {raw_to_kph(raw):g} km/h 기록(해제값 미설정)')

    if raw == kph_to_raw(expected):
        return done(ACTION_PASS, expected, f'0x202 = {raw_to_kph(raw):g} km/h, 기대값과 같음')
    if params.tx_enabled:
        return done(ACTION_CORRECT, expected, f'0x202 = {raw_to_kph(raw):g} km/h → 0x203 으로 {expected:g} 로 고침')
    return done(ACTION_REFUSE, expected, f'차량에 {raw_to_kph(raw):g} km/h 제한이 남아 있음 (--speed-limit-tx on 으로 해제 필요)')


@dataclass
class SpeedLimitOutput:
    tx_kph: float = None        # 이번 주기 0x203 으로 보낼 값(km/h). None = 보내지 않음
    tx_raw: int = None          # 같은 값의 raw — 인코더 반올림 차이를 확인할 때 쓴다
    events: list = field(default_factory=list)


class SpeedLimitManager:
    def __init__(self, params=None, race_mode=True):
        self.p = params if params is not None else SpeedLimitParams()
        if self.p.tx_enabled and self.p.release_kph is None:
            raise ValueError('--speed-limit-tx 를 켜려면 --speed-limit-release-kph 가 필요함')
        self._normal_raw = None
        if self.p.tx_enabled:
            self._normal_raw = kph_to_raw(self.p.release_kph if race_mode else self.p.practice_kph)
        self.state = STATE_IDLE
        self._target_raw = None
        self._last_tx = None
        self._match_n = 0
        self._mismatch_since = None
        self._t_change = None
        self._release_deadline = None
        self._startup_t0 = None
        self._startup_ok = False
        self._rx_raw = None
        self._rx_t = None
        self._baseline_raw = None       # tx off 읽기 점검의 기준(처음 본 값)
        self._foreign_n = 0
        self._foreign_logged_at = None
        self._events = []

    # --- 조회 ---------------------------------------------------------------------------------------------------
    @property
    def target_kph(self):
        """0x203 목표(km/h). 없으면 None(tx off 는 항상 None)."""
        return raw_to_kph(self._target_raw) if self._target_raw is not None else None

    @property
    def confirmed(self):
        return self.state == STATE_CONFIRMED

    @property
    def foreign_count(self):
        return self._foreign_n

    def vehicle_limit_kph(self, now, max_age_s=0.5):
        """0x202 마지막 값(km/h). 못 받았거나 max_age_s 보다 오래되면 None."""
        if self._rx_raw is None or now - self._rx_t > max_age_s:
            return None
        return raw_to_kph(self._rx_raw)

    # --- 입력 ---------------------------------------------------------------------------------------------------
    def on_0x202(self, now, kph):
        raw = kph_to_raw(kph)
        self._rx_raw, self._rx_t = raw, now
        watch = self._target_raw if self.p.tx_enabled else self._baseline_raw
        if not self.p.tx_enabled and self._baseline_raw is None:
            self._baseline_raw = watch = raw
        if watch is None:
            return
        if raw == watch:
            self._mismatch_since = None
            self._match_n += 1
            if self.state == STATE_CHANGING and self._match_n >= self.p.confirm_frames:
                self.state = STATE_CONFIRMED
                self._release_deadline = None
                self._startup_ok = self._startup_t0 is not None
                self._events.append(('speed_limit_confirm',
                                     f'target={raw_to_kph(raw):g} after={now - self._t_change:.3f}s'))
        else:
            self._match_n = 0
            if self._mismatch_since is None:
                self._mismatch_since = now

    def on_foreign_0x203(self, now, data_hex=''):
        """다른 송신자의 0x203(우리 소켓은 자기 송신을 받지 않으므로 수신되면 남의 것). 1 초에 한 줄만 기록."""
        self._foreign_n += 1
        if self._foreign_logged_at is None or now - self._foreign_logged_at >= _FOREIGN_LOG_PERIOD_S:
            self._events.append(('foreign_0x203', f'n={self._foreign_n} data={data_hex}'))
            self._foreign_logged_at = now

    # --- 목표 바꾸기 ----------------------------------------------------------------------------------------------
    def set_target(self, now, kph, reason='', is_release=False):
        """tx off 면 아무 일도 안 한다. 같은 목표를 다시 줘도 흐름을 흔들지 않는다."""
        if not self.p.tx_enabled:
            return
        raw = kph_to_raw(kph)
        if raw == self._target_raw:
            return
        self._target_raw = raw
        self.state = STATE_CHANGING
        self._last_tx = None
        self._match_n = 0
        self._mismatch_since = None
        self._t_change = now
        self._release_deadline = now + self.p.release_note_s if is_release else None
        self._events.append(('speed_limit_tx', f'target={raw_to_kph(raw):g} reason={reason}'))

    def start(self, now):
        """시작 점검 교정·시작 값 걸기: 평상시 값(해제값 또는 practice 값)을 목표로 한다. startup_result 로 결과를 본다."""
        if self.p.tx_enabled:
            self._startup_t0 = now
            self.set_target(now, raw_to_kph(self._normal_raw), reason='startup')

    def slow_on(self, now):
        self.set_target(now, self.p.slow_brake_kph, reason='slow_on')

    def slow_off(self, now):
        if self.p.tx_enabled:
            self.set_target(now, raw_to_kph(self._normal_raw), reason='slow_off', is_release=True)

    def startup_result(self, now):
        """True = 반영됨, False = startup_timeout_s 초과(시작 거부, EXIT_SPEED_LIMIT), None = 기다리는 중."""
        if self._startup_ok:
            return True
        if self._startup_t0 is not None and now - self._startup_t0 > self.p.startup_timeout_s:
            return False
        return None

    # --- 매 주기 ---------------------------------------------------------------------------------------------------
    def tick(self, now):
        out = SpeedLimitOutput()
        if self.p.tx_enabled:
            self._tick_tx(now, out)
        elif self._mismatch_since is not None and now - self._mismatch_since > self.p.mismatch_s:
            # tx off 읽기 점검: 처음 본 값에서 달라지면 기록만(고치지 않는다). 다시 같아질 때까지 한 번만
            self._events.append(('speed_limit_mismatch',
                                 f'rx={raw_to_kph(self._rx_raw):g} baseline={raw_to_kph(self._baseline_raw):g} tx=off'))
            self._baseline_raw = self._rx_raw
            self._mismatch_since = None
        out.events, self._events = self._events, []
        return out

    def _tick_tx(self, now, out):
        p = self.p
        if self.state == STATE_IDLE:
            return
        if self.state == STATE_CONFIRMED and self._mismatch_since is not None \
                and now - self._mismatch_since > p.mismatch_s:
            self._events.append(('speed_limit_mismatch',
                                 f'rx={raw_to_kph(self._rx_raw):g} target={raw_to_kph(self._target_raw):g} '
                                 f'for={now - self._mismatch_since:.3f}s'))
            self.state = STATE_CHANGING
            self._t_change = now
            self._match_n = 0
            self._mismatch_since = None
        if self._release_deadline is not None and now > self._release_deadline:
            # D14: 표시만 한다. 재시도·동작은 그대로
            self._events.append(('speed_limit_release_slow',
                                 f'target={raw_to_kph(self._target_raw):g} after={now - self._t_change:.3f}s'))
            self._release_deadline = None
        period = p.fast_period_s if self.state == STATE_CHANGING else p.keep_period_s
        if self._last_tx is None or now - self._last_tx >= period - _PERIOD_TOL_S:
            self._last_tx = now
            out.tx_raw = self._target_raw
            out.tx_kph = raw_to_kph(self._target_raw)
