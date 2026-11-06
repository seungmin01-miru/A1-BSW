"""대회 신호 수신 해석 — 0x124·0x125·0x126·0x127·0x128·0x202·0x710·0x711 (계획서 1.2절, WP1).

rx_decode.py 와 같은 원칙: 런타임에 DBC 를 읽지 않고 시각은 호출자가 넘긴다(순수 로직, I/O 없음). 정확성은
test/test_race_signal.py 가 cantools + `DBC/A1_dbc_2_fixed.dbc` 와 대조한다.

모든 신호 Intel. 0x124 의 start bit 7/6/5/4/3 은 byte0 의 비트 7/6/5/4/3 이다: GO=0x80, STOP=0x40, FINISH=0x20,
SLOW_ON=0x10, SLOW_OFF=0x08 (계획서 1.2절).

해석기의 기록은 `take_records()` 로 (이벤트 이름, detail 문자열) 목록을 꺼낸다 — can_guard 이벤트 로그 형식과 같다.
"""

FRAME_ID_RACE_SIGNAL = 0x124
FRAME_ID_PIT_ZONE = 0x125
FRAME_ID_REPAIR = 0x126
FRAME_ID_RES = 0x127
FRAME_ID_RES_FB = 0x128
FRAME_ID_SERVICE_INFO = 0x202
FRAME_ID_FB_STEER = 0x710
FRAME_ID_FB_ACC = 0x711

FLAG_GO = 'go'
FLAG_STOP = 'stop'
FLAG_FINISH = 'finish'
FLAG_SLOW_ON = 'slow_on'
FLAG_SLOW_OFF = 'slow_off'
RACE_FLAGS = (FLAG_GO, FLAG_STOP, FLAG_FINISH, FLAG_SLOW_ON, FLAG_SLOW_OFF)

# 해제 방향 깃발 — 시작 때 이미 1이면 이벤트를 내지 않는다(A1, R-01). 나머지는 제한 방향.
RELEASE_FLAGS = (FLAG_GO, FLAG_SLOW_OFF)

_FLAG_MASK = {FLAG_GO: 0x80, FLAG_STOP: 0x40, FLAG_FINISH: 0x20, FLAG_SLOW_ON: 0x10, FLAG_SLOW_OFF: 0x08}

DEFAULT_CONFIRM_FRAMES = 2          # D12·A7
DEFAULT_RES_ALIVE_TIMEOUT_S = 0.5   # 잠정, 계획서 10절
DEFAULT_SILENCE_ARM_S = 0.5         # 잠정, 브리프 A12


def _frame(data):
    """모자란 바이트는 0 으로 채운다 — 짧은 프레임 때문에 메인 루프가 예외로 죽지 않게."""
    return bytes(data[:8]).ljust(8, b'\0')


def decode_race_signal(data):
    """0x124 → dict(go, stop, finish, slow_on, slow_off), 값은 0/1."""
    b0 = _frame(data)[0]
    return {name: 1 if b0 & mask else 0 for name, mask in _FLAG_MASK.items()}


def decode_pit_zone(data):
    """0x125 → (위도 deg, 경도 deg). 용도 확인 중이라 기록만 한다."""
    d = _frame(data)
    return int.from_bytes(d[0:4], 'little') * 1e-7, int.from_bytes(d[4:8], 'little') * 1e-7


def decode_repair(data):
    """0x126 → SIG_REPAIR(0~255). 뜻이 정의되지 않아 기록만 한다."""
    return _frame(data)[0]


def decode_res(data):
    """0x127 → dict(res_start 0/1, alive 0~255)."""
    d = _frame(data)
    return {'res_start': (d[0] >> 7) & 1, 'alive': d[7]}


def decode_res_fb(data):
    """0x128 → dict(res_end 0/1, res_error 0~7). KIAPI↔이아우토반 신호라 우리는 기록만 한다(계획서 1.3절)."""
    b0 = _frame(data)[0]
    return {'res_end': (b0 >> 7) & 1, 'res_error': b0 & 0x07}


def decode_speed_limit_info_raw(data):
    """0x202 → raw(0.1 km/h 단위). 일치 비교는 이 값으로 한다(계획서 9.3절)."""
    return int.from_bytes(_frame(data)[0:2], 'little') & 0x3FF


