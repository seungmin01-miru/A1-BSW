#!/usr/bin/env python3
"""
can_guard — 안전 임계 CAN TX 메인 루프 (§7.2, docs/can_stack_development.md §5.D).

  python3 can_guard.py --channel vcan0
  sudo python3 can_guard.py --channel can0 --cpu 8 --rt-priority 90   # A-3 레시피(§5.C 에서 효과 실측됨)
  python3 can_guard.py --channel can0 --steer-limit-deg 15 --brake-limit-pct 60 --acc-limit-pct 10 \
      --status-interval-s 1          # 리프트 시험(tools/race_day/VEHICLE_MANUAL.md "C. 리프트 송신 시험")

2026-10-06 실차 프로토콜(0x200/0x201/0x210, DBC/A1_dbc_fixed.dbc)로 전환 — 이전 EAIT 0x156/0x157 는 git 기록.

매 주기(기본 20ms — 10-07 실측 차량 상태 주기 21.0 ms 보다 약간 빠르게):
  CommandChannel.read() → (0x201 차속·0x200 상태 non-blocking 수신) → state_machine.next_state()
  → command_policy.command_for_state() → plausibility.clamp_range(운용 한계)(방어 이중화)
  → tx_encode.encode_0x210 → bus.send(0x210)

시작 전 점검(--precheck-s, 기본 1초): 버스를 듣기만 해서 **다른 장치가 이미 0x210 을 보내고 있으면 시작을
거부**한다(원격조종 등과 명령 충돌 방지). 실행 중에 다른 0x210 이 보이면 1초에 한 번 경고만 한다 — can_guard 가
멈추면 안전 명령도 끊기므로 스스로 죽지는 않는다(운영자가 판단).

TODO(남은 작업, README 참고): 인지 하트비트는 --hb-shm 로 채널만 열어 두고 실제 값은 아직 아무도 안 씀
(인지 프로세스 쪽 구현 전) — 지금은 perception_age 가 항상 None 이라 상태머신이 DEGRADED 로 자주 갈 것이다,
이건 버그가 아니라 "인지 프로세스가 아직 없다"는 사실을 정직하게 반영한 것이다.

DEGRADED 감속·STOPPED 차속수렴 정책은 2026-09-21 초안(팀 확인 전) — state_machine.py/command_policy.py
docstring 과 README 참고. 이미 열려 있는 TX 용 `bus` 를 그대로 RX 에도 써서(같은 채널이니까) 0x201(차속)
등을 non-blocking 으로 읽는다 — 새 프로세스·새 IPC 채널 없이 can_guard 하나로 끝낸다(설계 결정).
비상 정지(2026-11-04, bsw_test 00 SAF-ESTOP): 원천 셋 중 하나라도 켜지면 ESTOP(최우선·래치).
  ① 공유메모리 `EstopChannel`(`estop.py` 로 누름/해제)  ② SIGUSR1(`pkill -USR1 -f can_guard.py`)
  ③ 원격(핸드폰 앱) 0x156 En=1 또는 0x157 ACC_Cmd ≤ 문턱(`--estop-can remote`, 기본) — 선택적으로 데드맨(`--estop-can-timeout`)
ESTOP 브레이크는 시작값 → 서서히 상한(`--estop-brake-*`), 가속 즉시 0, 조향 고정, auto 비트 유지(auto 000 금지).
INIT 은 기본 `--init-mode silent`: 첫 유효 명령 전에는 0x210 을 보내지 않는다(auto 000 = 페일세이프 브레이크 해제, 11-03).
제어 노드 명령이 auto 000(어댑터 disarm·lift_cmd auto off)이어도 기본 `--auto-off-mode silent` 로 보내지 않는다.
`--event-log` 로 비상 정지 수신·상태 전이·첫 송신 시각(벽시계 + monotonic)을 CSV 로 남긴다 — 00 시험 지연 계산용.
"""
import argparse
import signal
import sys
import time

import can

