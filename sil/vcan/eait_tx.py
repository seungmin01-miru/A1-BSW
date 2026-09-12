#!/usr/bin/env python3
"""DBC 기반 주기 송신기 (B-3/B-4) — 메시지 1종을 vcan0 에 스펙 주기로 보낸다.

  python3 eait_tx.py --range 0 60                 # EAIT_INFO_SPD(0x712) 10ms, 0~60 kph 사인파
  python3 eait_tx.py --msg EAIT_INFO_IMU --pattern const --value 0
  python3 eait_tx.py --msg EAIT_Control_01 --pattern const --set EPS_En=1 --set ACC_En=1 --set EPS_Speed=150
  python3 eait_tx.py --dbc /path/EAIT_CAN.dbc --duration 30

DBC 는 기본으로 저장소의 can_protocol/00. CAN 프로토콜/EAIT_CAN(AVANTE_CN7).dbc 를 찾는다 (보드 0-1 항목).
주기는 DBC 의 GenMsgCycleTime 속성을 따르고, 없으면 --period(기본 10ms).
종료(Ctrl+C 또는 --duration) 시 실제 송신 주기의 평균/최대 편차를 출력한다 — §3 통합시험(topic hz) 대조용.
"""
import argparse, math, os, signal, sys, time

import can
import cantools

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
# 팀 DBC 위치 후보 (앞이 우선). 2026-09-12 실제 위치는 저장소 DBC/ 디렉터리.
DBC_CANDIDATES = [os.path.join(REPO_ROOT, "DBC", "EAIT_CAN(AVANTE_CN7).dbc"),
                  os.path.join(REPO_ROOT, "can_protocol", "00. CAN 프로토콜", "EAIT_CAN(AVANTE_CN7).dbc")]
DEFAULT_DBC = next((c for c in DBC_CANDIDATES if os.path.exists(c)), DBC_CANDIDATES[0])


STOP = False


def _on_sigint(*_):
    global STOP
    STOP = True


def apply_rt_setup(cpus, rt_prio):
    """실행 환경을 실시간용으로 맞춘다 (A-3/A-4 최소 구현). 실패 항목은 경고만 내고 계속.
    - 타이머 여유(timer slack): 일반 우선순위 태스크는 커널이 기본 50 µs 여유를 둬 sleep 이 늦게 깬다 → 1 µs 로 (권한 불필요)
    - cpus: 격리 코어 배치. isolcpus 부팅에서는 자동 배치가 안 되므로 명시해야 함
    - rt_prio: SCHED_FIFO 우선순위 (root 또는 limits.conf 의 rtprio 필요) + mlockall (memlock 한도 필요)
    """
    import ctypes
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    PR_SET_TIMERSLACK, MCL_CURRENT, MCL_FUTURE = 29, 1, 2
    notes = []
    if libc.prctl(PR_SET_TIMERSLACK, ctypes.c_ulong(1000), 0, 0, 0) == 0:
        notes.append("timer_slack=1µs")
    if cpus:
        try:
            os.sched_setaffinity(0, cpus); notes.append(f"cpu={sorted(cpus)}")
        except OSError as e:
            notes.append(f"⚠️ CPU 배치 실패({e.strerror})")
    if rt_prio:
        try:
            os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(rt_prio)); notes.append(f"SCHED_FIFO {rt_prio}")
        except PermissionError:
            notes.append("⚠️ SCHED_FIFO 권한 없음 (ulimit -r 확인: /etc/security/limits.d 에 rtprio 설정 또는 sudo)")
        if libc.mlockall(MCL_CURRENT | MCL_FUTURE) == 0:
            notes.append("mlockall")
        else:
            notes.append(f"⚠️ mlockall 실패({os.strerror(ctypes.get_errno())}) — memlock 한도 확인")
    return notes


def parse_cpulist(txt):
    """"8-11,14" → {8,9,10,11,14}"""
    out = set()
    for part in txt.split(","):
        if "-" in part:
            a, b = part.split("-"); out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


