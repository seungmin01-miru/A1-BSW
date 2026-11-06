#!/usr/bin/env python3
"""데이터시트 화면 캡처 — data/pj/*.csv 를 **PlotJuggler 로 열어** 가상 화면(Xvfb)에서 찍는다. 창을 띄우지 않고 사용자 화면을 건드리지 않는다.

사용:  python3 pj_capture.py <보고서 폴더> [시트 이름 ...]
필요:  ros-humble-plotjuggler(설치됨), Xvfb, xdotool, xwd(x11-apps), ffmpeg.
       Xvfb·xdotool 이 PATH 에 없으면 환경변수 XTOOLS_ROOT(= dpkg -x 로 푼 폴더)를 준다 — sudo 없이 쓰는 방법, README 참고.
출력:  figs/pj_<시트>.png, pj_layouts/<시트>.xml(PlotJuggler 에서 직접 열 수 있는 레이아웃 — 그대로 App > Load layout).
PlotJuggler 설정은 임시 HOME 에 둔다(사용자 설정을 바꾸지 않음).
"""
import os
import subprocess
import sys
import tempfile
import time

REP = os.path.abspath(sys.argv[1])
ONLY = sys.argv[2:]
PJ_DIR = os.path.join(REP, 'data', 'pj')
LAY = os.path.join(REP, 'pj_layouts')
FIG = os.path.join(REP, 'figs')
XROOT = os.environ.get('XTOOLS_ROOT', '')
XENV = dict(os.environ)
if XROOT:
    XENV['LD_LIBRARY_PATH'] = os.path.join(XROOT, 'usr/lib/x86_64-linux-gnu')
XVFB = os.path.join(XROOT, 'usr/bin/Xvfb') if XROOT else 'Xvfb'
XDOT = os.path.join(XROOT, 'usr/bin/xdotool') if XROOT else 'xdotool'
DISP = ':%d' % (70 + os.getpid() % 20)
W, H = 1860, 1180
LEFT = 300          # PlotJuggler 왼쪽 패널 폭 — 잘라 낸다
BLUE, RED, GREEN, ORANGE, PURPLE, GRAY = '#1f77b4', '#d62728', '#2ca02c', '#ff7f0e', '#9467bd', '#7f7f7f'

# 시트: csv, 높이, plots = [(곡선들, y 아래, y 위)], 곡선 = (열 이름, 색)
SHEETS = {
    'long_map': dict(csv='long_map_v1', h=620, x0=-4, style='LinesAndDots', plots=[
        ([('pedal_pct_v1', BLUE)], -105, 105)]),
    'map_speed': dict(csv='sil_map', h=1060, plots=[
        ([('out_acc_pct', BLUE), ('out_brake_pct', RED)], -5, 105),
        ([('speed_used_kph', GREEN)], -1, 32),
        ([('extrapolated', ORANGE), ('speed_stale', PURPLE)], -0.1, 1.1)]),
    'fit_time': dict(csv='sil_fit_time', h=800, plots=[
        ([('cmd_acc_pct', BLUE), ('cmd_brake_pct', RED)], -5, 105),
        ([('gnss_kph', GRAY), ('wheel_kph', GREEN)], -1, 26)]),
    'fit_scatter': dict(csv='sil_fit_scatter', h=620, x0=-4, style='LinesAndDots', plots=[
        ([('measured_pedal_pct', GRAY), ('table_v1_pedal_pct', BLUE)], -105, 105)]),
    'bsw5_scatter': dict(csv='bsw5_scatter', h=620, x0=-3, style='LinesAndDots', plots=[
        ([('measured_pedal_pct', GRAY), ('table_v1_pedal_pct', BLUE)], -105, 105)]),
    'circle_time': dict(csv='sil_circle_time', h=800, plots=[
        ([('cmd_handle_deg', BLUE), ('pos_handle_deg', RED)], -165, 165),
        ([('wheel_kph', GREEN)], -0.5, 9)]),
    'circle_xy': dict(csv='sil_circle_xy', h=900, xy=True, plots=[
        ([('x_m', 'y_m', BLUE)], -60, 60)]),
    'latency': dict(csv='vcan_latency', h=620, plots=[
        ([('latency_idle_ms', BLUE), ('latency_load_ms', RED)], 0, 5.5)]),
}

