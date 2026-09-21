#!/usr/bin/env python3
"""
can_guard — 안전 임계 CAN TX 메인 루프 (§7.2, can_stack_development.md §5.D).

  python3 can_guard.py --channel vcan0
  sudo python3 can_guard.py --channel can0 --cpu 8 --rt-priority 90   # A-3 레시피(§5.C 에서 효과 실측됨)

매 주기(기본 10ms = 0x156/0x157 스펙 주기):
  CommandChannel.read() → (0x711 VS 있으면 같이 읽음) → state_machine.next_state()
  → command_policy.command_for_state() → plausibility.clamp_range()(방어 이중화)
  → tx_encode → bus.send(0x156), bus.send(0x157)

TODO(남은 작업, README 참고): 인지 하트비트는 --hb-shm 로 채널만 열어 두고 실제 값은 아직 아무도 안 씀
(인지 프로세스 쪽 구현 전) — 지금은 perception_age 가 항상 None 이라 상태머신이 DEGRADED 로 자주 갈 것이다,
이건 버그가 아니라 "인지 프로세스가 아직 없다"는 사실을 정직하게 반영한 것이다.

DEGRADED 감속·STOPPED 차속수렴 정책은 2026-09-21 초안(팀 확인 전) — state_machine.py/command_policy.py
docstring 과 README 참고. 이미 열려 있는 TX 용 `bus` 를 그대로 RX 에도 써서(같은 채널이니까) 0x711(VS)
만 non-blocking 으로 읽는다 — 새 프로세스·새 IPC 채널 없이 can_guard 하나로 끝낸다(설계 결정).
"""
import argparse
import signal
import sys
import time

import can

import sd_notify
from command_policy import command_for_state
from plausibility import RateLimiter, clamp_range
from protocol import Command, CommandChannel, HeartbeatChannel, TornReadError
from rt_setup import apply as apply_rt
from rt_setup import parse_cpulist
from rx_decode import FRAME_ID_INFO_ACC, decode_vs_kph
from state_machine import DEFAULT_PERCEPTION_LOST_STOPPED_AFTER_S, DEFAULT_PERCEPTION_TIMEOUT_S
from state_machine import DEFAULT_STOP_SPEED_KPH, DEFAULT_STOPPED_AFTER_S, DEFAULT_WATCHDOG_T_S
from state_machine import State, next_state
from tx_encode import encode_0x156, encode_0x157

SPIKE_THRESHOLD_S = 100e-6   # 스냅샷에서 "튐"으로 셀 자체측정 편차 문턱 — 8h 연속운전 관측용(§8, 2026-09-18)
VS_MAX_AGE_S = 1.0           # 이보다 오래된 VS 는 "모름" 취급(RX 가 없거나 끊겼을 때 시간 기반 안전망으로 복귀)
VS_DRAIN_MAX_PER_CYCLE = 16  # 한 주기에 non-blocking recv() 최대 시도 횟수 — 버스 폭주 시 무한 루프 방지


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--channel', default='vcan0')
    ap.add_argument('--interface', default='socketcan')
    ap.add_argument('--period', type=float, default=0.010, help='TX 주기(초) — DBC 스펙 10ms')
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
                    help='실측 차속(0x711 VS)이 이 이하면 정지로 간주 (2026-09-21 초안, 팀 확인 필요)')
    ap.add_argument('--eps-rate-limit', type=float, default=None, help='deg/s, 미정이면 무제한(§5.D 참고)')
    ap.add_argument('--acc-rate-limit', type=float, default=None, help='(m/s^2)/s, 미정이면 무제한')
    ap.add_argument('--cmd-shm', default=None)
    ap.add_argument('--hb-shm', default=None)
    ap.add_argument('--max-cycles', type=int, default=0, help='0=무한. SIL 시험용으로 유한 실행할 때 사용')
    ap.add_argument('--stats-interval-s', type=float, default=0.0,
                    help='이 간격(초)마다 자체측정 지연 스냅샷을 로그로 남김. 0=비활성(기본, 장시간 운전용)')
    ap.add_argument('--quiet', action='store_true')
    return ap.parse_args(argv)


_stop_requested = False


def _on_shutdown_signal(signum, frame):
    global _stop_requested
    _stop_requested = True


