# USER_right_wheel_info (0x201, 차량 → PC) — 이름과 달리 좌우 바퀴 둘 다. DBC/A1_dbc_fixed.dbc.
std_msgs/Header header
float32 right_speed_kph       # 12비트 ×0.1 km/h
uint16 right_rpm              # 12비트
float32 left_speed_kph
uint16 left_rpm
uint8 right_live_counter
uint8 left_live_counter
bool right_alive_ok
bool left_alive_ok