def decode_speed_limit_info(data):
    """0x202 → 차량 속도 제한 현재값 km/h. 자율 속도 상한이라는 뜻이 아니다(A5)."""
    return decode_speed_limit_info_raw(data) * 0.1


def decode_fb_steer(data):
    """0x710 → 기록용. `eps_user_can_err` 은 TODO-2(0x210 끊김 동작) 확인에 쓴다."""
    d = _frame(data)
    return {'acc_mode': (d[0] >> 1) & 1, 'brake_mode': (d[0] >> 2) & 1, 'steer_mode': (d[0] >> 3) & 1,
            'eps_user_can_err': (d[0] >> 4) & 1, 'eps_err': (d[0] >> 5) & 1, 'eps_sas_err': (d[0] >> 7) & 1,
            'str_ang_deg': int.from_bytes(d[2:4], 'little', signed=True) * 0.1, 'alive': d[7]}


def decode_fb_acc(data):
    """0x711 → 기록용. `vs_kph` 는 판단에 쓰지 않는다(차속 원천은 0x201)."""
    d = _frame(data)
    return {'acc_mode': (d[0] >> 1) & 1, 'brake_mode': (d[0] >> 2) & 1, 'steer_mode': (d[0] >> 3) & 1,
            'acc_user_can_err': (d[0] >> 4) & 1, 'acc_err': (d[0] >> 7) & 1,
            'vs_kph': (int.from_bytes(d[2:4], 'little') & 0xFFF) * 0.1, 'alive': d[7]}


def _is_undefined_pattern(data):
    """계획서 1.2절 예시는 깃발 하나씩이다. 깃발 둘 이상·정의 밖 비트(byte0 하위 3비트, byte1~7)는 정의 밖."""
    d = _frame(data)
    return bin(d[0] & 0xF8).count('1') > 1 or d[0] & 0x07 != 0 or any(d[1:])


IGNORED_LOG_PERIOD_S = 1.0     # race_signal_ignored 기록 간격(깜빡임이 이어져도 로그가 쏟아지지 않게)


class _FlagDebounce:
    """한 깃발의 연속 프레임 수 + 확인된 level."""

    def __init__(self, armed):
        self.on_run = 0
        self.off_run = 0
        self.level = False
        self.armed = armed
        self.trigger_t = None
        self.start_recorded = False
        self.ignored_t = None       # 마지막 race_signal_ignored 기록 시각
        self.ignored_n = 0          # 그 뒤 기록 안 하고 버린 수


