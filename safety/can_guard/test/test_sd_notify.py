"""sd_notify — systemd 밖(테스트 환경)에서 예외 없이 조용히 no-op 인지만 확인."""
import os

import sd_notify


def test_noop_without_notify_socket(monkeypatch):
    monkeypatch.delenv('NOTIFY_SOCKET', raising=False)
    assert sd_notify.ready() is False
    assert sd_notify.watchdog() is False
    assert sd_notify.status('INIT') is False


def test_sends_when_socket_present(tmp_path):
    """진짜 유닉스 데이터그램 소켓을 하나 만들어서 sd_notify 가 실제로 그리로 쓰는지 확인."""
    import socket
    sock_path = str(tmp_path / 'notify.sock')
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(sock_path)
    old = os.environ.get('NOTIFY_SOCKET')
    os.environ['NOTIFY_SOCKET'] = sock_path
    try:
        assert sd_notify.ready() is True
        data, _ = srv.recvfrom(1024)
        assert data == b'READY=1'
        assert sd_notify.watchdog() is True
        data, _ = srv.recvfrom(1024)
        assert data == b'WATCHDOG=1'
    finally:
        srv.close()
        if old is None:
            os.environ.pop('NOTIFY_SOCKET', None)
        else:
            os.environ['NOTIFY_SOCKET'] = old
