#!/usr/bin/env python3
"""A1 BSW 인터페이스 보고서(2026-11-05) 그림 생성.

사용:  python3 make_figs.py <repo_root> <out_dir>
  <repo_root>/measurements/2026-11-04_drive_log/tables/*.csv 를 읽어 <out_dir>/figs/*.png 를 만든다.
  손으로 넣은 숫자는 (1) 현재 track.yaml 표(<repo_root>/ros2_ws/src/a1_control_adapter/config/track.yaml 에서 읽음),
  (2) 10-07 리프트·11-03 정지 시험 참고값(출처 주석) 뿐이다.
의존성: python3, numpy, matplotlib. 글꼴: Noto Serif CJK(없으면 Noto Sans CJK).
"""
import csv
import os
import re
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch, Rectangle

ROOT = sys.argv[1] if len(sys.argv) > 1 else "."
OUT = sys.argv[2] if len(sys.argv) > 2 else "."
TAB = os.path.join(ROOT, "measurements/2026-11-04_drive_log/tables")
TRACK = os.path.join(ROOT, "ros2_ws/src/a1_control_adapter/config/track.yaml")
FIG = os.path.join(OUT, "figs")
os.makedirs(FIG, exist_ok=True)

# ── 스타일: IEEE 풍 흑백 ────────────────────────────────────────────
for p in ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
          "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
    if os.path.exists(p):
        font_manager.fontManager.addfont(p)
        FONT = font_manager.FontProperties(fname=p).get_name()
        break
else:
    FONT = "serif"
plt.rcParams.update({
    "font.family": FONT, "font.size": 8.5, "axes.unicode_minus": False,
    "axes.linewidth": 0.7, "axes.edgecolor": "black", "axes.labelsize": 8.5,
    "xtick.direction": "in", "ytick.direction": "in", "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "xtick.top": True, "ytick.right": True,
    "grid.color": "0.8", "grid.linestyle": ":", "grid.linewidth": 0.6,
    "legend.frameon": False, "legend.fontsize": 7.5,
    "savefig.dpi": 220, "savefig.bbox": "tight", "savefig.pad_inches": 0.03,
})
K, G1, G2, G3 = "black", "0.35", "0.6", "0.85"


def rd(name):
    with open(os.path.join(TAB, name), encoding="utf-8") as f:
        return list(csv.DictReader(l for l in f if not l.startswith("#")))


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def yaml_list(key):
    """track.yaml 의 '  key: [a, b, c]' 한 줄 리스트 읽기(pyyaml 없이)."""
    txt = open(TRACK, encoding="utf-8").read()
    m = re.search(r"^\s*%s:\s*\[([^\]]*)\]" % re.escape(key), txt, re.M)
    return [float(v) for v in m.group(1).split(",")] if m else []


def save(fig, name):
    fig.savefig(os.path.join(FIG, name))
    plt.close(fig)
    print("wrote", name)


# ── Fig.1 시스템 구성 ───────────────────────────────────────────────
def box(ax, x, y, w, h, text, fill="white", lw=0.9, ls="-", fs=8, bold=False):
    ax.add_patch(Rectangle((x, y), w, h, fc=fill, ec=K, lw=lw, ls=ls))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
            fontweight="bold" if bold else "normal", linespacing=1.35)


def arrow(ax, x0, y0, x1, y1, text="", dy=0.12, fs=6.8, style="-|>", ls="-", tx=None):
    ax.annotate("", (x1, y1), (x0, y0), arrowprops=dict(arrowstyle=style, lw=0.8, color=K, ls=ls,
                                                       shrinkA=0, shrinkB=0, mutation_scale=8))
    if text:
        ax.text(tx if tx is not None else (x0 + x1) / 2, (y0 + y1) / 2 + dy, text, ha="center",
                va="bottom", fontsize=fs, style="italic")