import kvaser_compat
import sd_notify
from command_policy import BrakeProfile, BrakeRamp, command_for_state
from command_policy import AUTO_OFF_MODES, INIT_MODES, STOPPED_MODES, held_steer_follow_remote, held_steer_on_transition
from plausibility import Limits, RateLimiter, clamp_range
from protocol import Command, CommandChannel, EstopChannel, HeartbeatChannel, TornReadError
from rt_setup import apply as apply_rt
from rt_setup import parse_cpulist
from rx_decode import FRAME_ID_CONTROL_COMMAND, FRAME_ID_CONTROL_INFO, FRAME_ID_WHEEL_INFO
from rx_decode import decode_control_info, decode_vehicle_speed_kph
from rx_decode import FRAME_ID_REMOTE_01, FRAME_ID_REMOTE_02, RemoteEstopDetector
from state_machine import DEFAULT_PERCEPTION_LOST_STOPPED_AFTER_S, DEFAULT_PERCEPTION_TIMEOUT_S
from state_machine import DEFAULT_STOP_SPEED_KPH, DEFAULT_STOPPED_AFTER_S, DEFAULT_WATCHDOG_T_S
from state_machine import State, next_state
from tx_encode import encode_0x210

SPIKE_THRESHOLD_S = 100e-6   # 스냅샷에서 "튐"으로 셀 자체측정 편차 문턱 — 8h 연속운전 관측용(§8, 2026-09-18)
VS_MAX_AGE_S = 1.0           # 이보다 오래된 차속은 "모름" 취급(RX 가 없거나 끊겼을 때 시간 기반 안전망으로 복귀)
VS_DRAIN_MAX_PER_CYCLE = 64  # 한 주기에 non-blocking recv() 최대 시도 횟수 — 버스 폭주 시 무한 루프 방지
#                              (11-03 버스: 0x200/0x201/0x128/0x202/0x710~712 + 원격 0x156/0x157 100 Hz ≈ 주기당 10여 개)
EXIT_FOREIGN_SENDER = 3      # 시작 전 점검에서 다른 0x210 송신자 발견
EXIT_NO_ESTOP_SOURCE = 4     # --estop-can-required 인데 시작 전 점검에서 원격 0x156/0x157 이 안 보임


