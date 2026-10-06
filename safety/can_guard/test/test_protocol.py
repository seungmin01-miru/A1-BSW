"""CommandChannel(seqlock 공유메모리) 검증 — 단일 프로세스 정합성 + 진짜 별도 프로세스 동시 접근 스트레스."""
import multiprocessing
import time
import uuid

import pytest

from protocol import Command, CommandChannel, TornReadError


@pytest.fixture
def shm_name():
    return f'a1_test_{uuid.uuid4().hex[:12]}'


def test_never_written_returns_none(shm_name):
    ch = CommandChannel.create(shm_name)
    try:
        cmd, age = ch.read()
        assert cmd is None and age is None
    finally:
        ch.close()


def test_write_then_read_roundtrip(shm_name):
    ch = CommandChannel.create(shm_name)
    try:
        sent = Command(steer_auto=True, brake_auto=False, acc_auto=True,
                       steer_cmd_deg=-12.5, brake_cmd_pct=0.0, acc_cmd_pct=7.25)
        ch.write(sent)
        got, age = ch.read()
        assert got == sent
        assert 0 <= age < 0.1   # 방금 썼으니 100ms 안
    finally:
        ch.close()


def test_age_grows_with_time(shm_name):
    ch = CommandChannel.create(shm_name)
    try:
        ch.write(Command())
        time.sleep(0.05)
        _, age = ch.read()
        assert age >= 0.05
    finally:
        ch.close()


def test_two_handles_same_segment(shm_name):
    """제어 노드(create)·can_guard(open) 를 흉내 — 서로 다른 CommandChannel 인스턴스가 같은 세그먼트를 공유."""
    writer = CommandChannel.create(shm_name)
    reader = CommandChannel.open(shm_name)
    try:
        writer.write(Command(steer_cmd_deg=42.0))
        got, _ = reader.read()
        assert got.steer_cmd_deg == 42.0
    finally:
        reader.close()
        writer.close()   # owner 라 unlink 까지


def test_create_recovers_from_stale_segment(shm_name):
    """비정상 종료로 세그먼트가 남아 있어도 create() 가 지우고 새로 만든다(재시작은 항상 안전 상태부터)."""
    first = CommandChannel.create(shm_name)
    first.write(Command(steer_cmd_deg=99.0))
    # unlink 없이 close 만 — "죽은 프로세스가 세그먼트를 남긴" 상황을 흉내. ctypes 구조체를 먼저 놓지 않으면
    # mmap.close() 가 BufferError 로 죽는다(protocol.close() 가 이 순서를 지키는 이유이기도 하다).
    del first._payload
    first._shm.close()

    second = CommandChannel.create(shm_name)
    try:
        cmd, age = second.read()
        assert cmd is None and age is None   # 새로 만들어졌으니 seq=0 부터
    finally:
        second.close()


def _writer_proc(shm_name, n, barrier):
    ch = CommandChannel.open(shm_name)
    barrier.wait()
    for i in range(n):
        ch.write(Command(steer_cmd_deg=float(i), acc_cmd_pct=float(-i)))
    ch.close()


def test_concurrent_writer_process_no_torn_reads(shm_name):
    """실제 별도 프로세스가 수천 번 연속으로 값을 바꾸는 동안, 읽는 쪽이 항상 (steer_cmd_deg, acc_cmd_pct) 가
    같은 i 에서 나온 쌍(steer_cmd_deg == -acc_cmd_pct)인지 확인 — 필드 두 개가 찢어져서 섞이면 이 불변식이 깨진다."""
    n = 20_000
    ch = CommandChannel.create(shm_name)
    barrier = multiprocessing.Barrier(2)
    proc = multiprocessing.Process(target=_writer_proc, args=(shm_name, n, barrier))
    proc.start()
    reader = CommandChannel.open(shm_name)
    try:
        barrier.wait()
        reads, last_i = 0, -1
        t_end = time.monotonic() + 5.0
        while proc.is_alive() or time.monotonic() < t_end:
            try:
                cmd, age = reader.read()
            except TornReadError:
                continue   # 이 스트레스 테스트의 무휴지 라이터는 실제로는 안 나오는 극단값 — 그냥 다음 시도
            if cmd is not None:
                assert cmd.steer_cmd_deg == -cmd.acc_cmd_pct, \
                    f'찢어진 읽기: steer={cmd.steer_cmd_deg} acc={cmd.acc_cmd_pct}'
                assert cmd.steer_cmd_deg >= last_i   # 값이 단조 증가(재정렬 없음)
                last_i = cmd.steer_cmd_deg
                reads += 1
            if not proc.is_alive() and last_i >= n - 1:
                break
        assert reads > 0, '한 번도 못 읽음 — 테스트 자체가 무효'
    finally:
        proc.join(timeout=5)
        reader.close()
        ch.close()


# ---- HeartbeatChannel ----
from protocol import HeartbeatChannel  # noqa: E402 (기존 임포트 블록과 떨어뜨려 이 절의 대상만 명확히)


@pytest.fixture
def hb_name():
    return f'a1_test_hb_{uuid.uuid4().hex[:12]}'


def test_heartbeat_never_beaten_returns_none(hb_name):
    ch = HeartbeatChannel.create(hb_name)
    try:
        assert ch.age() is None
    finally:
        ch.close()


def test_heartbeat_roundtrip(hb_name):
    writer = HeartbeatChannel.create(hb_name)
    reader = HeartbeatChannel.open(hb_name)
    try:
        writer.beat()
        age = reader.age()
        assert age is not None and 0 <= age < 0.1
    finally:
        reader.close()
        writer.close()


def test_heartbeat_age_grows(hb_name):
    ch = HeartbeatChannel.create(hb_name)
    try:
        ch.beat()
        time.sleep(0.05)
        assert ch.age() >= 0.05
    finally:
        ch.close()