def run(a):
    # SIGTERM 은 Python 기본 처리(즉시 커널 종료, finally 미실행)라 방치하면 cmd_ch/hb_ch.close() 의
    # unlink() 가 안 돌아 /dev/shm 세그먼트가 누수된다(재현 확인, 2026-09-18) — 정상 종료 경로로 흡수한다.
    signal.signal(signal.SIGTERM, _on_shutdown_signal)
    signal.signal(signal.SIGINT, _on_shutdown_signal)
    cpus = parse_cpulist(a.cpu)
    notes = apply_rt(cpus, a.rt_priority)
    log = (lambda *args, **kw: None) if a.quiet else (lambda *args, **kw: print(*args, **kw))
    log(f'[can_guard] 실행 환경: {", ".join(notes) if notes else "기본값"}', file=sys.stderr)

    cycle = 0
    tx_err_sum = 0.0
    tx_worst = 0.0
    tx_spikes = 0
    cmd_ch = hb_ch = bus = None
    try:
        cmd_kwargs = {'name': a.cmd_shm} if a.cmd_shm else {}
        hb_kwargs = {'name': a.hb_shm} if a.hb_shm else {}
        cmd_ch = CommandChannel.create(**cmd_kwargs)   # guard 가 먼저 뜬다 → 세그먼트도 guard 가 만든다(§7.2)
        hb_ch = HeartbeatChannel.create(**hb_kwargs)
        bus = can.Bus(channel=a.channel, interface=a.interface)
        log(f'[can_guard] 연결: {a.interface}:{a.channel}, 주기 {a.period * 1000:.1f}ms', file=sys.stderr)

        state = State.INIT
        aliv_cnt = 0
        last_valid_cmd = Command()
        last_cmd_mono = None       # 마지막으로 "성공 확인된" 명령 timestamp 의 monotonic 절대시각
        last_hb_mono = None
        holding_entered_at = None
        degraded_entered_at = None
        held_eps_cmd = 0.0
        last_vs_kph = None
        last_vs_mono = None
        eps_limiter = RateLimiter(a.eps_rate_limit)
        acc_limiter = RateLimiter(a.acc_rate_limit)

        sd_notify.ready()
        log(f'[can_guard] INIT — 안전 상태로 시작 (watchdog_t={a.watchdog_t * 1000:.0f}ms)', file=sys.stderr)

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
            late = now - next_t   # 자체 측정: eait_tx.py 와 같은 정의(now-next_t), 외부 관찰자 없음
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
                last_cmd_mono = now - cmd_age
            cmd_age_s = (now - last_cmd_mono) if last_cmd_mono is not None else None

            try:
                hb_age = hb_ch.age()
            except TornReadError:
                hb_age = None
            if hb_age is not None:
                last_hb_mono = now - hb_age
            perception_age_s = (now - last_hb_mono) if last_hb_mono is not None else None

            # 0x711(VS) non-blocking 드레인 — 같은 bus 재사용, 새 프로세스/IPC 없음(설계 결정, 위 docstring)
            for _ in range(VS_DRAIN_MAX_PER_CYCLE):
                msg = bus.recv(timeout=0)
                if msg is None:
                    break
                if msg.arbitration_id == FRAME_ID_INFO_ACC:
                    last_vs_kph = decode_vs_kph(msg.data)
                    last_vs_mono = now
            vs_age_s = (now - last_vs_mono) if last_vs_mono is not None else None
            vehicle_speed_kph = last_vs_kph if (vs_age_s is not None and vs_age_s <= VS_MAX_AGE_S) else None

            # --- 2. 상태 전이 ---
            holding_duration_s = (now - holding_entered_at) if holding_entered_at is not None else 0.0
            degraded_duration_s = (now - degraded_entered_at) if degraded_entered_at is not None else 0.0
            new_state = next_state(state, cmd_age_s, holding_duration_s, perception_age_s,
                                   watchdog_t_s=a.watchdog_t, stopped_after_s=a.stopped_after,
                                   perception_timeout_s=a.perception_timeout,
                                   degraded_duration_s=degraded_duration_s,
                                   perception_lost_stopped_after_s=a.perception_lost_stopped_after,
                                   vehicle_speed_kph=vehicle_speed_kph, stop_speed_kph=a.stop_speed_kph)
            if new_state != state:
                log(f'[can_guard] {state.value} → {new_state.value}'
                    f' (cmd_age={cmd_age_s}, perception_age={perception_age_s},'
                    f' vehicle_speed_kph={vehicle_speed_kph})', file=sys.stderr)
                sd_notify.status(new_state.value)
            if new_state == State.HOLDING and state != State.HOLDING:
                holding_entered_at = now
                held_eps_cmd = last_valid_cmd.eps_cmd   # 진입 순간 조향각을 얼린다
            elif new_state != State.HOLDING:
                holding_entered_at = None
            if new_state == State.DEGRADED and state != State.DEGRADED:
                degraded_entered_at = now
                held_eps_cmd = last_valid_cmd.eps_cmd   # HOLDING 과 같은 방식으로 진입 순간 조향각을 얼린다
            elif new_state != State.DEGRADED:
                degraded_entered_at = None
            state = new_state

            # --- 3. 명령 생성 → 방어 클램프 → 인코딩 → 전송 ---
            out = command_for_state(state, last_valid_cmd, held_eps_cmd, eps_limiter, acc_limiter, a.period)
            violations = []
            out = clamp_range(out, violations_out=violations)
            if violations and not a.quiet:
                log(f'[can_guard] plausibility 위반 {len(violations)}건: {violations}', file=sys.stderr)

            bus.send(can.Message(arbitration_id=0x156, data=encode_0x156(out, aliv_cnt), is_extended_id=False))
            bus.send(can.Message(arbitration_id=0x157, data=encode_0x157(out), is_extended_id=False))
            aliv_cnt = (aliv_cnt + 1) % 256

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
        avg_err = tx_err_sum / cycle * 1e6 if cycle else 0.0
        log(f'[can_guard] 종료 — {cycle} 주기 | 자체측정(late=now-next_t) 평균 {avg_err:.1f}µs'
            f' 최대 {tx_worst * 1e6:.1f}µs 스파이크(>{SPIKE_THRESHOLD_S * 1e6:.0f}µs)={tx_spikes}건',
            file=sys.stderr)


def main():
    run(parse_args())


if __name__ == '__main__':
    main()
