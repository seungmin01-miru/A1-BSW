# EAIT_INFO_IMU(0x713) 디코드 결과. 4 신호가 8바이트를 정확히 채워 Alive_Cnt 자리가 없다
# → 이 메시지는 E2E(alive_count) 체크 대상이 아니다(§2). 전부 바이트 정렬(16비트씩, 0/16/32/48비트).
std_msgs/Header header

float32 lat_accel      # LAT_ACCEL, m/s^2 — 부호 있음, ×0.01, offset -10.23
float32 long_accel     # Long_ACCEL, m/s^2 — 부호 없음, ×0.01, offset -10.23 (0x711 Long_Accel 과 같은 패턴)
float32 yaw_rate        # YAW_RATE, deg/s — 부호 있음, ×0.01, offset -40.95
float32 brk_cylinder    # BRK_CYLINDER — 부호 없음, ×0.1, offset 0 (DBC 에 단위 표기 없음)
