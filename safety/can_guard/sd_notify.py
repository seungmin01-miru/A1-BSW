"""systemd sd_notify — 외부 의존(python-systemd) 없이 표준 라이브러리만으로. `can_guard.service` 의
`Type=notify` + `WatchdogSec=` 가 이걸로 동작한다. systemd 밖에서 돌 때($NOTIFY_SOCKET 없음)는 조용히 아무 것도
안 한다 — SIL/vcan0 로컬 실행이나 유닛테스트에서 예외가 나면 안 되기 때문."""
import os
import socket


def _send(msg: str) -> bool:
    addr = os.environ.get('NOTIFY_SOCKET')
    if not addr:
        return False
    if addr.startswith('@'):
        addr = '\0' + addr[1:]   # 추상 네임스페이스 소켓(리눅스)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.connect(addr)
        sock.sendall(msg.encode())
        return True
    except OSError:
        return False
    finally:
        sock.close()


def ready():
    """Type=notify 유닛에 "초기화 끝났다"고 알림 — can_guard 는 INIT(안전 상태) 진입 직후 호출한다."""
    return _send('READY=1')


def watchdog():
    """`WatchdogSec` 하트비트 — 이게 그 시간 안에 안 오면 systemd 가 죽었다고 보고 재시작한다(§7.2 생존성)."""
    return _send('WATCHDOG=1')


def status(text: str):
    """`systemctl status` 에 뜨는 한 줄 — 지금 상태머신 상태를 사람이 보기 좋게."""
    return _send(f'STATUS={text}')
