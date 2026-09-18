"""
End-to-end Alive_Cnt continuity tracking (§2 category-C responsibility).

EAIT 보드가 보내는 상태 메시지의 Alive_Cnt(0~255 롤오버 카운터) 연속성만 감지한다.
stale 타임아웃에 따른 safe-state 진입 판단은 이 계층의 일이 아니다 — §5.D 헬스 슈퍼바이저
(Phase D, 아직 미착수)의 몫이다. 여기서는 "몇 프레임을 놓쳤는가"만 세어 /diagnostics 로 보고한다.
"""


class AliveCounter:
    """0~255 롤오버 Alive_Cnt 하나의 연속성을 추적."""

    def __init__(self):
        self.last = None
        self.frames = 0
        self.total_skips = 0
        self._interval_skips = 0

    def update(self, raw):
        """이번 프레임의 Alive_Cnt(0~255)를 반영하고 (연속 여부, 건너뛴 프레임 수)를 반환."""
        self.frames += 1
        if self.last is None:
            self.last = raw
            return True, 0
        expected = (self.last + 1) % 256
        ok = raw == expected
        gap = 0 if ok else (raw - expected) % 256
        if not ok:
            self.total_skips += 1
            self._interval_skips += gap
        self.last = raw
        return ok, gap

    def pop_interval_skips(self):
        """직전 보고 이후 건너뛴 프레임 수를 반환하고 0으로 리셋(1 Hz 진단 보고용)."""
        n = self._interval_skips
        self._interval_skips = 0
        return n