def load_db(path):
    if not os.path.exists(path):
        sys.exit(f"DBC 없음: {path}\n → 팀 DBC(EAIT_CAN(AVANTE_CN7).dbc)를 그 경로에 두거나 --dbc 로 지정 (보드 Phase 0-1)")
    return cantools.database.load_file(path)


# 스펙표(can_stack_development.md §5.B, 원본은 EAIT PDF) 주기. 실제 DBC 에는 GenMsgCycleTime 속성이 없어
# (2026-09-12 확인) DBC 만으로는 주기를 알 수 없다 → DBC 속성 > 이 표 > --period 순으로 결정.
SPEC_PERIOD_MS = {0x156: 10, 0x157: 10, 0x710: 20, 0x711: 10, 0x712: 10, 0x713: 10}


def cycle_time_ms(msg, fallback_ms):
    """(주기 ms, 출처) — DBC GenMsgCycleTime > 스펙표 > --period."""
    try:
        if msg.cycle_time:
            return float(msg.cycle_time), "DBC"
    except AttributeError:
        pass
    if msg.frame_id in SPEC_PERIOD_MS:
        return float(SPEC_PERIOD_MS[msg.frame_id]), "스펙표"
    return fallback_ms, "--period"


def signal_range(sig):
    lo = sig.minimum if sig.minimum is not None else 0.0
    hi = sig.maximum if sig.maximum is not None else lo
    if hi <= lo:  # DBC 에 범위가 없으면 raw 범위에서 물리값 계산
        raw_max = (1 << sig.length) - 1 if not sig.is_signed else (1 << (sig.length - 1)) - 1
        lo = sig.offset
        hi = sig.offset + raw_max * sig.scale
    return lo, hi


