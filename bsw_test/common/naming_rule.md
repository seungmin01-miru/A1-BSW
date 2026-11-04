# 파일 이름 규칙

`날짜_테스트ID_회차.확장자` — 원본은 **CAN 로그(.log)** 로 통일 (Kvaser 미러 `--log` / `candump -l` 형식)

| 종류 | 예 | 위치 |
|---|---|---|
| CAN 원본 | `20261104_LON-ACC-01_r1.log` | `0X_*/raw/` (git 밖, README 에 위치 기록) |
| 현장 세션 폴더(bringup `--out`) | `20261104_LON-ACC-01_s1/` — 안에 여러 회차, `notes.txt` 의 `r? 시작/끝` 으로 구분 | `0X_*/raw/` + `.sha256` |
| rosbag (GNSS RTX fix 시) | `20261104_LON-ACC-01_r1_bag/` | `0X_*/raw/` |
| 변환 CSV | `20261104_LON-ACC-01_r1.csv` | `0X_*/converted/` |
| 그래프 | `20261104_LON-ACC-01_r1_speed.png` | `0X_*/results/` |
| 생성기 명령 로그 | `20261104_LON-ACC-01_r1_cmd.csv` | `0X_*/raw/` |

테스트 ID: SAF-ESTOP / LAT-ALIGN / LON-PROF / LON-ACC / LON-DEC / LAT-MAXSTEER / LAT-ACK / LAT-PROF + `-01` 등 조건 번호.
