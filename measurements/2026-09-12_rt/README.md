# 2026-09-12 RT 커널 실측 원본 로그 (증거 스냅샷)

`can_stack_development.md` §5.A 의 수치가 나온 원본. 커널 `6.8.1-1059-realtime`, 측정 도구 `tools/rt/a1_rt.sh`.
cyclictest 로그의 `# Max Latencies` 줄이 스레드(CPU)별 최악 지연(µs), `# Histogram Overflows` 가 400 µs 초과 횟수.

| 파일 | 조건 | §5.A 수치 |
|---|---|---|
| `hwlat_20260912_155138.txt` | hwlatdetect 60초, 20 µs 기준 | 하드웨어(SMI) 지연 0건 |
| `cyclictest_20260912_160109.idle.log` | 튜닝 전 RT, CPU 0-7, 무부하 100초 | 최악 65 µs |
| `cyclictest_20260912_160109.cpu.log` | 튜닝 전 RT, CPU 부하 60초 | 최악 23 µs |
| `cyclictest_20260912_160109.gpu.log` | 튜닝 전 RT, CPU+GPU(83%) 부하 60초 | 최악 20 µs |
| `cyclictest_20260912_160109.hwlat.log` | 같은 bench 의 hwlatdetect | 0건 |
| `soak_20260912_165249.log` | **1차 30분** (튜닝 부팅, 격리 8-15) — GPU 도구 크래시·코어덤프·병행 시험으로 **오염, 참고용** | 최악 839 µs, 400 µs 초과 93회 |
| `trace_20260912_173651.log` | 5분, GPU+CPU 부하 안정, ftrace 임계 300 µs 미발동 | 최악 33 µs |
| `soak_20260912_174708.log` | **2차 30분 (정식)** — GPU 83% 유지 + CPU·메모리·I/O 부하, 격리 8-15 | **최악 467 µs, 400 µs 초과 2회(단일 사건)** |
| `soak_20260912_174708.gpu_util` | 2차 측정 중 GPU 사용률·glmark2 생존 (10초 간격) | 평균 83%, 중단 0회 |

실행 중 생기는 새 로그는 `tools/rt/logs/` 에 쌓이며 git 추적에서 제외된다. 문서 근거로 쓸 로그만 여기에 복사해 남긴다.
