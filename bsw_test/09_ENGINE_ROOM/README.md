# 09 ENGINE-ROOM

속도별 종방향 표와 조향 표를 재고 갱신하는 시험. 근거 [`docs/2026-11-06_engine_room_dev_plan.md`](../../docs/2026-11-06_engine_room_dev_plan.md).

| 파일 | 내용 |
|---|---|
| **`run_verify.sh`** | **대회 PC 판정 묶음 — 이것 하나만 돌리면 된다**(아래) |
| `docs/checklist.md` | 실차 날짜별 점검표 |
| `scripts/sil_engine_room.py` | 표·표 갱신·원 돌기 SIL |
| `scripts/e2e_bsw_up.py` | 떠 있는 SIL BSW 에 20 Hz 입력을 넣어 전체 경로 확인 |
| `ctrl/speed_ctrl_circle.yaml` | 원 돌기 저속 유지값(잠정) |
| `results/verify_*/` | `run_verify.sh` 결과, `VERDICT.md` |

## 대회 PC 판정

```bash
cd ~/git/A1-BSW && git fetch && git checkout claude/ecstatic-keller-uhtbhe && git pull
bash bsw_test/09_ENGINE_ROOM/run_verify.sh      # 약 30~40분, sudo 비밀번호 1회, 차량·실제 CAN 에 안 닿음
```

Claude 세션에서는 앞에 `!` 를 붙인다(`bsw_up`·`bsw_down` 은 Claude 가 직접 실행할 수 없다).

| 단계 | 내용 | 합격 |
|---|---|---|
| 준비 | `setup_env.sh --build-ws`, vcan0, colcon build | 실패하면 종료 2 |
| pytest | 단위 시험 전부 | 전부 통과 |
| lift | `rehearse_lift_guard` | 31/31 |
| sil | `run_sil_check --rt auto --track`: E1~E7, P1·P2, 세션, 트랙 | 각 시험 기준 |
| race | `r_race` socketcan: 시나리오 10, 지연 | 중앙 ≤ 5 ms |
| cruise | 히스테리시스, 입력 고정 | ≤ 5 회/분 |
| er | `sil_engine_room` | 전부 통과 |
| bsw | `bsw_up --sil` → 시작 점검 → `e2e_bsw_up` → `bsw_down` | 전부 통과 |

결과는 `results/verify_<시각>_<호스트>/VERDICT.md` 한 장. 제목이 **판정**이면 `~/git/A1-BSW`, 격리 코어, RT, 추적 파일 수정 없음을 모두 만족한 것이고, 아니면 **참고값**과 이유를 적는다. 끝에 나오는 `git add`·`commit` 으로 기록한다. 실패가 있으면 표의 로그부터 본다.

일부만: `--only race,bsw`. 다시 돌릴 때 준비 생략: `--skip-setup`. 모델 노드와 섞이지 않게 `ROS_DOMAIN_ID=77` 로 돈다.
