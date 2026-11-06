# 기록 신호표 (DBC `DBC/A1_dbc_2_fixed.dbc`)

| 분류 | 신호 | 원천 | 단위 / 비고 |
|---|---|---|---|
| 명령 | `steer_command` | 0x210 | 핸들 °, 0.1 °/raw, ±150 |
| 명령 | `break_command`, `acc_command` | 0x210 | 0~100 % |
| 명령 | `*_is_auto_command` | 0x210 | 축별 auto(1) |
| 명령 | 목표 속도·프로파일 명령 | 생성기 명령 로그 | km/h / % / ° |
| 응답 | `steer_postion` | 0x200 | 핸들 ° |
| 응답 | `break_postion` | 0x200 | 0~100 %(새 DBC ×1, D13), 82 % 포화. ROS `ControlInfo.brake_position` 만 0~10 유지. **압력 신호 없음** |
| 응답 | `left_speed`, `right_speed`, `*_rpm` | 0x201 | 후륜만(전륜 프리롤링), 부호 없음 |
| 자세·위치 | GNSS/IMU 위치·속도·요레이트·가속도 — **토픽·시간 동기 미정(TODO-5, localization 팀 전달 대기)** | rosbag | RTX fix 구간만 사용 |
| 상태 | `*_is_auto`, `*_live_counter` | 0x200·0x201 | 차량 heartbeat 없음 |
| 상태 | heartbeat, 상태 전이, 비상 정지 | can_guard 로그 | 우리 쪽 기준 |
| 기록만 | 0x128 | RES 응답(`RES_END`, `RES_ERROR`) — 차량 송신, 우리는 기록만 | 제어에 안 씀 |
| 관제용 | 0x710~0x712 | 축 모드, `*_USER_CAN_ERR`, `VS`, 휠속 | 제어에 안 씀, `*_USER_CAN_ERR` 은 TODO-2 확인에 활용 |
| 표시 | 0x202 | `speed_limit_info` — 현재 차량 속도 제한 표시. 11-04 전 세션 30.0(원격 조종 제한값, 자율에는 적용 안 됨 — 사용자 확인) | 업체 확인: `docs/engine_room_open_items.md` |
