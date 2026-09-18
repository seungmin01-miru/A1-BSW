# CAN Stack — Verification Checklist & Safety-MCU Plan (2026-09-14)

> Working file for the 2026-09-14 meeting. Numbers come from `can_stack_development.md` §5.A/§5.B and
> `vcan_milestone_phaseA_update.md` (measurements of 2026-09-12, main PC).
> Marks: `[ ]` todo · `[~]` in progress · `[x]` done · ⚠️ needs input / blocked
> Priority: 🔴 needed for a decision · 🟡 needed before the competition · 🟢 nice to have

---

## 0. Where we are (as of 2026-09-12)

- OS/kernel: Ubuntu 22.04.5 + `6.8.1-1059-realtime` (Ubuntu Pro backport). Isolated cores 8–15. Fallback kernel `6.8.0-138-generic` (GRUB default).
- Formal 30-min soak (GPU 83 % via glmark2 + CPU/mem/IO): avg 3.9 µs · 99.9993 % < 50 µs · 99.9995 % < 100 µs · **worst 467 µs**.
- §7 target (worst-case tens–100 µs): **not met**. CAN-stack deadline (10 ms period): met with ~5 % of the period used.
- NVIDIA 470 built with `IGNORE_PREEMPT_RT_PRESENCE=1` (officially unsupported). `BUG: scheduling while atomic` ×10, only around a GPU-process crash; 0 in the clean 30-min run.
- GPU activity injects IPIs into isolated cores (~20/s at 4K 83 %). stress-ng alone: 0 IPIs. `isolcpus`/`irqaffinity` cannot block IPIs (they are CPU→CPU broadcasts from the kernel, not device IRQs).
- vcan0 SIL running: 0x712 at 100.0 Hz from the real DBC. TX period jitter 5 / 9 µs (avg/max) with isolated core + timer slack 1 µs + SCHED_FIFO 80.
- ⚠️ **Control AI model is NOT selected yet.** Plan: a policy network trained with MPC as the teacher (MPC itself is *not* solved at runtime). Racing → track following → expected to be small. **Every item in §3 must be re-checked once the model is decided.**

### Shared-resource model (why the MCU matters)

Perception (GPU/AI) and control (CAN/safety) on one PC share four layers:

| Layer | Shared thing | Can we separate it? | How |
|---|---|---|---|
| ④ App data | ROS2 topics, shared memory | ✅ fully | control never subscribes to images/pointclouds; only small perception *results* |
| ③ Process | address space, CPU, priority | ✅ almost | separate processes, `taskset`, `chrt`, `mlockall`, cgroup limits |
| ② Kernel | one Linux, one `nvidia.ko`, kernel locks | ❌ no | this is where the NVIDIA BUG and the IPIs leak through |
| ① Hardware | DRAM, L3, PCIe, power/thermal | ❌ no (i7-12700 has no RDT/CAT/MBA) | keep the control working set inside private L2 |

Layers ② and ① can only be cut by a physically separate computer (safety MCU). **MCU deferred 2026-09-14** → see §7 for the PC-only design and the residual risks.

---

## Decisions this checklist feeds

| ID | Decision | Fed by |
|---|---|---|
| D1 | Keep the RT kernel, or return to generic + `preempt=full` | §1 |
| D2 | Run perception on the same PC / same RT kernel | §2 |
| D3 | Where the control policy NN runs (CPU isolated core vs GPU) — ⚠️ model TBD | §3 |
| D4 | Redefine the §7 real-time budget (proposal: worst 1 ms, 99.99 % < 100 µs) | §1, §4 |
| D5 | ~~Procure the safety MCU~~ **deferred 2026-09-14** → PC-only safety layer | §7 |

---

## 1. RT kernel — keep or drop (D1)

| # | Pri | Check | How | Pass / decision rule | Effort |
|---|---|---|---|---|---|
| 1-1 | 🔴 | [x] **Control group**: `6.8.0-138-generic` + `preempt=full` + same isolation tuning, 30 min | boot GRUB entry `a1-bsw-generic-tuned` → `sudo bash tools/rt/a1_rt.sh soak 30` | **Keep RT only if** generic worst > 2× RT worst **or** generic's >100 µs count > 3× RT's. Otherwise RT does not justify the unsupported NVIDIA build | 40 min |
| 1-2 | 🔴 | [x] Capture the 0.4 ms event | RT tuned boot → `sudo bash tools/rt/a1_rt.sh trace 30 300` → `python3 tools/rt/trace_analyze.py <.ftrace>` (events on isolated cores in the last ms, and who sent the IPIs) | Identify what precedes the spike (expect `CAL`/`TLB` IPI + nvidia function). Confirms/denies "GPU is the cause" | 30 min |
| 1-3 | 🔴 | [x] Observe IPI leakage directly | `bash tools/rt/ipi_check.sh` (idle → GPU load → idle, isolated-core CAL/TLB per s) | CPU8–15 columns rise only with GPU load → confirmed. Record rate/s (baseline ≈ 20/s) | 5 min |
| 1-4 | 🟡 | [ ] Hyper-thread sibling left idle | `taskset -c 0-19 cyclictest -p99 -m -i1000 -t4 -a8,10,12,14 --mainaffinity=0-7,16-19 -q -h400` 30 min (9/11/13/15 idle) | Compare worst & distribution vs 8-thread run. If better → production layout = 4 cores (8,10,12,14) | 30 min |

