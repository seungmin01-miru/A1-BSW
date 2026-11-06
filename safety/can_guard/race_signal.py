"""대회 신호 수신 해석 — 0x124·0x125·0x126·0x127·0x128·0x202·0x156 (지시서 docs/2026-11-06_engine_room_dev_plan.md 1.3·3절).

**전달만 한다.** 깃발을 해석(phase)하거나 차를 움직이는 판단을 넣지 않는다 — 반응은 모델 담당(지시서 1.1절).
같은 코드를 `race_state_publisher`(/race/state)와 can_guard(이벤트 로그)가 쓴다. 순수 로직: I/O 없음, 시각은 호출자가 넘김,
런타임에 DBC 를 읽지 않음. 정확성은 test/test_race_signal.py 가 cantools + `DBC/A1_dbc_2_fixed.dbc` 와 대조한다.

모든 신호 Intel. 0x124 byte0: GO=0x80, STOP=0x40, FINISH=0x20, SLOW_ON=0x10, SLOW_OFF=0x08.

해석기의 기록은 `take_records()` 로 (이벤트 이름, detail 문자열) 목록을 꺼낸다 — can_guard 이벤트 로그 형식과 같다.
"""
import math

from rx_decode import FRAME_ID_REMOTE_01, decode_remote_156

FRAME_ID_RACE_SIGNAL = 0x124
FRAME_ID_PIT_ZONE = 0x125
FRAME_ID_REPAIR = 0x126
FRAME_ID_RES = 0x127
FRAME_ID_RES_FB = 0x128
FRAME_ID_SERVICE_INFO = 0x202

# /race/state 해석에 쓰는 ID. 0x157 은 브리지 필터에는 있으나 해석에 쓰지 않는다(앱 En 은 0x156).
RACE_FRAME_IDS = (FRAME_ID_RACE_SIGNAL, FRAME_ID_PIT_ZONE, FRAME_ID_REPAIR, FRAME_ID_RES, FRAME_ID_RES_FB,
                  FRAME_ID_SERVICE_INFO, FRAME_ID_REMOTE_01)
# can_guard 이벤트 로그가 보는 ID. 앱은 can_guard 가 RemoteEstopDetector 로 이미 처리한다.
GUARD_LOG_FRAME_IDS = frozenset(RACE_FRAME_IDS) - {FRAME_ID_REMOTE_01}

FLAG_GO = 'go'
FLAG_STOP = 'stop'
FLAG_FINISH = 'finish'
FLAG_SLOW_ON = 'slow_on'
FLAG_SLOW_OFF = 'slow_off'
RACE_FLAGS = (FLAG_GO, FLAG_STOP, FLAG_FINISH, FLAG_SLOW_ON, FLAG_SLOW_OFF)
LAST_SIGNAL_NONE = 'NONE'

_FLAG_MASK = {FLAG_GO: 0x80, FLAG_STOP: 0x40, FLAG_FINISH: 0x20, FLAG_SLOW_ON: 0x10, FLAG_SLOW_OFF: 0x08}

DEFAULT_CONFIRM_FRAMES = 2          # 깃발만. RES 는 1 프레임 즉시(지시서 3절)
DEFAULT_RES_ALIVE_TIMEOUT_S = 0.5   # 잠정 — 대회 측 정의 없음(지시서 1.3절). 판단은 모델이 res_alive_ok·res_age_s 로 한다
IGNORED_LOG_PERIOD_S = 1.0          # race_signal_ignored 기록 간격(깜빡임이 이어져도 로그가 쏟아지지 않게)


def _frame(data):
    """모자란 바이트는 0 으로 채운다 — 짧은 프레임 때문에 메인 루프가 예외로 죽지 않게."""
    return bytes(data[:8]).ljust(8, b'\0')


def decode_race_signal(data):
    """0x124 → dict(go, stop, finish, slow_on, slow_off), 값은 0/1."""
    b0 = _frame(data)[0]
    return {name: 1 if b0 & mask else 0 for name, mask in _FLAG_MASK.items()}


def decode_pit_zone(data):
    """0x125 → (위도 deg, 경도 deg). 용도 미정이라 전달만 한다."""
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
    """0x128 → dict(res_end 0/1, res_error 0~7). KIAPI↔이아우토반 신호라 우리는 기록만 한다."""
    b0 = _frame(data)[0]
    return {'res_end': (b0 >> 7) & 1, 'res_error': b0 & 0x07}


def decode_speed_limit_info(data):
    """0x202 → 차량 속도 제한 현재값 km/h. 자율 속도 상한이라는 뜻이 아니다(11-04 30.0 = 원격 조종 제한)."""
    return (int.from_bytes(_frame(data)[0:2], 'little') & 0x3FF) * 0.1


def _is_undefined_pattern(data):
    """대회 자료의 예시는 깃발 하나씩이다. 깃발 둘 이상·정의 밖 비트(byte0 하위 3비트, byte1~7)는 기록해 둔다."""
    d = _frame(data)
    return bin(d[0] & 0xF8).count('1') > 1 or d[0] & 0x07 != 0 or any(d[1:])


