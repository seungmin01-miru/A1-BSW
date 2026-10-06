# 수신 원시 CAN 프레임 1개. can_raw_bridge 가 SocketCAN 에서 읽은 그대로 퍼블리시한다 (§6 walking skeleton 4단계).
# 물리값 디코딩은 하지 않는다 — 그건 디코더 노드(spd_decoder 등)의 책임.
std_msgs/Header header      # header.stamp = 커널이 프레임을 넘겨준 시각(수신 시각). 종단 지연(§3 통합 시험) 측정의 기준점.
uint32 id                   # 11/29비트 CAN ID (DBC 상 0x712 = EAIT_INFO_SPD 등)
uint8 dlc                   # 데이터 길이(바이트, 0~8. FD 확장 전제 없음 — 이 프로젝트 DBC 는 전부 8바이트 클래식 CAN)
uint8[8] data                # 페이로드. dlc 보다 뒤쪽은 0
bool is_extended             # 29비트 확장 ID 여부
bool is_error                # 에러 프레임 여부 (버스오프 등 §7.3 P-6 대비)
