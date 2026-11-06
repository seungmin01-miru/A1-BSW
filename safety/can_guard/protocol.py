"""CommandChannel — 제어 노드(ROS2, ros2_ws/) 와 can_guard(이 디렉터리) 사이의 공유메모리 인터페이스.

§7.2: "명령은 작은 공유메모리 ring(seq + timestamp) 으로 온다. ROS2/DDS 는 hot loop 에 안 넣는다."
실제로 필요한 건 **최신값 하나**뿐이다 — 제어 명령은 새 값이 오면 이전 값이 의미가 없다(큐가 아니다).
그래서 "ring" 대신 **seqlock 단일 슬롯**으로 구현한다(설계와의 차이는 docs/can_stack_development.md §5.D 에 기록).

seqlock 프로토콜:
  쓰기: seq 를 홀수로(쓰는 중 표시) → 페이로드 기록 → seq 를 +1(짝수, 완료 표시)
  읽기: seq1 읽기 → 페이로드 복사 → seq2 읽기 → seq1==seq2 이고 짝수면 성공, 아니면 재시도
이 프로토콜이 정합성을 보장하는 건 "재시도"지 "단일 명령어 원자성"이 아니다 — 필드별 쓰기가 찢어져도(torn write)
읽기 쪽이 그 순간의 seq 불일치로 걸러낸다. 프로세스 두 개(스레드 아님)가 공유하므로 `multiprocessing.shared_memory`
위에 `ctypes.Structure` 를 얹는다.

  guard = CommandChannel.create('a1_can_guard_cmd')     # can_guard 쪽 — §7.2 "guard 가 먼저 뜬다"라서
                                                          # 세그먼트도 guard 가 만든다(재시작 때마다 새로 =
                                                          # 항상 안전 상태에서 시작, §7.2 Startup 규칙).
  ctrl = CommandChannel.open('a1_can_guard_cmd')        # 제어 노드 쪽, 기존 세그먼트 열기(재시작해도 재사용)
  ctrl.write(Command(steer_auto=True, steer_cmd_deg=1.5, ...))
  cmd, age_s = guard.read()                             # age_s 는 monotonic 기준 나이(초) — staleness 판정용

`HeartbeatChannel` 도 같은 seqlock 패턴이지만 페이로드가 없다(살아있다는 사실 자체가 정보) — 인지 프로세스가
얼마나 자주 신호를 보내는지도 guard 입장에선 몰라도 되고, "마지막으로 본 게 언제냐"만 있으면 된다.
"""
import ctypes
import time
from dataclasses import dataclass
from multiprocessing import shared_memory
from multiprocessing import resource_tracker

HEARTBEAT_SHM_NAME_DEFAULT = 'a1_can_guard_perception_hb'

SHM_NAME_DEFAULT = 'a1_can_guard_cmd'


class TornReadError(RuntimeError):
    """read() 가 재시도를 다 써도 안정된 스냅샷을 못 얻었을 때 — 드물면 정상(§5.D 참고), 계속 나면 이상 신호."""


def _unregister_from_tracker(shm):
    """Python 3.10 의 known wart: `SharedMemory(create=False)` 도 기본적으로 resource_tracker 에 등록되는데,
    이 프로세스는 소유자가 아니라 unlink 할 일이 없다. 이 핸들을 연 프로세스가 SIGKILL 로 죽으면(P-1/P-2 SIL
    시험이 실제로 이렇게 한다) tracker 가 "추적하던 걸 못 지웠다"는 경고를 찍는다 — 실제 누수가 아니다(세그먼트는
    `create()` 쪽이 소유하고 있고 그쪽이 정상적으로 unlink 한다). 등록에서 빼서 이 무해한 경고를 없앤다."""
    try:
        resource_tracker.unregister(shm._name, 'shared_memory')
    except Exception:
        pass   # 이 최적화가 실패해도 기능에는 영향 없다 — 경고가 그대로 뜰 뿐