TITLES = {'long_map': ['long_map v1, 차속 10 km/h — 가로 목표 가감속 m/s², 세로 페달 %(+ 가속, − 브레이크)'], 'map_speed': ['어댑터 출력 %: 요청 +0.3 m/s² 고정 뒤 −3 m/s²', '변환에 쓴 차속 [km/h]', 'extrapolated(미측정 칸) · speed_stale(차속 끊김)'], 'fit_time': ['표 측정 SIL: 페달 명령 [%]', '차속 [km/h]: 가짜 GNSS(회색)·후륜(초록, 0.958 배)'], 'fit_scatter': ['표 측정 SIL — 가로 측정 가속도 m/s², 세로 페달 %: 측정 구간(회색) · 표 v1(파랑)'], 'bsw5_scatter': ['11-04 bsw5 실차 미리보기 — 가로 측정 가속도 m/s²(후륜 ÷ 0.958), 세로 페달 %: 측정 구간(회색) · 표 v1(파랑)'], 'circle_time': ['원 돌기 SIL: 핸들 명령·실제 [핸들 °]', '후륜 속도 [km/h]'], 'circle_xy': ['원 돌기 SIL 궤적 x–y [m] — 핸들 ±75°, ±150°, 가짜 조향비 10.7'], 'latency': ['0x127 프레임 → /race/state 수신 [ms], vcan0·브리지 별도 프로세스(토글 순번)']}


def x_end(csv):
    with open(os.path.join(PJ_DIR, csv + '.csv'), encoding='utf-8') as f:
        last = f.read().strip().rsplit('\n', 1)[1]
    return float(last.split(',')[0])


def plot_xml(curves, ymin, ymax, xend, sh):
    if sh.get('xy'):
        body = ''.join(f'<curve name="{y}" curve_x="{x}" curve_y="{y}" color="{c}"/>' for x, y, c in curves)
        return (f'<plot style="Lines" flip_y="false" flip_x="false" mode="XYPlot">'
                f'<range bottom="{ymin}" top="{ymax}" left="{ymin}" right="{ymax}"/><limitY/>{body}</plot><transform/>')
    body = ''.join(f'<curve name="{n}" color="{c}"/>' for n, c in curves)
    return (f'<plot style="{sh.get("style", "Lines")}" flip_y="false" flip_x="false" mode="TimeSeries">'
            f'<range bottom="{ymin}" top="{ymax}" left="{sh.get("x0", 0)}" right="{xend}"/>'
            f'<limitY min="{ymin}" max="{ymax}"/>{body}</plot><transform/>')


def layout(name, sh):
    xend = x_end(sh['csv'])
    n = len(sh['plots'])
    areas = ''.join(f'<DockArea name="{TITLES[name][i]}">{plot_xml(c, lo, hi, xend, sh)}</DockArea>'
                    for i, (c, lo, hi) in enumerate(sh['plots']))
    sizes = ';'.join([f'{1.0 / n:.4f}'] * n)
    xml = f"""<?xml version='1.0' encoding='UTF-8'?>
<root>
 <tabbed_widget name="Main Window" parent="main_window">
  <Tab containers="1" tab_name="{name}">
   <Container><DockSplitter orientation="-" sizes="{sizes}" count="{n}">{areas}</DockSplitter></Container>
  </Tab>
  <currentTabIndex index="0"/>
 </tabbed_widget>
 <use_relative_time_offset enabled="0"/>
 <Plugins/>
 <customMathEquations/>
 <snippets/>
</root>
"""
    path = os.path.join(LAY, name + '.xml')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(xml)
    return path