class _SkipTx(Exception):
    """이번 주기는 0x210 을 보내지 않는다(silent INIT 등)."""


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--channel', default='vcan0')
    ap.add_argument('--interface', default='socketcan',
                    help='socketcan(PEAK 카드 can0/can1, 기본) 또는 kvaser(Kvaser Leaf v3 — CANlib 채널 번호를 --channel 로)')
    ap.add_argument('--bitrate', type=int, default=500000,
                    help='socketcan 이 아닌 인터페이스(kvaser)에서만 사용 — socketcan 은 ip link 로 이미 정해져 있음')
    ap.add_argument('--period', type=float, default=0.020,
                    help='TX 주기(초) — 10-07 실측 차량 상태 주기(21.0 ms)에 맞춘 20 ms')
    ap.add_argument('--cpu', default='', help='격리 코어 배치, 예 "8" (A-3)')
    ap.add_argument('--rt-priority', type=int, default=0, help='SCHED_FIFO 우선순위, 0=미적용')
    ap.add_argument('--watchdog-t', type=float, default=DEFAULT_WATCHDOG_T_S, help='명령 staleness 임계(초)')
    ap.add_argument('--stopped-after', type=float, default=DEFAULT_STOPPED_AFTER_S,
                    help='HOLDING 이 이만큼 지속되면 STOPPED')
    ap.add_argument('--perception-timeout', type=float, default=DEFAULT_PERCEPTION_TIMEOUT_S)
    ap.add_argument('--perception-lost-stopped-after', type=float,
                    default=DEFAULT_PERCEPTION_LOST_STOPPED_AFTER_S,
                    help='DEGRADED 가 이만큼 지속되면 STOPPED (2026-09-21 초안, 팀 확인 필요)')
    ap.add_argument('--stop-speed-kph', type=float, default=DEFAULT_STOP_SPEED_KPH,
                    help='실측 차속(0x201 좌우 평균)이 이 이하면 정지로 간주 (2026-09-21 초안, 팀 확인 필요)')
    ap.add_argument('--steer-limit-deg', type=float, default=150.0, help='조향 운용 한계 ±deg (DBC 150)')
    ap.add_argument('--brake-limit-pct', type=float, default=100.0, help='브레이크 운용 한계 %% (DBC 100)')
    ap.add_argument('--acc-limit-pct', type=float, default=100.0, help='가속 운용 한계 %% (DBC 100)')
    ap.add_argument('--hold-brake-pct', type=float, default=30.0,
                    help='HOLDING/DEGRADED/STOPPED 진입 즉시 거는 브레이크 %% (시작값)')
    ap.add_argument('--hold-brake-ramp-pct-s', type=float, default=20.0,
                    help='명령이 다시 안 들어오면 그다음 올리는 속도 %%/s (2026-11-04: 30 시작 → 서서히, 잠정)')
    ap.add_argument('--hold-brake-max-pct', type=float, default=60.0, help='보호 상태 브레이크 상한 %% (잠정)')
    ap.add_argument('--estop-brake-start-pct', type=float, default=30.0, help='ESTOP 진입 즉시 브레이크 %%')
    ap.add_argument('--estop-brake-ramp-pct-s', type=float, default=40.0,
                    help='ESTOP 브레이크를 올리는 속도 %%/s — 브레이크 응답이 느려 휠락 방지로 서서히(잠정)')
    ap.add_argument('--estop-brake-max-pct', type=float, default=82.0,
                    help='ESTOP 브레이크 상한 %% (82 이상은 차량 위치 8.2 포화, 11-03)')
    ap.add_argument('--init-mode', choices=INIT_MODES, default='silent',
                    help='silent=첫 유효 명령 전엔 0x210 안 보냄(기본), legacy=auto 000·명령 0 송신(브레이크 풀림 주의)')
    ap.add_argument('--auto-off-mode', choices=AUTO_OFF_MODES, default='silent',
                    help='제어 노드 명령이 auto 000 일 때: silent=송신 안 함(기본), send=auto 000 송신(브레이크 풀림 주의)')
    ap.add_argument('--estop-shm', default=None, help='비상 정지 공유메모리 이름(기본 a1_can_guard_estop)')
    ap.add_argument('--estop-can', choices=('remote', 'off'), default='remote',
                    help='remote=원격(핸드폰 앱) 0x156 En=1 또는 0x157 ACC_Cmd ≤ --estop-acc-mps2 이면 비상 정지')
    ap.add_argument('--estop-acc-mps2', type=float, default=-1.0, help='0x157 ACC_Cmd 정지 문턱 m/s² (잠정)')
    ap.add_argument('--estop-confirm-frames', type=int, default=2, help='원격 조건이 이만큼 연속이면 인정')
    ap.add_argument('--estop-can-timeout', type=float, default=0.0,
                    help='>0 이면 데드맨: 원격 프레임이 이만큼(초) 끊기면 비상 정지. 0=끔(기본)')
    ap.add_argument('--estop-can-required', action='store_true',
                    help='시작 전 점검에서 원격 0x156/0x157 이 안 보이면 시작 거부(종료 코드 4)')
    ap.add_argument('--event-log', default=None, help='비상 정지·상태 전이 이벤트 CSV 경로')
    ap.add_argument('--stopped-mode', choices=STOPPED_MODES, default='hold',
                    help='hold=auto 유지+브레이크 유지(기본), manual=auto 0·명령 0(사람에게 넘김)')
    ap.add_argument('--steer-rate-limit', type=float, default=None, help='deg/s, 미정이면 무제한(§5.D 참고)')
    ap.add_argument('--acc-rate-limit', type=float, default=None, help='%%/s, 미정이면 무제한')
    ap.add_argument('--estop-remote-steer', choices=('follow', 'hold'), default='follow',
                    help='ESTOP 중 앱 En=1 동안 고정 조향각: follow=0x200 실제 핸들각을 따라감(기본, 앱 해제 순간 튐 방지 — '
                         '11-04 −53° 튐), hold=진입 순간 값(예전)')
    ap.add_argument('--precheck-s', type=float, default=1.0,
                    help='시작 전 이만큼 듣고 다른 0x210 송신자가 있으면 시작 거부. 0=점검 안 함')
    ap.add_argument('--status-interval-s', type=float, default=0.0,
                    help='이 간격(초)마다 상태·보낸 명령·차량 피드백(0x200/0x201) 한 줄. 0=끔(리프트 시험은 1)')
    ap.add_argument('--cmd-shm', default=None)
    ap.add_argument('--hb-shm', default=None)
    ap.add_argument('--max-cycles', type=int, default=0, help='0=무한. SIL 시험용으로 유한 실행할 때 사용')
    ap.add_argument('--stats-interval-s', type=float, default=0.0,
                    help='이 간격(초)마다 자체측정 지연 스냅샷을 로그로 남김. 0=비활성(기본, 장시간 운전용)')
    ap.add_argument('--quiet', action='store_true')
    return ap.parse_args(argv)