class _Payload(ctypes.Structure):
    """공유메모리에 그대로 얹히는 구조체 — 필드 순서·타입을 바꾸면 두 프로세스를 같이 재시작해야 한다."""
    _fields_ = [
        ('seq', ctypes.c_uint64),            # 홀수=쓰는 중, 짝수=완료. 0 = 아직 한 번도 안 씀(초기값)
        ('timestamp_ns', ctypes.c_int64),    # time.monotonic_ns() — 벽시계 아님(NTP 점프에 안전)
        # 실차 USER_control_command(0x210) 기준 — DBC/A1_dbc_2_fixed.dbc (2026-10-06 EAIT 0x156/0x157 에서 전환)
        ('steer_auto', ctypes.c_uint8),       # 축별 auto(1) / manual(0) — 0x210 bit40
        ('brake_auto', ctypes.c_uint8),       # bit41
        ('acc_auto', ctypes.c_uint8),         # bit42
        ('_pad', ctypes.c_uint8),
        ('steer_cmd_deg', ctypes.c_float),    # -150~150 deg (0x210 에는 0.1 deg/raw 로 인코딩 — 업체 DBC 그대로)
        ('brake_cmd_pct', ctypes.c_float),    # 0~100 %
        ('acc_cmd_pct', ctypes.c_float),      # 0~100 %
    ]


@dataclass
class Command:
    """`_Payload` 의 파이썬 쪽 값 객체(공유메모리 세부사항을 몰라도 되게)."""
    steer_auto: bool = False
    brake_auto: bool = False
    acc_auto: bool = False
    steer_cmd_deg: float = 0.0
    brake_cmd_pct: float = 0.0
    acc_cmd_pct: float = 0.0


class CommandChannel:
    """`create()`/`open()` 로 만들고, 끝나면 `close()`(만든 쪽은 `unlink()` 도)."""

    def __init__(self, shm, owner):
        self._shm = shm
        self._owner = owner   # True 면 이 프로세스가 만든 쪽 — unlink 책임
        self._payload = _Payload.from_buffer(self._shm.buf)

    @classmethod
    def create(cls, name=SHM_NAME_DEFAULT):
        size = ctypes.sizeof(_Payload)
        try:
            shm = shared_memory.SharedMemory(name=name, create=True, size=size)
        except FileExistsError:
            # 이전 실행이 비정상 종료해 세그먼트가 남아 있을 수 있다 — 지우고 새로 만든다(§7.2 재시작은 항상 안전 상태부터).
            stale = shared_memory.SharedMemory(name=name)
            stale.close()
            stale.unlink()
            shm = shared_memory.SharedMemory(name=name, create=True, size=size)
        ch = cls(shm, owner=True)
        ch._payload.seq = 0
        return ch

    @classmethod
    def open(cls, name=SHM_NAME_DEFAULT):
        shm = shared_memory.SharedMemory(name=name, create=False)
        _unregister_from_tracker(shm)   # 이 핸들은 소유자가 아니다 — 아래 함수 설명 참고
        return cls(shm, owner=False)

    def write(self, cmd: Command):
        p = self._payload
        seq = p.seq if p.seq % 2 == 0 else p.seq + 1   # 혹시 이전 쓰기가 중간에 죽었으면 홀수를 짝수로 보정
        p.seq = seq + 1                                  # 홀수 — "쓰는 중"
        p.timestamp_ns = time.monotonic_ns()
        p.steer_auto = int(cmd.steer_auto)
        p.brake_auto = int(cmd.brake_auto)
        p.acc_auto = int(cmd.acc_auto)
        p.steer_cmd_deg = cmd.steer_cmd_deg
        p.brake_cmd_pct = cmd.brake_cmd_pct
        p.acc_cmd_pct = cmd.acc_cmd_pct
        p.seq = seq + 2                                   # 짝수 — "완료"

    def read(self, max_retries=1000):
        """(Command, age_s) 를 반환. 아직 한 번도 안 써졌으면 (None, None).

        재시도는 매우 싸다(필드 몇 개 읽기 수준, 수십~수백 ns) — 기본값을 넉넉히 잡아 정상적인 쓰기 빈도
        (제어 노드의 제어 주기, 보통 수~수십 ms 간격)에서는 사실상 항상 첫 몇 번 안에 성공한다. 그럼에도
        전부 소진되면(쓰기 쪽이 죽었거나 이 스트레스 테스트처럼 쉬지 않고 쓰는 비정상 상황) `TornReadError` 를
        던진다 — 호출자(can_guard)는 이를 "이번 주기는 못 읽었다"로 보고 나이(staleness) 판단에 맡기면 된다
        (드물게 실패하는 것 자체는 워치독 로직이 이미 감당하도록 설계돼 있다 — §5.D 상태머신)."""
        p = self._payload
        for _ in range(max_retries):
            seq1 = p.seq
            if seq1 == 0:
                return None, None
            if seq1 % 2 != 0:
                continue   # 쓰는 중 — 바로 재시도
            cmd = Command(
                steer_auto=bool(p.steer_auto), brake_auto=bool(p.brake_auto), acc_auto=bool(p.acc_auto),
                steer_cmd_deg=p.steer_cmd_deg, brake_cmd_pct=p.brake_cmd_pct, acc_cmd_pct=p.acc_cmd_pct,
            )
            ts = p.timestamp_ns
            seq2 = p.seq
            if seq1 == seq2:
                age_s = (time.monotonic_ns() - ts) / 1e9
                return cmd, age_s
        raise TornReadError(
            f'CommandChannel: {max_retries}번 재시도해도 seqlock 이 안정되지 않음 — 쓰기 쪽이 멈췄거나 비정상적으로 빠름')

    def close(self):
        # ctypes.Structure.from_buffer() 는 버퍼 프로토콜로 mmap 에 "내보낸 포인터"를 하나 쥐고 있다 —
        # 이걸 먼저 놓지 않으면 SharedMemory.close() 가 BufferError("cannot close exported pointers exist")
        # 로 죽는다. del 로 참조를 0 으로 만들면 CPython 은 즉시(참조카운트) 해제하므로 순서만 지키면 된다.
        del self._payload
        self._shm.close()
        if self._owner:
            self._shm.unlink()


