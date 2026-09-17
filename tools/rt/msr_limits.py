#!/usr/bin/env python3
"""클럭 제한 사유·패키지 열/전력 상태 로그 비트를 읽고(옵션으로 지운다) — 커널이 못 보는 하드웨어 층의 사건 기록.
  sudo python3 tools/rt/msr_limits.py [--cpus 0-19] [--clear]
출력 한 줄: limit=<0x64F 로그 비트 합집합 이름들|none> pkg=<0x1B1 로그 비트 이름들|none>
 * MSR_CORE_PERF_LIMIT_REASONS (0x64F): 하위 16비트 = 현재 상태, 16~31 = 마지막 clear 이후 한 번이라도 켜졌던 사유(로그). --clear 로 로그를 0으로.
 * IA32_PACKAGE_THERM_STATUS (0x1B1): 짝수 비트 = 상태, 홀수 비트 = 로그. --clear 로 로그를 0으로.
 * 이름표는 Intel SDM 의 클라이언트(6th gen+) 정의 기준 — 12세대에서 비트 의미가 다르면 raw 값(hex)을 같이 보라."""
import argparse, os, sys
LIMIT_BITS = {0:"PROCHOT",1:"Thermal",4:"ResidencyState",5:"RunningAvgThermal",6:"VR_Therm",7:"VR_TDC(전류)",8:"Other(Vccin/EDP)",
              10:"PL1",11:"PL2",12:"MaxTurboLimit",13:"TurboAttenuation"}
PKG_BITS = {0:"ThermStatus",2:"ThermThreshold1",4:"ThermThreshold2",6:"PowerLimit",8:"CriticalTemp",10:"PROCHOT",12:"HWFeedbackChange"}
def cpulist(s):
    r=[]
    for p in s.split(","):
        a,_,b=p.partition("-"); r+=range(int(a),int(b or a)+1)
    return r
def rd(cpu,reg):
    fd=os.open(f"/dev/cpu/{cpu}/msr",os.O_RDONLY)
    try: return int.from_bytes(os.pread(fd,8,reg),"little")
    finally: os.close(fd)
def wr(cpu,reg,val):
    fd=os.open(f"/dev/cpu/{cpu}/msr",os.O_WRONLY)
    try: os.pwrite(fd,val.to_bytes(8,"little"),reg)
    finally: os.close(fd)
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--cpus",default="0-19"); ap.add_argument("--clear",action="store_true"); ap.add_argument("--raw",action="store_true")
    a=ap.parse_args()
    def online(c):
        f=f"/sys/devices/system/cpu/cpu{c}/online"
        return not os.path.exists(f) or open(f).read().strip()=="1"
    cpus=[c for c in cpulist(a.cpus) if online(c)]
    try:
        union=0; per={}
        for c in cpus:
            v=rd(c,0x64F); per[c]=v; union|=v
            if a.clear: wr(c,0x64F, v & 0xFFFF)          # 로그(상위 16비트)만 지움
        pk=rd(0,0x1B1)
        if a.clear: wr(0,0x1B1, pk & ~0x5555)            # 홀수(로그) 비트 지움
    except OSError as e:
        print(f"limit=? pkg=? (MSR 실패: {e})"); return 1
    lim=[n for b,n in LIMIT_BITS.items() if union>>(16+b) & 1]
    now=[n for b,n in LIMIT_BITS.items() if union>>b & 1]
    pkl=[n for b,n in PKG_BITS.items() if pk>>(b+1) & 1]
    out=f"limit={'+'.join(lim) or 'none'}"
    if now: out+=f" now={'+'.join(now)}"
    out+=f" pkg={'+'.join(pkl) or 'none'}"
    if a.raw: out+=f" raw64F=0x{union:08x} raw1B1=0x{pk:08x} cores_with_log={[c for c,v in per.items() if v>>16]}"
    print(out); return 0
if __name__=="__main__": sys.exit(main())
