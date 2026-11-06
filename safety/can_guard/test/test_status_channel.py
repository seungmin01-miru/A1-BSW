"""StatusChannel — 왕복, "값 없음" 표기, 오래됨 판정, 찢김 방지, 다른 프로세스 열기, 기존 채널 불변."""
import ctypes
import dataclasses
import math
import multiprocessing
import time
import uuid

import pytest

from protocol import (GUARD_STATE_CODE, GUARD_STATE_NAME, RACE_PHASE_CODE, RACE_PHASE_NAME, STATUS_MAX_AGE_S_DEFAULT,
                      STATUS_NONE, STATUS_SHM_NAME_DEFAULT, Command, CommandChannel, GuardStatus, StatusChannel,
                      TornReadError, _EstopPayload, _HeartbeatPayload, _Payload)
from state_machine import State


@pytest.fixture
def shm_name():
    return f'a1_test_status_{uuid.uuid4().hex[:12]}'


FULL = GuardStatus(
    race_enabled=True, guard_state=GUARD_STATE_CODE['HOLDING'], race_phase=RACE_PHASE_CODE['SLOW'], slow_active=True,
    long_override=True, sig_go=True, sig_slow_on=True, sig_slow_off=True, sig_finish=True, sig_stop=True,
    res_active=True, res_alive_ok=True, input_grace=True, speed_cap_kph=8.8, speed_target_kph=8.0,
    race_signal_age_s=0.02, vehicle_speed_limit_kph=9.3, speed_limit_target_kph=9.3, model_healthy_s=12.5,
    pit_lat_deg=37.123456789, pit_lon_deg=127.123456789)


def test_default_name():
    assert STATUS_SHM_NAME_DEFAULT == 'a1_can_guard_status'


def test_never_written_is_none(shm_name):
    ch = StatusChannel.create(shm_name)
    try:
        assert ch.read() is None
        assert ch.read(max_age_s=None) is None
    finally:
        ch.close()


def test_roundtrip_every_field_exact(shm_name):
    writer = StatusChannel.create(shm_name)
    reader = StatusChannel.open(shm_name)
    try:
        writer.write(FULL)
        got = reader.read()
        for f in dataclasses.fields(GuardStatus):
            if f.name not in ('seq', 'stamp_ns', 'age_s'):
                assert getattr(got, f.name) == getattr(FULL, f.name), f.name
        assert got.speed_cap_kph == 8.8         # double — float32 였으면 8.80000019
        assert got.seq == 2 and got.stamp_ns > 0 and 0 <= got.age_s < 0.1
    finally:
        reader.close()
        writer.close()


@pytest.mark.parametrize('field', ['race_enabled', 'slow_active', 'long_override', 'sig_go', 'sig_slow_on',
                                   'sig_slow_off', 'sig_finish', 'sig_stop', 'res_active', 'res_alive_ok',
                                   'input_grace'])
def test_each_bool_field_is_independent(shm_name, field):
    ch = StatusChannel.create(shm_name)
    try:
        ch.write(GuardStatus(**{field: True}))
        got = ch.read()
        for other in ('race_enabled', 'slow_active', 'long_override', 'sig_go', 'sig_slow_on', 'sig_slow_off',
                      'sig_finish', 'sig_stop', 'res_active', 'res_alive_ok', 'input_grace'):
            assert getattr(got, other) is (other == field), other
    finally:
        ch.close()


def test_required_fields_from_plan_exist():
    want = {'race_enabled', 'guard_state', 'race_phase', 'slow_active', 'long_override', 'speed_cap_kph',
            'speed_target_kph', 'sig_go', 'sig_slow_on', 'sig_slow_off', 'sig_finish', 'sig_stop',
            'race_signal_age_s', 'res_active', 'res_alive_ok', 'vehicle_speed_limit_kph', 'speed_limit_target_kph',
            'model_healthy_s', 'input_grace', 'pit_lat_deg', 'pit_lon_deg', 'seq', 'stamp_ns'}
    assert want <= {f.name for f in dataclasses.fields(GuardStatus)}


def test_defaults_are_race_off_and_no_values(shm_name):
    """"값 없음" = −1, 피트존 = NaN — 데이터시트 05 시트와 같다."""
    ch = StatusChannel.create(shm_name)
    try:
        ch.write(GuardStatus())
        got = ch.read()
        assert got.race_enabled is False
        assert STATUS_NONE == -1.0
        for f in ('speed_cap_kph', 'speed_target_kph', 'race_signal_age_s', 'vehicle_speed_limit_kph',
                  'speed_limit_target_kph'):
            assert getattr(got, f) == -1.0, f
        assert math.isnan(got.pit_lat_deg) and math.isnan(got.pit_lon_deg)
    finally:
        ch.close()


def test_speed_cap_zero_is_distinct_from_none(shm_name):
    ch = StatusChannel.create(shm_name)
    try:
        ch.write(GuardStatus(speed_cap_kph=0.0))
        assert ch.read().speed_cap_kph == 0.0
    finally:
        ch.close()


def test_guard_state_codes_cover_state_machine():
    assert set(GUARD_STATE_CODE) == {s.value for s in State}
    assert len(set(GUARD_STATE_CODE.values())) == len(GUARD_STATE_CODE)
    assert all(GUARD_STATE_NAME[v] == k for k, v in GUARD_STATE_CODE.items())


def test_race_phase_codes_match_plan():
    assert RACE_PHASE_CODE == {'PRE_GO': 0, 'RACING': 1, 'SLOW': 2, 'FINISHED': 3, 'PARKED': 4, 'SUSPENDED': 5}
    assert RACE_PHASE_NAME[2] == 'SLOW'


