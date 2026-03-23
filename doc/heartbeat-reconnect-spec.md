# C# 클라이언트 Heartbeat 재접속 요구사항

## 배경

서버에서 DataChannel로 5초마다 heartbeat를 보내고 있는데 클라이언트에서 이걸 감지하는 로직이 없어서, 네트워크 끊기거나 서버 재시작되면 클라이언트가 그대로 멈춰버리는 이슈가 있습니다.

클라이언트 쪽에서 heartbeat 수신을 감시해서 자동 재접속하는 기능이 필요합니다.

---

## 서버가 보내는 Heartbeat

서버가 DataChannel로 보내는 heartbeat 메시지:

```json
{"type": "heartbeat", "ts": 1710923607000}
```

- 5초 간격으로 전송
- `ts`는 Unix timestamp (milliseconds)

---

## 구현 요구사항

### 1. Heartbeat 수신 감시

- 마지막 heartbeat 수신 시각을 기록
- **15초간** (heartbeat 3회 누락) 미수신 시 연결 끊김으로 판단

### 2. 연결 끊김 감지 (둘 중 하나라도 해당되면)

- heartbeat 15초 미수신
- `RTCPeerConnection.connectionState`가 `disconnected`, `failed`, `closed` 중 하나로 변경

### 3. 자동 재접속

- 감지 즉시 기존 `RTCPeerConnection`을 `Close()` 후 폐기
- 새 `RTCPeerConnection` 생성 후 `/offer`로 SDP 교환
- 재접속 간격: Exponential backoff (2초 -> 4초 -> 8초 -> 최대 30초)
- 재접속 성공 판단: 서버에서 `welcome` 메시지 수신 시

---

## 서버 엔드포인트 및 메시지 형식

### SDP 교환 (재접속 요청)

```
POST http://10.145.165.8:8080/offer?client_id=V135-3&role=agent
Content-Type: application/json

{
  "type": "offer",
  "sdp": "v=0\r\no=- 1710923600 1 IN IP4 10.145.165.100\r\ns=-\r\nt=0 0\r\na=group:BUNDLE 0\r\nm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\nc=IN IP4 0.0.0.0\r\na=mid:0\r\na=ice-ufrag:aB1c\r\na=ice-pwd:dE2fG3hI4jK5lM6nO7pQ8r\r\na=ice-options:trickle\r\na=fingerprint:sha-256 A1:B2:C3:D4:E5:F6:01:02:03:04:05:06:07:08:09:0A:0B:0C:0D:0E:0F:10:11:12:13:14:15:16:17:18:19:1A\r\na=setup:actpass\r\na=sctp-port:5000\r\na=max-message-size:262144\r\na=candidate:1 1 udp 2130706431 10.145.165.100 50000 typ host\r\n"
}
```

### 서버 응답 (SDP Answer)

```json
{
  "type": "answer",
  "sdp": "v=0\r\n..."
}
```

### 재접속 성공 확인 메시지 (DataChannel)

```json
{"type": "welcome", "client_id": "V135-3", "mode": "live", "batch_forecast": null}
```

---

## 참고

- `client_id`를 동일하게 쓰면 서버에서 이전 연결을 자동 정리합니다.
- SDP의 `sdp` 필드는 SIPSorcery가 생성하는 값을 그대로 넣으면 됩니다. 위 예시는 형식 참고용입니다.
