"""python-can 4.6.1 ↔ Kvaser CANlib 5.52 호환 패치 — Kvaser 채널을 열 때만 쓴다(SocketCAN 경로와 무관).

문제(2026-10-07 재현): python-can 의 `KvaserBus.__init__` 은 `canIOCTL_SET_LOCAL_TXACK` 를 **1바이트**(c_byte) 버퍼로
설정하는데, CANlib 5.52 는 이 항목에 4바이트를 요구해 "Error in parameter [-1]" 로 실패한다 → Kvaser 채널이 아예 안 열린다.
같은 호출을 c_uint32/4바이트로 주면 성공한다(Kvaser 공식 canlib 패키지도 4바이트로 설정). `LOCAL_TXECHO` 는 1바이트로도 된다.

`apply()` 는 python-can kvaser 모듈의 `canIoCtlInit` 을 감싸 그 한 항목만 4바이트로 바꿔 넘긴다. 여러 번 불러도 한 번만 적용.
python-can 이 고쳐지면(4바이트로 넘기면) 이 패치는 아무것도 하지 않는다(size == 1 일 때만 개입).
"""
import ctypes


def apply():
    """패치 적용. CANlib(libcanlib)가 없으면 False — 그때 Kvaser 를 열려는 쪽이 원래 오류를 그대로 받는다."""
    try:
        from can.interfaces.kvaser import canlib as kv
        from can.interfaces.kvaser import constants as cs
    except Exception:   # noqa: BLE001 — libcanlib 미설치 등
        return False
    if getattr(kv, '_a1_txack_fix', False):
        return True
    orig = kv.canIoCtlInit

    def patched(handle, item, buf, size):
        if item == cs.canIOCTL_SET_LOCAL_TXACK and size == 1:
            val = getattr(getattr(buf, '_obj', None), 'value', 0)
            wide = ctypes.c_uint32(int(val))
            return orig(handle, item, ctypes.byref(wide), ctypes.sizeof(wide))
        return orig(handle, item, buf, size)

    kv.canIoCtlInit = patched
    kv._a1_txack_fix = True
    return True
