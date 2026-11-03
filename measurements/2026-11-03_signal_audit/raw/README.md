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

## 2단계 (재부팅 후, 21:41~)
- l4d: silent 기록 + kv0 DOWN 으로 bringup 중단 · l4e·l4f: 송신 실패(미러 핸들 구성) · l4g: single_handle 수정 후 첫 성공·auto 비트 · l4h: 조향 스윕·브레이크
eb01743587d5af2a66f232cbf7b463ab148057ab7ef8fcd97022d88dc3aedaf7  l4d/pre_listen.log
7199543c3ffc8fb7737f2985d1a89bd3cbd35849a3228eff040f7af9d607e51a  l4b/kvaser_ch0.log
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  l4c/kvaser_ch0.log
b6727a3f54eebb8246c89af607f93c1d3a6efb5d7b15a32e06fed8dc4835a45a  l4d/kvaser_ch0.log
afa26fd6e53efef2829c4031b60e42ca87c37a70833ec0d46e986cb30e106107  l4e/kvaser_ch0.log
30b505b60197b9f8c980c8ce63b5d8afd28d273cf547f51f0b44f39040d9a0c5  l4f/kvaser_ch0.log
5a6b5e7f4fd671a308d07a4ce190a52cd5733e7ca2cbe49b969e79a055d6e38a  l4g/kvaser_ch0.log
2cd977afb58600382e76269c9e39fcf187c475934af0709e06fa40f743132967  l4h/kvaser_ch0.log
5d40fa514df637549395eeaab62438790535d0d8164c8f43306f06aa17b85c9f  l4/kvaser_ch0.log
ffcea2926433da05990212e6e27ca65cba65ba6a1e94cba21f9a6cd92130da44  l4b/can_guard.log
e0ee37cef7527ed68e39228634a1d275437689857c581542d3bd23fbc3d416b2  l4/can_guard.log
8370b2722cda5c05553d7aaf30ee7b7976d0fddee78abed68c47df7573845296  l4e/can_guard.log
812485c99f4fad848d7a2413dc460ed86bd0719387b69a6168a7065ecef9ad61  l4f/can_guard.log
9a2f2154d9adc881f855aa3ca88981056bc8f1699ddc329973027784c715b54b  l4g/can_guard.log
11b0fac5c1b4274d17de0a9c61d9eb918eac13f01698defcae05df3c4a2c1f15  l4h/can_guard.log
db434bdeb9af855907ec0b023718af7c628381a1dd7d5e035e57f9dc90878ba3  l4g/lift_cmd.log
f8c25f3284daf974428e43d2801cdcd77331937835d2365e472ad275aa9acf3b  l4h/lift_cmd.log