def fig_architecture():
    fig, ax = plt.subplots(figsize=(7.0, 3.1))
    ax.set_xlim(0, 16); ax.set_ylim(0, 6.6); ax.axis("off")
    fs = 6.9
    ax.add_patch(Rectangle((0.05, 2.7), 7.2, 3.8, fc="none", ec=K, lw=0.7, ls=(0, (4, 3))))
    ax.text(0.2, 6.22, "ROS2 Humble (모델 팀 + 어댑터)", fontsize=6.6, style="italic", va="center")
    ax.add_patch(Rectangle((7.7, 2.7), 3.1, 3.8, fc="none", ec=K, lw=0.7, ls=(0, (4, 3))))
    ax.text(7.82, 6.22, "격리 RT 코어 8–15", fontsize=6.6, style="italic", va="center")

    box(ax, 0.25, 4.0, 2.3, 1.6, "경로 추종기\n(follower·MPC)", fs=fs)
    box(ax, 4.1, 4.0, 2.6, 1.6, "a1_control_\nadapter\n타이어° → 핸들°\nm/s² → %", fill="0.93", fs=6.2)
    arrow(ax, 2.55, 4.8, 4.1, 4.8, "/control/\ntarget", dy=0.08, fs=6.2)
    box(ax, 8.0, 4.0, 2.5, 1.6, "can_guard\nFIFO 90\n20 ms 주기\n상태기계", fill="0.82", fs=6.3)
    arrow(ax, 6.7, 4.8, 8.0, 4.8, "shm\n+HB", dy=0.08, fs=6.0, tx=7.2)
    box(ax, 11.75, 4.0, 1.3, 1.6, "Kvaser\nLeaf\nch 0", fs=6.6)
    arrow(ax, 10.5, 5.15, 11.75, 5.15, "0x210\n20 ms", dy=0.06, fs=6.2)
    arrow(ax, 13.05, 5.15, 13.6, 5.15)
    box(ax, 13.6, 3.7, 2.3, 2.2, "차량 ECU\n조향·제동·구동\nby-wire", fs=6.4)
    ax.annotate("", (10.5, 4.35), (11.75, 4.35), arrowprops=dict(arrowstyle="-|>", lw=0.8, color=K,
                mutation_scale=8, shrinkA=0, shrinkB=0))
    ax.annotate("", (13.05, 4.35), (13.6, 4.35), arrowprops=dict(arrowstyle="-", lw=0.8, color=K, shrinkA=0, shrinkB=0))
    ax.text(11.13, 3.62, "0x200/0x201\n21 ms", ha="center", va="top", fontsize=6.2, style="italic")

    box(ax, 4.1, 2.85, 2.6, 0.95, "a1_can_bridge\n/control/status/*", fs=5.8)
    ax.plot([12.4, 12.4, 7.0], [4.0, 3.1, 3.1], color=G1, lw=0.6, ls=(0, (3, 2)))
    ax.annotate("", (6.7, 3.1), (7.0, 3.1), arrowprops=dict(arrowstyle="-|>", lw=0.6, color=G1,
                mutation_scale=7, shrinkA=0, shrinkB=0))
    ax.annotate("", (1.4, 4.0), (4.1, 3.32), arrowprops=dict(arrowstyle="-|>", lw=0.6, color=G1,
                ls=(0, (3, 2)), mutation_scale=7, connectionstyle="angle,angleA=180,angleB=-90,rad=0"))
    ax.text(2.55, 3.05, "감시 등급", fontsize=6.0, color=G1, ha="center", va="top")

    box(ax, 13.6, 0.35, 2.3, 1.55, "원격 앱\n0x156/0x157\n10 ms", ls=(0, (4, 2)), fs=6.2)
    arrow(ax, 14.75, 1.9, 14.75, 3.7)
    ax.text(14.9, 2.8, "원격 우선", fontsize=6.3, style="italic", ha="left", va="center")
    ax.annotate("", (9.25, 4.0), (13.6, 1.1), arrowprops=dict(arrowstyle="-|>", lw=0.6, color=G1,
                ls=(0, (3, 2)), mutation_scale=7, connectionstyle="angle,angleA=180,angleB=-90,rad=0"))
    ax.text(11.3, 0.78, "ESTOP 감지 (--estop-can remote)", fontsize=6.1, color=G1, ha="center")
    box(ax, 0.25, 0.35, 2.3, 1.55, "GNSS/INS\n+ LiDAR\n100 Hz", ls=(0, (4, 2)), fs=6.2)
    arrow(ax, 1.4, 1.9, 1.4, 4.0)
    save(fig, "fig1_architecture.png")