class _Flag:
    """한 깃발의 연속 프레임 수 + 확인된 현재값."""

    __slots__ = ('on_run', 'off_run', 'level', 'seen', 'run_start_t', 'ignored_t', 'ignored_n')

    def __init__(self):
        self.on_run = self.off_run = 0
        self.level = self.seen = False
        self.run_start_t = None
        self.ignored_t = None
        self.ignored_n = 0


class RaceSignalDecoder:
    """0x124 프레임 → 2프레임 확인 현재값(level), seen 래치, last_signal, 나이.

    - 켜짐·꺼짐 모두 `confirm_frames` 프레임 연속이어야 바뀐다. 못 채우고 꺼진 켜짐은 무시 + `race_signal_ignored` 기록.
    - seen 은 한 번 확인되면 객체 수명(= BSW 재시작 전까지) 동안 유지. 해제 규칙 없음 — 해석은 모델.
    - last_signal 은 0→1 이 확인된 마지막 깃발, last_signal_t 는 그 연속 구간의 첫 프레임 시각.
    """

    def __init__(self, confirm_frames=DEFAULT_CONFIRM_FRAMES):
        self.confirm_frames = max(1, confirm_frames)
        self._flags = {name: _Flag() for name in RACE_FLAGS}
        self._records = []
        self._last_unknown = None
        self.last_seen_t = None
        self.last_signal = LAST_SIGNAL_NONE
        self.last_signal_t = None

    def feed(self, data, t):
        """0x124 프레임 한 개. 다른 ID 는 호출자가 거른다. 데이터 없는 프레임은 값으로 치지 않는다."""
        raw = bytes(data[:8])
        if not raw:
            self._records.append(('race_signal_unknown', 'empty_frame'))
            return
        self.last_seen_t = t
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
                f.run_start_t = t
            f.on_run += 1
            f.off_run = 0
            if f.on_run >= self.confirm_frames and not f.level:
                f.level = f.seen = True
                self.last_signal, self.last_signal_t = name.upper(), f.run_start_t
                self._records.append(('race_signal', f'{name} on'))
            return
        self._note_ignored(name, f, t)
        f.off_run += 1
        f.on_run = 0
        if f.off_run >= self.confirm_frames and f.level:
            f.level = False
            self._records.append(('race_signal', f'{name} off'))

    def _note_ignored(self, name, f, t):
        if not 0 < f.on_run < self.confirm_frames or f.level:
            return
        if f.ignored_t is not None and t - f.ignored_t < IGNORED_LOG_PERIOD_S:
            f.ignored_n += 1
            return
        self._records.append(('race_signal_ignored', f'{name} frames={f.on_run} suppressed={f.ignored_n}'))
        f.ignored_t, f.ignored_n = t, 0

    def take_records(self):
        out = self._records
        self._records = []
        return out

    @property
    def levels(self):
        return {name: f.level for name, f in self._flags.items()}

    @property
    def seen_flags(self):
        return {name: f.seen for name, f in self._flags.items()}

    def age_s(self, now):
        """마지막 0x124 수신 후 경과. 한 번도 못 봤으면 None."""
        return None if self.last_seen_t is None else now - self.last_seen_t


class ResMonitor:
    """0x127 프레임 → `active`(RES_START, 1 프레임 즉시), `alive_ok`, 나이.

    - active 는 켜짐·꺼짐 모두 프레임 값을 그대로 쓴다(지시서 3절). 해제 순간 차량은 즉시 우리 명령을 따르므로 지연을 두지 않는다.
    - alive_ok: 프레임이 `alive_timeout_s` 안에 왔고 alive 값이 그 안에 바뀌었으면 True. 못 봤으면 False.
      서버가 카운터를 안 올리는 경우도 False 로 알린다(한 번도 안 바뀌면 `res_alive_never_changed` 기록) — 판단은 모델.
    - alive 건너뜀은 기록만(`res_alive_jump`). 255 → 0 은 정상.
    """

    def __init__(self, alive_timeout_s=DEFAULT_RES_ALIVE_TIMEOUT_S):
        self.alive_timeout_s = alive_timeout_s
        self.active = False
        self.alive_ok = False
        self.last_seen_t = None
        self.last_alive = None
        self._last_change_t = None
        self._changed = False
        self._never_recorded = False
        self._was_ok = False
        self._records = []

    def feed(self, data, t):
        """0x127 프레임 한 개. 다른 ID 는 호출자가 거른다."""
        f = decode_res(data)
        alive = f['alive']
        if self.last_seen_t is None:
            self._last_change_t = t
        elif alive != self.last_alive:
            self._last_change_t = t
            self._changed = True
            if alive != ((self.last_alive + 1) & 0xFF):
                self._records.append(('res_alive_jump', f'{self.last_alive}->{alive}'))
        self.last_seen_t, self.last_alive = t, alive
        active = bool(f['res_start'])
        if active != self.active:
            self.active = active
            self._records.append(('res', f'{"start" if active else "end"} alive={alive}'))
        self.update(t)

    def update(self, now):
        """끊김·alive 정지 판정. 프레임이 없어도 주기적으로 부른다."""
        if self.last_seen_t is None:
            return
        fresh = now - self.last_seen_t <= self.alive_timeout_s
        moving = now - self._last_change_t <= self.alive_timeout_s
        self.alive_ok = fresh and moving
        if self._was_ok and not self.alive_ok:
            self._records.append(('res_alive_lost', 'timeout' if not fresh else 'alive_stall'))
        elif self.alive_ok and not self._was_ok and self._changed:
            self._records.append(('res_alive_restored', f'alive={self.last_alive}'))
        elif not self.alive_ok and not self._changed and not self._never_recorded:
            self._never_recorded = True
            self._records.append(('res_alive_never_changed', f'alive={self.last_alive}'))
        self._was_ok = self.alive_ok

    def take_records(self):
        out = self._records
        self._records = []
        return out

    def age_s(self, now):
        return None if self.last_seen_t is None else now - self.last_seen_t


