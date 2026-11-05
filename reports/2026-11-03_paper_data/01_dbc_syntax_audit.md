# 표 1. DBC 문법·의미 대조 (줄 단위)

> 생성: `tools/paper_data/dbc_lint.py` — 기준: Vector DBC File Format(BO_/SG_/VAL_/CM_/BA_* 문법). 판정 O = 엄격 정규식 일치, X = 불일치. 의미 점검은 비트 범위·겹침·배율·표현 가능 범위·VAL_/CM_ 참조·이름 일관성.

## 1. 요약

| 파일 | 줄 | 개행 | 메시지 | 신호 | 문법 O / X | 오류 | 경고 | 참고 |
|---|---|---|---|---|---|---|---|---|
| `DBC/A1_dbc.dbc` | 103 | CRLF | 10 | 46 | 84 / 0 | 1 | 7 | 8 |
| `DBC/A1_dbc_fixed.dbc` | 106 | CRLF | 10 | 46 | 86 / 0 | 0 | 6 | 8 |

## 2. 의미 점검 결과 — `DBC/A1_dbc.dbc`

| 등급 | 위치 | 내용 |
|---|---|---|
| 오류 | L60 0x200.steer_is_auto | 1비트 플래그인데 배율 0.1 → 물리값 0/0.1 (VAL_ 0/1 과 불일치) |
| 경고 | L26 0x110.steer_postion | 단위 없음 ("") |
| 경고 | L31 0x111.break_postion | 단위 없음 ("") |
| 경고 | L46 0x113.wheel_right_size | 왼쪽 바퀴 메시지에 right 이름(복사 오류 추정) |
| 경고 | L58 0x200.steer_postion | 단위 없음 ("") |
| 경고 | L59 0x200.break_postion | 단위 없음 ("") |
| 경고 | L97 0x200.steer_is_auto | VAL_ 은 raw 기준인데 배율 0.1 — 물리값과 어긋남 |
| 경고 | steer_command | 같은 이름 다른 배율: 0x100 ×1, 0x210 ×0.1 |
| 참고 | L31 0x111.break_postion | 부호형(-)인데 범위 [0\|300] 가 음수 없음 |
| 참고 | L59 0x200.break_postion | 부호형(-)인데 범위 [0\|300] 가 음수 없음 |
| 참고 | L93 0x112.enc_right_side | VAL_ 미정의 raw 값 [0, 3] |
| 참고 | L94 0x113.enc_left_side | VAL_ 미정의 raw 값 [0, 3] |
| 참고 | L96 0x301.set_left_right_side | VAL_ 미정의 raw 값 [0, 3] |
| 참고 | 신호 10개 | 철자 postion→position, break→brake (호환 위해 이름 유지 권장): 0x100.break_command, 0x100.break_is_auto_command, 0x110.steer_postion, 0x111.break_postion, 0x200.steer_postion, 0x200.break_postion, 0x200.break_is_auto, 0x200.break_live_counter, 0x210.break_command, 0x210.break_is_auto_command |
| 참고 | 파일 | GenMsgCycleTime(주기) 속성 없음 — 주기는 03_bus_timing 실측값 사용 |
| 참고 | 파일 | 모든 메시지 송신/수신 노드 Vector__XXX — 송수신 주체는 실측(03)으로 구분 |

## 2. 의미 점검 결과 — `DBC/A1_dbc_fixed.dbc`

| 등급 | 위치 | 내용 |
|---|---|---|
| 경고 | L26 0x110.steer_postion | 단위 없음 ("") |
| 경고 | L31 0x111.break_postion | 단위 없음 ("") |
| 경고 | L46 0x113.wheel_right_size | 왼쪽 바퀴 메시지에 right 이름(복사 오류 추정) |
| 경고 | L58 0x200.steer_postion | 단위 없음 ("") |
| 경고 | L59 0x200.break_postion | 단위 없음 ("") |
| 경고 | steer_command | 같은 이름 다른 배율: 0x100 ×1, 0x210 ×0.1 |
| 참고 | L31 0x111.break_postion | 부호형(-)인데 범위 [0\|300] 가 음수 없음 |
| 참고 | L59 0x200.break_postion | 부호형(-)인데 범위 [0\|300] 가 음수 없음 |
| 참고 | L96 0x112.enc_right_side | VAL_ 미정의 raw 값 [0, 3] |
| 참고 | L97 0x113.enc_left_side | VAL_ 미정의 raw 값 [0, 3] |
| 참고 | L99 0x301.set_left_right_side | VAL_ 미정의 raw 값 [0, 3] |
| 참고 | 신호 10개 | 철자 postion→position, break→brake (호환 위해 이름 유지 권장): 0x100.break_command, 0x100.break_is_auto_command, 0x110.steer_postion, 0x111.break_postion, 0x200.steer_postion, 0x200.break_postion, 0x200.break_is_auto, 0x200.break_live_counter, 0x210.break_command, 0x210.break_is_auto_command |
| 참고 | 파일 | GenMsgCycleTime(주기) 속성 없음 — 주기는 03_bus_timing 실측값 사용 |
| 참고 | 파일 | 모든 메시지 송신/수신 노드 Vector__XXX — 송수신 주체는 실측(03)으로 구분 |

