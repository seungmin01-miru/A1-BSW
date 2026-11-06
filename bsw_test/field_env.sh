# 현장용 짧은 명령 — 대회 차량 PC 의 **터미널마다 한 번**:  source ~/git/A1-BSW/bsw_test/field_env.sh
# 실제로 무엇을 실행하는지는 각 함수 한 줄을 보면 된다(숨긴 동작 없음). 입력 순서는 FIELD_CHEATSHEET.txt.
R=${A1_REPO:-$HOME/git/A1-BSW}
CAP=$HOME/a1_race_capture
KV="--interface kvaser --channel 0 --bitrate 500000"       # Kvaser Leaf v3 채널 0 (bringup --kvaser 0 과 같은 장치)
P=$R/bsw_test/02_LON_profile/profiles                       # 생성기 yaml
DAY=$(date +%Y%m%d)
CTRL="--ctrl $R/bsw_test/02_LON_profile/ctrl/speed_ctrl.yaml"   # 속도 제어기 파라미터(현장에서 맞춘 값)
OFS=${OFS:-0}                                               # 조향 오프셋(핸들 °, 01 결과) — ofs 로 바꿈. gen 이 자동으로 붙인다

# up <테스트ID_세션> <가속한계%> [bringup 추가옵션]   예) up SAF-ESTOP-01_s1 10
up()   { local s=${1:?세션 이름 예: SAF-ESTOP-01_s1} a=${2:?가속 한계 %}; shift 2
         bash "$R/tools/track_day/bringup.sh" --kvaser 0 --no-adapter --acc-limit "$a" --estop-required \
              --out "$CAP/${DAY}_$s" "$@"; }
# down — 세션 끝내기(먼저 lift_cmd 는 brake 100 → q, 생성기는 q)
down() { bash "$R/tools/track_day/stop.sh"; }
# lc [lift_cmd 옵션]   예) lc --steer-limit-deg 15 --acc-limit-pct 10 --acc-hold-s 10
lc()   { python3 "$R/safety/can_guard/lift_cmd.py" $KV --brake-limit-pct 100 --log "$CAP/track_latest/lift_cmd.log" "$@"; }
# chk <yaml> [--var 이름=값 …]          — 생성기 검사만(차 안 움직임). 오프셋 $OFS 넣고 검사
chk()  { local y=$1; shift; python3 "$R/bsw_test/common/scripts/profile_gen.py" check "$P/$y" --steer-offset "$OFS" "$@"; }
# gen <yaml> <회차> [--var 이름=값 …]    예) gen acc_step.yaml LON-ACC-01_r4 --var acc=15
#   세션(여러 회차): gen <yaml> <테스트ID> --reps 3 [--sweep 이름=값,값]  → 회차 이름 <테스트ID>_r1 …
#   조향 오프셋 $OFS 를 항상 --steer-offset 으로 붙인다(뒤에 직접 쓰면 그 값이 이김). 0 이면 0.
gen()  { local y=$1 r=${2:?회차 예: LON-ACC-01_r1}; shift 2
         echo "gen: 조향 오프셋 ${OFS}° (핸들)"
         python3 "$R/bsw_test/common/scripts/profile_gen.py" run "$P/$y" --run "${DAY}_$r" $KV --out "$CAP/track_latest" \
                 --steer-offset "$OFS" "$@"; }
# circ <회차 이름> [circle_test 옵션]   원 돌기 — 예) circ ER-CIRCLE-01 --dry-run
circ() { local r=${1:?회차 이름}; shift; python3 "$R/bsw_test/common/scripts/circle_test.py" --run "${DAY}_$r" $KV --out "$CAP/track_latest" "$@"; }
# ofs <핸들°>  — 01 오프셋을 이 터미널에 설정(그 뒤 gen 전부 적용).   예) ofs 1.2   / 끄기: ofs 0
ofs()  { OFS=${1:?핸들 °}; echo "조향 오프셋 = ${OFS}° (핸들) — 이 터미널의 gen 에 적용"; }
# es / esr — 비상 정지 / 해제(정지 상태에서만)
es()   { python3 "$R/safety/can_guard/estop.py" "$@"; }
esr()  { python3 "$R/safety/can_guard/estop.py" --reset; }
# m <메모>                                 예) m r1 시작
m()    { echo "$(date +%s.%N) $(date +%H:%M:%S) $*" >> "$CAP/track_latest/notes.txt"; echo "메모: $*"; }
# app — 앱 프레임 보기(00-0)
app()  { candump -ta kv0,156:7FF,157:7FF | tee "$CAP/track_latest/app_frames.txt"; }

echo "field_env: up down lc chk gen circ ofs es esr m app  \$CTRL  (저장소 $R, 날짜 $DAY, 오프셋 ${OFS}°)"
