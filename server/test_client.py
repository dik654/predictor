"""
간단한 WebRTC 테스트 클라이언트 - 서버 연결 확인용
"""
import asyncio
import json
import aiohttp
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer


async def main():
    server_url = "http://192.168.100.62:8080"
    client_id = "test-python-01"

    pc = RTCPeerConnection(
        configuration=RTCConfiguration(
            iceServers=[
                RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
                RTCIceServer(urls=["stun:stun1.l.google.com:19302"]),
            ]
        )
    )

    channel = pc.createDataChannel("hub")

    @channel.on("open")
    def on_open():
        print("[OK] DataChannel opened!")
        channel.send(json.dumps({"type": "hello", "role": "test"}))
        # 테스트 데이터 전송
        channel.send(json.dumps({
            "type": "data",
            "ts": 1234567890,
            "payload": {
                "AgentId": "TEST-AGENT-01",
                "Timestamp": "2025-01-01 12:00:00",
                "CPU": 25.0,
                "Memory": 60.0,
                "DiskIO": 0.3,
                "Network": {"Sent": 500, "Recv": 200},
            }
        }))
        print("[OK] Test data sent!")

    @channel.on("message")
    def on_message(message):
        print(f"[RECV] {message}")

    @pc.on("connectionstatechange")
    async def on_state():
        print(f"[ICE] connectionState = {pc.connectionState}")
        if pc.connectionState == "failed":
            print("[FAIL] WebRTC connection failed!")

    @pc.on("icegatheringstatechange")
    def on_ice_gathering():
        print(f"[ICE] gatheringState = {pc.iceGatheringState}")

    # Create offer
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    # Wait for ICE gathering
    while pc.iceGatheringState != "complete":
        await asyncio.sleep(0.1)
    print(f"[ICE] gathering complete, candidates in SDP")

    # Send offer via HTTP
    async with aiohttp.ClientSession() as session:
        resp = await session.post(
            f"{server_url}/offer?client_id={client_id}&role=test",
            json={"sdp": pc.localDescription.sdp, "type": pc.localDescription.type},
        )
        if resp.status != 200:
            print(f"[FAIL] offer HTTP failed: {resp.status}")
            return
        answer = await resp.json()
        print(f"[OK] Got answer from server")

    await pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))

    # 10초 대기 후 종료
    print("[WAIT] Waiting 10s for messages...")
    await asyncio.sleep(10)

    # 연결 상태 확인
    async with aiohttp.ClientSession() as session:
        resp = await session.get(f"{server_url}/who")
        who = await resp.json()
        print(f"[WHO] {json.dumps(who, indent=2)}")

    channel.close()
    await pc.close()
    print("[DONE]")


if __name__ == "__main__":
    asyncio.run(main())
