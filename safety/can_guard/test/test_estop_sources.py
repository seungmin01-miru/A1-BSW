"""비상 정지 원천: EstopChannel(공유메모리), 원격 0x156/0x157 판정 (2026-11-04)."""
import uuid

from protocol import EstopChannel
from rx_decode import RemoteEstopDetector, decode_remote_156, decode_remote_157


def _f156(eps=0, acc=0, aeb=0):
    d = bytearray(8)
    d[0] = eps
    d[2] = acc | (aeb << 6)
    return bytes(d)


def _f157(acc_mps2=0.0, eps_deg=0.0):
    d = bytearray(8)
    d[0:2] = int(round(eps_deg * 10)).to_bytes(2, 'little', signed=True)
    d[3:5] = int(round((acc_mps2 + 10.23) / 0.01)).to_bytes(2, 'little')
    return bytes(d)


def test_decode_matches_10_07_idle_and_brake_values():
    assert decode_remote_157(_f157(0.0))['acc_cmd_mps2'] == 0.0
    assert decode_remote_157(_f157(-3.0))['acc_cmd_mps2'] == -3.0
    assert decode_remote_156(_f156(1, 1, 0)) == {'eps_en': 1, 'acc_en': 1, 'aeb_en': 0, 'alive': 0}


def test_idle_remote_never_triggers():
    det = RemoteEstopDetector()
    for i in range(200):
        det.feed(0x156, _f156(), i * 0.01)
        det.feed(0x157, _f157(0.0), i * 0.01 + 0.005)
    assert not det.asserted and det.seen


def test_en_needs_confirm_frames_and_reports_first_frame_time():
    det = RemoteEstopDetector(confirm_frames=2)
    det.feed(0x156, _f156(eps=1, acc=1), 1.000)
    assert not det.asserted                       # 단발은 무시
    det.feed(0x157, _f157(0.0), 1.005)            # 0x156 값이 남아 있어 조건 유지 → 2번째
    assert det.asserted and det.trigger_t == 1.000 and 'remote_en' in det.reason


def test_single_glitch_frame_is_ignored():
    det = RemoteEstopDetector(confirm_frames=2)
    det.feed(0x157, _f157(-3.0), 0.0)
    det.feed(0x157, _f157(0.0), 0.01)
    det.feed(0x157, _f157(-3.0), 0.02)
    assert not det.asserted


def test_negative_acc_cmd_triggers():
    det = RemoteEstopDetector(acc_threshold_mps2=-1.0)
    det.feed(0x157, _f157(-1.5), 0.0)
    det.feed(0x157, _f157(-1.5), 0.01)
    assert det.asserted and 'acc_cmd' in det.reason


def test_mild_negative_acc_below_threshold_does_not_trigger():
    det = RemoteEstopDetector(acc_threshold_mps2=-1.0)
    for i in range(5):
        det.feed(0x157, _f157(-0.5), i * 0.01)
    assert not det.asserted


def test_deadman_off_by_default_and_on_when_set():
    det = RemoteEstopDetector()
    det.feed(0x156, _f156(), 0.0)
    det.update(10.0)
    assert not det.asserted
    det = RemoteEstopDetector(timeout_s=0.5)
    det.update(10.0)
    assert not det.asserted                       # 한 번도 못 봤으면 데드맨 안 걸림(시작 전 점검이 따로 경고)
    det.feed(0x156, _f156(), 0.0)
    det.update(0.4)
    assert not det.asserted
    det.update(0.6)
    assert det.asserted and 'lost' in det.reason


def test_estop_channel_trigger_and_reset_roundtrip():
    name = f't_estop_{uuid.uuid4().hex[:8]}'
    guard = EstopChannel.create(name)
    tool = EstopChannel.open(name)
    try:
        assert guard.read() is None
        tool.trigger('pytest')
        st = guard.read()
        assert st.request and st.source == 'pytest' and st.reset_seq == 0 and st.trigger_mono_ns > 0
        tool.reset()
        st2 = guard.read()
        assert not st2.request and st2.reset_seq == 1
    finally:
        tool.close()
        guard.close()


def test_remote_enabled_matches_11_04_frames():
    """11-04 모든 세션의 0x156 은 '00 96 00'(대기)과 '01 96 01'(EPS/ACC En) 두 가지뿐이었다."""
    det = RemoteEstopDetector()
    det.feed(0x156, bytes.fromhex('0096000000000027'), 0.0)
    assert not det.remote_enabled
    det.feed(0x156, bytes.fromhex('01960100000000B0'), 0.01)
    assert det.remote_enabled
    det.feed(0x156, bytes.fromhex('00960000000000D7'), 0.02)
    assert not det.remote_enabled