## 3. 메시지별 비트 사용 — `DBC/A1_dbc_fixed.dbc`

| ID | 이름 | DLC | 신호 수 | 사용 비트 | 미사용 비트 |
|---|---|---|---|---|---|
| 0x100 | SYS_control_command | 8 | 6 | 43/64 | 43–63 |
| 0x110 | SYS_steer_info | 8 | 3 | 25/64 | 17–55 |
| 0x111 | SYS_break_info | 8 | 3 | 25/64 | 17–55 |
| 0x112 | SYS_right_wheel_info | 8 | 5 | 42/64 | 24–39, 42–47 |
| 0x113 | SYS_left_wheel_info | 8 | 5 | 42/64 | 24–39, 42–47 |
| 0x114 | SYS_acc_info | 2 | 2 | 9/16 | 1–7 |
| 0x200 | USER_control_info | 8 | 8 | 59/64 | 35–39 |
| 0x201 | USER_right_wheel_info | 8 | 6 | 64/64 | — |
| 0x210 | USER_control_command | 8 | 6 | 43/64 | 43–63 |
| 0x301 | SYS_ENCODER_SETTINGS | 2 | 2 | 10/16 | 2–7 |

## 4. 줄별 대조 — `DBC/A1_dbc_fixed.dbc`

| 줄 | 키워드 | 판정 | 해석 / 메모 |
|---|---|---|---|
| 1 | `VERSION` | O |  |
| 4 | `NS_` | O |  |
| 12 | `BS_` | O |  |
| 14 | `BU_` | O |  |
| 17 | `BO_` | O | 송신 노드 미지정; 0x100, DLC 8 |
| 18 | `SG_` | O | **steer_command**; 들여쓰기 없음(관례 위반, 파서 허용); 0\|16 부호 ×1 [-150\|150] 표현 [-32768\|32767] |
| 19 | `SG_` | O | **steer_is_auto_command**; 들여쓰기 없음(관례 위반, 파서 허용); 40\|1 무부호 ×1 [0\|1] 표현 [0\|1] |
| 20 | `SG_` | O | **break_command**; 들여쓰기 없음(관례 위반, 파서 허용); 16\|16 무부호 ×1 [0\|100] 표현 [0\|65535] |
| 21 | `SG_` | O | **break_is_auto_command**; 들여쓰기 없음(관례 위반, 파서 허용); 41\|1 무부호 ×1 [0\|1] 표현 [0\|1] |
| 22 | `SG_` | O | **acc_command**; 들여쓰기 없음(관례 위반, 파서 허용); 32\|8 무부호 ×1 [0\|100] 표현 [0\|255] |
| 23 | `SG_` | O | **acc_is_auto_command**; 들여쓰기 없음(관례 위반, 파서 허용); 42\|1 무부호 ×1 [0\|1] 표현 [0\|1] |
| 25 | `BO_` | O | 송신 노드 미지정; 0x110, DLC 8 |
| 26 | `SG_` | O | **steer_postion**; 들여쓰기 없음(관례 위반, 파서 허용); 0\|16 부호 ×0.1 [-200\|200] 표현 [-3276.8\|3276.7] |
| 27 | `SG_` | O | **is_auto**; 들여쓰기 없음(관례 위반, 파서 허용); 16\|1 무부호 ×1 [0\|1] 표현 [0\|1] |
| 28 | `SG_` | O | **live_counter**; 들여쓰기 없음(관례 위반, 파서 허용); 56\|8 무부호 ×1 [0\|255] 표현 [0\|255] |
| 30 | `BO_` | O | 송신 노드 미지정; 0x111, DLC 8 |
| 31 | `SG_` | O | **break_postion**; 들여쓰기 없음(관례 위반, 파서 허용); 0\|16 부호 ×0.1 [0\|300] 표현 [-3276.8\|3276.7] |
| 32 | `SG_` | O | **is_auto**; 들여쓰기 없음(관례 위반, 파서 허용); 16\|1 무부호 ×1 [0\|1] 표현 [0\|1] |
| 33 | `SG_` | O | **live_counter**; 들여쓰기 없음(관례 위반, 파서 허용); 56\|8 무부호 ×1 [0\|255] 표현 [0\|255] |
| 35 | `BO_` | O | 송신 노드 미지정; 0x112, DLC 8 |
| 36 | `SG_` | O | **right_speed**; 들여쓰기 없음(관례 위반, 파서 허용); 0\|12 무부호 ×0.1 [0\|409.5] 표현 [0\|409.5] |
| 37 | `SG_` | O | **right_rpm**; 들여쓰기 없음(관례 위반, 파서 허용); 12\|12 무부호 ×1 [0\|4095] 표현 [0\|4095] |
| 38 | `SG_` | O | **enc_right_side**; 들여쓰기 없음(관례 위반, 파서 허용); 40\|2 무부호 ×1 [0\|3] 표현 [0\|3] |
| 39 | `SG_` | O | **wheel_right_size**; 들여쓰기 없음(관례 위반, 파서 허용); 48\|8 무부호 ×1 [0\|255] 표현 [0\|255] |
| 40 | `SG_` | O | **live_counter**; 들여쓰기 없음(관례 위반, 파서 허용); 56\|8 무부호 ×1 [0\|255] 표현 [0\|255] |
| 42 | `BO_` | O | 송신 노드 미지정; 0x113, DLC 8 |
| 43 | `SG_` | O | **left_speed**; 들여쓰기 없음(관례 위반, 파서 허용); 0\|12 무부호 ×0.1 [0\|409.5] 표현 [0\|409.5] |
| 44 | `SG_` | O | **left_rpm**; 들여쓰기 없음(관례 위반, 파서 허용); 12\|12 무부호 ×1 [0\|4095] 표현 [0\|4095] |
| 45 | `SG_` | O | **enc_left_side**; 들여쓰기 없음(관례 위반, 파서 허용); 40\|2 무부호 ×1 [0\|3] 표현 [0\|3] |
| 46 | `SG_` | O | **wheel_right_size**; 들여쓰기 없음(관례 위반, 파서 허용); 48\|8 무부호 ×1 [0\|255] 표현 [0\|255] |
| 47 | `SG_` | O | **live_counter**; 들여쓰기 없음(관례 위반, 파서 허용); 56\|8 무부호 ×1 [0\|255] 표현 [0\|255] |
| 49 | `BO_` | O | 송신 노드 미지정; 0x114, DLC 2 |
| 50 | `SG_` | O | **live_counter**; 들여쓰기 없음(관례 위반, 파서 허용); 8\|8 무부호 ×1 [0\|255] 표현 [0\|255] |
| 51 | `SG_` | O | **is_auto**; 들여쓰기 없음(관례 위반, 파서 허용); 0\|1 무부호 ×1 [0\|1] 표현 [0\|1] |
| 53 | `BO_` | O | 송신 노드 미지정; 0x301, DLC 2 |
| 54 | `SG_` | O | **set_wheel_size**; 들여쓰기 없음(관례 위반, 파서 허용); 8\|8 무부호 ×1 [0\|255] 표현 [0\|255] |
| 55 | `SG_` | O | **set_left_right_side**; 들여쓰기 없음(관례 위반, 파서 허용); 0\|2 무부호 ×1 [0\|3] 표현 [0\|3] |
| 57 | `BO_` | O | 송신 노드 미지정; 0x200, DLC 8 |
| 58 | `SG_` | O | **steer_postion**; 들여쓰기 없음(관례 위반, 파서 허용); 0\|16 부호 ×0.1 [-200\|200] 표현 [-3276.8\|3276.7] |
| 59 | `SG_` | O | **break_postion**; 들여쓰기 없음(관례 위반, 파서 허용); 16\|16 부호 ×0.1 [0\|300] 표현 [-3276.8\|3276.7] |
| 60 | `SG_` | O | **steer_is_auto**; 들여쓰기 없음(관례 위반, 파서 허용); 32\|1 무부호 ×1 [0\|1] 표현 [0\|1] |
| 61 | `SG_` | O | **break_is_auto**; 들여쓰기 없음(관례 위반, 파서 허용); 33\|1 무부호 ×1 [0\|1] 표현 [0\|1] |
| 62 | `SG_` | O | **acc_is_auto**; 들여쓰기 없음(관례 위반, 파서 허용); 34\|1 무부호 ×1 [0\|1] 표현 [0\|1] |
| 63 | `SG_` | O | **steer_live_counter**; 들여쓰기 없음(관례 위반, 파서 허용); 40\|8 무부호 ×1 [0\|255] 표현 [0\|255] |
| 64 | `SG_` | O | **break_live_counter**; 들여쓰기 없음(관례 위반, 파서 허용); 48\|8 무부호 ×1 [0\|255] 표현 [0\|255] |
| 65 | `SG_` | O | **acc_live_counter**; 들여쓰기 없음(관례 위반, 파서 허용); 56\|8 무부호 ×1 [0\|255] 표현 [0\|255] |
| 67 | `BO_` | O | 송신 노드 미지정; 0x201, DLC 8 |
| 68 | `SG_` | O | **right_speed**; 들여쓰기 없음(관례 위반, 파서 허용); 0\|12 무부호 ×0.1 [0\|409.5] 표현 [0\|409.5] |
| 69 | `SG_` | O | **right_rpm**; 들여쓰기 없음(관례 위반, 파서 허용); 12\|12 무부호 ×1 [0\|4095] 표현 [0\|4095] |
| 70 | `SG_` | O | **left_speed**; 들여쓰기 없음(관례 위반, 파서 허용); 24\|12 무부호 ×0.1 [0\|409.5] 표현 [0\|409.5] |
| 71 | `SG_` | O | **left_rpm**; 들여쓰기 없음(관례 위반, 파서 허용); 36\|12 무부호 ×1 [0\|4095] 표현 [0\|4095] |
| 72 | `SG_` | O | **right_live_counter**; 들여쓰기 없음(관례 위반, 파서 허용); 48\|8 무부호 ×1 [0\|255] 표현 [0\|255] |
| 73 | `SG_` | O | **left_live_counter**; 들여쓰기 없음(관례 위반, 파서 허용); 56\|8 무부호 ×1 [0\|255] 표현 [0\|255] |
| 75 | `BO_` | O | 송신 노드 미지정; 0x210, DLC 8 |
| 76 | `SG_` | O | **steer_command**; 들여쓰기 없음(관례 위반, 파서 허용); 0\|16 부호 ×0.1 [-150\|150] 표현 [-3276.8\|3276.7] |
| 77 | `SG_` | O | **steer_is_auto_command**; 들여쓰기 없음(관례 위반, 파서 허용); 40\|1 무부호 ×1 [0\|1] 표현 [0\|1] |
| 78 | `SG_` | O | **break_command**; 들여쓰기 없음(관례 위반, 파서 허용); 16\|16 무부호 ×1 [0\|100] 표현 [0\|65535] |
| 79 | `SG_` | O | **break_is_auto_command**; 들여쓰기 없음(관례 위반, 파서 허용); 41\|1 무부호 ×1 [0\|1] 표현 [0\|1] |
| 80 | `SG_` | O | **acc_command**; 들여쓰기 없음(관례 위반, 파서 허용); 32\|8 무부호 ×1 [0\|100] 표현 [0\|255] |
| 81 | `SG_` | O | **acc_is_auto_command**; 들여쓰기 없음(관례 위반, 파서 허용); 42\|1 무부호 ×1 [0\|1] 표현 [0\|1] |
| 83 | `CM_` | O | 주석 → 0x210.steer_command |
| 84 | `CM_` | O | 주석 → 0x200.steer_is_auto |
| 86 | `BA_DEF_` | O |  |
| 88 | `BA_DEF_DEF_` | O |  |
| 90 | `BA_` | O |  |
| 91 | `VAL_` | O | 0x100.steer_is_auto_command = 0:Manual, 1:Auto |
| 92 | `VAL_` | O | 0x100.break_is_auto_command = 0:Manual, 1:Auto |
| 93 | `VAL_` | O | 0x100.acc_is_auto_command = 0:Manual, 1:Auto |
| 94 | `VAL_` | O | 0x110.is_auto = 0:Manual, 1:Auto |
| 95 | `VAL_` | O | 0x111.is_auto = 0:Manual, 1:Auto |
| 96 | `VAL_` | O | 0x112.enc_right_side = 1:Right, 2:Left |
| 97 | `VAL_` | O | 0x113.enc_left_side = 1:Right, 2:Left |
| 98 | `VAL_` | O | 0x114.is_auto = 0:Manual, 1:Auto |
| 99 | `VAL_` | O | 0x301.set_left_right_side = 1:Right, 2:Left |
| 100 | `VAL_` | O | 0x200.steer_is_auto = 0:Manual, 1:Auto |
| 101 | `VAL_` | O | 0x200.break_is_auto = 0:Manual, 1:Auto |
| 102 | `VAL_` | O | 0x200.acc_is_auto = 0:Manual, 1:Auto |
| 103 | `VAL_` | O | 0x210.steer_is_auto_command = 0:Manual, 1:Auto |
| 104 | `VAL_` | O | 0x210.break_is_auto_command = 0:Manual, 1:Auto |
| 105 | `VAL_` | O | 0x210.acc_is_auto_command = 0:Manual, 1:Auto |

## 5. 업체 원본 대비 수정본 차이

| 신호 | 원본 | 수정본 | 근거 |
|---|---|---|---|
| 0x200.steer_is_auto | ×0.1 [0\|0.1] | ×1 [0\|1] | 1비트 플래그 배율 오류 (CM_ 주석) |
