# 우리 PC(기준) 스냅샷 — 2026-09-29
대회 PC 결과와 비교하는 기준값. 생성: 우리 PC 에서 `dpkg --get-selections`, `/proc/cmdline` 등.

| 항목 | 기준값 |
|---|---|
| 커널 | 6.8.1-1059-realtime (PREEMPT_RT), 기본 GRUB 항목 `a1-bsw-rt-poll` |
| cmdline | `cmdline.txt` (isolcpus 8-15, nohz_full, rcu_nocbs, idle=poll, intel_pstate=disable …) |
| 운용 유닛 | `a1-bsw-rt-tune.service` (eco 800 MHz + hk ondemand) |
| soak 30분 최악 | **27 µs** (>50 µs 0건, >200 µs 0건) — `measurements/2026-09-16_eco/` |
| soak 8시간 최악 | 920 µs, 100 µs 초과 0.000022 % — 운용 설정 확정 실측 |
| NVIDIA | 470.256.02-server (RT 용은 `IGNORE_PREEMPT_RT_PRESENCE=1` 우회 빌드) |
| can_guard | 유닛 64개 통과(README 기준), SIL P-1/P-2 PASS, A-3 자체측정 평균 17 µs / 최대 29 µs |
