#!/usr/bin/env bash
# vcan0 가상 CAN 인터페이스 생성·기동 (B-1). 여러 번 실행해도 안전(멱등).
#   sudo bash vcan_up.sh            # vcan0 생성 + up
#   sudo bash vcan_up.sh vcan1      # 다른 이름
#   sudo bash vcan_up.sh --install  # 부팅 시 자동 생성되는 systemd 유닛 설치 (vcan0.service)
set -euo pipefail

IF=vcan0
[[ ${1:-} == --install ]] && INSTALL=1 || { INSTALL=0; IF=${1:-vcan0}; }
[[ $EUID -eq 0 ]] || { echo "sudo 로 실행하세요"; exit 1; }

if ((INSTALL)); then
  # NetworkManager 환경(systemd-networkd 비활성)이라 .netdev 대신 oneshot 유닛으로 영속화
  cat > /etc/systemd/system/vcan0.service <<'UNIT'
[Unit]
Description=A1-BSW vcan0 virtual CAN interface (SIL)
After=network-pre.target
Before=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/sbin/modprobe vcan
ExecStart=/bin/sh -c '/sbin/ip link show vcan0 >/dev/null 2>&1 || /sbin/ip link add dev vcan0 type vcan'
ExecStart=/sbin/ip link set up vcan0
ExecStop=/sbin/ip link del dev vcan0

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable --now vcan0.service
  systemctl --no-pager --lines=0 status vcan0.service | head -3
  ip -br link show vcan0
  exit 0
fi

modprobe vcan
ip link show "$IF" >/dev/null 2>&1 || ip link add dev "$IF" type vcan
ip link set up "$IF"
ip -d link show "$IF" | sed 's/^/  /'
echo "OK: $IF up"