def test_name_properties():
    st = GuardStatus(guard_state=GUARD_STATE_CODE['ESTOP'], race_phase=RACE_PHASE_CODE['PARKED'])
    assert st.guard_state_name == 'ESTOP' and st.race_phase_name == 'PARKED'
    assert GuardStatus(guard_state=99).guard_state_name == '?99'


# --- 오래됨 -------------------------------------------------------------------------------------------------------
def test_default_max_age_is_0_1s():
    assert STATUS_MAX_AGE_S_DEFAULT == 0.1


def test_stale_reads_as_none_and_fresh_write_revives(shm_name):
    """can_guard 가 죽어 쓰기를 멈추면 0.1 s 뒤부터 없음."""
    ch = StatusChannel.create(shm_name)
    try:
        ch.write(FULL)
        assert ch.read() is not None
        time.sleep(0.12)
        assert ch.read() is None
        got = ch.read(max_age_s=None)           # 나이 무시(표시용)
        assert got is not None and got.age_s > 0.1
        ch.write(FULL)
        assert ch.read() is not None
    finally:
        ch.close()


def test_max_age_argument(shm_name):
    ch = StatusChannel.create(shm_name)
    try:
        ch.write(FULL)
        time.sleep(0.02)
        assert ch.read(max_age_s=0.01) is None
        assert ch.read(max_age_s=1.0) is not None
    finally:
        ch.close()


# --- 열기 -------------------------------------------------------------------------------------------------------
def test_open_or_none_missing_segment(shm_name):
    assert StatusChannel.open_or_none(shm_name) is None
    with pytest.raises(FileNotFoundError):
        StatusChannel.open(shm_name)


def test_create_recovers_from_stale_segment(shm_name):
    first = StatusChannel.create(shm_name)
    first.write(FULL)
    del first._payload
    first._shm.close()      # unlink 없음 — 죽은 can_guard 흉내
    second = StatusChannel.create(shm_name)
    try:
        assert second.read(max_age_s=None) is None
    finally:
        second.close()


def _reader_proc(shm_name, q):
    ch = StatusChannel.open(shm_name)
    for _ in range(500):
        st = ch.read()
        if st is not None:
            q.put((st.race_phase, st.speed_cap_kph, st.guard_state_name))
            break
        time.sleep(0.01)
    else:
        q.put(None)
    ch.close()


def test_other_process_can_open_and_read_fresh(shm_name):
    """monotonic 시계가 프로세스 사이에 같아서 나이 판정이 다른 프로세스에서도 맞다."""
    ch = StatusChannel.create(shm_name)
    q = multiprocessing.Queue()
    proc = multiprocessing.Process(target=_reader_proc, args=(shm_name, q))
    proc.start()
    try:
        deadline = time.monotonic() + 5.0
        while proc.is_alive() and q.empty() and time.monotonic() < deadline:
            ch.write(FULL)
            time.sleep(0.01)
        assert q.get(timeout=5) == (RACE_PHASE_CODE['SLOW'], 8.8, 'HOLDING')
    finally:
        proc.join(timeout=5)
        ch.close()


def _status_writer_proc(shm_name, n, barrier):
    ch = StatusChannel.open(shm_name)
    barrier.wait()
    for i in range(n):
        v = float(i)
        ch.write(GuardStatus(speed_cap_kph=v, speed_target_kph=-v, model_healthy_s=v, pit_lat_deg=v, pit_lon_deg=-v,
                             race_signal_age_s=v, guard_state=i % 6, race_phase=i % 6, sig_go=bool(i % 2),
                             sig_stop=not (i % 2)))
    ch.close()


def test_concurrent_writer_process_no_torn_reads(shm_name):
    """별도 프로세스가 쉬지 않고 쓰는 동안 첫 필드와 마지막 필드가 항상 같은 i 에서 나온 값인지 확인."""
    n = 20_000
    ch = StatusChannel.create(shm_name)
    barrier = multiprocessing.Barrier(2)
    proc = multiprocessing.Process(target=_status_writer_proc, args=(shm_name, n, barrier))
    proc.start()
    reader = StatusChannel.open(shm_name)
    try:
        barrier.wait()
        reads, last_i = 0, -1.0
        t_end = time.monotonic() + 5.0
        while proc.is_alive() or time.monotonic() < t_end:
            try:
                st = reader.read(max_age_s=None)
            except TornReadError:
                continue
            if st is not None:
                i = st.speed_cap_kph
                assert st.speed_target_kph == -i and st.model_healthy_s == i and st.pit_lat_deg == i
                assert st.pit_lon_deg == -i and st.race_signal_age_s == i
                assert st.guard_state == int(i) % 6 and st.race_phase == int(i) % 6
                assert st.sig_go == bool(int(i) % 2) and st.sig_stop == (not int(i) % 2)
                assert i >= last_i
                last_i = i
                reads += 1
            if not proc.is_alive() and last_i >= n - 1:
                break
        assert reads > 0, '한 번도 못 읽음 — 시험 자체가 무효'
    finally:
        proc.join(timeout=5)
        reader.close()
        ch.close()


# --- 기존 채널 불변 ----------------------------------------------------------------------------------------------
def test_existing_structures_unchanged():
    """어댑터·lift_cmd 와 공유하는 구조체 크기가 그대로 — 바뀌면 두 프로세스를 같이 재시작해야 한다."""
    assert ctypes.sizeof(_Payload) == 32
    assert ctypes.sizeof(_HeartbeatPayload) == 16
    assert ctypes.sizeof(_EstopPayload) == 40


def test_command_channel_still_works(shm_name):
    ch = CommandChannel.create(shm_name)
    try:
        ch.write(Command(steer_cmd_deg=1.0))
        assert ch.read()[0].steer_cmd_deg == 1.0
    finally:
        ch.close()
