# EAIT_INFO_EPS(0x710) 디코드 결과. 필드는 DBC 신호 1:1 (필드명 뒤 주석에 DBC 원 신호명).
# 값 코드는 EAIT_CAN(AVANTE_CN7).dbc VAL_ 테이블 참고. control_status 상수는 EPS/ACC 공통 체계.
std_msgs/Header header

bool en                        # EPS_En_Status (0=Off 1=On)

uint8 control_board_status     # EPS_Control_Board_Status (3비트, 0~2 만 정의됨)
uint8 BOARD_ABNORMAL = 0
uint8 BOARD_INITIAL  = 1
uint8 BOARD_NORMAL   = 2

bool user_can_err              # EPS_USER_CAN_ERR (0=Normal 1=Abnormal)
bool err                       # EPS_ERR
bool veh_can_err               # EPS_Veh_CAN_ERR
bool sas_err                   # EPS_SAS_ERR

uint8 control_status           # EPS_Control_Status (4비트)
uint8 STATUS_NONE          = 0
uint8 STATUS_READY         = 1
uint8 STATUS_ALL_ON        = 2
uint8 STATUS_ACC_ON        = 3
uint8 STATUS_EPS_ON        = 4
uint8 STATUS_ACC_OVERRIDE  = 7
uint8 STATUS_BRK_OVERRIDE  = 8   # DBC 원문 'BRK_Overrdie'(오타), 값은 그대로
uint8 STATUS_EPS_OVERRIDE  = 9

bool ignore_override           # Override_Ignore_Status (0=Do_Override 1=Do_Ignore)
bool override                  # Override_Status (0=Normal 1=Override)

float32 str_ang                # StrAng, deg (부호 있음, ×0.1)
float32 str_drv_tq             # Str_Drv_Tq, ×0.01 offset -20.48
float32 str_out_tq             # Str_Out_Tq, ×0.1 offset -204.8

uint8 alive_cnt                 # EPS_Alive_Cnt (0~255 롤오버)
bool alive_ok                   # true = 직전 프레임 대비 정확히 +1(롤오버 포함). 첫 프레임은 true(§2 E2E 체크)
