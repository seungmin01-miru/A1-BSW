# 09 ENGINE-ROOM — 현장 점검표

기준: [지시서](../../../docs/2026-11-06_engine_room_dev_plan.md) 2.3~2.5·4절. 입력 순서는 [`FIELD_CHEATSHEET.txt`](../../FIELD_CHEATSHEET.txt) "★ 11-14". 공통 안전은 [`FIELD_MANUAL.md`](../../FIELD_MANUAL.md) 2절.
합격 기준은 지시서 값을 옮겼다. **속도 상향 관문: 직전 구간 정확도 ±20 % 통과 후에만 다음 속도.** 판정은 대회 차량 PC 와 실차 결과로만 한다.

## 전날
- [ ] 대회 PC: `bash bsw_test/09_ENGINE_ROOM/run_verify.sh` → `VERDICT.md` 가 "✅ 전부 통과 — **판정**" (약 30~40분, [`README`](../README.md))
- [ ] 모델 팀: 서킷 주행 가능 여부(없으면 BSW 단독 항목만), `/race/state` 구독 확인
- [ ] 로컬라이제이션 팀: bag 토픽(요레이트·피치·RTX 상태) 확인

## 11-14(토) 0~20 km/h

| # | 항목 | 명령 | 합격·판단 | 결과 파일 |
|---|---|---|---|---|
| ① | 점검·RTX, 조향 부호 | `up` → `lc` → `steer 30` | 앞바퀴 왼쪽, RTX fix | `notes.txt` |
| ② | 원 돌기 ±37/75/112/150°, 2바퀴, ≤ 8 km/h | `circ ER-CIRCLE-01 --dry-run` 으로 공간 확인 → `circ ER-CIRCLE-01` | 지름 약 23~80 m ★ 공간. 첫 회차에서 `speed_ctrl_circle.yaml` 출발·유지 확인 | `*_cmd.csv` → `fit_circle.py` |
| ③ | 조향 최대각(정지) | `lc`: `steer 150`, `steer -150` | 상태줄 포화점 좌/우 기록 | `notes.txt` |
| ④ | 직선 계단, **칸당 3회 이상** | `gen acc_step.yaml …`, `gen brake_step.yaml …` (메모장) | 코스 안에서 정지. 앱 담당 위치 확인 | CAN 로그 + bag |
| ⑤ | 현장 1차 표 | `fit_long_map.py --can <세션> --bag <bag> --out reports/engine_room/20261114_fit` | 종료 코드 0 → `report.md` 사람 확인 → `long_map.yaml` 수정·커밋·이력 / 3 → `UPDATE_BLOCKED.md` 해결 | `reports/engine_room/` |
| ⑥ | 서킷 15 → 20 (모델) | `bsw_up` | 직선 50 m 횡편차 ≤ 0.125 m 목표, +0.33 m 보다 나빠지면 `steer_map_v0` 복귀. 가속 전환 수 기록 | CAN 로그 + bag |

조향 표: `fit_circle.py` 추천을 사람이 보고 `track.yaml` 에 넣고 `map_version` 을 올린다.

## 11-15(일) 20~40 km/h
- [ ] 서킷 20 → 30 → 40, 바퀴마다 `fit_long_map.py`
- [ ] 30 km/h 브레이크 계단 50/80/100 %
- [ ] 휠 오돔: 정속 10/20/30 km/h 각 20 s
- [ ] 깃발 전달 확인(0x124 흉내 — **현장 허락 시만**)

## 11-21·22 40~80 km/h
- [ ] 서킷 상향(60 이상은 표 갱신 뒤 저속 한 바퀴 확인 후), 50·80 km/h 브레이크 계단, 긴 직선 가속 100 %, 횡가속 한계(여유 시)

## 11-23·24 80~100 km/h, 동결
- [ ] 대회 서버 신호 실제 확인: `/race/state`·상태판, 0x124~0x127 프레임 기록 대조
- [ ] 전 구간 정확도 ±20 %(`report.md` 정확도 표), 재측정, **코드·표 동결**

## 매일 끝에
- [ ] `measurements/<날짜>_engine_room/` 원본, `reports/engine_room/<날짜>_fit/`, 표 버전 커밋, `docs/engine_room_map_history.md`
