# A1-BSW

A1 CHALLENGE 자율주행 차량의 BSW(기본 소프트웨어) 계층 — 메인 PC 한 대가 안전 MCU 없이 "상위 제어기(AI) ↔ 차량 CAN"
사이의 실시간성·통신·안전을 맡는다. **기준 프로토콜: 실차 A1 DBC `DBC/A1_dbc_2_fixed.dbc`**(대회 배포 `A1_dbc_2.dbc` 의
오타만 고친 사본, 2026-11-06 전환. 이전 기준은 `archive/dbc_2026-10/`). 0x210 조향은 업체 DBC 그대로
**×0.1°/raw**(2026-10-07 실차 확정). 대회측 원격조종(핸드폰 앱)은 0x156/0x157 로 0x210 보다 우선한다 — 앱 비상정지는 원격 인수다(`bsw_test/00_SAF_estop/docs/goal.md` 0절).
0x128 은 RES 응답(기록만), 0x710~0x712 는 관제용 피드백이다(제어에 안 씀).

```
ROS2 제어 노드 ──공유메모리──▶ can_guard ──0x210 20 ms──▶ CAN(PEAK can0 / Kvaser) ──▶ 차량
인지 프로세스 ──하트비트──▶   (상태머신·한계·안전 동작)          │
                                                      ◀── 0x200·0x201 ──┘──▶ ros2_ws 브리지 → /control/status/*
```

## 주행 날 — BSW 한 번에 켜기 (`tools/bsw/`, 2026-11-05, 상태판 창 2026-11-06)

```bash
bash tools/bsw/bsw_up.sh --name LAP1          # 연습 주행: 전부 켜기 + 상태판 창
bash tools/bsw/bsw_up.sh --name RACE --no-ui  # 대회 본 주행: 상태판 없이(볼 사람이 없을 때)
bash tools/bsw/bsw_down.sh                     # 차가 선 뒤: ESTOP 브레이크 → 정리 → CAN 로그 zstd·sha256
```

`bsw_up` 이 끝나면 **모델이 `/control/target`(가감속 m/s², 타이어각 °)을 0.3 s 넘게 내는 순간 자동 arm 되어 차가 움직인다**.
자동 arm 은 한 번뿐이고, 래치·disarm 뒤에는 다시 자동으로 올라가지 않는다.
모델은 서 있을 때도 정지 목표를 계속 내야 한다(150 ms 끊기면 브레이크). ROS bag 은 다른 팀이 딴다 — 여기서는 CAN 로그만 남긴다.
**주행 중 disarm 은 정지가 아니다** — 세울 때는 앱·`estop.py`. 자세한 규칙: [`tools/bsw/README.md`](tools/bsw/README.md),
근거·시나리오: [`reports/2026-11-05_bsw_update_report/`](reports/2026-11-05_bsw_update_report/).

### `bsw_up` 이 띄우는 것 (주행 중 실행 구성)

| # | 구성 | 코드 | 하는 일 | 실행 위치 | 없으면 |
|---|---|---|---|---|---|
| 1 | Kvaser 미러 | `tools/kvaser/kvaser_mirror.py` | Kvaser ch0 수신을 `kv0`(vcan)에 복사 + **원본 CAN 로그** `kvaser_ch0.log` | 일반 코어 | 브리지·상태판·candump 가 못 받음 |
| 2 | **can_guard** | `safety/can_guard/can_guard.py` | **0x210 의 유일한 송신자**: 상태기계(INIT/ACTIVE/HOLDING/STOPPED/ESTOP), 한계·램프, 앱(0x156/0x157) 감시, ESTOP | **격리 코어 8 · SCHED_FIFO 90** | 차가 명령을 못 받음 |
| 3 | 상태 브리지 | `ros2_ws/src/a1_can_bridge` (`can_raw_bridge`) | `kv0` 의 0x200/0x201/0x210 → `/control/status/*` | 일반 코어 | 모델·어댑터가 차량 상태를 모름 |
| 4 | 어댑터 | `ros2_ws/src/a1_control_adapter` (`cmd_adapter`, `track.yaml`) | `/control/target`(m/s², 타이어 °) → %·핸들 ° → 공유메모리 → can_guard. **자동 arm**, 고장 래치(150 ms·NaN) | 일반 코어 | 모델 명령이 안 나감 |
| 5 | CAN 녹화 | `candump -L kv0` | `candump_kv0.log`(끄면 zstd·sha256) | 일반 코어 | 기록만 빠짐 |
| 6 | 상태판 창 | `tools/bsw/bsw_monitor.py` | 속도·명령 vs 실제·앱·수신·can_guard 상태·경고(5 Hz). **읽기 전용** | 일반 코어, nice 10 | 주행 영향 없음 |

