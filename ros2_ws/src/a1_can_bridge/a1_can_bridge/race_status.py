"""StatusChannel 읽기·변환 — ROS 무관(시험 가능). 어댑터(cmd_adapter)와 race_state_publisher 가 같이 쓴다.

경기 상태를 판단하는 곳은 can_guard 하나뿐이다(D5). 여기는 공유메모리를 읽어 옮기기만 한다.
protocol.py 는 호출자가 넘긴다(can_guard_dir 파라미터로 불러온 그 모듈 — 구조체 정의를 복사하지 않으려고).
"""
import math

RESULT_NO_SEGMENT = '없음'          # 세그먼트 없음 = can_guard 미실행·구버전
RESULT_NO_DATA = '오래됨'           # 세그먼트는 있는데 stamp 가 오래됐거나 한 번도 안 써짐
RESULT_OK = '정상'


class StatusReader:
    """오래 사는 읽는 쪽의 재연결 처리.

    poll(now) 는 GuardStatus 또는 None(없음)을 돌려주고 예외를 내지 않는다.
      - 세그먼트가 없으면 reopen_s 마다 open_or_none 재시도.
      - read() 가 None(오래됨·미기록)이면 reopen_s 마다 닫고 다시 연다. can_guard 가 재시작하면 세그먼트를 새로 만들므로
        옛 핸들은 옛 내용을 계속 보여 준다(protocol.StatusChannel 설명).
      - TornReadError 는 "이번 주기 새 값 없음": 직전 값은 자기 stamp 가 max_age_s 안일 때만 유지하고 넘으면 None.
        한 번의 찢어진 읽기 때문에 기존 규칙(150 ms·±15 래치)으로 튀지 않게 하면서, 오래된 값을 더 믿지도 않는다.
    """

    def __init__(self, proto, name=None, reopen_s=1.0, max_age_s=None):
        self.proto = proto
        self.channel_cls = getattr(proto, 'StatusChannel', None)   # 구버전 protocol.py 면 None → 항상 "없음"
        self.name = name or getattr(proto, 'STATUS_SHM_NAME_DEFAULT', None)
        self.reopen_s = reopen_s
        self.max_age_s = getattr(proto, 'STATUS_MAX_AGE_S_DEFAULT', 0.1) if max_age_s is None else max_age_s
        self.ch = None
        self.last_try = None
        self.last = None               # 직전 GuardStatus
        self.last_stamp = None         # 그 stamp(호출자 시계 기준 = now − age_s)
        self.torn_count = 0
        self.result = RESULT_NO_SEGMENT

    def _due(self, now):
        return self.last_try is None or now - self.last_try >= self.reopen_s

    def _open(self, now):
        self.last_try = now
        if self.channel_cls is None:
            return
        try:
            self.ch = self.channel_cls.open_or_none(self.name)
        except (ValueError, OSError):   # 만드는 도중(크기 0)·크기가 다른 구버전 세그먼트 — 없음으로 보고 다음에 다시
            self.ch = None

    def close(self):
        if self.ch is not None:
            try:
                self.ch.close()
            except Exception:
                pass
        self.ch = None

    def _none(self, result):
        self.result = result
        self.last = self.last_stamp = None
        return None

    def _good(self, st, now):
        self.last, self.last_stamp = st, now - st.age_s
        self.result = RESULT_OK
        return st

    def poll(self, now):
        if self.ch is None:
            if self._due(now):
                self._open(now)
            if self.ch is None:
                return self._none(RESULT_NO_SEGMENT)
        try:
            st = self.ch.read(max_age_s=self.max_age_s)
        except self.proto.TornReadError:
            self.torn_count += 1
            if self.last is not None and now - self.last_stamp <= self.max_age_s:
                return self.last
            return self._none(RESULT_NO_DATA)
        except (ValueError, OSError):
            self.close()
            return self._none(RESULT_NO_SEGMENT)
        if st is not None:
            return self._good(st, now)
        if self._due(now):
            self.close()
            self._open(now)
            if self.ch is None:
                return self._none(RESULT_NO_SEGMENT)
            try:
                st = self.ch.read(max_age_s=self.max_age_s)
            except (self.proto.TornReadError, ValueError, OSError):
                st = None
            if st is not None:
                return self._good(st, now)
        return self._none(RESULT_NO_DATA)


def _finite_or(x, default):
    return float(x) if math.isfinite(x) else default


def display_phase(st, proto):
    """/race/state.phase 표시값(계획서 3.1): RACING + slow_active = SLOW(2). can_guard 가 어느 쪽으로 써도 같다."""
    code = int(st.race_phase)
    if code == proto.RACE_PHASE_CODE['RACING'] and st.slow_active:
        return proto.RACE_PHASE_CODE['SLOW']
    return code


def status_to_fields(st, proto):
    """GuardStatus → RaceState 메시지 필드(dict). 없음 표기: 숫자 −1, 좌표 NaN. 비유한값이 들어와도 표기를 지킨다."""
    phase = display_phase(st, proto)
    return {
        'phase': phase,
        'phase_name': proto.RACE_PHASE_NAME.get(phase, f'?{phase}'),
        'sig_go': bool(st.sig_go),
        'sig_slow_on': bool(st.sig_slow_on),
        'sig_slow_off': bool(st.sig_slow_off),
        'sig_finish': bool(st.sig_finish),
        'sig_stop': bool(st.sig_stop),
        'race_signal_age_s': _finite_or(st.race_signal_age_s, -1.0),
        'res_active': bool(st.res_active),
        'res_alive_ok': bool(st.res_alive_ok),
        'long_override': bool(st.long_override),
        'speed_cap_kph': _finite_or(st.speed_cap_kph, -1.0),
        'vehicle_speed_limit_kph': _finite_or(st.vehicle_speed_limit_kph, -1.0),
        'model_healthy_s': _finite_or(st.model_healthy_s, 0.0),
        'guard_state': st.guard_state_name,
        'pit_lat_deg': float(st.pit_lat_deg),
        'pit_lon_deg': float(st.pit_lon_deg),
    }


# 값이 바뀌면 다음 주기를 기다리지 않고 바로 낸다. 계속 변하는 값(나이·연속 정상 시간)은 넣지 않는다 — 넣으면 매번 "바뀜"이다.
_VOLATILE = ('race_signal_age_s', 'model_healthy_s')


def change_key(fields):
    """같으면 "안 바뀜". NaN 은 자기 자신과 같지 않으므로 문자열로 비교한다(안 그러면 좌표 없을 때 매번 바뀜)."""
    return tuple(repr(v) if isinstance(v, float) else v
                 for k, v in sorted(fields.items()) if k not in _VOLATILE)


class PublishGate:
    """poll 때마다 부른다: 값이 바뀌었거나 마지막 발행 뒤 period_s 가 지났으면 True."""

    def __init__(self, period_s=0.02):
        self.period_s = period_s
        self.last_key = None
        self.last_pub = None

    def should_publish(self, fields, now):
        key = change_key(fields)
        if key != self.last_key or self.last_pub is None or now - self.last_pub >= self.period_s:
            self.last_key, self.last_pub = key, now
            return True
        return False
