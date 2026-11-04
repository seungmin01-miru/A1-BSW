# 원본 목록 (git 밖)

원본 보관 위치: ______ (외장 디스크·공유 폴더 경로)

끝나고 아래로 목록을 갱신한다:

    cd "/home/ailab/git/A1-BSW/measurements/2026-11-04_signal_audit/raw" && sha256sum -- $(find . -type f \( -name '*.log' -o -name '*.mcap' -o -name '*.txt' \) | sort) | tee -a README.md
    du -sh "/home/ailab/git/A1-BSW/measurements/2026-11-04_signal_audit/raw"/* | tee -a README.md

## 2026-11-04 기록 (Claude, 원본 수정 없음)

```
25461e0a93c6ef543048109bba2f431fa7801196872a56993a7981fe663d8ef5  ./kvaser_ch0_listen.log
3fac9c75e874155cb3ba529cb6f26802efc30fe4248e6bd5104171ce403d7122  ./mirror_console.txt
48M	./kvaser_ch0_listen.log
168K	./mirror_console.txt
```