Notes
- The 2026-09-11 decision to skip before/after was valid then. It changed because RT failed §7: the question is no longer "does RT meet the target" but "is RT enough better than generic to pay for the unsupported NVIDIA build". 1-1 answers that.
- First attempt at 1-1 failed because `a1_rt.sh` refused a non-RT kernel; script fixed, boot entry ready.
- **2026-09-14 1-1 RESULT**: generic+`preempt=full`+isolation, 30 min, GPU 80 %: **worst 935 µs**, >400 µs ×9 (6 of them in the *same cycle* on 6 cores), >100 µs ×324, 50–100 µs ×748, avg 3.4 µs. RT (9/12): worst 467, >400 ×2, >100 ×73, 50–100 ×29. Rule A 935 > 934 ✅ (marginal), Rule B 324 > 219 ✅ (4.4×). Events >50 µs: RT 102 vs generic 1,072 (10×). **→ D1 = keep RT**, subject to 2-1 (perception loss ≤ 10 %). Logs: `measurements/2026-09-14_rt/`.
- **2026-09-14 1-2 attempt**: cyclictest broke at **937 µs after 121 s** (7 of 8 threads in the same cycle) — but the ftrace buffer was not saved: cyclictest `-b` writes `/sys/kernel/debug/tracing/tracing_on` (old path) while the script checked `/sys/kernel/tracing`; debugfs has no separate tracing mount on this PC. Script fixed (mount old path, judge by `Break value`, `--tracemark`). **Re-run on the next RT tuned boot.** Spike-in-2-min + all-threads-at-once is consistent with a system-wide IPI event.
- **2026-09-14 1-2 RESULT (2nd run, captured)**: break at **819 µs**, 7/8 threads same cycle. ftrace (11.7 ms, 600 events) shows **nothing entered the isolated cores** — no IRQ, no IPI, no other task; all 8 cyclictest threads simply woke 1.4–1.8 ms after sleeping instead of 1.0 ms, **at the same instant**. Housekeeping cores were busy but the longest all-CPU quiet gap was 304 µs. → The event is a **package-level timer/wake-up stall**, not interference on a core. Candidates: package C-state / `intel_pstate` transitions (only core C-states are capped by `max_cstate=1`), SMI (60-s hwlatdetect cannot see a 1-per-30-min event), memory/cache hardware stall. Software causes (IPI, NVIDIA, interrupts) are **excluded for this event** — 1-3's GPU→IPI leakage is a separate, smaller effect. Next: 1-6 below.
- **2026-09-15 4-3/4-4 RESULT (8-h overnight, synthetic upper-bound load)**: avg 4.1 µs, 99.9985 % < 50 µs, **worst 828 µs, >400 µs ×231 (65 episodes), 1 ms ×0**, BUG 0, throttle 0, package 42–47 °C. **All 231 events on CPU 12·13 = one physical core (6)**; the other 6 isolated cores never exceeded 375 µs in 8 h. IPI/IRQ counters are uniform across cores → not software injection; likely a per-core hardware/power-domain event. No correlation with temperature, clock, or GPU-load dips. Per 10-min run: **1.35 events expected (74 % chance of ≥1), size 0.4–0.83 ms**; with core 6 excluded: ~0. Caveat: 7/65 episodes coincided with the session's 30-min status checks (read-only) — next long run: no checks. Logs: `measurements/2026-09-14_rt/soak_20260914_212042.*`.
- **2026-09-14 1-3 RESULT**: isolated-core CAL idle 0/s → glmark2 4K **4/s** → idle 0/s; TLB 0. GPU→IPI leakage confirmed. (The "≈20/s" baseline was an estimate from cumulative counters; direct measurement is 4–5/s at 4K.)
- 2026-09-14: 1-1 started. Judge with `python3 tools/rt/compare_soak.py measurements/2026-09-12_rt/soak_20260912_174708.log <generic soak log>` (implements the pass rule). Machine was on plain generic (no isolation) at session start → reboot into `a1-bsw-generic-tuned` required first.

---

## 2. Perception on the same PC / RT kernel (D2)

RT trades throughput for latency (threaded IRQs, more context switches). Perception needs throughput. Measure the loss, and the GPU driver's stability under RT with a *real* CUDA workload (glmark2 is OpenGL; different IPI/memory pattern).

| # | Pri | Check | How | Pass | Effort |
|---|---|---|---|---|---|
| 2-1 | 🔴 | [ ] Perception throughput RT vs generic | same model, same input; fps and p99 latency on both kernels | RT within **10 %** of generic | 1 h |
| 2-2 | 🔴 | [ ] cyclictest soak under **real CUDA load** | replace glmark2 with the inference loop (or a PyTorch/TensorRT dummy at 30 fps) → `soak 30` | Compare with 467 µs. This is the "real" number | 30 min + CUDA env |
| 2-3 | 🔴 | [ ] GPU-crash injection | `kill -9` the inference process 10× under load, cyclictest running, `dmesg -w` for `BUG:` / `Xid` | Record BUG count and **how far the isolated-core latency spikes** at each crash. Quantifies "perception crash → control impact" | 30 min |
| 2-4 | 🔴 | [ ] GPU long-run stability | real inference **2 h+** (overnight 8 h if possible) | `dmesg`: `BUG:`/`Xid` = 0 | machine time |
| 2-5 | 🟡 | [ ] Sensor Ethernet packet loss on RT | LiDAR/camera streaming; `ethtool -S <nic> \| grep -i drop`, `nstat -az \| grep -i UdpInErrors` | 0 drops (or equal to generic) | 30 min |
| 2-6 | 🟡 | [ ] Housekeeping-core headroom | full perception pipeline running; `mpstat -P ALL 1` | CPU 0–7,16–19 average **< 70 %**. If exceeded, perception is CPU-bound → revisit isolated-core count | 10 min |
| 2-7 | 🟡 | [ ] Perception GPU-memory allocation pattern | measure `CAL` rate (1-3) during inference; then change perception to **allocate once at start, reuse** and re-measure | Large IPI drop → becomes a coding rule for the perception team | half day |

Decision matrix (from 1-1 and 2-1):