class _HeartbeatPayload(ctypes.Structure):
    _fields_ = [('seq', ctypes.c_uint64), ('timestamp_ns', ctypes.c_int64)]


class HeartbeatChannel:
    """인지 프로세스 → can_guard "나 살아있음" 신호. 페이로드가 없어 `CommandChannel` 보다 훨씬 단순하지만
    같은 seqlock 프로토콜(찢어진 읽기 방지)을 쓴다 — 필드가 seq+timestamp 둘뿐이라 이론상 안 겹쳐도 되지만,
    프로토콜을 하나만 유지하는 쪽이 실수할 여지가 적다.

      guard = HeartbeatChannel.create()      # can_guard 쪽 — 세그먼트 생성(먼저 뜬다)
      perc = HeartbeatChannel.open()         # 인지 프로세스 쪽
      perc.beat()                             # 매 프레임(또는 주기적으로) 호출
      age_s = guard.age()                     # None = 한 번도 안 옴
    """

    def __init__(self, shm, owner):
        self._shm = shm
        self._owner = owner
        self._payload = _HeartbeatPayload.from_buffer(self._shm.buf)

    @classmethod
    def create(cls, name=HEARTBEAT_SHM_NAME_DEFAULT):
        size = ctypes.sizeof(_HeartbeatPayload)
        try:
            shm = shared_memory.SharedMemory(name=name, create=True, size=size)
        except FileExistsError:
            stale = shared_memory.SharedMemory(name=name)
            stale.close()
            stale.unlink()
            shm = shared_memory.SharedMemory(name=name, create=True, size=size)
        ch = cls(shm, owner=True)
        ch._payload.seq = 0
        return ch

    @classmethod
    def open(cls, name=HEARTBEAT_SHM_NAME_DEFAULT):
        shm = shared_memory.SharedMemory(name=name, create=False)
        _unregister_from_tracker(shm)
        return cls(shm, owner=False)

    def beat(self):
        p = self._payload
        seq = p.seq if p.seq % 2 == 0 else p.seq + 1
        p.seq = seq + 1
        p.timestamp_ns = time.monotonic_ns()
        p.seq = seq + 2

    def age(self, max_retries=1000):
        """마지막 beat() 로부터 지난 초. 한 번도 안 왔으면 None. 너무 자주 찢어지면 TornReadError."""
        p = self._payload
        for _ in range(max_retries):
            seq1 = p.seq
            if seq1 == 0:
                return None
            if seq1 % 2 != 0:
                continue
            ts = p.timestamp_ns
            seq2 = p.seq
            if seq1 == seq2:
                return (time.monotonic_ns() - ts) / 1e9
        raise TornReadError(f'HeartbeatChannel: {max_retries}번 재시도해도 seqlock 이 안정되지 않음')

    def close(self):
        del self._payload
        self._shm.close()
        if self._owner:
            self._shm.unlink()


# --- 비상 정지 채널 (2026-11-04, bsw_test 00 SAF-ESTOP) -------------------------------------------------------------
ESTOP_SHM_NAME_DEFAULT = 'a1_can_guard_estop'