def fig_states():
    fig, ax = plt.subplots(figsize=(7.0, 2.5))
    ax.set_xlim(0, 16.3); ax.set_ylim(0, 5.2); ax.axis("off")
    W = 2.5
    spec = {"INIT": (0.15, 3.25, 1.4, "silent\n(0x210 미송신)", "white"),
            "ACTIVE": (3.55, 3.25, 1.4, "명령 통과\n+ 클램프", "0.88"),
            "HOLDING": (7.0, 3.85, 1.2, "제어 노드 끊김\n(50 ms)", "white"),
            "DEGRADED": (7.0, 2.2, 1.2, "인지 HB 끊김\n(0.5 s)", "white"),
            "STOPPED": (10.9, 3.25, 1.4, "정지 유지\n(재시작으로 해제)", "white"),
            "ESTOP": (10.9, 0.1, 1.4, "래치 · 최우선", "0.72")}
    for k, (x, y, h, s, fill) in spec.items():
        ax.add_patch(Rectangle((x, y), W, h, fc=fill, ec=K, lw=0.9))
        ax.text(x + W / 2, y + h * 0.72, k, ha="center", va="center", fontsize=7.6, fontweight="bold")
        ax.text(x + W / 2, y + h * 0.32, s, ha="center", va="center", fontsize=5.9, linespacing=1.2)

    def ar(a, b, ls="-", c=K):
        ax.annotate("", b, a, arrowprops=dict(arrowstyle="-|>", lw=0.8, color=c, ls=ls, mutation_scale=8,
                                             shrinkA=0, shrinkB=0))
    ar((2.65, 3.95), (3.55, 3.95)); ax.text(3.1, 4.75, "첫 유효\n명령", fontsize=5.9, ha="center", va="center")
    ar((6.05, 4.3), (7.0, 4.45)); ar((6.05, 3.6), (7.0, 2.8))
    ar((9.5, 4.45), (10.9, 4.3)); ar((9.5, 2.8), (10.9, 3.6))
    ax.text(10.2, 3.75, "≤3 km/h\n또는 2 s", fontsize=5.6, ha="center", va="center")
    ar((4.8, 3.25), (10.9, 0.8))
    ax.text(6.3, 1.45, "앱 0x156 En · estop.py · SIGUSR1\n(모든 상태에서)", fontsize=5.9, ha="center", va="center")
    ar((11.9, 3.25), (11.9, 1.5), ls=(0, (3, 2)))
    ar((12.4, 1.5), (12.4, 3.25), ls=(0, (1, 1.5)), c=G1)
    ax.text(12.55, 2.38, "해제: 요청 off\n+ ≤ 3 km/h", fontsize=5.6, color=G1, va="center")
    ax.text(13.6, 3.95, "정지 계열 출력\n가속 0 · 조향 고정\n브레이크 30→60 %\nauto 비트 유지", fontsize=5.9,
            va="center", linespacing=1.25)
    ax.text(13.6, 0.8, "ESTOP 출력\n가속 0 · 조향 고정\n브레이크 30→82 %\n(40 %/s), auto 유지", fontsize=5.9,
            va="center", linespacing=1.25)
    save(fig, "fig2_states.png")


