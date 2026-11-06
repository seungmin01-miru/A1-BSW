# 경기 상태 — can_guard 가 판단한 값을 옮긴 것(/race/state). 필드 뜻은 docs/A1_BSW_토픽_데이터시트_v0.3.xlsx 05 시트.
# QoS RELIABLE + TRANSIENT_LOCAL, depth 1. 50 Hz + 바뀌면 즉시. can_guard 경기 모드가 아니면 발행하지 않는다
# → 늦게 켠 구독자는 옛 마지막 값을 받을 수 있으니 header.stamp 로 신선도를 볼 것.
uint8 PRE_GO=0
uint8 RACING=1
uint8 SLOW=2
uint8 FINISHED=3
uint8 PARKED=4
uint8 SUSPENDED=5

std_msgs/Header header
uint8 phase                       # 위 상수. 모델은 이 값 하나로 동작을 정하면 된다
string phase_name                 # 같은 값의 문자열(로그·화면용)
bool sig_go                       # 0x124 KIAPI_Race_Signal 비트 원본
bool sig_slow_on
bool sig_slow_off
bool sig_finish
bool sig_stop
float64 race_signal_age_s         # 마지막 0x124 수신 후 경과(s), -1 = 한 번도 못 받음
bool res_active                   # 원격비상제동 작동 중(0x127 RES_START)
bool res_alive_ok                 # 0x127 alive 카운터 정상
bool long_override                # true = BSW 가 종방향을 넘겨받아 모델 가감속을 무시 중
float32 speed_cap_kph             # 현재 BSW 속도 상한(km/h), -1 = 상한 없음, 0 = 정지 유지
float32 vehicle_speed_limit_kph   # 0x202 차량 측 표시값(km/h), -1 = 못 받음. 30.0 = 원격 조종 제한(자율 상한 아님)
float32 model_healthy_s           # 모델 입력 연속 정상 시간(s, 계획서 8.4)
string guard_state                # INIT / ACTIVE / HOLDING / DEGRADED / STOPPED / ESTOP
float64 pit_lat_deg               # 피트존 좌표(0x125), NaN = 없음
float64 pit_lon_deg