class RaceSignalDecoder:
    """0x124 프레임 → 깃발 이벤트(올라가는 순간)·level·나이. 계획서 4.1·4.2절, D12, A1.

    - 깃발은 `confirm_frames` 프레임 연속 1 이어야 인정한다(1 프레임만 켜진 신호는 이벤트가 아니다). 못 채우고 꺼지면 기록 `race_signal_ignored`(S3).
    - level 은 `confirm_frames` 프레임 연속 0 이어야 내려간다. 사이의 단발 0 은 새 이벤트를 만들지 않는다.
    - A1 비대칭: 해제 방향(go, slow_off)은 **0 을 확인한 뒤의 상승만** 이벤트다. 시작 때 이미 켜져 있으면 이벤트 없이
      `race_signal` 기록(`go_level_at_start` 등)만 남긴다 — 재시작 직후 옛 Go 로 출발하면 R-01 위반.
      0 확인도 `confirm_frames` 연속으로 본다(옛 Go 위의 단발 0 + 복귀를 새 Go 로 오인하지 않게).
    - 제한 방향(stop, finish, slow_on)은 처음부터 1 이어도 확인 뒤 이벤트다.
    메인 루프 한 주기에 프레임 여러 개가 와도 이벤트를 잃지 않도록 `take_events()` 까지 쌓아 둔다.
    - A12 조용함 무장: 0x124 가 깃발 있을 때만 송신되는 방식이면 0 프레임을 볼 수 없어 첫 Go 를 영영 못 받는다. 듣기 시작한 뒤(`note_silence`
      첫 호출) 또는 마지막 프레임 뒤 `silence_arm_s` 동안 프레임이 없으면 해제 방향 깃발을 무장하고, 연속 프레임 수·level 을 지운다(조용한 구간을
      사이에 둔 프레임은 "연속"이 아니다). 옛 Go 가 계속 나오는 경우는 조용한 구간이 없으므로 R-01 은 지켜진다.
    """

    def __init__(self, confirm_frames=DEFAULT_CONFIRM_FRAMES, silence_arm_s=DEFAULT_SILENCE_ARM_S):
        self.confirm_frames = max(1, confirm_frames)
        self.silence_arm_s = silence_arm_s
        self._listen_t = None
        self._quiet_applied = False
        self._flags = {name: _FlagDebounce(name not in RELEASE_FLAGS) for name in RACE_FLAGS}
        self._events = []
        self._records = []
        self.last_seen_t = None
        self._last_unknown = None

    def feed(self, data, t):
        """0x124 프레임 한 개. 다른 ID 는 호출자가 거른다. 데이터 없는 프레임은 값으로 치지 않는다."""
        raw = bytes(data[:8])
        if not raw:
            self._records.append(('race_signal_unknown', 'empty_frame'))
            return
        if self._listen_t is not None:
            self.note_silence(t)     # 메인 루프가 밀렸어도 이 프레임 앞의 조용한 구간을 먼저 반영한다
        self.last_seen_t = t
        self._quiet_applied = False
        if _is_undefined_pattern(raw):
            if raw != self._last_unknown:
                self._records.append(('race_signal_unknown', f'raw={raw.hex()}'))
            self._last_unknown = raw
        else:
            self._last_unknown = None
        values = decode_race_signal(raw)
        for name in RACE_FLAGS:
            self._feed_flag(name, self._flags[name], values[name], t)

    def _feed_flag(self, name, f, value, t):
        if value:
            if f.on_run == 0:
                f.trigger_t = t
            f.on_run += 1
            f.off_run = 0
            if f.on_run >= self.confirm_frames and not f.level:
                f.level = True
                if f.armed:
                    self._events.append(name)
                elif not f.start_recorded:
                    f.start_recorded = True
                    self._records.append(('race_signal', f'{name}_level_at_start'))
        else:
            self._note_ignored(name, f, t)
            f.off_run += 1
            f.on_run = 0
            if f.off_run >= self.confirm_frames:
                f.level = False
                f.armed = True

    def _note_ignored(self, name, f, t):
        """확인 프레임 수를 못 채우고 꺼진 깃발(계획서 S3 "무시, 이벤트 기록"). 깜빡임이 이어져도 1 초에 한 줄만."""
        if not 0 < f.on_run < self.confirm_frames or f.level:
            return
        if f.ignored_t is not None and t - f.ignored_t < IGNORED_LOG_PERIOD_S:
            f.ignored_n += 1
            return
        self._records.append(('race_signal_ignored', f'{name} frames={f.on_run} suppressed={f.ignored_n}'))
        f.ignored_t, f.ignored_n = t, 0

    def note_silence(self, now):
        """A12. 매 주기 호출(프레임이 없어도). 조용한 구간이 `silence_arm_s` 를 넘으면 한 번 무장한다. ≤ 0 이면 끔."""
        if self._listen_t is None:
            self._listen_t = now
        if self.silence_arm_s <= 0 or self._quiet_applied:
            return
        ref = self.last_seen_t if self.last_seen_t is not None else self._listen_t
        if now - ref < self.silence_arm_s:
            return
        self._quiet_applied = True
        newly = []
        for name, f in self._flags.items():
            if not f.armed:
                newly.append(name)
            self._note_ignored(name, f, now)
            f.armed = True
            f.on_run = f.off_run = 0
            f.level = False
        if newly:
            self._records.append(('race_signal', f'silence_armed {"+".join(newly)} quiet={now - ref:.3f}s'))

    def take_events(self):
        """쌓인 깃발 이벤트를 꺼낸다(중복 없음, 도착 순서). 우선순위는 race_state 가 적용한다."""
        out = tuple(dict.fromkeys(self._events))
        self._events.clear()
        return out

    def take_records(self):
        out = self._records
        self._records = []
        return out

    @property
    def levels(self):
        """확인된 현재값 — 기록·표시와 go-reset 조건(STOP=0)에만 쓴다. 전이 판단은 이벤트로 한다."""
        return {name: f.level for name, f in self._flags.items()}

    def trigger_t(self, name):
        """그 깃발 연속 구간의 첫 프레임 시각(감지 지연 계산용). 본 적 없으면 None."""
        return self._flags[name].trigger_t

    def age_s(self, now):
        """마지막 0x124 수신 후 경과. 한 번도 못 봤으면 None."""
        return None if self.last_seen_t is None else now - self.last_seen_t

    @property
    def seen(self):
        return self.last_seen_t is not None


