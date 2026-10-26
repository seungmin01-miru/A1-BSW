# 대회 차량 제원 체크리스트 (mpc/)

현재 값은 MA Formula 4 (630 kg) 기준이다. `[Vehicle]` 값은 `config/control.ini` 에서 읽으며, 키가 없으면 코드 기본값(아래 "현재값")이 조용히 쓰인다. 대회 차량 값을 채운 뒤 모든 키를 명시할 것.

## control.ini 템플릿
```ini
[Vehicle]
wheel_base=2.35             ; [m]    P0  모든 횡방향 모델
vehicle_mass=630.0          ; [kg]   P0  주행 상태(드라이버 포함) 질량
vehicle_wheel_radius=0.28   ; [m]    P0  동적 반경, trq = m*a*r
max_steer_deg=25.0          ; [deg]  P0  타이어각 한계 (신규 키)
max_steer_rate_deg_s=40.0   ; [deg/s]P0  조향 속도 한계 (신규 키, Delayed LMPC)
gravity=9.806
rolling_resistance=0.015    ; P1  코스트다운으로 식별
air_density=1.205
vehicle_front_area=1.2      ; [m2] P1
air_drag_coeff=0.7          ; P1

[Longitudinal Delayed Kinematic LMPC]
time_constant=0.15          ; P0  페달 스텝 응답으로 식별
max_jerk=6.0
max_state_accel=20.0        ; [m/s2] P0 |ax| 상태 제약 (신규 키)
```

## 아직 하드코딩이라 코드 수정이 필요한 항목
| 항목 | 위치 | 현재값 |
|---|---|---|
| `VEHICLE_WHEEL_BASE/MASS/WHEEL_RADIUS`, `*_AXLE_TO_CG`, 치수, `STEERING_RATIO`, Iz | `bsw/system/interface/interface_constants.hpp` | MA F4 |
| 코너링 강성 Cf, Cr | 동일 | 98047 / 164000 |
| 휠 토크 상·하한 `T_UL/T_LL` | 동일 | 6443 / -10000 |
| GGV 최대 가속 테이블 `kAxMax[]` | `util_function/function_control.hpp` | MA F4 GGV |
| 최대 가감속 | `scc_replanner/.../scc_speed_replanner.hpp:34-36` | 15 / 10 |
| `kMaxJerkAbs` | `longitudinal_delayed_kinematic_lmpc.cpp` | 100 |
| Pedal map 파일 (`pedal_map_file_name`) | `resources/pedal_map/` (깨진 symlink) | - |

`interface_constants.hpp` 와 ini 가 같은 값을 이중 정의하므로 (`wheel_base`, `mass`, `wheel_radius`) 둘 다 갱신해야 한다.

## 확보 방법
- 제원표: 휠베이스, 치수, 질량, 최대 조향각, 조향비, Cd·A
- 정적 실측: 축중(lf, lr), 동적 타이어 반경
- 시험: 조향/페달 스텝(τ, dead time), 코스트다운(Crr, Cd·A), 페달 스윕(pedal map), 풀가속/제동(GGV), 정상원선회(Kv)
- dead time 은 횡/종 Delayed LMPC 모두 **6·step_dt 로 구조 고정** — 측정값과 다르면 step_dt 조정 또는 코드 수정
