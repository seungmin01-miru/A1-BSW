# 아카이브: 2026-10 기준 DBC — 새 코드에 쓰지 말 것

- `A1_dbc_fixed.dbc`: 2026-10-06 ~ 2026-11-06 의 기준 DBC. 업체 `DBC/A1_dbc.dbc` 에서 0x200 `steer_is_auto` 오타를 고친 사본.
- 현재 기준: `DBC/A1_dbc_2_fixed.dbc` (2026-11-06 전환, `docs/2026-11-06_race_flag_dev_plan.md` WP0).
- 이 파일은 과거 기록을 다시 만들 때만 쓴다(`measurements/2026-10-07_vehicle_lift/analysis/dbc_check.py`).

## 새 기준과 다른 점 (버스 프레임은 같고 해석만 다르다)

| 항목 | 이 파일 | 새 기준 |
|---|---|---|
| `break_postion` (0x200, 0x111) | ×0.1, 0~10 표기 | ×1, % |
| 0x201 메시지 이름 | `USER_right_wheel_info` | `USER_wheel_info` |
| 0x100 `steer_command` | ×1 | ×0.1 |
| 0x124~0x128, 0x156, 0x157, 0x202, 0x203, 0x710~0x712 | 없음 | 정의됨 |

`measurements/`·`reports/` 의 옛 표·그림에 있는 브레이크 위치 0~10 은 이 파일 기준 값이다. ×10 하면 % 가 된다.