# ── Fig.3 조향: 핸들 → 유효 타이어각 ─────────────────────────────────
def fig_steer():
    r = rd("steer_ratio_gnss.csv")
    w = np.array([fnum(x["wheel_mean_deg"]) for x in r]); t = np.array([fnum(x["tire_mean_deg"]) for x in r])
    lo = np.array([fnum(x["tire_p10"]) for x in r]); hi = np.array([fnum(x["tire_p90"]) for x in r])
    n = np.array([fnum(x["n"]) for x in r])
    fit = {x["item"]: x for x in rd("steer_ratio_fit.csv")}
    off = fnum(fit["fit |wheel|<40: wheel = a*tire + b"]["intercept_wheel_deg"])
    kr = fnum(fit["fit right (wheel<-3)"]["slope_wheel_per_tire"]); br = fnum(fit["fit right (wheel<-3)"]["intercept_wheel_deg"])
    tm = yaml_list("tire_deg"); wm = yaml_list("wheel_deg")

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(7.0, 2.75), gridspec_kw=dict(width_ratios=[1.55, 1]))
    ax.plot(wm, tm, color=G1, ls="--", lw=0.9, label="현재 steer_map (÷7.5, 0 점 0°)")
    xx = np.linspace(-150, 0, 50)
    ax.plot(xx, (xx - br) / kr, color=K, lw=0.7, ls=(0, (1, 1.2)), label="우측 회귀 (기울기 %.1f, 절편 %.1f°)" % (kr, br))
    big = n >= 100
    ax.errorbar(w[big], t[big], yerr=[t[big] - lo[big], hi[big] - t[big]], fmt="o", ms=3.6, color=K, mfc=K,
                capsize=1.8, lw=0.6, label="11-04 실측 (n ≥ 100, p10–p90)")
    ax.errorbar(w[~big], t[~big], yerr=[t[~big] - lo[~big], hi[~big] - t[~big]], fmt="o", ms=3.6, color=K,
                mfc="white", capsize=1.8, lw=0.6, label="11-04 실측 (n < 100)")
    ax.axvline(off, color=K, lw=0.5, ls="-.")
    ax.text(off + 3, -16.5, "직진 핸들각 %.1f°" % off, fontsize=6.8)
    ax.axhline(0, color=G3, lw=0.5)
    ax.add_patch(Rectangle((100, -21), 55, 42, fc="0.92", ec="none", zorder=0))
    ax.text(127, 14, "좌 100° 이상\n미측정", ha="center", fontsize=6.6, color=G1)
    ax.set_xlim(-155, 155); ax.set_ylim(-21, 21)
    ax.set_xlabel("조향 위치 0x200 [핸들 °] (+ = 좌)"); ax.set_ylabel("유효 타이어각 [타이어 °]")
    ax.grid(True); ax.legend(loc="upper left", fontsize=6.5)
    ax.set_title("(a) 핸들 → 유효 타이어각", fontsize=8.5)

    ratio = np.array([fnum(x["ratio_offset_corrected"]) for x in r])
    m = np.isfinite(ratio) & (np.abs(w - off) > 20)
    ax2.plot(w[m], ratio[m], "o", ms=3.6, color=K, mfc=[K if b else "white" for b in big[m]][0])
    for wi, ri, bi in zip(w[m], ratio[m], big[m]):
        ax2.plot(wi, ri, "o", ms=3.6, color=K, mfc=K if bi else "white")
    ax2.axhline(7.5, color=G1, ls="--", lw=0.9)
    ax2.text(-148, 7.0, "현재 7.5", fontsize=6.8, color=G1, va="top")
    ax2.set_xlim(-155, 120); ax2.set_ylim(0, 20)
    ax2.set_xlabel("조향 위치 [핸들 °]"); ax2.set_ylabel("유효 조향비 (오프셋 보정)")
    ax2.grid(True); ax2.set_title("(b) 유효 조향비 (|핸들 − 직진각| > 20°)", fontsize=8.5)
    fig.tight_layout(w_pad=1.5)
    save(fig, "fig3_steer_map.png")


# ── 가속 표 계산 (속도 대역 × % , n 가중 중앙값 평균) ───────────────
ACC_BINS = ["0-1", "1-50", "50-70", "70-75", "75-80", "80-85", "85-90", "90-95", "95-101"]
ACC_X = {"0-1": 0, "1-50": 25, "50-70": 60, "70-75": 72.5, "75-80": 77.5, "80-85": 82.5, "85-90": 87.5,
         "90-95": 92.5, "95-101": 97.5}
BANDS = {"0–10 km/h": ["1-5", "5-10"], "10–20 km/h": ["10-15", "15-20"], "20 km/h 이상": ["20-25", "25-35"]}


def accel_table():
    rows = rd("long_accel_map.csv")
    out = {}
    for b, sp in BANDS.items():
        for a in ACC_BINS:
            sel = [x for x in rows if x["acc_pct"] == a and x["speed_kph(true)"] in sp]
            n = sum(fnum(x["n(0.05s)"]) for x in sel)
            if n > 0:
                out[(b, a)] = (sum(fnum(x["n(0.05s)"]) * fnum(x["a_p50_mps2"]) for x in sel) / n, int(n))
    return out


