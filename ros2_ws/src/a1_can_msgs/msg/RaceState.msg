# 대회 신호 전달 — /race/state. 비트 원본과 래치만 준다, 해석·판단 없음(지시서 2026-11-06_engine_room_dev_plan.md 3절, 데이터시트 05 시트).
# QoS RELIABLE + TRANSIENT_LOCAL, depth 1. 값이 바뀌면 즉시 + 50 Hz.
std_msgs/Header header                  # stamp = 발행 시각
bool sig_go                             # 0x124 현재 비트, 2 프레임 연속 확인 후
bool sig_slow_on
bool sig_slow_off
bool sig_finish
bool sig_stop
bool seen_go                            # 한 번이라도 확인됐는가(래치, BSW 재시작 전까지 유지)
bool seen_slow_on
bool seen_slow_off
bool seen_finish
bool seen_stop
string last_signal                      # GO / SLOW_ON / SLOW_OFF / FINISH / STOP / NONE — 마지막으로 0→1 이 확인된 깃발
builtin_interfaces/Time last_signal_stamp   # 그 깃발 첫 프레임의 수신 시각, 0 = 없음
float64 race_signal_age_s               # 마지막 0x124 수신 후 경과(s), -1 = 한 번도 못 받음
bool res_active                         # 0x127 RES_START, 1 프레임 즉시. 작동 중 차량은 0x210 을 무시
bool res_alive_ok                       # 0x127 이 끊기지 않았고 alive 카운터가 움직임(못 받았으면 false)
float64 res_age_s                       # 마지막 0x127 수신 후 경과(s), -1 = 한 번도 못 받음
bool res_end                            # 0x128 RES_END(기록·표시용)
uint8 res_error                         # 0x128 RES_ERROR 0~7(기록·표시용)
bool remote_app_active                  # 0x156 En(EPS/ACC/AEB) 중 하나라도 1 — 앱이 잡고 있음, 차량은 0x210 을 무시
float32 vehicle_speed_limit_kph         # 0x202 차량 측 제한 현재값(km/h), -1 = 못 받음. 30.0 = 원격 조종 제한(자율 상한 아님)
string guard_state                      # 예약. 지금은 항상 빈 문자열(can_guard 상태는 ROS 로 나오지 않음 — docs/engine_room_open_items.md E-1)
float64 pit_lat_deg                     # 0x125 피트존 좌표, NaN = 없음
float64 pit_lon_deg