def make_values(msg, t, pattern, const_value, amp_ratio, freq_hz, counters, rng=None, overrides=None):
    """t 초 시점의 신호값 dict. 신호마다 DBC 범위 안에서만 값을 만든다.
    overrides: {신호명: 값} — --set 으로 지정한 신호는 패턴과 무관하게 그 값으로 고정 (TX 제어 메시지 시험용)."""
    vals = {}
    overrides = overrides or {}
    for sig in msg.signals:
        if sig.name in overrides:
            vals[sig.name] = overrides[sig.name]
            continue
        if sig.choices:                       # enum 신호(상태·기어·방향지시 등): 기본 0(=꺼짐). Turn_Signal 처럼 VAL_ 에
            # 0 이 없어도 DBC 최소값이 0 이면 0 이 "꺼짐"이다. 최소값이 0 보다 크면 첫 정의값.
            vals[sig.name] = 0 if (sig.minimum is None or sig.minimum <= 0) else min(sig.choices)
            continue
        lo, hi = signal_range(sig)
        if sig.length <= 2:                    # 플래그(EPS_En 등)는 사인파가 무의미 → --value 를 0/1 로 고정
            vals[sig.name] = int(min(max(round(const_value), lo), hi))
            continue
        if "alive" in sig.name.lower() or "cnt" in sig.name.lower():   # Alive_Cnt 류: 롤오버 카운터
            counters[sig.name] = (counters.get(sig.name, -1) + 1) % (int(hi) + 1 if hi > 0 else 256)
            vals[sig.name] = counters[sig.name]
            continue
        if rng:                                # --range LO HI: 현실적인 물리 범위로 제한 (DBC 범위 안에서)
            lo, hi = max(lo, rng[0]), min(hi, rng[1])
        if pattern == "const":
            vals[sig.name] = min(max(const_value, lo), hi)
        else:                                  # sine: 범위 중앙을 기준으로 진폭 amp_ratio
            mid, half = (lo + hi) / 2, (hi - lo) / 2 * amp_ratio
            vals[sig.name] = mid + half * math.sin(2 * math.pi * freq_hz * t)
    return vals


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dbc", default=DEFAULT_DBC)
    ap.add_argument("--msg", default="EAIT_INFO_SPD", help="DBC 메시지 이름 (기본 0x712 EAIT_INFO_SPD)")
    ap.add_argument("--channel", default="vcan0")
    ap.add_argument("--interface", default="socketcan", help="python-can 인터페이스 (시험용: virtual)")
    ap.add_argument("--period", type=float, default=10.0, help="DBC 에 주기 속성이 없을 때 쓰는 주기(ms)")
    ap.add_argument("--pattern", choices=["sine", "const"], default="sine")
    ap.add_argument("--value", type=float, default=0.0, help="const 패턴 값(물리 단위)")
    ap.add_argument("--amp", type=float, default=0.5, help="sine 진폭 = 범위 절반 × amp (0~1)")
    ap.add_argument("--freq", type=float, default=0.2, help="sine 주파수(Hz)")
    ap.add_argument("--range", nargs=2, type=float, metavar=("LO", "HI"),
                    help="sine 물리 범위 (예: 휠속 0 60). 생략하면 DBC 범위 전체")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                    help="신호 고정값 (반복 가능). 예: --set EPS_En=1 --set EPS_Speed=150")
    ap.add_argument("--duration", type=float, default=0.0, help="송신 시간(초), 0 = 무한")
    ap.add_argument("--cpu", metavar="LIST", help="실행 CPU (예: 8 또는 8-11). 격리 코어에 올릴 때 필수 — A-3")
    ap.add_argument("--rt", type=int, default=0, metavar="PRIO", help="SCHED_FIFO 우선순위(1~99) + mlockall — A-3/A-4 (rtprio 권한 필요)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    notes = apply_rt_setup(parse_cpulist(a.cpu) if a.cpu else None, a.rt)
    print("실행 환경: " + ", ".join(notes), file=sys.stderr)
    db = load_db(a.dbc)
    msg = db.get_message_by_name(a.msg)
    names = {sg.name for sg in msg.signals}
    overrides = {}
    for item in a.set:
        k, _, v = item.partition("=")
        if k not in names:
            sys.exit(f"--set {item}: {a.msg} 에 없는 신호. 가능한 신호: {sorted(names)}")
        overrides[k] = float(v)
    period_ms, src = cycle_time_ms(msg, a.period)
    period = period_ms / 1000.0
    bus = can.Bus(channel=a.channel, interface=a.interface)
    print(f"{a.msg} (0x{msg.frame_id:X}, {msg.length}B) → {a.channel}, 주기 {period_ms:.1f} ms ({src}), "
          f"DBC={os.path.relpath(a.dbc, REPO_ROOT)}\n신호 {[s.name for s in msg.signals]}", file=sys.stderr)

    signal.signal(signal.SIGINT, _on_sigint)
    counters, n, worst, err_sum = {}, 0, 0.0, 0.0
    t0 = time.perf_counter(); next_t = t0
    while not STOP and not (a.duration and time.perf_counter() - t0 >= a.duration):
        now = time.perf_counter()
        if now < next_t:
            time.sleep(next_t - now)
            now = time.perf_counter()
        late = now - next_t                      # 예정 시각 대비 늦은 정도
        vals = make_values(msg, now - t0, a.pattern, a.value, a.amp, a.freq, counters, a.range, overrides)
        data = msg.encode(vals, strict=True)
        bus.send(can.Message(arbitration_id=msg.frame_id, data=data, is_extended_id=msg.is_extended_frame))
        n += 1; err_sum += abs(late); worst = max(worst, late)
        if not a.quiet and n % int(max(1, 1.0 / period)) == 0:   # 1초마다 한 줄
            print(f"[{n:7d}] {' '.join(f'{k}={v:.2f}' for k, v in vals.items())}", file=sys.stderr)
        next_t += period
    bus.shutdown()
    el = time.perf_counter() - t0
    print(f"\n송신 {n}프레임 / {el:.1f}s = {n/el:.1f} Hz (목표 {1/period:.1f}) | "
          f"주기 편차 평균 {err_sum/max(n,1)*1e6:.0f} µs, 최대 {worst*1e6:.0f} µs", file=sys.stderr)


if __name__ == "__main__":
    main()
