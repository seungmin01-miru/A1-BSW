#!/usr/bin/env python3
"""DBC 기반 수신·디코더 (B-4 검증) — vcan0 프레임을 물리값으로 풀고 수신 주기를 잰다.

  python3 eait_rx.py                       # 모든 DBC 메시지 디코드, 1초마다 요약
  python3 eait_rx.py --msg EAIT_INFO_SPD --count 100
  python3 eait_rx.py --raw                 # 디코드 없이 hz 만

메시지별로 수신 Hz, 주기 편차(평균/최대), Alive_Cnt 류 신호의 건너뜀(=프레임 손실)을 집계한다.
"""
import argparse, os, sys, time
from collections import defaultdict

import can
import cantools

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
# 팀 DBC 위치 후보 (앞이 우선). 2026-09-12 실제 위치는 저장소 DBC/ 디렉터리.
DBC_CANDIDATES = [os.path.join(REPO_ROOT, "DBC", "EAIT_CAN(AVANTE_CN7).dbc"),
                  os.path.join(REPO_ROOT, "can_protocol", "00. CAN 프로토콜", "EAIT_CAN(AVANTE_CN7).dbc")]
DEFAULT_DBC = next((c for c in DBC_CANDIDATES if os.path.exists(c)), DBC_CANDIDATES[0])


class Stats:
    def __init__(self):
        self.n = 0; self.last_t = None; self.dt_sum = 0.0; self.dt_max = 0.0
        self.cnt_last = None; self.cnt_skips = 0; self.last_vals = {}

    def add(self, t, vals):
        if self.last_t is not None:
            dt = t - self.last_t; self.dt_sum += dt; self.dt_max = max(self.dt_max, dt)
        self.last_t = t; self.n += 1; self.last_vals = vals
        for k, v in vals.items():
            if "alive" in k.lower() or "cnt" in k.lower():
                if self.cnt_last is not None and isinstance(v, (int, float)) and (self.cnt_last + 1) % 256 != int(v) % 256:
                    self.cnt_skips += 1
                self.cnt_last = int(v) if isinstance(v, (int, float)) else None

    def line(self, name, fid):
        hz = (self.n - 1) / self.dt_sum if self.dt_sum > 0 else 0.0
        avg = self.dt_sum / (self.n - 1) * 1000 if self.n > 1 else 0.0
        vals = " ".join(f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}" for k, v in self.last_vals.items())
        return (f"{name:<16} 0x{fid:03X} n={self.n:6d} {hz:6.1f} Hz  주기 평균 {avg:6.2f} ms 최대 {self.dt_max*1000:6.2f} ms"
                f"  카운터 건너뜀 {self.cnt_skips}  | {vals}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dbc", default=DEFAULT_DBC)
    ap.add_argument("--msg", help="이 메시지만 (DBC 이름)")
    ap.add_argument("--channel", default="vcan0")
    ap.add_argument("--interface", default="socketcan")
    ap.add_argument("--count", type=int, default=0, help="이만큼 받으면 종료 (0 = 무한)")
    ap.add_argument("--duration", type=float, default=0.0, help="초, 0 = 무한")
    ap.add_argument("--raw", action="store_true", help="디코드 없이 ID 별 hz 만")
    ap.add_argument("--cpu", metavar="LIST", help="실행 CPU (예: 10). 격리 코어에 올릴 때 명시")
    a = ap.parse_args()
    if a.cpu:
        cpus = set()
        for part in a.cpu.split(","):
            lo, _, hi = part.partition("-"); cpus.update(range(int(lo), int(hi or lo) + 1))
        os.sched_setaffinity(0, cpus)

    db = None if a.raw else cantools.database.load_file(a.dbc) if os.path.exists(a.dbc) else sys.exit(f"DBC 없음: {a.dbc}")
    want = db.get_message_by_name(a.msg).frame_id if (db and a.msg) else None
    bus = can.Bus(channel=a.channel, interface=a.interface)
    stats = defaultdict(Stats); t0 = time.perf_counter(); last_print = t0; total = 0
    try:
        while True:
            m = bus.recv(timeout=0.5)
            now = time.perf_counter()
            if m is not None and (want is None or m.arbitration_id == want):
                vals = {}
                if db:
                    try:
                        vals = db.decode_message(m.arbitration_id, m.data, decode_choices=False)
                    except KeyError:
                        vals = {"(DBC에 없는 ID)": 0}
                stats[m.arbitration_id].add(now, vals); total += 1
            if now - last_print >= 1.0:
                last_print = now
                for fid, st in sorted(stats.items()):
                    name = db.get_message_by_frame_id(fid).name if db and fid in {x.frame_id for x in db.messages} else "?"
                    print(st.line(name, fid))
                print("-" * 60)
            if (a.count and total >= a.count) or (a.duration and now - t0 >= a.duration):
                break
    except KeyboardInterrupt:
        pass
    bus.shutdown()
    print("\n== 최종 ==")
    for fid, st in sorted(stats.items()):
        name = db.get_message_by_frame_id(fid).name if db and fid in {x.frame_id for x in db.messages} else "?"
        print(st.line(name, fid))
    sys.exit(0 if total else 1)


if __name__ == "__main__":
    main()