_stop_requested = False
_estop_signal_t = None   # SIGUSR1 받은 monotonic 시각(래치, 해제 요청으로만 지움)


def _on_shutdown_signal(signum, frame):
    global _stop_requested
    _stop_requested = True


def _on_estop_signal(signum, frame):
    global _estop_signal_t
    if _estop_signal_t is None:
        _estop_signal_t = time.monotonic()


class EventLog:
    """00 SAF-ESTOP 지연 계산용 CSV. 벽시계(CAN 로그와 맞추기용)와 monotonic(내부 지연용)을 같이 적는다."""

    def __init__(self, path):
        self.f = open(path, 'a', buffering=1) if path else None
        if self.f and self.f.tell() == 0:
            self.f.write('wall_s,mono_s,event,detail\n')

    def __call__(self, event, detail='', mono=None):
        if self.f:
            m = time.monotonic() if mono is None else mono
            w = time.time() - (time.monotonic() - m)
            self.f.write(f'{w:.6f},{m:.6f},{event},"{detail}"\n')

    def close(self):
        if self.f:
            self.f.close()


def run(a):
    # SIGTERM 은 Python 기본 처리(즉시 커널 종료, finally 미실행)라 방치하면 cmd_ch/hb_ch.close() 의
    # unlink() 가 안 돌아 /dev/shm 세그먼트가 누수된다(재현 확인, 2026-09-18) — 정상 종료 경로로 흡수한다.
    signal.signal(signal.SIGTERM, _on_shutdown_signal)
    signal.signal(signal.SIGINT, _on_shutdown_signal)
    signal.signal(signal.SIGUSR1, _on_estop_signal)
    global _estop_signal_t
    _estop_signal_t = None
    cpus = parse_cpulist(a.cpu)
    notes = apply_rt(cpus, a.rt_priority)
    log = (lambda *args, **kw: None) if a.quiet else (lambda *args, **kw: print(*args, **kw))
    log(f'[can_guard] 실행 환경: {", ".join(notes) if notes else "기본값"}', file=sys.stderr)

    cycle = 0
    tx_fail = 0
    tx_err_sum = 0.0
    tx_worst = 0.0
    tx_spikes = 0
    cmd_ch = hb_ch = es_ch = bus = None
    limits = Limits(a.steer_limit_deg, a.brake_limit_pct, a.acc_limit_pct)   # DBC 범위 밖이면 여기서 거부
    hold_profile = BrakeProfile(a.hold_brake_pct, a.hold_brake_ramp_pct_s, a.hold_brake_max_pct)
    estop_profile = BrakeProfile(a.estop_brake_start_pct, a.estop_brake_ramp_pct_s, a.estop_brake_max_pct)
    remote = RemoteEstopDetector(a.estop_acc_mps2, a.estop_confirm_frames, a.estop_can_timeout)
    ev = EventLog(a.event_log)
    try:
        cmd_kwargs = {'name': a.cmd_shm} if a.cmd_shm else {}
        hb_kwargs = {'name': a.hb_shm} if a.hb_shm else {}
        cmd_ch = CommandChannel.create(**cmd_kwargs)   # guard 가 먼저 뜬다 → 세그먼트도 guard 가 만든다(§7.2)
        hb_ch = HeartbeatChannel.create(**hb_kwargs)
        es_ch = EstopChannel.create(**({'name': a.estop_shm} if a.estop_shm else {}))
        bus_kw = {} if a.interface == 'socketcan' else {'bitrate': a.bitrate}
        if a.interface == 'kvaser':
            kvaser_compat.apply()   # python-can 4.6.1 ↔ CANlib 5.52 LOCAL_TXACK 버퍼 크기 호환
        bus = can.Bus(channel=a.channel, interface=a.interface, receive_own_messages=False, **bus_kw)
        log(f'[can_guard] 연결: {a.interface}:{a.channel}, 주기 {a.period * 1000:.1f}ms, 운용 한계 {limits}, '
            f'보호 브레이크 {a.hold_brake_pct:g}→{a.hold_brake_max_pct:g}% ({a.hold_brake_ramp_pct_s:g}%/s), '
            f'ESTOP 브레이크 {a.estop_brake_start_pct:g}→{a.estop_brake_max_pct:g}% ({a.estop_brake_ramp_pct_s:g}%/s), '
            f'STOPPED={a.stopped_mode}, INIT={a.init_mode}, auto000={a.auto_off_mode}, 원격 비상정지={a.estop_can}', file=sys.stderr)
        ev('start', f'{a.interface}:{a.channel} init={a.init_mode} estop_can={a.estop_can}')

        if a.precheck_s > 0:
            seen = {}
            t_end = time.monotonic() + a.precheck_s
            while time.monotonic() < t_end and not _stop_requested:
                m = bus.recv(timeout=0.05)
                if m is not None and not m.is_error_frame:
                    seen[m.arbitration_id] = seen.get(m.arbitration_id, 0) + 1
            summary = ', '.join(f'0x{k:03x}×{v}' for k, v in sorted(seen.items())) or '(수신 없음)'
            log(f'[can_guard] 시작 전 점검 {a.precheck_s:g}s: {summary}', file=sys.stderr)
            if seen.get(FRAME_ID_CONTROL_COMMAND):
                log('[can_guard] 🛑 다른 장치가 이미 0x210 을 보내고 있다 — 시작 거부(원격조종 등을 끄고 재실행)',
                    file=sys.stderr)
                return EXIT_FOREIGN_SENDER
            remote_seen = seen.get(FRAME_ID_REMOTE_01) or seen.get(FRAME_ID_REMOTE_02)
            if a.estop_can == 'remote' and not remote_seen:
                log('[can_guard] ⚠️ 원격(핸드폰 앱) 0x156/0x157 이 안 보임 — 앱 비상 정지가 동작하지 않는다',
                    file=sys.stderr)
                if a.estop_can_required:
                    log('[can_guard] 🛑 --estop-can-required — 시작 거부(앱 연결 후 재실행)', file=sys.stderr)
                    return EXIT_NO_ESTOP_SOURCE
            if not seen.get(FRAME_ID_CONTROL_INFO) and not seen.get(FRAME_ID_WHEEL_INFO):
                log('[can_guard] ⚠️ 차량 메시지(0x200/0x201)가 안 보임 — 비트레이트·배선 확인(송신은 진행)',
                    file=sys.stderr)

        state = State.INIT
        last_valid_cmd = Command()
        last_auto_cmd = None   # auto 비트가 있던 마지막 명령 — ESTOP·STOPPED 가 disarm(auto 000)에 풀리지 않게(2026-11-05)
        last_cmd_mono = None       # 마지막으로 "성공 확인된" 명령 timestamp 의 monotonic 절대시각
        last_hb_mono = None
        holding_entered_at = None
        degraded_entered_at = None
        held_steer_deg = 0.0
        last_vs_kph = None
        last_vs_mono = None
        last_info = None
        foreign_n = 0
        foreign_warned_at = None
        status_at = time.monotonic()
        steer_limiter = RateLimiter(a.steer_rate_limit)
        acc_limiter = RateLimiter(a.acc_rate_limit)
        brake_ramp = BrakeRamp()
        es_reset_seq = None        # 마지막으로 본 EstopChannel.reset_seq
        es_shm_on = sig_on = remote_on = False   # 원천별 이전 값(상승 에지 로그용)
        estop_tx_pending = False   # ESTOP 진입 뒤 첫 송신을 이벤트로 남기기

        sd_notify.ready()
        log(f'[can_guard] INIT — {"첫 유효 명령 전까지 0x210 송신 안 함" if a.init_mode == "silent" else "auto 0·명령 0 송신"}'
            f' (watchdog_t={a.watchdog_t * 1000:.0f}ms)', file=sys.stderr)

        next_t = time.monotonic()
        win_start_t = next_t
        win_err_sum = 0.0
        win_worst = 0.0
        win_n = 0
        win_spikes = 0
        while not _stop_requested and (a.max_cycles == 0 or cycle < a.max_cycles):
            now = time.monotonic()
            if now < next_t:
                time.sleep(next_t - now)
                now = time.monotonic()
            late = now - next_t   # 자체 측정: (삭제된) eait_tx.py 와 같은 정의(now-next_t), 외부 관찰자 없음
            tx_err_sum += abs(late)
            tx_worst = max(tx_worst, late)
            if abs(late) > SPIKE_THRESHOLD_S:
                tx_spikes += 1

            if a.stats_interval_s > 0:
                win_err_sum += abs(late)
                win_worst = max(win_worst, late)
                win_n += 1
                if abs(late) > SPIKE_THRESHOLD_S:
                    win_spikes += 1
                if now - win_start_t >= a.stats_interval_s:
                    log(f'[can_guard] 지연 스냅샷 {win_n}주기(윈도 {now - win_start_t:.0f}s) | '
                        f'평균={win_err_sum / win_n * 1e6:.1f}µs 최대={win_worst * 1e6:.1f}µs '
                        f'스파이크(>{SPIKE_THRESHOLD_S * 1e6:.0f}µs)={win_spikes} | '
                        f'누적 평균={tx_err_sum / (cycle + 1) * 1e6:.1f}µs 최대={tx_worst * 1e6:.1f}µs '
                        f'스파이크={tx_spikes}', file=sys.stderr)
                    win_start_t, win_err_sum, win_worst, win_n, win_spikes = now, 0.0, 0.0, 0, 0

            # --- 1. 채널 읽기 (실패해도 마지막으로 확인된 절대시각 기준으로 나이를 계속 계산) ---
            try:
                cmd, cmd_age = cmd_ch.read()
            except TornReadError:
                cmd, cmd_age = None, None
            if cmd is not None:
                last_valid_cmd = cmd
                if cmd.steer_auto or cmd.brake_auto or cmd.acc_auto:
                    last_auto_cmd = cmd
                last_cmd_mono = now - cmd_age
            cmd_age_s = (now - last_cmd_mono) if last_cmd_mono is not None else None

            try:
                hb_age = hb_ch.age()
            except TornReadError:
                hb_age = None
            if hb_age is not None:
                last_hb_mono = now - hb_age
            perception_age_s = (now - last_hb_mono) if last_hb_mono is not None else None

            # 차량 메시지 non-blocking 드레인 — 같은 bus 재사용, 새 프로세스/IPC 없음(설계 결정, 위 docstring)
            for _ in range(VS_DRAIN_MAX_PER_CYCLE):
                msg = bus.recv(timeout=0)
                if msg is None:
                    break
                if msg.is_error_frame or len(msg.data) < 8:
                    continue
                if msg.arbitration_id == FRAME_ID_WHEEL_INFO:
                    last_vs_kph = decode_vehicle_speed_kph(msg.data)
                    last_vs_mono = now
                elif msg.arbitration_id == FRAME_ID_CONTROL_INFO:
                    last_info = decode_control_info(msg.data)
                elif msg.arbitration_id in (FRAME_ID_REMOTE_01, FRAME_ID_REMOTE_02):
                    if a.estop_can == 'remote':
                        # 수신 시각: socketcan 은 epoch 타임스탬프 → monotonic 으로 환산(아니면 이번 주기 시각)
                        ts = msg.timestamp
                        lag = time.time() - ts if ts else None
                        t_rx = now - lag if (lag is not None and 0 <= lag < 1.0) else now
                        remote.feed(msg.arbitration_id, msg.data, t_rx)
                elif msg.arbitration_id == FRAME_ID_CONTROL_COMMAND:
                    foreign_n += 1   # 우리 소켓은 자기 송신을 안 받는다 → 다른 송신자
                    if foreign_warned_at is None or now - foreign_warned_at >= 1.0:
                        log(f'[can_guard] ⚠️ 다른 0x210 송신자 감지(누적 {foreign_n}) {bytes(msg.data).hex()} '
                            f'— 명령 충돌 중', file=sys.stderr)
                        # 11-04 bsw5: 원격 유닛이 can_guard 실행 중에 켜져 124 s 동안 0x210 이 두 송신자로 섞였는데
                        # 이벤트 로그에는 안 남았다 → 00 분석(analysis.py)이 잡도록 1 초에 한 줄 남긴다(동작은 그대로)
                        ev('foreign_0x210', f'n={foreign_n} data={bytes(msg.data).hex()} state={state.value}', now)
                        foreign_warned_at = now
            vs_age_s = (now - last_vs_mono) if last_vs_mono is not None else None

            # --- 1b. 비상 정지 원천 모으기 ---
            if a.estop_can == 'remote':
                remote.update(now)
            try:
                es = es_ch.read()
            except TornReadError:
                es = None
            estop_reset = False
            if es is not None:
                if es_reset_seq is not None and es.reset_seq != es_reset_seq:
                    estop_reset = True
                es_reset_seq = es.reset_seq
            if estop_reset:
                _estop_signal_t = None   # SIGUSR1 래치도 해제 요청으로 지운다
            es_shm_now = bool(es and es.request)
            sig_now = _estop_signal_t is not None
            remote_now = a.estop_can == 'remote' and remote.asserted
            if es_shm_now and not es_shm_on:
                trig = es.trigger_mono_ns / 1e9
                ev('estop_request', f'shm source={es.source} trigger_mono={trig:.6f} detect_lag_ms={(now - trig) * 1e3:.1f}')
            if sig_now and not sig_on:
                ev('estop_request', f'SIGUSR1 detect_lag_ms={(now - _estop_signal_t) * 1e3:.1f}', mono=now)
            if remote_now and not remote_on:
                ev('estop_request', f'remote {remote.reason} detect_lag_ms={(now - remote.trigger_t) * 1e3:.1f}')
            es_shm_on, sig_on, remote_on = es_shm_now, sig_now, remote_now
            estop_any = es_shm_now or sig_now or remote_now
            vehicle_speed_kph = last_vs_kph if (vs_age_s is not None and vs_age_s <= VS_MAX_AGE_S) else None

            # --- 2. 상태 전이 ---
            holding_duration_s = (now - holding_entered_at) if holding_entered_at is not None else 0.0
            degraded_duration_s = (now - degraded_entered_at) if degraded_entered_at is not None else 0.0
            new_state = next_state(state, cmd_age_s, holding_duration_s, perception_age_s,
                                   watchdog_t_s=a.watchdog_t, stopped_after_s=a.stopped_after,
                                   perception_timeout_s=a.perception_timeout,
                                   degraded_duration_s=degraded_duration_s,
                                   perception_lost_stopped_after_s=a.perception_lost_stopped_after,
                                   vehicle_speed_kph=vehicle_speed_kph, stop_speed_kph=a.stop_speed_kph,
                                   estop=estop_any, estop_reset=estop_reset)
            if estop_reset:
                ok = state == State.ESTOP and new_state != State.ESTOP
                ev('estop_reset', 'accepted' if ok else
                   f'refused state={state.value} estop_active={estop_any} speed_kph={vehicle_speed_kph}')
                if state == State.ESTOP and not ok:
                    log(f'[can_guard] 비상 정지 해제 거부 — 정지 요청 남음={estop_any}, 차속={vehicle_speed_kph} '
                        f'(정지 문턱 {a.stop_speed_kph:g} km/h 이하·차속 확인 필요)', file=sys.stderr)
            if new_state != state:
                ev('state', f'{state.value}->{new_state.value} cmd_age={cmd_age_s} speed_kph={vehicle_speed_kph}')
                if new_state == State.ESTOP:
                    estop_tx_pending = True
                log(f'[can_guard] {state.value} → {new_state.value}'
                    f' (cmd_age={cmd_age_s}, perception_age={perception_age_s},'
                    f' vehicle_speed_kph={vehicle_speed_kph}, 차량 0x200={last_info})', file=sys.stderr)
                sd_notify.status(new_state.value)
            # 조향 고정값: ACTIVE/INIT → (HOLDING|DEGRADED|STOPPED) 첫 진입 순간에만 캡처(command_policy 설명 참고)
            held_steer_deg = held_steer_on_transition(state, new_state, held_steer_deg, last_valid_cmd)
            # ESTOP 중 앱 En=1 이면 차량은 앱을 따른다 → 고정값을 실제 핸들각으로(앱 해제 순간 조향 튐 방지, 11-04 bsw4)
            held_steer_deg = held_steer_follow_remote(
                new_state, held_steer_deg, a.estop_can == 'remote' and remote.remote_enabled,
                last_info['steer_pos_deg'] if last_info else None, a.estop_remote_steer)
            if new_state == State.HOLDING and state != State.HOLDING:
                holding_entered_at = now
            elif new_state != State.HOLDING:
                holding_entered_at = None
            if new_state == State.DEGRADED and state != State.DEGRADED:
                degraded_entered_at = now
            elif new_state != State.DEGRADED:
                degraded_entered_at = None
            state = new_state

            # --- 3. 명령 생성 → 방어 클램프 → 인코딩 → 전송 ---
            out = command_for_state(state, last_valid_cmd, held_steer_deg, steer_limiter, acc_limiter, brake_ramp,
                                    a.period, last_auto_cmd=last_auto_cmd, hold_profile=hold_profile, estop_profile=estop_profile,
                                    stopped_mode=a.stopped_mode, init_mode=a.init_mode,
                                    auto_off_mode=a.auto_off_mode)
            if out is not None:
                violations = []
                out = clamp_range(out, limits=limits, violations_out=violations)
                if violations and not a.quiet:
                    log(f'[can_guard] plausibility 위반 {len(violations)}건: {violations}', file=sys.stderr)
            elif estop_tx_pending:
                ev('estop_no_tx', '제어권을 받은 적 없음(auto 000) — 송신 안 함, 차량 페일세이프에 맡김')
                estop_tx_pending = False

            try:
                if out is None:
                    raise _SkipTx
                # timeout: 실버스에서 ACK 없음·bus-off 로 송신 큐가 차면 무한 대기로 루프 전체가 멈춘다 → 반 주기 상한
                bus.send(can.Message(arbitration_id=FRAME_ID_CONTROL_COMMAND, data=encode_0x210(out),
                                     is_extended_id=False), timeout=a.period / 2)
                if estop_tx_pending:
                    ev('estop_first_tx', f'brake={out.brake_cmd_pct:.1f} acc={out.acc_cmd_pct:.1f} '
                       f'auto={int(out.steer_auto)}{int(out.brake_auto)}{int(out.acc_auto)}')
                    estop_tx_pending = False
            except _SkipTx:
                pass
            except can.CanError as e:
                tx_fail += 1
                if tx_fail in (1, 10) or tx_fail % 500 == 0:
                    log(f'[can_guard] ⚠️ 송신 실패 누적 {tx_fail}: {e} (버스 문제·bus-off·E-stop 가능)',
                        file=sys.stderr)

            if a.status_interval_s > 0 and now - status_at >= a.status_interval_s:
                status_at = now
                fb = (f'조향위치 {last_info["steer_pos_deg"]:+.1f}° 브레이크위치 {last_info["brake_pos"]:.1f} '
                      f'auto {last_info["steer_auto"]}{last_info["brake_auto"]}{last_info["acc_auto"]}'
                      if last_info else '0x200 없음')
                sent = ('송신 안 함' if out is None else
                        f'보냄: 조향 {out.steer_cmd_deg:+.1f}° 브레이크 {out.brake_cmd_pct:.0f}% 가속 {out.acc_cmd_pct:.0f}% '
                        f'auto {int(out.steer_auto)}{int(out.brake_auto)}{int(out.acc_auto)}')
                log(f'[can_guard] {state.value:8s} {sent} | 차량: {fb} | 차속 '
                    f'{vehicle_speed_kph if vehicle_speed_kph is not None else "?"} km/h | 송신오류 누적 {tx_fail}건'
                    + (' (정상)' if tx_fail == 0 else ' ⚠️'),
                    file=sys.stderr)

            sd_notify.watchdog()
            cycle += 1
            next_t += a.period
    finally:
        if bus is not None:
            bus.shutdown()
        if cmd_ch is not None:
            cmd_ch.close()
        if hb_ch is not None:
            hb_ch.close()
        if es_ch is not None:
            es_ch.close()
        ev('stop', f'cycles={cycle} tx_fail={tx_fail}')
        ev.close()
        avg_err = tx_err_sum / cycle * 1e6 if cycle else 0.0
        log(f'[can_guard] 종료 — {cycle} 주기 | 자체측정(late=now-next_t) 평균 {avg_err:.1f}µs'
            f' 최대 {tx_worst * 1e6:.1f}µs 스파이크(>{SPIKE_THRESHOLD_S * 1e6:.0f}µs)={tx_spikes}건,'
            f' 송신 실패 {tx_fail}건', file=sys.stderr)
    return 0


def main():
    sys.exit(run(parse_args()))


if __name__ == '__main__':
    main()
