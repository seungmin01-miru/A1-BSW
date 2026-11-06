# tools/engine_room — 표 갱신·원 돌기 분석

저장된 로그만 읽는다(CAN·차량에 닿지 않음). **표를 자동으로 바꾸지 않는다** — 추천 파일을 사람이 보고 고친 뒤 커밋하고 [`docs/engine_room_map_history.md`](../../docs/engine_room_map_history.md) 에 적는다. 절차는 [`bsw_test/09_ENGINE_ROOM/docs/checklist.md`](../../bsw_test/09_ENGINE_ROOM/docs/checklist.md).

```bash
# 종방향 표(현장: 한 바퀴 뒤 1분 안에)
python3 tools/engine_room/fit_long_map.py --can <로그|세션 폴더> --bag <bag> --out reports/engine_room/<날짜>_fit
python3 tools/engine_room/fit_long_map.py --can <로그> --gnss-csv <csv> --out <폴더>     # t, speed_mps, fix, pitch_deg
python3 tools/engine_room/fit_long_map.py --can <로그> --can-only-preview --out <폴더>   # GNSS 없음: 값만, 추천 없음
# 원 돌기
python3 bsw_test/common/scripts/circle_test.py --run <이름> --dry-run                    # 계획·필요 공간·시간
python3 tools/engine_room/fit_circle.py --run-dir <세션 폴더> --bag <bag> --out <폴더>
# 시험
python3 -m pytest -q tools/engine_room/test_fit_tools.py
python3 bsw_test/09_ENGINE_ROOM/scripts/sil_engine_room.py --out <폴더>                  # vcan0, ROS 필요
```

## fit_long_map

| 종료 코드 | 뜻 | 다음 행동 |
|---|---|---|
| 0 | 전 검사 통과(또는 미리보기) | `report.md` 확인 → `long_map_recommended.yaml` 을 사람이 반영 |
| 2 | 입력 오류 | 경로·형식 확인 |
| 3 | ⛔ 차단 | `UPDATE_BLOCKED.md` 원인 해결. 표를 바꾸지 않는다 |

**확인할 수 없는 검사는 통과가 아니다.**

| 검사 | 기준(지시서 2.3절, 잠정) | 재는 법 | 확인 불가 |
|---|---|---|---|
| GNSS 품질 | fix ≥ 90 % | INS `status.gnss` = 4, CSV `fix` | odom 만 있는 bag |
| 다른 조작(CAN) | 없을 것 | 0x156 En, 0x127 RES, 0x210 > 70 프레임/s | — |
| 다른 조작(ESTOP) | 없을 것 | 이벤트 `estop_request`·`res`·`foreign_0x210`·`->ESTOP` | 이벤트 파일 없음 |
| 물리 한계 | 감속 ≤ 4.5, 가속 ≤ 1.6 m/s² | 구간 평균 | — |
| 시간 어긋남 | ≤ 0.05 s | 후륜·GNSS 가속도 상호상관 | 겹침 < 20 s, 상관 < 0.7 |
| 표본 부족 | 칸당 ≥ 3 구간 | 아래 참고 | — |
| 경사 | \|피치\| ≤ 2° | INS 피치, CSV `pitch_deg` | 피치 없음 |
| 급변 | 기존 대비 ≤ 20 % | 칸별 | 걸리면 사람 확인 |
| 표 형식 | 단조·부호 | 어댑터와 같은 `LongMap2D` 검사 | — |

**표본 부족의 해석(E-13):** 지시서를 글자대로 읽으면 표본 < 3 칸이 하나만 있어도 차단인데, 11-14 계단은 "각 2회"라 늘 차단된다. 그래서 **표본 < 3 칸은 추천에서 빼고 표시**하며, 쓸 수 있는 칸이 하나도 없을 때만 차단한다. 계단을 3회 이상 하면 그 칸이 표에 들어간다.

구간 고르기: 페달이 ±3 % 로 유지되고 다른 축이 0 인 구간. 앞부분은 지연만큼 버린다(가속 0.7 s, 브레이크 0.4 s). 속도 칸 경계에서 나눈다. 칸의 속도 = 0x201, 가속도 = GNSS 0.3 s 이동평균 미분. 페달과 가속도 부호가 다른 구간은 뺀다(가속 % 를 주는데 감속 중 등).

추천 표: 표본 ≥ 3 칸만 바꾼다. 그 칸과 뒤집히는 옛 칸은 미측정(null)으로 되돌린다. `report.md` 의 정확도 표는 기존 표가 약속한 가속도와 실측을 비교한다(지시서 2.4절 ±20 %).

## fit_circle

입력은 `circle_test` 회차 CSV(`<run>_cmd.csv`)와 GNSS. 조향이 명령 ±5° 안이고 속도가 안정된 구간의 궤적에 원을 맞춘다. 호 < 270°, 맞춤 RMS > 0.5 m, 최고 > 9 km/h 회차는 뺀다(잠정). 출력: `circle_fit.csv`, `report.md`(영점·좌우 실효 조향비), `steer_map_recommended.yaml`.

## 한계

- 저장소에 GNSS bag 이 없어 GNSS 경로는 합성 로그와 SIL 가짜 GNSS 로만 시험했다. 실제 INS 메시지 필드(`total_speed`, `status.gnss`, `pitch`)와 토픽 이름은 TODO-5 확정 때 확인.
- 경사 미보정, 슬립 미보정.
- 시간 어긋남 값에는 기록 시각 차이와 센서 필터 지연이 같이 들어 있다.