| | RT costs perception little (2-1 ✅) | RT costs perception a lot (2-1 ❌) |
|---|---|---|
| RT clearly better than generic (1-1) | keep RT | **conflict** — one kernel can't serve both → MCU required (control on MCU, PC on generic) |
| RT ≈ generic (1-1) | return to generic (removes NVIDIA risk) | return to generic |

---

## 3. Control policy NN placement (D3) — ⚠️ MODEL NOT SELECTED, RE-CHECK LATER

Assumption: runtime = NN inference only (MPC used offline as the teacher). Inference time is input-independent, so WCET is fixed — a big advantage over runtime MPC. Items below are written for that case; **revisit all of them when the model is chosen.**

**Question to settle first**: what are the NN inputs?

| If inputs are | Consequence |
|---|---|
| vehicle pose (GPS/INS/wheel odom) + stored track map | 🟢 **no GPU in the control loop at all.** Camera/LiDAR become a safety monitor; their failure = safe-state reason, not control failure |
| perception outputs (detected track boundary etc.) | 🟡 GPU pipeline feeds control; staleness/age check mandatory (3-6) |
| raw images (end-to-end) | 🔴 control NN must run on GPU; exposed to the kernel-BUG and IPI problems |

| # | Pri | Check | How | Pass | Effort |
|---|---|---|---|---|---|
| 3-1 | 🔴 ⚠️ | [ ] Model size | parameter count, input dim, layers | track-following MLP is typically 10^4–10^5 params → CPU is enough | 5 min (after model chosen) |
| 3-2 | 🔴 ⚠️ | [ ] CPU single-core inference WCET | one isolated core, `chrt -f 80`, **single thread** (`OMP_NUM_THREADS=1`, `torch.set_num_threads(1)`), 100k iterations, record max | **max < 1 ms** (10 % of period) → CPU confirmed. Expect tens of µs | 30 min |
| 3-3 | 🔴 ⚠️ | [ ] No allocation in the inference loop | compare `/proc/<pid>/stat` minflt/majflt before/after, or `perf stat -e page-faults` | **0 page faults** during the loop (preallocate and reuse tensors) | 30 min |
| 3-4 | 🟡 ⚠️ | [ ] Warm-up | first-inference time vs steady state | if first call is tens of ms (lazy init) → rule: 100 warm-up runs at startup | 5 min |
| 3-5 | 🟡 ⚠️ | [ ] Denormal floats | `torch.set_flush_denormal(True)` / FTZ+DAZ in C++; test with near-zero inputs/weights | denormal math can be ~100× slower; flags on → timing variance gone | 10 min |
| 3-6 | 🟡 | [ ] Input staleness | check timestamp age of pose/track inputs every cycle | age > threshold (e.g. 50 ms) → hold last safe command + warn. **Control never blocks waiting for perception** | design |
| 3-7 | 🟡 | [ ] Output clamping | clamp steering angle, steering rate, accel/decel to limits; count violations | NN can emit garbage on out-of-distribution inputs. **This check later moves to the MCU** (§7) | design |

---

## 4. Budget redefinition & Phase A closure (D4)

| # | Pri | Check | How | Pass | Effort |
|---|---|---|---|---|---|
| 4-1 | 🔴 | [ ] A-3: control node launch unit | systemd unit → `ps -eo pid,psr,policy,rtprio,comm \| grep <node>` | `psr` ∈ {8,10,12,14}, `policy`=FF, `rtprio`=80. **Without this the isolation has zero effect** | half day |
| 4-2 | 🔴 | [ ] A-4: `mlockall` test program | `mlockall(MCL_CURRENT\|MCL_FUTURE)`, then walk a large array; compare `getrusage` minflt/majflt with/without | with: 0 faults; without: > 0 → effect proven | 2 h |
| 4-3 | 🟡 | [x] Long soak | RT tuned, **8 h overnight** — first with synthetic load (upper bound, 2026-09-14 night), again with real perception load after Phase C. Pre-flight: `bash tools/rt/soak_guard.sh on` (screen lock off, apt timers stopped) | 8-h worst value = the number behind the budget redefinition ("1 event/30 min" → how many/8 h, how big) | machine time |
| 4-4 | 🟡 | [x] Thermal / clock throttling | during full load: `grep MHz /proc/cpuinfo` trend, `sensors` | `max_cstate=1` raises idle heat. Clock drop → latency rises → cooling needed | 30 min (with 4-3) |
| 4-5 | 🔴 | [ ] **Team decision D4** | based on 1-1, 2-2, 4-3 — 8-h data: worst 0.83 ms, 0 × 1 ms, ~1.4 sub-ms events per 10-min run on the current core set (0 if core 6 is dropped) | adopt "worst ≤ 1 ms, 99.99 % ≤ 100 µs" **or** move 100 µs-class loops to the MCU | meeting |

---

### 1.x Drive-profile test (added 2026-09-14 — runs are 2 × ~10 min, so test the *shape* of a run, not only steady state)

