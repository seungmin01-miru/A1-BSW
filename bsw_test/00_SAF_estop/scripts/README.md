# 00 SAF-ESTOP — 대회 차량 PC SIL 확인

차량 버스는 건드리지 않는다(vcan0 만). 개발 PC 결과는 로직 확인용, **00 판정에는 대회 PC 결과**를 쓴다.

```bash
cd ~/git/A1-BSW && git fetch && git checkout claude/ecstatic-keller-uhtbhe && git pull
bash bsw_test/00_SAF_estop/scripts/setup_env.sh          # vcan0(sudo)·python-can 4.6.1·cantools 44.0.0·pytest
bash bsw_test/00_SAF_estop/scripts/run_sil_check.sh --rt auto   # 격리 코어·SCHED_FIFO 90 (격리 부팅 아니면 --rt 빼기)
git add bsw_test/00_SAF_estop/results/sil_* && git commit -m "00 SIL 대회 PC 결과" && git push
```

| 스크립트 | 내용 |
|---|---|
| `setup_env.sh [--check] [--build-ws]` | 환경 점검·설치. 오프라인이면 `/var/local/a1-wheels`(배포 번들) 또는 `A1_WHEELS=<경로>` |
| `run_sil_check.sh [--rt N\|auto] [--trials N] [--track]` | 단위 시험 → E1~E6 → P-1/P-2 → (선택) rehearse_track. 결과 `results/sil_<시각>_<호스트>/summary.md` |

배포 흐름(`tools/deploy/90_validate.sh`)에도 E1~E6 이 들어가 있다(격리 코어 자동).