def fig_accel():
    tab = accel_table()
    print("accel table (m/s², n):")
    for a in ACC_BINS:
        print("  %-7s" % a, "  ".join("%s=%s" % (b[:5], "%.2f(%d)" % tab[(b, a)] if (b, a) in tab else "—")
                                      for b in BANDS))
    am = yaml_list("accel_mps2"); ap = yaml_list("acc_pct")
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(7.0, 2.7), gridspec_kw=dict(width_ratios=[1.45, 1]))
    marks = {"0–10 km/h": ("o", K), "10–20 km/h": ("s", G1), "20 km/h 이상": ("^", G2)}
    for b in BANDS:
        xs = [ACC_X[a] for a in ACC_BINS if (b, a) in tab]
        ys = [tab[(b, a)][0] for a in ACC_BINS if (b, a) in tab]
        mk, c = marks[b]
        ax.plot(xs, ys, marker=mk, ms=3.8, color=c, mfc=c if b != "20 km/h 이상" else "white", lw=0.8, label=b)
    ax.plot(ap, am, "x--", color=K, ms=5, lw=0.8, label="현재 accel_map")
    ax.axhline(0, color=G3, lw=0.6)
    ax.axvspan(70, 80, color="0.9", zorder=0)
    ax.text(75, 0.78, "정지 출발\n문턱 70–80 %", ha="center", va="top", fontsize=6.5)
    ax.text(25, -0.92, "1–50 % 구간 평균", ha="center", fontsize=6.3, color=G1)
    ax.set_xlim(-3, 102); ax.set_ylim(-1.0, 0.85)
    ax.set_xlabel("가속 명령 [%] (브레이크 0)"); ax.set_ylabel("종가속도 [m/s²]")
    ax.grid(True); ax.legend(loc="upper left", fontsize=6.5)
    ax.set_title("(a) 가속 % → 가속도 (속도 대역별, 구간 중앙값)", fontsize=8.5)

    co = rd("long_coast.csv")
    fitrow = [x for x in co if x["group"].startswith("fit")][0]
    c0, c2 = fnum(fitrow["a_p50_mps2"]), fnum(fitrow["a_p25"])
    for g, mk, mf in (("remote", "o", K), ("auto", "s", "white")):
        sel = [x for x in co if x["group"] == g]
        v = [np.mean([float(s) for s in x["speed_kph"].split("-")]) for x in sel]
        y = [fnum(x["a_p50_mps2"]) for x in sel]
        lo = [fnum(x["a_p50_mps2"]) - fnum(x["a_p25"]) for x in sel]
        hi = [fnum(x["a_p75"]) - fnum(x["a_p50_mps2"]) for x in sel]
        ax2.errorbar(v, y, yerr=[lo, hi], fmt=mk, ms=3.6, color=K, mfc=mf, capsize=1.8, lw=0.6,
                     label={"remote": "원격 (0x201÷0.958)", "auto": "자율 (INS)"}[g])
    vv = np.linspace(0, 30, 50)
    ax2.plot(vv, c0 + c2 * (vv / 3.6) ** 2, color=G1, ls="--", lw=0.8, label="a = %.2f + %.4f·v² (v: m/s)" % (c0, c2))
    ax2.axhline(0, color=G3, lw=0.6)
    ax2.set_xlim(0, 30); ax2.set_ylim(-1.0, 0.32)
    ax2.set_xlabel("차속 [km/h]"); ax2.set_ylabel("타행 가속도 [m/s²]")
    ax2.grid(True); ax2.legend(loc="upper left", fontsize=6.0, ncol=2, columnspacing=0.8)
    ax2.set_title("(b) 타행 (가속 0 · 브레이크 0), p25–p75", fontsize=8.5)
    fig.tight_layout(w_pad=1.5)
    save(fig, "fig4_accel_map.png")


