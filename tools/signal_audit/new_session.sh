#!/usr/bin/env bash
# 실차 신호 검증 기록 폴더 만들기: measurements/<날짜>_signal_audit[_N]/
#
#   tools/signal_audit/new_session.sh            # 오늘 날짜
#   tools/signal_audit/new_session.sh 2026-11-10 # 날짜 지정 (같은 날 두 번째면 _2, _3 …)
#
# 만든 뒤 출력되는 D=... 를 현장 터미널에 붙여 넣고 녹화·lift_cmd 를 "$D/raw" 에서 실행한다.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
DATE="${1:-$(date +%F)}"
[[ "$DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || { echo "날짜 형식은 YYYY-MM-DD: $DATE" >&2; exit 2; }

D="$REPO/measurements/${DATE}_signal_audit"
n=2
while [[ -e "$D" ]]; do D="$REPO/measurements/${DATE}_signal_audit_$n"; n=$((n + 1)); done

mkdir -p "$D/raw" "$D/analysis"
for f in README.md answers.md; do
  sed "s/__DATE__/$DATE/g" "$HERE/session_template/$f" > "$D/$f"
done
cp "$HERE/vendor_questions.md" "$D/vendor_answers.md"
python3 "$HERE/gen_cards.py" -o "$D/cards.md"

# 원본은 커서 git 에 올리지 않는다 — 목록·크기·해시만 raw/README.md 로 남긴다(끝나고 아래 명령으로).
cat > "$D/raw/.gitignore" <<'EOF'
*
!.gitignore
!README.md
EOF
cat > "$D/raw/README.md" <<EOF
# 원본 목록 (git 밖)

원본 보관 위치: ______ (외장 디스크·공유 폴더 경로)

끝나고 아래로 목록을 갱신한다:

    cd "$D/raw" && sha256sum -- $(find . -type f \( -name '*.log' -o -name '*.mcap' -o -name '*.txt' \) | sort) | tee -a README.md
    du -sh "$D/raw"/* | tee -a README.md
EOF

echo "만듦: ${D#$REPO/}"
echo
echo "현장 터미널에서 (터미널마다):"
echo "  D=\"$D/raw\"; S=\"$D\"; cd \"\$D\""
echo "  m() { echo \"\$(date +%s.%N) \$(date +%T) \$*\" >> \"\$D/notes.txt\"; }"
echo "  # 절차: tools/signal_audit/AUDIT_MANUAL.md (0단계 → 1단계 원격 한계 → 2단계 0x210 매칭 → 3단계 정리)"
echo
echo "끝나고:"
echo "  python3 tools/signal_audit/judge.py ${D#$REPO/}"
