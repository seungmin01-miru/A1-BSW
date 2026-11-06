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
    'steer_hairpin': dict(csv='bsw5_hairpin', h=760, plots=[
        ([('cmd_steer_handle_deg', BLUE), ('pos_steer_handle_deg', RED)], -165, 20),
        ([('wheel_speed_kph', GREEN)], 0, 22)]),
    'accel_straight': dict(csv='bsw5_straight', h=760, plots=[
        ([('cmd_acc_pct', BLUE), ('cmd_brake_pct', RED)], -5, 105),
        ([('wheel_speed_kph', GREEN)], 18, 21)]),
    'accel_sil': dict(csv='sil_cruise', h=1000, plots=[
        ([('in_accel_mps2', GRAY)], 0, 0.2),
        ([('acc_old_pct', RED)], -5, 105),
        ([('acc_hysteresis_pct', BLUE)], -5, 105)]),
    'brake': dict(csv='bsw5_brake', h=760, plots=[
        ([('cmd_brake_pct', BLUE), ('pos_brake_pct', RED), ('cmd_acc_pct', GRAY)], -5, 105),
        ([('wheel_speed_kph', GREEN)], 0, 22)]),
    'lap': dict(csv='bsw5_lap', h=1000, plots=[
        ([('cmd_steer_handle_deg', BLUE), ('pos_steer_handle_deg', RED)], -165, 120),
        ([('cmd_acc_pct', BLUE), ('cmd_brake_pct', RED)], -5, 105),
        ([('wheel_speed_kph', GREEN)], 0, 22)]),
    'launch': dict(csv='quick_lon_s5', h=760, plots=[
        ([('cmd_acc_pct', BLUE), ('cmd_brake_pct', RED), ('pos_brake_pct', ORANGE)], -5, 105),
        ([('wheel_speed_kph', GREEN)], -0.2, 5)]),
    'steer_map': dict(csv='steer_xy', h=760, x0=-150, style='LinesAndDots', plots=[
        ([('measured_tire_deg', GRAY), ('new_map_tire_deg', BLUE), ('old_map_tire_deg', RED)], -21, 21)]),
    'race_flags': dict(csv='sil_flags', h=1000, plots=[
        ([('level_sig_go', BLUE), ('level_seen_go', RED)], -0.1, 1.1),
        ([('pulse2_sig_go', BLUE), ('pulse2_seen_go', RED)], -0.1, 1.1),
        ([('pulse1_sig_go', BLUE), ('pulse1_seen_go', RED)], -0.1, 1.1)]),
    'race_res': dict(csv='sil_res', h=760, plots=[
        ([('res_active', RED), ('seen_go', BLUE)], -0.1, 1.1),
        ([('res_alive_ok', GREEN), ('res_age_s', ORANGE)], -0.1, 1.1)]),
    'race_alive': dict(csv='sil_alive', h=760, plots=[
        ([('res_alive_ok', GREEN)], -0.1, 1.1),
        ([('res_age_s', ORANGE), ('race_signal_age_s', PURPLE)], -0.2, 6)]),
    'latency': dict(csv='sil_latency', h=620, plots=[
        ([('latency_idle_ms', BLUE), ('latency_load_ms', RED)], 0, 5.5)]),
    'period': dict(csv='bsw5_period', h=620, plots=[
        ([('dt_0x200_ms', BLUE), ('dt_0x201_ms', GREEN), ('dt_0x210_ms', RED)], 15, 28)]),
}

TITLES = {'steer_hairpin': ['핸들각 명령 vs 실제 [핸들 °]', '후륜 평균 속도 [km/h]'], 'accel_straight': ['가속·브레이크 명령 [%]', '후륜 평균 속도 [km/h]'], 'accel_sil': ['입력: 모델 목표 가속도 [m/s²]', '예전 표 출력: 가속 [%]', '히스테리시스 출력: 가속 [%]'], 'brake': ['브레이크 명령·실제 위치, 가속 명령 [%]', '후륜 평균 속도 [km/h]'], 'lap': ['핸들각 명령 vs 실제 [핸들 °]', '가속·브레이크 명령 [%]', '후륜 평균 속도 [km/h]'], 'launch': ['가속·브레이크 명령, 브레이크 위치 [%]', '후륜 평균 속도 [km/h]'], 'steer_map': ['가로 핸들 ° / 세로 타이어 ° — 측정(회색, 11-04 GNSS 요레이트 역산) · 새 표(파랑) · 옛 표 7.5(빨강)'], 'race_flags': ['level: 계속 켜짐', 'pulse2: 2 프레임', 'pulse1: 1 프레임(무시되어야 함)'], 'race_res': ['RES 작동·seen_go', 'alive 정상·res_age [s]'], 'race_alive': ['res_alive_ok(5 s 에 0x127 끊김)', '마지막 수신 후 경과 [s]'], 'latency': ['0x127 프레임 → /race/state 수신 [ms] (토글 순번)'], 'period': ['프레임 간격 [ms], 주행 중 30 s']}


def x_end(csv):
    with open(os.path.join(PJ_DIR, csv + '.csv'), encoding='utf-8') as f:
        last = f.read().strip().rsplit('\n', 1)[1]
    return float(last.split(',')[0])


def plot_xml(curves, ymin, ymax, xend, sh):
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
        xd('mousemove', W - 18, 78, 'click', '--repeat', 3, '--delay', 300, 1)   # 시간 표시 끔(누를 때마다 다음 모드)
        time.sleep(0.5)
        if sh.get('x0'):
            xd('mousemove', W - 18, 281, 'click', 1)          # 시작 시각 0 맞춤 끄기: 가로축 = 핸들 ° 그대로
            time.sleep(0.8)
            xd('mousemove', W - 18, 78, 'click', '--repeat', 2, '--delay', 300, 1)
            time.sleep(0.3)
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
