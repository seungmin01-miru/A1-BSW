# a1_control_adapter 상태 — 50 Hz, 감시·녹화용(/control/adapter/status). 차량으로는 아무것도 안 간다.
std_msgs/Header header
bool steer_armed              # 조향 축 auto 를 켜서 보내는 중
bool long_armed               # 종방향(브레이크·가속) 축 auto 를 켜서 보내는 중
bool writing                  # can_guard 공유메모리에 이번 주기 기록함(false = 제어기 끊김 → can_guard HOLDING)
float64 input_age_s           # 마지막 /control/target 수신 후 경과(초), 한 번도 못 받았으면 -1
float64 in_accel_mps2         # 받은 값 그대로
float64 in_steer_tire_deg
float64 out_steer_wheel_deg   # can_guard 로 넘긴 핸들각(표 변환·클램프·속도 제한 뒤)
float64 out_brake_pct
float64 out_acc_pct
uint32 rejected_count         # NaN·범위 밖이라 버린 입력 누적 개수
string note                   # 마지막 이상 사유(없으면 빈 문자열)
