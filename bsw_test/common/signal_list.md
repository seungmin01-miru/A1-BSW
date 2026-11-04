# 기록 신호표 (DBC `DBC/A1_dbc_fixed.dbc`)

| 분류 | 신호 | 원천 | 단위 / 비고 |
|---|---|---|---|
| 명령 | `steer_command` | 0x210 | 핸들 °, 0.1 °/raw, ±150 |
| 명령 | `break_command`, `acc_command` | 0x210 | 0~100 % |
| 명령 | `*_is_auto_command` | 0x210 | 축별 auto(1) |
| 명령 | 목표 속도·프로파일 명령 | 생성기 명령 로그 | km/h / % / ° |
| 응답 | `steer_postion` | 0x200 | 핸들 ° |
| 응답 | `break_postion` | 0x200 | 0~10 (= % ÷ 10), 82 % 이상 8.2 포화. **압력 신호 없음** |
| 응답 | `left_speed`, `right_speed`, `*_rpm` | 0x201 | 후륜만(전륜 프리롤링), 부호 없음 |
| 자세·위치 | GNSS/IMU 위치·속도·요레이트·가속도 — **토픽·시간 동기 미정(TODO-5, localization 팀 전달 대기)** | rosbag | RTX fix 구간만 사용 |
| 상태 | `*_is_auto`, `*_live_counter` | 0x200·0x201 | 차량 heartbeat 없음 |
| 상태 | heartbeat, 상태 전이, 비상 정지 | can_guard 로그 | 우리 쪽 기준 |
| 미확인 | 0x128, 0x202, 0x710~0x712 | DBC 밖 | TODO-1 |