상태판의 성능 영향은 CPU 약 3 %(1코어 기준, 화면 서버 포함)이다. 20 ms 주기 지연도 p99 기준 0.1 ms 늘어나는 데 그쳤고, can_guard 는 격리 코어라 상태판과 겹치지 않는다.
측정 근거는 [`tools/bsw/README.md`](tools/bsw/README.md) 에 있다. 시험(00~07)용 개별 띄우기는 `tools/track_day/bringup.sh`(같은 코드)를 쓴다.

### 패키지 한눈에

| 패키지 | 무엇 | 주로 쓰는 명령 | 안내 |
|---|---|---|---|
| `tools/bsw/` | **주행 날 켜기·끄기·상태판** | `bsw_up.sh`, `bsw_down.sh`, `bsw_monitor.py`, `bsw_status.sh`(점검표) | [README](tools/bsw/README.md) |
| `safety/can_guard/` | 0x210 송신·안전 상태기계·비상 정지 | `estop.py`(비상 정지), `lift_cmd.py`(리프트 시험), `sil_tests/e_estop.py`(SIL E1–E7) | [README](safety/can_guard/README.md) |
| `ros2_ws/src/a1_control_adapter` | 모델 목표 → 차량 명령 변환, arm/disarm, 래치 | `ros2 launch a1_control_adapter track.launch.py`, `ros2 service call /control/adapter/arm_*` | [README](ros2_ws/src/a1_control_adapter/README.md) |
| `ros2_ws/src/a1_can_bridge` | CAN → ROS 상태 토픽 | `ros2 launch a1_can_bridge a1_bridge.launch.py` | `ros2_ws/README.md` |
| `ros2_ws/src/a1_can_msgs` | 메시지 정의(`AdapterStatus`, `ControlCommand` 등) | — | — |
| `tools/kvaser/` | Kvaser 드라이버 설치·미러 | `kvaser_mirror.py`, `install_kvaser.sh` | [README](tools/kvaser/README.md) |
| `sil/vcan/` | 차량 없이 시험(가상 버스·가짜 차량) | `vcan_up.sh`, `fake_a1_vehicle.py` | [README](sil/vcan/README.md) |
| `tools/track_day/` | 시험(00~07)용 띄우기·녹화·종료·변환표 맞춤 | `bringup.sh`, `stop.sh`, `record.sh`, `fit_maps.py` | `TRACK_MANUAL.md` |
| `bsw_test/` | 실차 시험 00~07(목표·분석·결과), 현장 매뉴얼 | 각 `scripts/analysis.py`, `field_env.sh` | [FIELD_MANUAL](bsw_test/FIELD_MANUAL.md) |
| `tools/drive_log/` | 주행 로그 분석·Kvaser 수신율 점검 | `extract.py`, `kvaser_rx_check.py` | README |
| `tools/signal_audit/`, `tools/race_day/`, `tools/rt/`, `tools/deploy/`, `tools/paper_data/` | 신호 검증 / 인수일 / RT 튜닝·측정 / 오프라인 배포 / 논문 그림 | — | 각 README |

## 저장소 구조 (2026-11-05 정리: 코드 / 날짜별 데이터 / 문서 / 보고서)

