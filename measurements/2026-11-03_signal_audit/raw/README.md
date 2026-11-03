# 원본 목록 (git 밖)

원본 보관 위치: ______ (외장 디스크·공유 폴더 경로)

- 녹화: 2026-11-03 15:56:09 ~ 16:33:10, Kvaser Leaf v3 ch0 500 kbit/s listen-only (`kvaser_mirror.py --listen-only`)
- 프레임 442,631 / 에러프레임 371 (17개씩 몰림 — 0x128·0x202 연속 재전송 때)
- 버스 꺼짐 구간: 16:07:10~16:17:03, 16:22:06~16:26:36, 16:28:06~16:31:49 (차량/원격 전원)
- `l0_watch.txt`: L0 중 실시간 변화 감시(16:04:33~16:07:33)

## sha256
4a712d4a4032a81dac719daeb9dc9338a399063929fba7976deee2d0b15cd0f0  ./kvaser_ch0_listen.log
3178efcd7ea9bf7a70d5e97212a5d4ebc77957c2079850d09545be3c2cfbee57  ./kvaser_mirror_stdout.txt
9683ae5117d340a90a353d36703a14864c2068d147fee4139f3de8ed63aac3f0  ./l0_watch.txt
3bf72d50fd9a377b131c38537f93a80b0956c950e08b0f171f3b31c25c75eb72  ./notes.txt
20M	.

## 2단계 시도 (git 밖, 송신 0)
- `l4/` 16:48 원격 켠 채 bringup → can_guard 시작 거부(원격 0x210) · `l4b/` 드라이브 넣고 재시도 → 같은 거부 · `l4c/` 원격 끔 → 차량 CAN 침묵, bringup 중단
- 세 번 모두 can_guard "종료 — 0 주기, 송신 실패 0건"