class _EstopPayload(ctypes.Structure):
    _fields_ = [
        ('seq', ctypes.c_uint64),
        ('timestamp_ns', ctypes.c_int64),   # 마지막 trigger() 의 time.monotonic_ns() — 감지 지연 계산용
        ('request', ctypes.c_uint8),         # 1 = 정지 요청(래치, reset() 전까지 유지)
        ('_pad', ctypes.c_uint8 * 3),
        ('reset_seq', ctypes.c_uint32),      # reset() 마다 +1 — can_guard 는 값이 바뀐 것을 "해제 요청"으로 본다
        ('source', ctypes.c_char * 16),      # 누가 눌렀나(로그용)
    ]


@dataclass
class EstopState:
    request: bool
    trigger_mono_ns: int
    reset_seq: int
    source: str


class EstopChannel:
    """정지 요청 → can_guard. `HeartbeatChannel` 과 같은 seqlock. can_guard 가 만들고(`create`), `estop.py` 가 연다.

      guard = EstopChannel.create();  st = guard.read()     # EstopState 또는 None(한 번도 안 씀)
      tool = EstopChannel.open();     tool.trigger('laptop'); tool.reset()
    """

    def __init__(self, shm, owner):
        self._shm = shm
        self._owner = owner
        self._payload = _EstopPayload.from_buffer(self._shm.buf)

    @classmethod
    def create(cls, name=ESTOP_SHM_NAME_DEFAULT):
        size = ctypes.sizeof(_EstopPayload)
        try:
            shm = shared_memory.SharedMemory(name=name, create=True, size=size)
        except FileExistsError:
            stale = shared_memory.SharedMemory(name=name)
            stale.close()
            stale.unlink()
            shm = shared_memory.SharedMemory(name=name, create=True, size=size)
        ch = cls(shm, owner=True)
        ch._payload.seq = 0
        return ch

    @classmethod
    def open(cls, name=ESTOP_SHM_NAME_DEFAULT):
        shm = shared_memory.SharedMemory(name=name, create=False)
        _unregister_from_tracker(shm)
        return cls(shm, owner=False)

    def _write(self, request=None, reset=False, source=None, stamp=False):
        p = self._payload
        seq = p.seq if p.seq % 2 == 0 else p.seq + 1
        p.seq = seq + 1
        if stamp:
            p.timestamp_ns = time.monotonic_ns()
        if request is not None:
            p.request = int(request)
        if reset:
            p.reset_seq = (p.reset_seq + 1) & 0xFFFFFFFF
        if source is not None:
            p.source = source.encode()[:15]
        p.seq = seq + 2

    def trigger(self, source='manual'):
        self._write(request=True, source=source, stamp=True)

    def reset(self, source='manual'):
        self._write(request=False, reset=True, source=source)

    def read(self, max_retries=1000):
        p = self._payload
        for _ in range(max_retries):
            seq1 = p.seq
            if seq1 == 0:
                return None
            if seq1 % 2 != 0:
                continue
            st = EstopState(bool(p.request), p.timestamp_ns, p.reset_seq, p.source.decode(errors='replace'))
            if seq1 == p.seq:
                return st
        raise TornReadError(f'EstopChannel: {max_retries}번 재시도해도 seqlock 이 안정되지 않음')

    def close(self):
        del self._payload
        self._shm.close()
        if self._owner:
            self._shm.unlink()


# --- 상태 채널 (2026-11-06, 경기 깃발 계획서 WP3) ---------------------------------------------------------------------
# can_guard → 어댑터·race_state_publisher·상태판. 경기 상태를 판단하는 곳은 can_guard 하나뿐이고(D5) 다른 프로세스는 읽기만 한다.
STATUS_SHM_NAME_DEFAULT = 'a1_can_guard_status'
STATUS_MAX_AGE_S_DEFAULT = 0.1      # 이보다 오래되면 can_guard 가 죽은 것으로 보고 "없음"(계획서 WP3, 브리프 A10)
STATUS_NONE = -1.0                  # "값 없음"(pit_lat/lon 만 NaN) — 데이터시트 05 시트와 같은 표기. speed_cap_kph 는 0 = 정지 유지

# 안전 상태 코드 — state_machine.State 의 이름과 1:1. 공유메모리에 문자열을 얹지 않으려고 정수로 쓴다
GUARD_STATE_CODE = {'INIT': 0, 'ACTIVE': 1, 'HOLDING': 2, 'DEGRADED': 3, 'STOPPED': 4, 'ESTOP': 5}
GUARD_STATE_NAME = {v: k for k, v in GUARD_STATE_CODE.items()}
# 경기 표시 코드 — 계획서 3.1, 데이터시트 05 시트 `phase`
RACE_PHASE_CODE = {'PRE_GO': 0, 'RACING': 1, 'SLOW': 2, 'FINISHED': 3, 'PARKED': 4, 'SUSPENDED': 5}
RACE_PHASE_NAME = {v: k for k, v in RACE_PHASE_CODE.items()}


