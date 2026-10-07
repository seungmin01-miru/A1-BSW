# A1-BSW

A1 CHALLENGE 자율주행 차량의 BSW(기본 소프트웨어) 계층 — 메인 PC 한 대가 안전 MCU 없이 "상위 제어기(AI) ↔ 차량 CAN"
사이의 실시간성·통신·안전을 맡는다. **기준 프로토콜: 실차 A1 DBC `DBC/A1_dbc_fixed.dbc`**(업체 `A1_dbc.dbc` 에서
0x200 `steer_is_auto` 오타 1곳만 고친 사본, 2026-10-06~07 전환. 9월의 EAIT DBC 는 삭제). 0x210 조향은 업체 DBC 그대로
**×0.1°/raw**(2026-10-07 실차 확정). 대회측 원격조종은 0x156/0x157 로 0x210 보다 우선한다.

```
ROS2 제어 노드 ──공유메모리──▶ can_guard ──0x210 20 ms──▶ CAN(PEAK can0 / Kvaser) ──▶ 차량
인지 프로세스 ──하트비트──▶   (상태머신·한계·안전 동작)          │
                                                      ◀── 0x200·0x201 ──┘──▶ ros2_ws 브리지 → /control/status/*
```

| 디렉터리 | 내용 | 안내 |
|---|---|---|
| `DBC/` | `A1_dbc.dbc`(업체 원본), `A1_dbc_fixed.dbc`(기준 — 0x200 `steer_is_auto` 오타만 수정) | 근거: `docs/2026-10-06_a1_dbc_update_and_lift_plan.md` §0 |
| `safety/can_guard/` | 차량 명령(0x210)의 유일한 송신자, `lift_cmd.py` | `safety/can_guard/README.md` |
| `ros2_ws/` | ROS2 상태 브리지(`a1_status_decoder`) — 감시 등급 | `ros2_ws/README.md` |
| `sil/vcan/` | 가상 버스 SIL(가짜 실차 `fake_a1_vehicle.py`) | `sil/vcan/README.md` |
| `tools/race_day/` | **실차 인수일 절차(`VEHICLE_MANUAL.md`)**, 리프트 송신 도구·리허설, 답변 양식 | `tools/race_day/README.md` |
| `tools/kvaser/` | Kvaser Leaf v3 드라이버 설치·미러·리허설 | `tools/kvaser/README.md` |
| `tools/rt/`, `tools/deploy/` | RT 커널 이행·튜닝 / 오프라인 배포 번들 | 각 README |
| `can_stack_development.md` | 살아있는 구현 스펙(§5.A RT, §5.B CAN, §5.C 브리지, §5.D can_guard) | 맨 위 "현재 기준" |
| `2026-09-14_can_verification_checklist.md` | 결정(D1~D5)·시험 항목·주최 측 질문 | §0 "A1 DBC 반영" |
| `docs/` | 계획·브리핑(날짜가 붙은 문서는 그 시점 기록) | — |
| `measurements/` | 실측 원본 로그와 요약 | — |

빠른 확인(하드웨어 불필요):
```bash
python3 -m pytest -q -p no:cacheprovider safety/can_guard/test tools/race_day/test_lift_tx.py
bash tools/race_day/rehearse_lift_guard.sh
```
