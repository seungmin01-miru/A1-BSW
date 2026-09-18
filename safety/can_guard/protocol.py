"""CommandChannel — 제어 노드(ROS2, ros2_ws/) 와 can_guard(이 디렉터리) 사이의 공유메모리 인터페이스.

§7.2: "명령은 작은 공유메모리 ring(seq + timestamp) 으로 온다. ROS2/DDS 는 hot loop 에 안 넣는다."
실제로 필요한 건 **최신값 하나**뿐이다 — 제어 명령은 새 값이 오면 이전 값이 의미가 없다(큐가 아니다).
그래서 "ring" 대신 **seqlock 단일 슬롯**으로 구현한다(설계와의 차이는 can_stack_development.md §5.D 에 기록).

seqlock 프로토콜:
  쓰기: seq 를 홀수로(쓰는 중 표시) → 페이로드 기록 → seq 를 +1(짝수, 완료 표시)
  읽기: seq1 읽기 → 페이로드 복사 → seq2 읽기 → seq1==seq2 이고 짝수면 성공, 아니면 재시도
이 프로토콜이 정합성을 보장하는 건 "재시도"지 "단일 명령어 원자성"이 아니다 — 필드별 쓰기가 찢어져도(torn write)
읽기 쪽이 그 순간의 seq 불일치로 걸러낸다. 프로세스 두 개(스레드 아님)가 공유하므로 `multiprocessing.shared_memory`
위에 `ctypes.Structure` 를 얹는다.

  ctrl = CommandChannel.create('a1_can_guard_cmd')      # 제어 노드 쪽, 세그먼트 생성
  guard = CommandChannel.open('a1_can_guard_cmd')       # can_guard 쪽, 기존 세그먼트 열기
  ctrl.write(Command(eps_en=True, eps_cmd=1.5, ...))
  cmd, age_s = guard.read()                             # age_s 는 monotonic 기준 나이(초) — staleness 판정용
"""
import ctypes
import time
from dataclasses import dataclass
from multiprocessing import shared_memory

SHM_NAME_DEFAULT = 'a1_can_guard_cmd'


class TornReadError(RuntimeError):
    """read() 가 재시도를 다 써도 안정된 스냅샷을 못 얻었을 때 — 드물면 정상(§5.D 참고), 계속 나면 이상 신호."""


class _Payload(ctypes.Structure):
    """공유메모리에 그대로 얹히는 구조체 — 필드 순서·타입을 바꾸면 두 프로세스를 같이 재시작해야 한다."""
    _fields_ = [
        ('seq', ctypes.c_uint64),            # 홀수=쓰는 중, 짝수=완료. 0 = 아직 한 번도 안 씀(초기값)
        ('timestamp_ns', ctypes.c_int64),    # time.monotonic_ns() — 벽시계 아님(NTP 점프에 안전)
        ('eps_en', ctypes.c_uint8),
        ('eps_override_ignore', ctypes.c_uint8),
        ('acc_en', ctypes.c_uint8),
        ('aeb_en', ctypes.c_uint8),
        ('eps_speed', ctypes.c_uint8),       # 10~250 (DBC EPS_Speed 그대로)
        ('turn_signal', ctypes.c_uint8),     # 0(없음)/1/2/4 (DBC 값 그대로, 비트마스크 아님)
        ('aeb_decel_value', ctypes.c_float),  # 0~1 g
        ('eps_cmd', ctypes.c_float),          # -500~500 deg (부호 있음)
        ('acc_cmd', ctypes.c_float),          # -3~1 m/s^2
    ]


@dataclass
class Command:
    """`_Payload` 의 파이썬 쪽 값 객체(공유메모리 세부사항을 몰라도 되게)."""
    eps_en: bool = False
    eps_override_ignore: bool = False
    acc_en: bool = False
    aeb_en: bool = False
    eps_speed: int = 10
    turn_signal: int = 0
    aeb_decel_value: float = 0.0
    eps_cmd: float = 0.0
    acc_cmd: float = 0.0


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
        return cls(shm, owner=False)

    def write(self, cmd: Command):
        p = self._payload
        seq = p.seq if p.seq % 2 == 0 else p.seq + 1   # 혹시 이전 쓰기가 중간에 죽었으면 홀수를 짝수로 보정
        p.seq = seq + 1                                  # 홀수 — "쓰는 중"
        p.timestamp_ns = time.monotonic_ns()
        p.eps_en = int(cmd.eps_en)
        p.eps_override_ignore = int(cmd.eps_override_ignore)
        p.acc_en = int(cmd.acc_en)
        p.aeb_en = int(cmd.aeb_en)
        p.eps_speed = cmd.eps_speed
        p.turn_signal = cmd.turn_signal
        p.aeb_decel_value = cmd.aeb_decel_value
        p.eps_cmd = cmd.eps_cmd
        p.acc_cmd = cmd.acc_cmd
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
                eps_en=bool(p.eps_en), eps_override_ignore=bool(p.eps_override_ignore),
                acc_en=bool(p.acc_en), aeb_en=bool(p.aeb_en), eps_speed=p.eps_speed,
                turn_signal=p.turn_signal, aeb_decel_value=p.aeb_decel_value,
                eps_cmd=p.eps_cmd, acc_cmd=p.acc_cmd,
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