class _StatusPayload(ctypes.Structure):
    """필드 순서·타입을 바꾸면 쓰는 쪽·읽는 쪽을 같이 재시작해야 한다. 실수는 전부 double — float32 로 줄이면 9.3 이 9.30000019 가 된다."""
    _fields_ = [
        ('seq', ctypes.c_uint64),
        ('timestamp_ns', ctypes.c_int64),       # stamp. time.monotonic_ns() — 같은 PC 의 프로세스끼리는 시계가 같다
        ('race_enabled', ctypes.c_uint8),
        ('guard_state', ctypes.c_uint8),        # GUARD_STATE_CODE
        ('race_phase', ctypes.c_uint8),         # RACE_PHASE_CODE
        ('slow_active', ctypes.c_uint8),
        ('long_override', ctypes.c_uint8),
        ('sig_go', ctypes.c_uint8),
        ('sig_slow_on', ctypes.c_uint8),
        ('sig_slow_off', ctypes.c_uint8),
        ('sig_finish', ctypes.c_uint8),
        ('sig_stop', ctypes.c_uint8),
        ('res_active', ctypes.c_uint8),
        ('res_alive_ok', ctypes.c_uint8),
        ('input_grace', ctypes.c_uint8),
        ('_pad', ctypes.c_uint8 * 3),
        ('speed_cap_kph', ctypes.c_double),
        ('speed_target_kph', ctypes.c_double),
        ('race_signal_age_s', ctypes.c_double),
        ('vehicle_speed_limit_kph', ctypes.c_double),
        ('speed_limit_target_kph', ctypes.c_double),
        ('model_healthy_s', ctypes.c_double),
        ('pit_lat_deg', ctypes.c_double),
        ('pit_lon_deg', ctypes.c_double),
    ]


@dataclass
class GuardStatus:
    """StatusChannel 의 값 객체. 기본값 = 경기 모드 꺼짐 + 전부 "값 없음"."""
    race_enabled: bool = False
    guard_state: int = GUARD_STATE_CODE['INIT']
    race_phase: int = RACE_PHASE_CODE['PRE_GO']
    slow_active: bool = False
    long_override: bool = False
    sig_go: bool = False
    sig_slow_on: bool = False
    sig_slow_off: bool = False
    sig_finish: bool = False
    sig_stop: bool = False
    res_active: bool = False
    res_alive_ok: bool = False
    input_grace: bool = False
    speed_cap_kph: float = STATUS_NONE            # −1 = 상한 없음, 0 = 정지 유지
    speed_target_kph: float = STATUS_NONE         # −1 = override 목표 없음
    race_signal_age_s: float = STATUS_NONE        # −1 = 0x124 를 한 번도 못 받음
    vehicle_speed_limit_kph: float = STATUS_NONE  # 0x202. −1 = 못 받음
    speed_limit_target_kph: float = STATUS_NONE   # 0x203 목표. −1 = 없음(tx off 포함)
    model_healthy_s: float = 0.0                  # 모델 입력 연속 정상 시간
    pit_lat_deg: float = float('nan')             # NaN = 없음
    pit_lon_deg: float = float('nan')             # NaN = 없음
    seq: int = 0                                  # read() 가 채움(쓸 때는 무시)
    stamp_ns: int = 0                             # read() 가 채움(쓸 때는 무시)
    age_s: float = 0.0                            # read() 가 채움(쓸 때는 무시)

    @property
    def guard_state_name(self):
        return GUARD_STATE_NAME.get(self.guard_state, f'?{self.guard_state}')

    @property
    def race_phase_name(self):
        return RACE_PHASE_NAME.get(self.race_phase, f'?{self.race_phase}')


_STATUS_BOOL_FIELDS = ('race_enabled', 'slow_active', 'long_override', 'sig_go', 'sig_slow_on', 'sig_slow_off',
                       'sig_finish', 'sig_stop', 'res_active', 'res_alive_ok', 'input_grace')
_STATUS_INT_FIELDS = ('guard_state', 'race_phase')
_STATUS_FLOAT_FIELDS = ('speed_cap_kph', 'speed_target_kph', 'race_signal_age_s', 'vehicle_speed_limit_kph',
                        'speed_limit_target_kph', 'model_healthy_s', 'pit_lat_deg', 'pit_lon_deg')


