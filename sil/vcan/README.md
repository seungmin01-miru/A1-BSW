# sil/vcan — vcan0 SIL 환경

실물 CAN 없이 `vcan0`(가상 버스) 위에서 실차 A1 프로토콜(`DBC/A1_dbc_fixed.dbc`)을 흉내 내 can_guard·브리지·리프트
도구를 시험하는 도구. 2026-10-07 EAIT DBC 시절 도구(`eait_tx.py`·`eait_rx.py`)를 지우고 A1 기준으로 바꿨다(git 기록).

## 준비 (1회)

```bash
sudo apt install -y can-utils                       # candump / cansend / cansniffer / cangen
python3 -m pip install --user cantools python-can   # 설치 확인: python3 -c "import can, cantools"
```

**DBC**: `DBC/A1_dbc_fixed.dbc` (업체 원본 `A1_dbc.dbc` 에서 0x200 `steer_is_auto` 오타 1곳만 고친 사본 — 0x210 조향 ×0.1 은 10-07 실차로 확정, 근거는
`docs/2026-10-06_a1_dbc_update_and_lift_plan.md` §0). 이 DBC 에도 `GenMsgCycleTime` 이 없어 주기는 9/17 실차 로그
실측값(0x200·0x201·0x210 ≈ 20 ms)을 쓴다.

## 도구

| 파일 | 역할 |
|---|---|
| `vcan_up.sh` | `vcan0` 생성(멱등). `--install` 이면 부팅 시 자동 생성(systemd `vcan0.service`) |
| `roundtrip_check.sh` | `cansend` → `candump` 왕복 자동 판정 |
| `fake_a1_vehicle.py` | **가짜 실차** — 0x200·0x201 을 20 ms 로 송신(카운터 포함), 0x210 을 받으면 auto 비트가 켜진 축만 반응(조향 1차 지연 추종, 브레이크, 단순 속도 적분). `--interface kvaser --channel 1` 이면 Kvaser CANlib 가상 채널에서 동작 |

```bash
sudo bash sil/vcan/vcan_up.sh                       # 1회(이미 enabled 면 불필요)
bash sil/vcan/roundtrip_check.sh
python3 sil/vcan/fake_a1_vehicle.py --channel vcan0 &
candump vcan0 | python3 -m cantools decode --single-line DBC/A1_dbc_fixed.dbc
```

이 도구들을 쓰는 자동 리허설: `tools/race_day/rehearse_lift_guard.sh`(can_guard + lift_cmd, C0~C7 재현),
`tools/race_day/rehearse_lift_tx.sh`(비상용 lift_tx). can_guard 의 P-1·P-2 SIL: `safety/can_guard/sil_tests/`.

## RT 실행 조건 실측 (2026-09-12, EAIT 시절 기록 — 레시피는 지금도 유효)

당시 송신기(`eait_tx.py`, 삭제됨)로 잰 송신 주기 편차. 같은 레시피(격리 코어 + 타이머 여유 1 µs + SCHED_FIFO +
mlockall)가 `safety/can_guard/rt_setup.py`, `ros2_ws/.../rt_utils.py` 에 그대로 들어 있다.

| 조건 | 송신 주기 편차 평균 / 최대 |
|---|---|
| 비격리 코어(0–7), 일반 우선순위 | 61 / 88 µs |
| 격리 코어, 일반 우선순위 | 57 / 63 µs |
| 격리 코어 + 타이머 여유 1 µs | 17 / 27 µs |
| 격리 코어 + SCHED_FIFO 80 | **5 / 9 µs** |

일반 우선순위 태스크는 커널이 기본 50 µs 타이머 여유를 두어 `sleep` 이 늦게 깬다 — 편차 ~57 µs 의 정체.
**RT 우선순위로 측정할 때**: 이 PC는 계정에 rtprio 를 영구 부여하지 않는다(2026-09-12 결정). 측정할 때만
`sudo chrt -f 90 sudo -u ailab python3 …` 처럼 한 번씩 올린다.

## 한계
vcan은 로컬 루프백이라 버스 전송지연·중재·에러프레임이 없다. 실시간성·버스 동작 최종 검증은 실물 CAN(PEAK `can0`/`can1`
또는 Kvaser)에서 재측정.
