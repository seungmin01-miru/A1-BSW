# EAIT_INFO_SPD(0x712) 디코드 결과. DBC: 4 바퀴 × uint16 LE, scale 0.03125, offset 0, unit kph (§5.B 참조)
std_msgs/Header header       # 원본 CanFrame.header 를 그대로 물려받는다(재스탬프하지 않음) → 종단 지연 측정 가능
float32 fr                   # WHEEL_SPD_FR (km/h)
float32 fl                   # WHEEL_SPD_FL
float32 rr                   # WHEEL_SPD_RR
float32 rl                   # WHEEL_SPD_RL