class StatusChannel:
    """can_guard → 다른 프로세스. 다른 채널과 같은 seqlock. can_guard 가 만들고(`create`) 매 주기 쓰며 읽는 쪽은 `open` 한다.

      guard = StatusChannel.create();  guard.write(GuardStatus(race_enabled=True, ...))
      rd = StatusChannel.open_or_none()   # 세그먼트가 없으면(can_guard 미실행·구버전) None
      st = rd.read()                      # GuardStatus, 또는 None = 없음(한 번도 안 씀 / stamp 가 max_age_s 초과)

    read() 가 None 이면 "경기 모드 아님"으로 보고 기존 규칙으로 동작한다(브리프 A10). can_guard 가 재시작하면 세그먼트를 새로
    만들므로, 오래 사는 읽는 쪽은 None 이 이어질 때 다시 `open_or_none()` 해야 새 세그먼트를 본다(옛 핸들은 옛 내용 = 계속 None)."""

    def __init__(self, shm, owner):
        self._shm = shm
        self._owner = owner
        self._payload = _StatusPayload.from_buffer(self._shm.buf)

    @classmethod
    def create(cls, name=STATUS_SHM_NAME_DEFAULT):
        size = ctypes.sizeof(_StatusPayload)
        try:
            shm = shared_memory.SharedMemory(name=name, create=True, size=size)
        except FileExistsError:
            stale = shared_memory.SharedMemory(name=name)
            stale.close()
            stale.unlink()
            shm = shared_memory.SharedMemory(name=name, create=True, size=size)
        ch = cls(shm, owner=True)
        ch._payload.seq = 0
        return ch

    @classmethod
    def open(cls, name=STATUS_SHM_NAME_DEFAULT):
        shm = shared_memory.SharedMemory(name=name, create=False)
        _unregister_from_tracker(shm)
        return cls(shm, owner=False)

    @classmethod
    def open_or_none(cls, name=STATUS_SHM_NAME_DEFAULT):
        """세그먼트가 없으면 None — can_guard 가 안 떠 있거나 StatusChannel 이 없는 구버전이다.
        can_guard 가 세그먼트를 막 만드는 순간(크기 0)에 열면 mmap 이 ValueError 를 낸다 — 이것도 "아직 없음"이다."""
        try:
            return cls.open(name)
        except (FileNotFoundError, ValueError):
            return None

    def write(self, st: GuardStatus):
        p = self._payload
        seq = p.seq if p.seq % 2 == 0 else p.seq + 1
        p.seq = seq + 1
        p.timestamp_ns = time.monotonic_ns()
        for f in _STATUS_BOOL_FIELDS:
            setattr(p, f, int(bool(getattr(st, f))))
        for f in _STATUS_INT_FIELDS:
            setattr(p, f, int(getattr(st, f)))
        for f in _STATUS_FLOAT_FIELDS:
            setattr(p, f, float(getattr(st, f)))
        p.seq = seq + 2

    def read(self, max_age_s=STATUS_MAX_AGE_S_DEFAULT, max_retries=1000):
        """GuardStatus 또는 None(없음). max_age_s=None 이면 나이를 보지 않는다(상태판의 "마지막 값" 표시용, 판단에 쓰지 말 것).
        재시도를 다 쓰면 TornReadError — 다른 채널과 같다. 호출자는 "이번 주기 없음"으로 본다."""
        p = self._payload
        for _ in range(max_retries):
            seq1 = p.seq
            if seq1 == 0:
                return None
            if seq1 % 2 != 0:
                continue
            kw = {f: bool(getattr(p, f)) for f in _STATUS_BOOL_FIELDS}
            kw.update({f: int(getattr(p, f)) for f in _STATUS_INT_FIELDS})
            kw.update({f: float(getattr(p, f)) for f in _STATUS_FLOAT_FIELDS})
            ts = p.timestamp_ns
            if seq1 != p.seq:
                continue
            age_s = (time.monotonic_ns() - ts) / 1e9
            if max_age_s is not None and age_s > max_age_s:
                return None
            return GuardStatus(seq=seq1, stamp_ns=ts, age_s=age_s, **kw)
        raise TornReadError(f'StatusChannel: {max_retries}번 재시도해도 seqlock 이 안정되지 않음')

    def close(self):
        del self._payload
        self._shm.close()
        if self._owner:
            self._shm.unlink()
