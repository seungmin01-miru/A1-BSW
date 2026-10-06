# EAIT_INFO_ACC(0x711) 디코드 결과. 필드는 DBC 신호 1:1. control_status 상수는 EpsStatus 와 공통 체계.
std_msgs/Header header

bool en                        # ACC_En_Status (0=Off 1=On)

uint8 control_board_status     # ACC_Control_Board_Status (3비트, 0~2 만 정의됨)
uint8 BOARD_ABNORMAL = 0
uint8 BOARD_INITIAL  = 1
uint8 BOARD_NORMAL   = 2

bool user_can_err              # ACC_USER_CAN_ERR
bool veh_err                   # ACC_Veh_ERR
bool err                       # ACC_ERR

uint8 control_status           # ACC_Control_Status (4비트)
uint8 STATUS_NONE          = 0
uint8 STATUS_READY         = 1
uint8 STATUS_ALL_ON        = 2
uint8 STATUS_ACC_ON        = 3
uint8 STATUS_EPS_ON        = 4
uint8 STATUS_ACC_OVERRIDE  = 7
uint8 STATUS_BRK_OVERRIDE  = 8
uint8 STATUS_EPS_OVERRIDE  = 9

float32 vs                     # VS, km/h (차속, 8비트 부호없음 — WHEEL_SPD 보다 해상도 낮음)
float32 long_accel             # Long_Accel, m/s^2, ×0.01 offset -10.23

bool turn_right_en             # Turn_Right_En
bool hazard_en                 # Hazard_En
bool turn_left_en              # Turn_Left_En

uint8 gear_sel                 # G_SEL_DISP (4비트)
uint8 GEAR_P = 0
uint8 GEAR_D = 5
uint8 GEAR_N = 6
uint8 GEAR_R = 7

uint8 alive_cnt                 # ACC_Alive_Cnt (0~255 롤오버)
bool alive_ok                   # true = 직전 프레임 대비 정확히 +1(롤오버 포함). 첫 프레임은 true(§2 E2E 체크)