class RaceTracker:
    """대회 신호 프레임 전부 → /race/state 필드 값. `feed` 를 ID 가릴 것 없이 부르고 `fields(now)` 로 읽는다."""

    def __init__(self, confirm_frames=DEFAULT_CONFIRM_FRAMES, alive_timeout_s=DEFAULT_RES_ALIVE_TIMEOUT_S):
        self.flags = RaceSignalDecoder(confirm_frames)
        self.res = ResMonitor(alive_timeout_s)
        self.res_end = False
        self.res_error = 0
        self.remote_app_active = False
        self.vehicle_speed_limit_kph = -1.0
        self.pit_lat_deg = self.pit_lon_deg = math.nan
        self.repair = None
        self._records = []

    def feed(self, can_id, data, t):
        """프레임 한 개. 처리한 ID 면 True."""
        if can_id == FRAME_ID_RACE_SIGNAL:
            self.flags.feed(data, t)
        elif can_id == FRAME_ID_RES:
            self.res.feed(data, t)
        elif can_id == FRAME_ID_RES_FB:
            fb = decode_res_fb(data)
            end, err = bool(fb['res_end']), fb['res_error']
            if end != self.res_end or err != self.res_error:
                self._records.append(('res_fb', f'end={int(end)} error={err}'))
            self.res_end, self.res_error = end, err
        elif can_id == FRAME_ID_SERVICE_INFO:
            kph = round(decode_speed_limit_info(data), 1)
            if kph != self.vehicle_speed_limit_kph:
                self._records.append(('speed_limit_info', f'{kph:.1f}'))
            self.vehicle_speed_limit_kph = kph
        elif can_id == FRAME_ID_REMOTE_01:
            d = decode_remote_156(_frame(data))
            active = bool(d['eps_en'] or d['acc_en'] or d['aeb_en'])
            if active != self.remote_app_active:
                self._records.append(('remote_app', 'active' if active else 'released'))
            self.remote_app_active = active
        elif can_id == FRAME_ID_PIT_ZONE:
            lat, lon = decode_pit_zone(data)
            if (lat, lon) != (self.pit_lat_deg, self.pit_lon_deg):
                self._records.append(('pit_zone', f'{lat:.7f},{lon:.7f}'))
            self.pit_lat_deg, self.pit_lon_deg = lat, lon
        elif can_id == FRAME_ID_REPAIR:
            v = decode_repair(data)
            if v != self.repair:
                self._records.append(('repair', str(v)))
            self.repair = v
        else:
            return False
        return True

    def update(self, now):
        self.res.update(now)

    def state_key(self):
        """나이를 뺀 값 전부 — 바뀌면 곧바로 발행한다."""
        return (tuple(self.flags.levels.values()), tuple(self.flags.seen_flags.values()), self.flags.last_signal,
                self.flags.last_signal_t, self.res.active, self.res.alive_ok, self.res_end, self.res_error,
                self.remote_app_active, self.vehicle_speed_limit_kph, repr(self.pit_lat_deg), repr(self.pit_lon_deg))

    def fields(self, now):
        """RaceState 메시지에 넣을 값. last_signal_t 는 초(float) 또는 None — Time 변환은 발행자가 한다."""
        lv, seen = self.flags.levels, self.flags.seen_flags
        out = {f'sig_{n}': lv[n] for n in RACE_FLAGS}
        out.update({f'seen_{n}': seen[n] for n in RACE_FLAGS})
        age, res_age = self.flags.age_s(now), self.res.age_s(now)
        out.update(last_signal=self.flags.last_signal, last_signal_t=self.flags.last_signal_t,
                   race_signal_age_s=-1.0 if age is None else age,
                   res_active=self.res.active, res_alive_ok=self.res.alive_ok,
                   res_age_s=-1.0 if res_age is None else res_age,
                   res_end=self.res_end, res_error=self.res_error, remote_app_active=self.remote_app_active,
                   vehicle_speed_limit_kph=self.vehicle_speed_limit_kph,
                   pit_lat_deg=self.pit_lat_deg, pit_lon_deg=self.pit_lon_deg)
        return out

    def take_records(self):
        out = self.flags.take_records() + self.res.take_records() + self._records
        self._records = []
        return out
