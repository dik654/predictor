# Python WebRTC Hub Server (uv + aiortc)

This server is the **central hub**:
- React ↔ Python (DataChannel)
- .NET ↔ Python (DataChannel)
- Python routes messages (`send` / `broadcast`) between connected clients.

## Run with uv

```bash
cd server
uv venv
uv sync
uv run webrtc-hub
```

Server:
- `POST /offer?client_id=<id>&role=<role>`  (HTTP signaling)
- `GET /who` (debug: connected clients/rooms)
- `GET /health`

### Signaling contract
Client sends offer (JSON):
```json
{ "type": "offer", "sdp": "..." }
```
Server responds answer (JSON):
```json
{ "type": "answer", "sdp": "..." }
```

### DataChannel protocol (JSON string messages)
- hello: `{ "type":"hello", "role":"react|pos-agent|...", "meta":{...} }`
- join: `{ "type":"join", "room":"store-101" }`
- send: `{ "type":"send", "to":"other-client-id", "payload":{...} }`
- broadcast: `{ "type":"broadcast", "room":"store-101", "payload":{...} }`
- ping: `{ "type":"ping", "ts": 123 }` -> server replies `{ "type":"pong", "ts":123 }`