| # | Pri | Check | How | Pass | Effort |
|---|---|---|---|---|---|
| 1-6 | 🔴 | [x] Resolved by 1-6b (cause = power management). Core-6 drop **not needed**. Isolate the stall source (from 1-2 and the 8-h soak). Order: **(1) count device IRQs landing on CPU 12·13 under GPU load** → (2) if non-zero, move that IRQ's `smp_affinity_list` to 0-7,16-19 and re-count/re-soak → (3) only if (1) is zero or the IRQ is managed/immovable, drop core 6 (`isolcpus=8-11,14-15`) and `soak 30`. **2026-09-15 step (1) done: 0 device IRQs on CPU 12·13 / isolated set in 60 s at GPU 93 % (nvidia IRQ 219 = 8,114/min, all on CPU 6); NVMe q9/q10 (bound to 12/13) fired 0; IPI uniform across cores → verdict A, software injection excluded → proceed to (3)** (`measurements/2026-09-15_irq/`)
  **2026-09-15 per-thread re-cut of the 8-h histogram**: >400 µs is 100 % core 6, but 50–400 µs is uniform (core 6 = 20–23 % share) and **200 µs+ totals are equal on CPU 8/10/12/14 (248/253/271/245)** → a *common* 200–400 µs trigger hits all cores; core 6 only amplifies it past 400 µs (buckets shift, count doesn't). Dropping core 6 removes the >400 tail but not the ~250/core/8 h common events → **palliative**; root cause still needs 1-6 (b) (power-management test). Even/odd asymmetry (first HT sibling gets the long stalls) suggests a per-physical-core event shared by both siblings. | (a) `hwlatdetect --duration=1800 --threshold=100` (30 min, catches 1-per-30-min SMI) · (b) add `intel_pstate=disable processor.max_cstate=0 intel_idle.max_cstate=0 idle=poll` to the RT tuned entry → `soak 30` (kills all C-states and frequency transitions; heat/power cost) · (c) BIOS: disable C-states / SpeedStep / package C-state | if (b) removes the 0.4–0.9 ms events → power management is the cause → decide between `idle=poll` on isolated cores only (`cpuidle.off` per core via sysfs) and the thermal cost | 30 min each |
| 1-6b | 🔴 | [x] **Power-management test (root cause) — CAUSE CONFIRMED = C-state/frequency transitions.** RT tuned + `idle=poll intel_pstate=disable max_cstate=0`, same 30-min load: >50 µs events **102 → 0**, worst 467 → **43 µs**, all 8 cores 16–43 µs incl. core 6 (so core 6 was the slowest *wake-up*, not a defect; dropping it is unnecessary). `max_cstate=1` alone was insufficient (package C-states and HWP frequency transitions remained; the 8-h '800 MHz' clock log was a sysfs placeholder under HWP). Cost: all cores poll at max clock (+3–4 °C package, throttle 0). Next: per-core application to isolated cores only (sysfs cpuidle disable + per-CPU governor, or `intel_pstate=passive`), then re-soak → A-3 unit. `measurements/2026-09-15_rt_poll/` | add a GRUB entry = RT tuned + `idle=poll intel_pstate=disable processor.max_cstate=0 intel_idle.max_cstate=0` (extend `tools/rt/a1_rt.sh tune`, e.g. `tune rt-poll`) → **ask before rebooting** → boot it → `soak 30` → compare per-thread 200 µs+ counts against the 30-min formal run (24) and the 8-h run (~250/core) | events vanish → cause = C-state/frequency transitions → decide whether to pay the heat/power cost (cores at 100 % poll, no 800 MHz idle) or apply only to isolated cores; events stay → SMI (`hwlatdetect --duration=1800 --threshold=100`) / hardware stall path | 30 min + reboot |
| 1-6c | 🔴 | [~] **Isolated-cores-only PM lock (C)** — is the cause per-core or package-wide? **C (1st try, governor=performance = max-turbo request) FAILED: worst 995 µs, >400 µs ×1,660, avg 10 µs — 8 polling threads at max turbo hit the platform's RAPL PL1 = 35 W → hardware kept re-adjusting the clock (3.47–3.69 GHz), far more transitions than A. B was good because acpi-cpufreq pinned 2.1 GHz base. → C2: pin isolated cores at base 2.1 GHz.** **C2 RESULT: clocks held at 2,095–2,100 MHz for the whole run, yet worst 899 µs, >400 ×143, 200+ ×204 — B not reproduced. Only remaining difference vs B = housekeeping cores' HWP frequency scaling → the stall is a package-level (shared voltage/power) transition triggered by busy housekeeping cores; `isolcpus` cannot shield it. → C3: freeze ALL cores at base (no_turbo=1 + per-CPU min=max=base under HWP, runtime, no reboot). Cost = no turbo for perception → weigh with 2-1.** `measurements/2026-09-15_iso_pm/` | normal RT tuned boot → `sudo bash tools/rt/iso_pm.sh on` (CPU 8–15 only: governor=performance → HWP min=max, cpuidle states disabled; housekeeping untouched) → `soak 30`. Verify pinning from the soak's MSR clock lines (APERF/MPERF ≈ 4.8–4.9 GHz, not 800) | ≈ B (43 µs, 0 × >50 µs) → per-core cause → **this is the production setting** (becomes the A-3 unit); ≈ A (hundreds of µs) → package C-state from housekeeping cores → a global knob is needed | 30 min, no reboot |
| 1-6d | 🔴 | [x] **C3 — freeze ALL cores at base clock (runtime)** — **RESULT: worst 837 µs, >400 ×119, 200+ ×164 (uniform over 8 cores) → B NOT reproduced.** Tuned boots only ever had the POLL idle state, so C-states were never the cause; freezing HWP requests is not the same as HWP off. Remaining B-vs-C3 differences are boot-time only (HWP enabled at all; intel_idle/cpuidle idle path) → 1-6e bisection. | RT tuned boot → `sudo bash tools/rt/iso_pm.sh all` (no_turbo=1, every CPU governor=performance + min=max=its base_frequency: P-cores 2.1 GHz, E-cores their own; C-states off) → `soak 30` | ≈ B (≤ ~50 µs, 0 × >50 µs) → cause = package-level clock transitions from housekeeping cores, **production setting = all-core base-clock freeze** (goes into the A-3 unit); cost = no turbo for perception → check with 2-1. Still bad → something else global (reboot into B-style entry and bisect `idle=poll` vs `intel_pstate=disable`) | 30 min, needs tuned boot |
| 1-6e | 🟢 | [x] **Bisect B — done 2026-09-16.** B1 (HWP off only) 640 µs / >50 ×94; B2 (`idle=poll` only) 813 / ×192 — both A-family. **B-repeat (both flags, unattended): worst 157 µs, >50 ×2, >200 ×0, >400 ×0 → B reproduces.** Conclusion: `idle=poll` AND `intel_pstate=disable` are **jointly required** (interaction; neither alone works). 9/15 "cause = C-state/clock transition" wording retracted — correct statement is "cpuidle framework path + intel_pstate/HWP path together". **Production boot = `a1-bsw-rt-poll`.** acpi-cpufreq keeps turbo (`boost=1`, cores reach 2.5 GHz), so the "no turbo" cost note was wrong; cost = all-core idle polling (+3–4 °C) and no housekeeping P-state downshift. | B1 `tune rt-nohwp`; B2 `tune rt-idlepoll`; B-repeat `grub-reboot a1-bsw-rt-poll` → each `soak 30` | done | 3 × (reboot + 30 min) |
| 1-1b | 🟢 | [x] **D1 final — done 2026-09-16 (unattended): generic 6.8 + isolation + `preempt=full` + `idle=poll intel_pstate=disable max_cstate=0` → worst 514 µs, >50 ×1,889, >200 ×21, >400 ×4. RT + same flags (B-repeat) = 157 / 2 / 0 / 0. → KEEP RT.** PM flags help generic too (935→514) but preempt=full leaves a 50–200 µs tail on all 8 cores (0.1 % > 100 µs vs budget 0.01 %). Production boot = `a1-bsw-rt-poll`. | `tune generic-poll` → `soak 30` | done — `measurements/2026-09-16_generic_poll/` | 30 min + reboot |
| 1-6f | 🟢 | [x] **eco + housekeeping governor — done 2026-09-16.** ④ SMT-off rejected (498 µs). ② `eco 800` (isolated 8 thr @ 800 MHz): 67 µs / >50 ×1. ⑤ housekeeping governor: `schedutil` **does not lower clocks on PREEMPT_RT** (RT-runnable→max rule; 16k IRQ/s as FIFO threads) → `ondemand` works: idle **35 → 14.2 W**, 45 → 37 °C. 30-min soak with eco 800 + hk ondemand (unattended): **worst 27 µs, >50 ×0, >200 ×0** — best of all runs. **Final production = `a1-bsw-rt-poll` + `eco 800` + `hk ondemand`.** Housekeeping clock transitions proven irrelevant to isolated latency (C3 fixed / this run transitioning). | `measurements/2026-09-16_eco/README.md` | → 8 h with this setting, then A-3 unit | 3 × 30 min |
| 1-7 | 🟢 | [x] **8 h unattended with production setting (rt-poll + eco 800 + hk ondemand), 2026-09-16 22:22 → 06:22.** worst **920 µs**, avg 7.0, >50 ×81, >100 ×51 (0.000022 %), >200 ×31, >400 ×26 = **3 episodes** (all 8 cores simultaneous: 00:07:14, 06:12:18 ×2). vs 9/14 8 h: >200 1,424 → 31 (46×), episodes 65 → 3. **D4 met** (≤1 ms, ≤0.01 % >100 µs) with 8 % headroom on worst. Episode 2/3 coincide with WiFi reconnect + PackageKit refresh (NM dispatcher — not covered by soak_guard); episode 1 near a rtw LPS message (238/boot, weak). | `measurements/2026-09-17_8h_prod/README.md` | Next: 8 h again under competition conditions (rfkill wifi, PackageKit/snapd/timers stopped) to see if the 3 episodes vanish; then A-3 unit | 8 h |
| 1-8 | 🟢 | [x] **Positive control 2026-09-17 14:08–14:38**: production setting + `provoke_net.sh` (WiFi off/on + `pkcon refresh` every 2 min) → worst **892 µs, >400 ×1,127 = 130 episodes** (vs 0 yesterday, same setting). **Causal: network/package activity → global 8-core stalls.** Phase rates: WiFi-off 0.9/min, reconnect 3.8, pkcon 3.2, post-pkcon idle **5.8**/min. Mechanism still open (broadcast IPI candidates) → `ipi_watch.sh`. | `measurements/2026-09-17_provoke/README.md` | next: 5-min `ipi_watch` + evening 8 h under `soak_guard on race` | 30 min |
| 1-9 | 🟡 | [~] **HW-layer 3-min contrasts 2026-09-17 (post-reboot): T0 bare / T0c ondemand-only / T0b eco-only / T0d production — all with provoke_net → 0 episodes each** (14:08 pre-reboot same setting = 130). Governor/eco/power-transient hypotheses rejected (ondemand hit 4.1 GHz, 72 W, EDP log bits, still 0). Enabling condition = accumulated long-uptime state (unknown), network activity amplifies ×5. New operating rule candidate: fresh boot before driving. | `measurements/2026-09-17_hw_quick/README.md` | evening 8 h race → **do not reboot** → morning 3-min provoke in same boot to test uptime dependence | 4 × 3 min |
| 1-10 | 🔴 | [x] **8 h under competition conditions (WiFi/WWAN off, PackageKit/snapd/timers stopped), 2026-09-17 20:13 → 04:13: worst 889 µs, >400 ×1,181 = 70 episodes — 23× WORSE than the 9/16 8 h (3). Network hypothesis REJECTED as cause** (it only amplified an already-stalling machine on 9/17 14:08). Episodes from hour 0; MSR limit reasons show nothing (PL1 steady, 35 W, HK 2.2–2.6 GHz). Confounders introduced by race mode: screen kept unlocked all night (the quiet 9/16 8 h had the screen locked), snapd-desktop-integration 1 Hz error loop (snapd stopped). Morning 3-min (uptime 17 h): 6 events → stalling state persists. | `measurements/2026-09-18_8h_race/README.md` | **Next: switch-off tests S0–S5 while the machine is in the stalling state** (restart snapd-desktop loop, lock screen/DPMS, unplug USB WiFi, no-GPU, fresh boot) | 8 h + 3 min |
| 1-11 | 🟢 | [x] **Switch-off tests 2026-09-18 (machine in stalling state): S0 baseline 322 µs / >200 ×11; S1 kill snapd-desktop loop → 482 µs / >200 ×5 (still stalling); S2 lock screen + DPMS off → worst 19 µs, all zero.** Display/compositor activity (NVIDIA 470 on RT) is the enabling condition; network only amplifies. Consistent with 9/16 8 h (screen locked → 3) vs 9/17 8 h (screen on → 70). Operating rule: display inactive while driving (+ fresh boot, WiFi off, updates off). `soak_guard on race` now auto-locks after 60 s and no longer stops snapd. | `measurements/2026-09-18_switch/README.md` | final 8 h under this rule (overnight, parallel with Phase C) | 3 × 3 min |
| 1-5 | 🟡 | [ ] Cold-boot drive profile ×3 | cold boot into RT tuned → `sudo bash tools/rt/a1_rt.sh drive` (load 10 min → idle 5 min → load 10 min) → `drive-report` | worst per 10-min run recorded incl. start-up transients; compare run1 vs run2 (warm) | 3 × 30 min (daytime) |

Test-duration decision (2026-09-14): a 60-min soak was **rejected** — with ~1 event/30 min it narrows the rate estimate only from 223× to 30× (95 % CI); the 8-h run (≈16 events → 3×) is what gives a usable number. Load profile: synthetic (glmark2 80 % + stress-ng) is the **upper bound**; the real stack (GNSS/INS + 1 LiDAR + 1 camera, localization-only) is lighter on CPU/GPU but adds **sensor Ethernet traffic** which no test has covered yet → add a UDP stream to the synthetic profile, then repeat with the real stack after Phase C.

## 5. Phase B/C — physical CAN bus, available now

| # | Pri | Check | How | Why | Effort |
|---|---|---|---|---|---|
| 5-1 | 🔴 | [ ] **can0 ↔ can1 physical loopback** | wire the two PEAK PCAN-PCIe FD channels with twisted pair + 120 Ω at both ends; `eait_tx.py` on can0, `eait_rx.py` on can1 | **a real CAN bus without the car** (bit timing, arbitration, error frames). Removes vcan0's "optimistic" limitation *today* | 1 h |
| 5-2 | 🟡 | [ ] Physical bus latency | TX vs RX timestamps in 5-1 | 500 kbps, 8-byte frame ≈ 0.26 ms wire time + driver; number goes into the budget | with 5-1 |
| 5-3 | 🟢 | [x] **Phase C slice end-to-end — done 2026-09-18.** 0x712 → `can_raw_bridge` → `/interface/can/read/raw` → `spd_decoder` → `/control/status/wheel`. hz 99.97–100.02 (both topics), `ros2 topic delay` avg 2 ms / max 2–3 ms, decode cross-checked against `cantools` on 50 live frames + 4 unit tests (0 mismatches). `colcon test` (flake8/pep257/copyright/xmllint) all pass. Run without A-3 recipe (no isolated core / no SCHED_FIFO) — next: re-measure with `cpu_affinity`/`rt_priority` launch args. | `ros2_ws/`, `can_stack_development.md` §5.C | §3 V-model integration row (partially — A-3 re-measure pending) | done |
| 5-3c | 🟢 | [x] **A-3 recipe re-measure — done and CONFIRMED 2026-09-18.** `cpu_affinity=8` alone, `+rt_priority=80` without root (FIFO denied, `mlockall` succeeded), and finally **+ real `SCHED_FIFO 80`** (user ran `sudo chrt -f 80 sudo -u ailab ros2 launch ...`, verified live with `chrt -p <pid>` → policy=SCHED_FIFO, priority=80) all give the same ~1–2 ms delay as no isolation at all — **isolated-core placement + real SCHED_FIFO does not move this slice's latency.** Gap vs `eait_tx.py`'s 5–9 µs with the same recipe → DDS publish/subscribe path dominates, not scheduling; A-3 belongs on `can_guard`'s raw-SocketCAN path (Phase D), not this ROS2 bridge. Caught + fixed a real bug: `cpu_affinity:=8` via `ros2 launch` was written as a YAML int, colliding with the declared string parameter (`InvalidParameterTypeException`) — fixed with `launch_ros.parameter_descriptions.ParameterValue(..., value_type=str/int)` in both launch files. | `ros2_ws/src/a1_can_bridge/launch/*.launch.py`, `can_stack_development.md` §5.C, `ros2_ws/README.md` | done | done |
| 5-3b | 🟢 | [x] **EPS/ACC decoders (0x710/0x711) — done 2026-09-18.** `eps_decoder`/`acc_decoder`, bit-packed fields via generic `dbc_bits.unpack()` (byte-aligned struct wasn't enough — 1–16-bit fields cross byte boundaries). First E2E(alive_count) check: `e2e.AliveCounter` tracks `EPS_Alive_Cnt`/`ACC_Alive_Cnt` rollover continuity, reports skip counts to `/diagnostics` at 1 Hz. hz 49.99/99.98 (target 50/100), `/diagnostics` level=OK/total_skips=0 on clean vcan0. Caught + fixed a real bug during lint cleanup: a test's assert-message variable named `msg` shadowed the outer `cantools.Message` object across loop iterations. | `ros2_ws/src/a1_can_bridge/{eps,acc}_decoder.py`, `dbc_bits.py`, `e2e.py`, `can_stack_development.md` §5.C | §2 category-C "E2E(alive_count)" row | done |
| 5-3d | 🟢 | [x] **IMU decoder (0x713) — done 2026-09-18.** `imu_decoder`, all 4 signals byte-aligned (16 bits each at 0/16/32/48) → decoded via `struct.unpack_from('<hHhH', ...)`, same style as `spd_decoder`. No `Alive_Cnt` in this message (4 signals fill all 8 bytes) → not an E2E-check target, documented as such. hz 99.97 (target 100, 0x713 period 10 ms), delay avg 2 ms, 50 live frames cross-checked against `cantools` (0 mismatches) + 4 unit tests. All 28 `colcon test` pass across the whole `a1_can_bridge` package. | `ros2_ws/src/a1_can_bridge/imu_decoder.py`, `can_stack_development.md` §5.C | done | done |
| 5-4 | 🟡 | [ ] Fault injection on the physical bus | frame drop, stuck `Alive_Cnt`, out-of-range values (board Phase E) | H8 / E2E items | after Phase D |

---

## 6. Operating rules — verify before the competition

| # | Pri | Check | How |
|---|---|---|---|
| 6-1 | 🟡 | [ ] GRUB fallback works | boot RT tuned entry → hard power cut → confirm next boot is generic |
| 6-2 | 🟡 | [ ] Package hold in place | `apt-mark showhold` lists kernel + nvidia packages |
| 6-3 | 🟡 | [ ] Wireless off during driving | `rfkill block all`, then `soak 30` — also tells whether the WiFi `lps state` bug contributed to spikes |
| 6-4 | 🟢 | [ ] Remove `5.15.0-1114-realtime` | avoid wrong GRUB pick |
| 6-5 | 🟡 | [ ] DKMS rebuild check after any driver/kernel change | `dkms status`; GPU visible after reboot |

---

## 7. Safety layer on the main PC — MCU deferred (decision 2026-09-14)

Decision: **no MCU for this competition.** The safety layer runs on the main PC. This section replaces the MCU plan.

### 7.1 What this means, honestly

- Layers ② (kernel) and ① (hardware) stay shared. The 0.4 ms tail and the NVIDIA kernel BUG on GPU-process crash **remain**. → D4 is effectively forced: adopt "worst ≤ 1 ms, 99.99 % ≤ 100 µs" for the PC.
- The **only hardware backstop is the vendor fail-safe**: 0x156 silent ≥ 1000 ms → vehicle AEB. At 200 km/h that is ~55 m. This is what protects the car when the PC/kernel is dead.
- H8's ≤ 100 ms target is therefore reachable **only while the PC is alive**. Consequence: the software fallback must be the **most survivable thing on the PC** (§7.2).
- E-stop: the PC has no GPIO. Hard E-stop = a physical switch that **opens the PC→vehicle CAN line** → vendor AEB within 1 s. ⚠️ Confirm what remote E-stop the competition / DBW kit already provides. Optional fast path: USB button → guard sends decel within one cycle.

### 7.2 Design rules — the "software MCU" process (`can_guard`)

| Rule | Detail |
|---|---|
| One tiny separate process owns vehicle-facing TX | `can_guard` sends 0x156/0x157 and owns `Alive_Cnt`. **Not inside the ROS2 control node.** Raw SocketCAN; **no ROS2/DDS in the hot loop** — commands arrive via a small shared-memory ring (seq + timestamp) |
| Placement | isolated core, `SCHED_FIFO 90` (above control 80), `mlockall`, no malloc, no disk I/O, no GPU, static buffers |
| Control watchdog | no fresh command for **T ms** (proposal 50 ms) → hold steering, controlled decel ramp, En=0 at stop |
| Plausibility | range / rate limits / alive continuity (3-7) live here |
| Survivability | systemd `Restart=always` + `WatchdogSec=` (sd_notify heartbeat) → a hung guard is restarted in < 1 s; `OOMScoreAdjust=-1000`; perception under a cgroup memory limit so an OOM kills perception, never the guard |
| Startup | guard starts first, **in safe-state (En=0)**; every restart begins in safe-state |
| GPU-crash rule | perception-process death is a safety event: guard notices a missing perception heartbeat → degraded mode / decel per team policy |
| Logging | lock-free queue to a housekeeping thread only |

### 7.3 Tests for the PC-only safety layer (bench: PEAK can0 = PC TX, can1 = fake vehicle counting gaps)

| # | Pri | Test | Pass |
|---|---|---|---|
| P-1 | 🟢 | [x] `kill -9` the ROS2 control node — **PASS 2026-09-19 (SIL, vcan0, fake control node)** | first decel frame within one tick after T; `Alive_Cnt` continuous — transition logged at 55ms (T=50ms+1 tick), 0x156 Aliv_Cnt 101 frames continuous, 0x157 ACC_Cmd ramped 0.150→0.000 (cantools-verified). `safety/can_guard/sil_tests/p1_kill_control_node.py`. Real ROS2 node still pending — this used `fake_control_node.py`. |
| P-2 | 🟢 | [x] `kill -9` perception (GPU) under load — **PASS 2026-09-19 (SIL, vcan0, fake perception)** | guard TX period unaffected — record max jitter; `dmesg` BUG count — 0x156 gap avg 10.00ms (max 10.2–10.4ms) before/after kill, `ACTIVE → DEGRADED` logged, Aliv_Cnt continuous, 0 new dmesg BUG lines. `safety/can_guard/sil_tests/p2_kill_perception.py`. Real perception process still pending. |
| P-3 | 🔴 | [ ] `kill -9` the guard itself | systemd restarts it; **gap in 0x156 ≪ 1000 ms (target < 200 ms)**; fake vehicle never times out |
| P-4 | 🔴 | [ ] Hang the guard (`SIGSTOP`) | `WatchdogSec` restart; same gap metric |
| P-5 | 🔴 | [ ] Plausibility: out-of-range / rate / stuck alive from control | rejected, counted, safe value forwarded |
| P-6 | 🔴 | [ ] CAN line cut (hard E-stop emulation): unplug can0 | fake vehicle sees silence (vendor-AEB path); guard sees TX errors / bus-off and recovers on reconnect |
| P-7 | 🟡 | [ ] OOM injection: perception allocates until OOM | guard survives, TX unaffected |
| P-8 | 🟡 | [ ] DDS storm: heavy topic traffic | guard unaffected (no DDS in loop) |
| P-9 | 🔴 | [ ] 8-h bench, full stack + guard | **zero spurious fallbacks**; TX period max recorded |
| P-10 | 🟡 | [ ] Cold boot order | guard transmitting safe-state before control/perception are up |
| P-11 | 🟡 | [ ] PC hard-reset time | know the number — during it only the vendor AEB protects |

### 7.4 Residual risks accepted (write into the report)

- Kernel panic / power loss / hard hang → **no software fallback**; the vehicle stops via vendor AEB after 1 s (~55 m at 200 km/h).
- NVIDIA kernel BUG on GPU-process crash may delay the guard by an unknown amount → measured in P-2.
- No hardware error detection (no lockstep / ECC).
- MCU revisited after the competition.

### 7.5 What changes elsewhere in this checklist

- D5 dropped. D4: adopt "worst ≤ 1 ms, 99.99 % ≤ 100 µs" for the PC.
- Decision-matrix "conflict" cell: choose the kernel that keeps the **guard's TX** within budget (control safety > perception fps), then cut perception load.
- Phase D on Linux is now the **real** safety layer, not a prototype: build `can_guard` right after the Phase C slice, before widening decoders, then run P-1…P-6.
- Mandatory now: 1-1, 2-2, 2-3, 4-1, 4-2, and **2-7 becomes 🔴** (perception must preallocate GPU memory).

---

## 8. Minimum set before the meeting (~2 h)

1. [x] 1-1 generic control group (40 min) → D1 = **keep RT** (pending 2-1)
2. [x] 1-3 IPI observation (5 min) → GPU confirmed as the IPI source (4/s at 4K, 0 idle)
3. [ ] 3-1 / 3-2 NN size + CPU WCET (30 min) — ⚠️ only if a candidate model exists; otherwise mark "pending model selection"
4. [ ] 5-1 can0 ↔ can1 physical loopback (1 h) → "real bus testing has started"
5. [~] Agree the `can_guard` design (§7.2) and residual risks (§7.4); schedule P-1…P-6 right after the Phase C slice — **design + main loop + P-1/P-2 done 2026-09-19** (`can_stack_development.md` §5.D, `safety/can_guard/`: `CommandChannel`/`HeartbeatChannel` seqlock, plausibility, state machine, command policy, 0x156/0x157 encoder, sd_notify — 51/51 unit tests pass; `can_guard.py` main loop wired and live-verified on vcan0; P-1/P-2 PASS via real `SIGKILL` on fake control/perception processes). Real ROS2 control node + perception integration, rate-limit values, and P-3+ bench tests still open; team must still confirm/agree on the design.

## 9. Open items to confirm with the team / organizer

- Control model: architecture, inputs (pose+map vs perception outputs vs images), size — drives all of §3.
- `Turn_Signal` mapping (DBC 2=left/4=right vs PDF opposite), `KIAPI_1~6` purpose, radar actually fitted?, real period of `status/acc`·`wheel` (10 vs 20 ms).
- Budget redefinition (D4). MCU deferred (D5) — confirm the team accepts the residual risks in §7.4.
- What remote E-stop the competition / DBW kit provides (PC has no GPIO).

### 9.1 Questions to the organizer (KIAPI) — answers change §7.4 residual risks

DBC hints: nodes `EAIT` (vendor DBW controller — itself an MCU with the 1000 ms fail-safe), `USER` (our PC, sends 0x156/0x157 **directly**), `KIAPI` (organizer node, 0x124–0x129, no signals defined — role unknown).

**A. Topology**
1. Is the main PC on the same bus as EAIT, or is there an organizer device in between?
2. Connector, 500 kbps confirmed, bus already terminated at both ends?
3. Is the DBW bus separate from the OEM vehicle bus?
4. May we attach our second channel (`can1`) listen-only?

**B. EAIT safety behaviour**
5. `USER_CAN_ERR` condition: 0x156 only (1000 ms)? 0x157 too? Latched? Recovery procedure?
6. On trigger: decel value (fixed / `AEB_decel_value`?), steering held or released, state after stop?
7. Does EAIT validate our commands (EPS_Cmd range/rate, ACC_Cmd limits, Alive_Cnt discontinuity)?
8. What triggers `Override_Status` in driverless operation?

**C. Organizer safety devices (most important)**
9. What is the KIAPI node (0x124–0x129)? Remote E-stop, supervisor, logger? Anything we must receive/handle?
10. Is there a remote E-stop / kill switch? Reaction time and mechanism (power cut / AEB command / EPS release)?
11. Organizer joystick SW vs our autonomy: who wins when both send 0x156? Is there a mode-switch message on the bus? (H7)
12. What does the PC receive when the safety operator intervenes?

**D. Rules**
13. May we add a node (e.g. MCU) to the bus? Harness modification limits?
14. PC power budget (12 V current), rules for PC reboot while on track.

| Answer | Effect on our design |
|---|---|
| KIAPI = remote E-stop, < 100 ms | §7.4 residual risk shrinks a lot; "PC dead → 1 s AEB" becomes "operator kill → immediate" |
| KIAPI = logger only | current plan stands (vendor AEB is the only backstop) |
| EAIT validates commands | `can_guard` clamps become a second layer → set ours more conservative |
| EAIT does not validate | `can_guard` clamps are the **only** check → P-5 mandatory |
| `USER_CAN_ERR` latches | design the recovery procedure (else the lap is over) |
| joystick and autonomy share 0x156 | bus-conflict risk → agree the mode-arbitration mechanism (H7) |
| adding nodes forbidden | MCU question closed permanently; PC-only confirmed |
| PC not directly on the bus | the intermediate device's latency/timeout adds to our budget → re-measure |
