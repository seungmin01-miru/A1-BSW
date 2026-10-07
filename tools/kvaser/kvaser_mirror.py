#!/usr/bin/env python3
"""Kvaser(CANlib) → 가상 SocketCAN(kv0) 미러 + 원본 기록.

Kvaser Leaf v3 는 이 PC(6.8 커널)에서 SocketCAN 인터페이스가 안 생기고 CANlib 채널로만 보인다
(tools/kvaser/README.md). 그러면 매뉴얼의 candump·cansniffer·cantools decode 를 못 쓴다 — 이 도구가 Kvaser 에서
받은 프레임을 그대로 vcan 인터페이스(kv0)에 복사해 준다. 이후 매뉴얼 명령의 `can0` 을 `kv0` 로만 바꾸면 된다.

  sudo ip link add dev kv0 type vcan && sudo ip link set kv0 up      # 부팅마다 한 번
  python3 tools/kvaser/kvaser_mirror.py --channel 0 --bitrate 500000 --listen-only --log "$D/kvaser_ch0.log"

  --listen-only : Kvaser 를 **silent 모드**로 연다(ACK·에러프레임을 버스에 전혀 안 보냄 = SocketCAN listen-only 와 같음).
                  비트레이트 스캔·녹화·원격 판정까지는 반드시 이 옵션으로. C단계(can_guard 송신) 동안에는 같은 채널을
                  can_guard 가 normal 모드로 열어야 하므로 미러를 이 옵션 **없이** 다시 띄운다(silent 핸들이 남아 있으면
                  채널이 송신을 못 할 수 있다).
  --log         : Kvaser 가 받은 원본을 candump -L 형식(`(시각) kv0 ID#DATA`)으로 줄 단위 기록 — 1차 원본.
                  kv0 에서 candump 로 따로 녹화해도 되지만(2차), 시각은 이쪽이 미러 지연 없는 값이다.

같은 채널의 다른 핸들(can_guard)이 보낸 프레임도 보이도록 로컬 TX 에코를 켠다(receive_own_messages) — 그래서 C단계에서
can_guard 의 0x210 도 kv0 에 나타난다. 이 도구 자체는 Kvaser 버스에 **아무것도 보내지 않는다**(kv0 에만 쓴다).
에러 프레임은 kv0 로 복사하지 않고 1초마다 개수만 표시한다(비트레이트가 틀리면 여기서 늘어난다).
"""
import argparse
import os
import signal
import sys
import time

try:
    import can
except ImportError:
    sys.exit('[kvaser_mirror] python-can 없음 — pip install --user python-can')

_stop = False


def _on_signal(signum, frame):
    global _stop
    _stop = True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--channel', default='0', help='Kvaser CANlib 채널 번호 (listChannels 로 확인)')
    ap.add_argument('--bitrate', type=int, default=500000)
    ap.add_argument('--listen-only', action='store_true', help='silent 모드(버스에 ACK·에러프레임 안 보냄)')
    ap.add_argument('--out', default='kv0', help='복사할 vcan 인터페이스')
    ap.add_argument('--log', default='', help='원본 candump -L 형식 기록 파일(비우면 기록 안 함)')
    ap.add_argument('--interface', default='kvaser', help=argparse.SUPPRESS)   # 테스트용(virtual 등)
    a = ap.parse_args()
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    kw = {'bitrate': a.bitrate, 'receive_own_messages': True}
    if a.listen_only:
        kw['driver_mode'] = False   # python-can kvaser DRIVER_MODE_SILENT
    if a.interface == 'kvaser':
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'safety', 'can_guard'))
        import kvaser_compat
        kvaser_compat.apply()
    try:
        src = can.Bus(channel=a.channel, interface=a.interface, **kw)
    except Exception as e:  # noqa: BLE001
        sys.exit(f'[kvaser_mirror] Kvaser 채널 {a.channel} 열기 실패 — {e} (드라이버 설치·USB 연결 확인: '
                 f'bash tools/kvaser/install_kvaser.sh check)')
    try:
        dst = can.Bus(channel=a.out, interface='socketcan')
    except Exception as e:  # noqa: BLE001
        src.shutdown()
        sys.exit(f'[kvaser_mirror] {a.out} 열기 실패 — {e}\n  sudo ip link add dev {a.out} type vcan && '
                 f'sudo ip link set {a.out} up')
    logf = open(a.log, 'a', buffering=1) if a.log else None
    mode = 'silent(listen-only)' if a.listen_only else 'normal(ACK 함)'
    print(f'[kvaser_mirror] Kvaser ch{a.channel} {a.bitrate} bps {mode} → {a.out}'
          f'{", 기록 " + a.log if a.log else ""}', file=sys.stderr, flush=True)
    n = n_err = 0
    last_report = time.monotonic()
    n_win = err_win = 0
    try:
        while not _stop:
            try:
                m = src.recv(timeout=0.2)
            except Exception:  # noqa: BLE001
                # Ctrl+C·SIGTERM 이 CANlib 의 대기 중 읽기를 끊으면 "Interrupted system call" 로 예외가 난다
                # (2026-10-07 현장에서 종료 때마다 트레이스백). 종료 중이면 정상 종료로 취급한다.
                if _stop:
                    break
                raise
            now = time.monotonic()
            if m is not None:
                if m.is_error_frame:
                    n_err += 1
                    err_win += 1
                else:
                    if logf:
                        logf.write(f'({m.timestamp:.6f}) {a.out} {m.arbitration_id:03X}#{bytes(m.data).hex().upper()}\n')
                    try:
                        dst.send(can.Message(arbitration_id=m.arbitration_id, data=m.data,
                                             is_extended_id=m.is_extended_id, is_remote_frame=m.is_remote_frame),
                                 timeout=0.01)
                    except can.CanError:
                        pass   # kv0 가 가득 찬 드문 경우 — 원본 기록(--log)은 계속된다
                    n += 1
                    n_win += 1
            if now - last_report >= 1.0:
                print(f'[kvaser_mirror] 최근 1초 프레임 {n_win}, 에러프레임 {err_win} (누적 {n} / {n_err})'
                      + ('  ← 에러만 있으면 비트레이트가 틀렸을 가능성' if err_win and not n_win else ''),
                      file=sys.stderr, flush=True)
                n_win = err_win = 0
                last_report = now
    finally:
        for bus in (src, dst):
            try:
                bus.shutdown()
            except Exception:  # noqa: BLE001
                pass   # 종료 중 드라이버 오류는 무시 — 원본 기록은 이미 줄 단위로 flush 됨
        if logf:
            logf.close()
        print(f'[kvaser_mirror] 종료 — 프레임 {n}, 에러프레임 {n_err}', file=sys.stderr)


if __name__ == '__main__':
    main()
