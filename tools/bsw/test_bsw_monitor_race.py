"""상태판 경기 신호 칸 — 표준 라이브러리만, CAN·창 없이 돈다."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bsw_monitor as m  # noqa: E402


def feed(s, cid, data, t):
    s.clock = t
    s.feed(t, cid, data)


def test_no_frames_says_no_data():
    s = m.State()
    s.clock = 1.0
    flags, res = m.race_lines(s.snapshot()['race'])
    assert '수신 없음' in flags and '수신 없음' in res


def test_flags_two_frames_seen_and_res_warning():
    s = m.State()
    feed(s, 0x124, bytes([0x80]) + bytes(7), 1.00)
    feed(s, 0x124, bytes([0x80]) + bytes(7), 1.02)
    feed(s, 0x124, bytes(8), 1.04)
    feed(s, 0x124, bytes(8), 1.06)
    feed(s, 0x127, bytes([0x80]) + bytes(6) + bytes([1]), 1.06)
    feed(s, 0x202, bytes.fromhex('2C01000000000000'), 1.06)
    v = s.snapshot()
    flags, res = m.race_lines(v['race'])
    assert 'GO ○' in flags and '마지막 GO' in flags
    assert 'RES 작동 중' in res and '30.0 km/h' in res
    assert any('RES' in w for _, w in m.warnings(v, {}))


def test_race_ids_are_not_in_rx_row():
    s = m.State()
    feed(s, 0x124, bytes(8), 1.0)
    assert all(i not in m.RACE_IDS for i, _, _ in s.snapshot()['rx'])