def capture(name, sh, home):
    lay = layout(name, sh)
    data = os.path.join(PJ_DIR, sh['csv'] + '.csv')
    env = dict(os.environ, HOME=home, XDG_CONFIG_HOME=os.path.join(home, '.config'), DISPLAY=DISP, QT_QPA_PLATFORM='xcb')
    env.pop('WAYLAND_DISPLAY', None)
    env.pop('XDG_SESSION_TYPE', None)
    xenv = dict(XENV, DISPLAY=DISP)

    def xd(*a):
        return subprocess.run([XDOT, *map(str, a)], env=xenv, capture_output=True, text=True).stdout

    pj = subprocess.Popen(['bash', '-c', f'source /opt/ros/humble/setup.bash; exec /opt/ros/humble/lib/plotjuggler/plotjuggler -n --disable_opengl -d {data} -l {lay}'],
                          env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(3.5)
        # CSV Loader: 열 목록 첫 줄(t) 클릭 → OK. 창 위치를 기준으로 잡는다
        wid = xd('search', '--name', 'CSV Loader').split()
        if wid:
            geo = dict(ln.split('=') for ln in xd('getwindowgeometry', '--shell', wid[0]).split() if '=' in ln)
            x0, y0 = int(geo['X']), int(geo['Y'])
            xd('mousemove', x0 + 18, y0 + 140, 'click', 1)
            time.sleep(0.3)
            xd('mousemove', x0 + int(geo['WIDTH']) - 50, y0 + int(geo['HEIGHT']) - 24, 'click', 1)
        time.sleep(1.5)
        wins = xd('search', '--name', 'PlotJuggler').split()
        for w in wins:
            xd('windowmove', w, 0, 0)
            xd('windowsize', w, W, sh['h'])
        time.sleep(1.5)
        for y in range(sh['h'] - 240, sh['h'] - 20, 11):     # 알림 말풍선 닫기(새 버전·인사말, 위치가 바뀌어 훑는다)
            xd('mousemove', W - 37, y, 'click', 1)
        time.sleep(1.0)
        xd('mousemove', W - 18, 78)
        time.sleep(0.3)
        for _ in range(3):                                    # 시간 표시 끔(누를 때마다 다음 모드)
            xd('click', 1)
            time.sleep(0.5)
        if sh.get('x0'):
            xd('mousemove', W - 18, 281, 'click', 1)          # 시작 시각 0 맞춤 끄기: 가로축 = 값 그대로
            time.sleep(0.8)
            xd('mousemove', W - 18, 78)
            time.sleep(0.3)
            for _ in range(2):
                xd('click', 1)
                time.sleep(0.5)
        xd('mousemove', W // 2, sh['h'] - 22)
        time.sleep(0.8)
        raw = os.path.join(home, 's.xwd')
        subprocess.run(['xwd', '-root', '-silent', '-display', DISP, '-out', raw], check=True, env=xenv)
        out = os.path.join(FIG, f'pj_{name}.png')
        if os.environ.get('PJ_DEBUG'):
            subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', raw, '-vf', 'colorchannelmixer=rr=0:rb=1:bb=0:br=1',
                            os.environ['PJ_DEBUG']], check=True)
        subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', raw,
                        '-vf', f'crop={W - LEFT - 42}:{sh["h"] - 62 - 54}:{LEFT + 2}:62,colorchannelmixer=rr=0:rb=1:bb=0:br=1',
                        out], check=True)
        print('ok', out)
    finally:
        pj.terminate()
        pj.wait(timeout=5)


def main():
    os.makedirs(LAY, exist_ok=True)
    os.makedirs(FIG, exist_ok=True)
    xv = subprocess.Popen([XVFB, DISP, '-screen', '0', f'{W}x{H}x24', '-nolisten', 'tcp'], env=XENV,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    try:
        with tempfile.TemporaryDirectory() as home:
            for name, sh in SHEETS.items():
                if not ONLY or name in ONLY:
                    capture(name, sh, home)
    finally:
        xv.terminate()


if __name__ == '__main__':
    main()
