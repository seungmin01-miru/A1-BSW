"""표준 라이브러리만으로 DBC 를 읽고 프레임을 해석하는 최소 파서 — signal_audit 공용.

cantools 가 없는 PC(현장 노트북 포함)에서도 돌게 하려고 직접 읽는다. 지원 범위는 이 저장소 DBC 에 필요한 만큼:
BO_ / SG_ / CM_ SG_ 줄, Intel(@1) 바이트 순서, 부호(+/-), factor/offset/min/max/unit.
Motorola(@0) 신호가 나오면 조용히 틀리게 읽지 않고 ValueError 로 멈춘다.
"""
import re
from dataclasses import dataclass, field

_BO = re.compile(r'^BO_\s+(\d+)\s+(\w+)\s*:\s*(\d+)\s+(\S+)')
_SG = re.compile(r'^SG_\s+(\w+)\s*(\S*)\s*:\s*(\d+)\|(\d+)@([01])([+-])\s*'
                 r'\(([^,]+),([^)]+)\)\s*\[([^|]+)\|([^\]]+)\]\s*"([^"]*)"\s*(.*)$')
_CM = re.compile(r'^CM_\s+SG_\s+(\d+)\s+(\w+)\s+"(.*)";?\s*$')


@dataclass
class Signal:
    name: str
    start: int
    length: int
    signed: bool
    factor: float
    offset: float
    minimum: float
    maximum: float
    unit: str
    comment: str = ''

    def raw(self, data):
        v = int.from_bytes(bytes(data).ljust(8, b'\0'), 'little')
        v = (v >> self.start) & ((1 << self.length) - 1)
        if self.signed and v & (1 << (self.length - 1)):
            v -= 1 << self.length
        return v

    def phys(self, data):
        return self.raw(data) * self.factor + self.offset


@dataclass
class Message:
    frame_id: int
    name: str
    dlc: int
    sender: str
    signals: list = field(default_factory=list)

    def decode(self, data):
        return {s.name: s.phys(data) for s in self.signals}


def remote_messages():
    """대회측 원격조종 0x156/0x157 — 원격 유닛이 이 형식으로 보낸다. A1_dbc_2 에도 정의가 있지만 ACC_Cmd 가 (1,-1023), 즉
    0.01 m/s² 단위이고 EPS_Speed 가 없다. 도구는 m/s² 로 쓰므로 load() 뒤 update(remote_messages()) 로 이 정의를 우선한다.

    배치는 tools/race_day/summarize_session.py 의 decode_remote_156/157 과 같다(10-07 녹화로 확인). 범위는 원격 유닛 기준
    (ACC_Cmd 는 −3~+1 이라고 되어 있으나 10-07 에 +1.17 관측). 이 정의는 듣기·분석 전용 — 우리는 0x156/0x157 을 보내지 않는다.
    """
    S = Signal
    return {
        0x156: Message(0x156, 'REMOTE_Control_01', 8, 'REMOTE', [
            S('EPS_En', 0, 1, False, 1, 0, 0, 1, ''),
            S('EPS_Speed', 8, 8, False, 1, 0, 0, 255, ''),
            S('ACC_En', 16, 1, False, 1, 0, 0, 1, ''),
            S('AEB_En', 22, 1, False, 1, 0, 0, 1, ''),
            S('Aliv_Cnt', 56, 8, False, 1, 0, 0, 255, ''),
        ]),
        0x157: Message(0x157, 'REMOTE_Control_02', 8, 'REMOTE', [
            S('EPS_Cmd', 0, 16, True, 0.1, 0, -500, 500, 'deg'),
            S('ACC_Cmd', 24, 16, False, 0.01, -10.23, -3, 1, 'm/s^2'),
        ]),
    }


def load(path):
    msgs = {}
    cur = None
    with open(path, errors='replace') as f:
        for line in f:
            line = line.strip()
            m = _BO.match(line)
            if m:
                cur = Message(int(m.group(1)), m.group(2), int(m.group(3)), m.group(4))
                msgs[cur.frame_id] = cur
                continue
            m = _SG.match(line)
            if m and cur is not None:
                if m.group(5) != '1':
                    raise ValueError(f'{cur.name}.{m.group(1)}: Motorola(@0) 신호는 지원하지 않음')
                cur.signals.append(Signal(
                    name=m.group(1), start=int(m.group(3)), length=int(m.group(4)),
                    signed=m.group(6) == '-', factor=float(m.group(7)), offset=float(m.group(8)),
                    minimum=float(m.group(9)), maximum=float(m.group(10)), unit=m.group(11)))
                continue
            m = _CM.match(line)
            if m:
                msg = msgs.get(int(m.group(1)))
                for s in (msg.signals if msg else []):
                    if s.name == m.group(2):
                        s.comment = m.group(3)
            if not line:
                cur = None
    return msgs