class ResMonitor:
    """0x127 프레임 → `active`(RES_START 확인값), `alive_lost`. 계획서 1.3절, A7.

    - RES_START 는 `confirm_frames` 프레임 연속이어야 인정, 내려갈 때도 같다.
    - 끊김: **한 번 본 뒤** `alive_timeout_s` 넘게 프레임이 없으면 `alive_lost`. 못 본 동안은 끊김이 아니다.
    - alive 정지: alive 가 **변하는 것을 본 뒤** 그 시간 넘게 그대로면 `alive_lost`. 한 번도 안 변했으면(서버가
      카운터를 안 올리는 경우일 수 있다) 경기를 끝내지 않고 `res_alive_never_changed` 기록만 한다.
    - alive 건너뜀: 기록만(`res_alive_jump`). 255 → 0 은 정상.
    - 프레임·alive 가 돌아오면 `alive_lost` 는 풀린다(래치는 race_state 의 몫). `alive_timeout_s` ≤ 0 이면 끊김 판정 끔.
    `update(now)` 를 매 주기 호출한다.
    """

    def __init__(self, confirm_frames=DEFAULT_CONFIRM_FRAMES, alive_timeout_s=DEFAULT_RES_ALIVE_TIMEOUT_S):
        self.confirm_frames = max(1, confirm_frames)
        self.alive_timeout_s = alive_timeout_s
        self.active = False
        self.alive_lost = False
        self.trigger_t = None
        self.last_seen_t = None
        self.last_alive = None
        self._on_run = 0
        self._off_run = 0
        self._run_start_t = None
        self._first_seen_t = None
        self._last_change_t = None
        self._never_changed_recorded = False
        self._records = []

    def feed(self, data, t):
        """0x127 프레임 한 개. 다른 ID 는 호출자가 거른다."""
        f = decode_res(data)
        if self._first_seen_t is None:
            self._first_seen_t = t
        self.last_seen_t = t
        alive = f['alive']
        if self.last_alive is not None and alive != self.last_alive:
            self._last_change_t = t
            expected = (self.last_alive + 1) & 0xFF
            if alive != expected:
                self._records.append(('res_alive_jump', f'{self.last_alive}->{alive}'))
        self.last_alive = alive
        if f['res_start']:
            if self._on_run == 0:
                self._run_start_t = t
            self._on_run += 1
            self._off_run = 0
            if self._on_run >= self.confirm_frames and not self.active:
                self.active = True
                self.trigger_t = self._run_start_t
                self._records.append(('res_start', f'alive={alive}'))
        else:
            self._off_run += 1
            self._on_run = 0
            if self._off_run >= self.confirm_frames and self.active:
                self.active = False
                self._records.append(('res_end', f'alive={alive}'))

    def update(self, now):
        """끊김·alive 정지 판정. 매 주기 호출."""
        if self.alive_timeout_s <= 0 or self.last_seen_t is None:
            return
        reason = None
        if now - self.last_seen_t > self.alive_timeout_s:
            reason = 'timeout'
        elif self._last_change_t is not None and now - self._last_change_t > self.alive_timeout_s:
            reason = 'alive_stall'
        elif self._last_change_t is None and now - self._first_seen_t > self.alive_timeout_s \
                and not self._never_changed_recorded:
            self._never_changed_recorded = True
            self._records.append(('res_alive_never_changed', f'alive={self.last_alive}'))
        if reason and not self.alive_lost:
            self.alive_lost = True
            self.trigger_t = now
            self._records.append(('res_alive_lost', reason))
        elif reason is None and self.alive_lost:
            self.alive_lost = False
            self._records.append(('res_alive_restored', f'alive={self.last_alive}'))

    def take_records(self):
        out = self._records
        self._records = []
        return out

    @property
    def seen(self):
        return self.last_seen_t is not None

    @property
    def asserted(self):
        return self.active or self.alive_lost
