# 제어기 → CAN 어댑터(a1_control_adapter) 입력 — 제어기가 내는 값은 이 두 개뿐이다(2026-10-26 확정).
# 토픽 /control/target. 어댑터가 핸들각·브레이크 %·가속 % 로 바꿔 can_guard 공유메모리에 넣는다
# (차량으로 보내는 건 여전히 can_guard 하나뿐). 주기는 자유(10~100 Hz) — 어댑터가 50 Hz 로 다시 기록한다.
# 마지막 수신 후 150 ms(기본) 넘게 새 값이 없으면 어댑터가 기록을 멈춰 can_guard 가 HOLDING(브레이크 램프)으로 간다.
std_msgs/Header header        # stamp = 제어기가 이 값을 계산한 시각(진단용 — 나이 판정은 어댑터 수신 시각 기준)
float64 accel_mps2            # 목표 가감속도 [m/s^2], + 가속 / − 감속
float64 steer_tire_deg        # 목표 조향각 = 앞 타이어각 [deg] (핸들각 아님), + 좌회전