```
A1-BSW/
├─ README.md · CLAUDE.md            안내 · Claude 작업 규칙
├─ DBC/                             기준 DBC (A1_dbc_2_fixed.dbc)
│
├─ ── 코드 ───────────────────────────────────────────────
├─ safety/can_guard/                0x210 유일 송신자 (상태기계·ESTOP·클램프) + 단위/SIL 시험
├─ ros2_ws/src/                     a1_can_bridge · a1_can_msgs · a1_control_adapter(track.yaml 변환표)
├─ sil/vcan/                        가상 버스 · 가짜 차량
├─ bsw_test/                        실차 주행 테스트 00~07 (각 goal/results) + 현장 매뉴얼·field_env.sh
├─ tools/
│   ├─ bsw/                         ★ 주행 날 한 번에 켜기·끄기·상태판 (bsw_up/down/monitor)
│   ├─ track_day/ race_day/ kvaser/ 현장 시험 띄우기·녹화·종료, 인수일 도구, Kvaser
│   ├─ signal_audit/                신호 검증(judge·limits·plot·dbc_lite)
│   ├─ drive_log/                   주행 로그 분석·데이터시트 생성 (11-04~)
│   ├─ paper_data/                  논문 표·그림 생성
│   └─ rt/ deploy/                  RT 커널 튜닝 · 오프라인 배포
│
├─ ── 데이터 (날짜별) ─────────────────────────────────────
├─ measurements/
│   ├─ 2026-09-1x_*                 RT·전원관리 실측 (13개)
│   ├─ 2026-10-07_vehicle_lift/     리프트 시험 (capture/ = 원본)
│   ├─ 2026-11-03_signal_audit/     신호 전수 검증·원격 주행
│   ├─ 2026-11-04_signal_audit/     현장 빠른 시험
│   └─ 2026-11-04_drive_log/        원격·자율 주행 요약 · raw_can/ · tables/ · sheets/
│
├─ ── 문서 · 보고서 ────────────────────────────────────────
├─ docs/                            스펙(can_stack_development.md)·계획·브리핑·할 일
│   └─ reference/                   외부 참고 자료 (주행 보고서 예시, KF1600 GPS-CAN)
├─ reports/                         보고서·논문 자료 (날짜별)
└─ archive/                         폐기된 9월 DBC
```

| 디렉터리 | 내용 | 안내 |
|---|---|---|
| `DBC/` | `A1_dbc.dbc`(10-06 업체 원본), `A1_dbc_2_fixed.dbc`(기준 — 대회 배포본의 오타 수정, 고친 곳은 파일 안 `CM_`) | 근거: `docs/2026-11-06_race_flag_dev_plan.md` WP0 |
| `safety/can_guard/` | 차량 명령(0x210)의 유일한 송신자, `lift_cmd.py` | `safety/can_guard/README.md` |
| `ros2_ws/` | ROS2 상태 브리지·제어기 출력 어댑터(`a1_control_adapter`) | `ros2_ws/README.md` |
| `sil/vcan/` | 가상 버스 SIL(가짜 실차 `fake_a1_vehicle.py`) | `sil/vcan/README.md` |
| `bsw_test/` | 실차 주행 테스트 00~07, 현장 매뉴얼 | `bsw_test/docs/test_plan.md`, `bsw_test/FIELD_MANUAL.md` |
| `tools/bsw/` | **주행 날 BSW 한 번에 켜기**(자동 arm, CAN 로그 자동 녹화·압축) | [`tools/bsw/README.md`](tools/bsw/README.md) |
| `tools/track_day/`, `tools/race_day/` | 현장 띄우기·녹화·종료 / 실차 인수일 절차(`VEHICLE_MANUAL.md`) | 각 README |
| `tools/signal_audit/`, `tools/drive_log/`, `tools/paper_data/` | 신호 검증 / 주행 로그 분석 / 논문 그림 | 각 README |
| `tools/kvaser/`, `tools/rt/`, `tools/deploy/` | Kvaser / RT 커널 튜닝 / 오프라인 배포 번들 | 각 README |
| `measurements/` | 날짜별 실측 데이터·분석 결과 | [`measurements/README.md`](measurements/README.md) |
| `docs/can_stack_development.md` | 살아있는 구현 스펙(§5.A RT, §5.B CAN, §5.C 브리지, §5.D can_guard) | 맨 위 "현재 기준" |
| `docs/` | 계획·브리핑(날짜가 붙은 문서는 그 시점 기록), `reference/` 외부 자료 | — |
| `reports/` | 보고서·논문 자료 | [`reports/README.md`](reports/README.md) |

빠른 확인(하드웨어 불필요):
```bash
python3 -m pytest -q -p no:cacheprovider safety/can_guard/test tools/race_day/test_lift_tx.py
bash tools/race_day/rehearse_lift_guard.sh
```