# ── Fig.5 출발 문턱 ─────────────────────────────────────────────────
def fig_launch():
    at = rd("launch_attempts.csv")
    rb = rd("launch_rolling_bins.csv")
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(7.0, 2.45), gridspec_kw=dict(width_ratios=[1, 1.25]))
    q = [x for x in at if not x["session"].startswith("remote")]
    rm = [x for x in at if x["session"].startswith("remote")]
    for grp, yv, lab in ((q, 1.0, "빠른 시험 · 자율"), (rm, 0.0, "원격")):
        for x in grp:
            lvl = fnum(x["acc_level"] if grp is q else x["acc_max_before_move"]); mv = x["moved"] == "1"
            ax.plot(lvl, yv + (0.12 if mv else -0.12), "o" if mv else "x", ms=4.2 if mv else 4.8,
                    color=K, mfc=K if mv else "none", lw=0)
    ax.axvspan(70, 80, color="0.9", zorder=0)
    ax.set_yticks([0, 1]); ax.set_yticklabels(["원격", "빠른 시험\n· 자율"])
    ax.set_ylim(-0.5, 1.5); ax.set_xlim(-3, 103)
    ax.plot([], [], "o", color=K, ms=4, label="출발 (위)"); ax.plot([], [], "x", color=K, ms=4.5, label="못 감 (아래)")
    ax.legend(loc="upper left", fontsize=6.5, ncol=1)
    ax.set_xlabel("정지 후 가속 명령 [%] (원격: 출발 전 최대)"); ax.grid(True, axis="x")
    ax.set_title("(a) 정지 출발 시도 (1 s 정지 → 가속)", fontsize=8.5)

    xs, ys, lo, hi, ns = [], [], [], [], []
    for x in rb:
        b = x["acc_bin"].split(" ")[0]
        a, c = (0, 0) if b == "0" else [float(v) for v in b.replace("–", "-").split("-")]
        xs.append((a + c) / 2); ys.append(fnum(x["accel_p50"])); ns.append(int(x["n_segments"]))
        lo.append(fnum(x["accel_p50"]) - fnum(x["accel_p25"])); hi.append(fnum(x["accel_p75"]) - fnum(x["accel_p50"]))
    for xi, yi, l, h, n in zip(xs, ys, lo, hi, ns):
        ax2.errorbar(xi, yi, yerr=[[l], [h]], fmt="o", ms=3.6, color=K, mfc=K if n >= 9 else "white",
                     capsize=1.8, lw=0.6)
        ax2.text(xi, yi + h + 0.04, str(n), fontsize=5.8, ha="center", color=G1)
    ax2.axhline(0, color=G3, lw=0.6)
    ax2.axvspan(50, 60, color="0.93", zorder=0)
    ax2.text(55, 0.88, "토크 시작\n50–60 %", ha="center", va="top", fontsize=6.3)
    ax2.set_xlim(-3, 102); ax2.set_ylim(-1.0, 0.95)
    ax2.set_xlabel("주행 중 유지 가속 [%] (모든 속도)"); ax2.set_ylabel("가속도 [m/s²]")
    ax2.grid(True); ax2.set_title("(b) 주행 중 유지 구간 (숫자 = 구간 수, 흰 점 n < 9)", fontsize=8.5)
    fig.tight_layout(w_pad=1.5)
    save(fig, "fig5_launch.png")


# ── Fig.6 브레이크 ──────────────────────────────────────────────────
def fig_brake():
    rows = [x for x in rd("long_brake_map.csv") if x["group"] == "all"]
    bins = []
    for x in rows:
        if x["brake_pct"] not in bins:
            bins.append(x["brake_pct"])
    xs, ys, lo, hi, ns = [], [], [], [], []
    for b in bins:
        sel = [x for x in rows if x["brake_pct"] == b]
        n = sum(fnum(x["n(0.05s)"]) for x in sel)
        w = lambda k: sum(fnum(x["n(0.05s)"]) * fnum(x[k]) for x in sel) / n
        a, c = [float(v) for v in b.split("-")]
        xs.append((a + min(c, 100)) / 2); ys.append(-w("a_p50_mps2"))
        lo.append(-w("a_p50_mps2") + w("a_p75")); hi.append(-w("a_p25") + w("a_p50_mps2")); ns.append(int(n))
    print("brake table:", ["%s→%.2f(n=%d)" % (b, y, n) for b, y, n in zip(bins, ys, ns)])
    dm = yaml_list("decel_mps2"); bp = yaml_list("brake_pct")
    fig, ax = plt.subplots(figsize=(3.45, 2.55))
    ax.plot(bp, dm, "x--", color=G1, ms=5, lw=0.8, label="현재 brake_map")
    ax.errorbar(xs, ys, yerr=[lo, hi], fmt="o", ms=3.8, color=K, mfc=K, capsize=1.8, lw=0.6,
                label="11-04 실측 (타행 포함, p25–p75)")
    ax.axvspan(60, 95, color="0.9", zorder=0)
    ax.text(77.5, 0.35, "60–95 %\n표본 없음", ha="center", fontsize=6.6)
    ax.axvline(82, color=K, lw=0.5, ls=":")
    ax.text(83, 4.05, "위치 포화 82 %", fontsize=6.0, rotation=90, va="top")
    ax.set_xlim(-2, 102); ax.set_ylim(0, 4.3)
    ax.set_xlabel("브레이크 명령 [%] (가속 0)"); ax.set_ylabel("감속도 [m/s²]")
    ax.grid(True); ax.legend(loc="upper left", fontsize=6.3)
    save(fig, "fig6_brake_map.png")


