# USER_control_command (0x210, PC → 차량)를 **버스에서 본 그대로** — can_guard 가 보낸 것이든 다른 장치(원격조종 등)든.
# 감시·기록용. 차량으로 명령을 보내는 건 can_guard 만 한다(이 메시지를 퍼블리시해도 차에 아무것도 안 간다).
std_msgs/Header header
float32 steer_cmd_deg         # ×0.1 deg/raw — 업체 DBC 그대로(2026-10-07 실차로 확정)
uint16 brake_cmd_pct
uint8 acc_cmd_pct
bool steer_auto
bool brake_auto
bool acc_auto
