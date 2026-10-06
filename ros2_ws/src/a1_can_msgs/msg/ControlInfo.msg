# USER_control_info (0x200, 차량 → PC) — DBC/A1_dbc_fixed.dbc. 2026-10-06 실차 프로토콜.
std_msgs/Header header        # 원본 CanFrame.header 를 그대로 물려받는다(종단 지연 측정 가능)
float32 steer_position_deg    # steer_postion, int16 ×0.1 deg
float32 brake_position        # break_postion, int16 ×0.1 (단위 DBC 에 없음 — 9/17 로그에서 명령 0~100 % 일 때 0~10)
bool steer_auto               # steer_is_auto (0 Manual / 1 Auto)
bool brake_auto               # break_is_auto
bool acc_auto                 # acc_is_auto
uint8 steer_live_counter      # 축별 0~255 롤오버 카운터
uint8 brake_live_counter
uint8 acc_live_counter
bool steer_alive_ok           # true = 직전 프레임 대비 정확히 +1(롤오버 포함). 첫 프레임은 true
bool brake_alive_ok
bool acc_alive_ok