# ── Fig.7 지연 분포 ─────────────────────────────────────────────────
def fig_delays():
    st = rd("steer_steps.csv"); b = rd("brake_steps.csv"); L = rd("long_launch.csv")
    sdead = [fnum(x["dead_ms(>2deg)"]) for x in st if fnum(x["dead_ms(>2deg)"]) > 0]
    st90 = [fnum(x["t90_ms"]) for x in st]
    up = [x for x in b if fnum(x["cmd_to"]) > fnum(x["cmd_from"])]
    dn = [x for x in b if fnum(x["cmd_to"]) < fnum(x["cmd_from"])]
    g = lambda rs, k: [v for v in (fnum(x[k]) for x in rs) if np.isfinite(v)]
    items = [
        ("조향 무반응 (계단)", sdead, (167, 190)),
        ("조향 90 % 도달", st90, None),
        ("브레이크 걸기 무반응", g(up, "dead_ms(>0.2)"), (56, 112)),
        ("브레이크 걸기 90 %", g(up, "t90_ms"), None),
        ("브레이크 풀기 무반응", g(dn, "dead_ms(>0.2)"), None),
        ("브레이크 풀기 90 %", g(dn, "t90_ms"), None),
        ("출발 (원격, 가속 ≥ 75 %)", [fnum(x["first_move_ms(0x201>0)"]) for x in L if x["source"] == "remote"], None),
        ("출발 (자율·어댑터)", [fnum(x["first_move_ms(0x201>0)"]) for x in L if x["source"] != "remote"], None),
    ]
    fig, ax = plt.subplots(figsize=(7.0, 2.6))
    rng = np.random.default_rng(0)
    for i, (lab, v, ref) in enumerate(items):
        y = len(items) - 1 - i
        v = np.array(v)
        ax.plot(v, y + rng.uniform(-0.17, 0.17, len(v)), "o", ms=2.6, color=G2, mfc="none", mew=0.6)
        med = np.median(v)
        ax.plot([med, med], [y - 0.32, y + 0.32], color=K, lw=1.6)
        ax.text(1285, y, "%.0f ms (n=%d)" % (med, len(v)), va="center", fontsize=6.8)
        if ref:
            ax.add_patch(Rectangle((ref[0], y - 0.38), ref[1] - ref[0], 0.76, fc="none", ec=K, lw=0.7,
                                   ls=(0, (2, 1.5))))
    ax.set_yticks(range(len(items))); ax.set_yticklabels([x[0] for x in items][::-1], fontsize=7.2)
    ax.set_xlim(0, 1500); ax.set_xticks(range(0, 1300, 200))
    ax.set_xlabel("명령 계단 → 응답 [ms] (Kvaser 수신 시각)")
    ax.grid(True, axis="x")
    ax.plot([], [], "o", color=G2, mfc="none", ms=3, label="11-04 개별 사건")
    ax.plot([], [], "-", color=K, lw=1.6, label="11-04 중앙값")
    ax.add_patch(Rectangle((0, 0), 0, 0, fc="none", ec=K, ls=(0, (2, 1.5)), label="10-07 리프트 범위"))
    ax.legend(loc="lower right", fontsize=6.5, bbox_to_anchor=(0.80, 0.0))
    save(fig, "fig7_delays.png")


if __name__ == "__main__":
    fig_architecture()
    fig_states()
    fig_steer()
    fig_accel()
    fig_launch()
    fig_brake()
    fig_delays()
