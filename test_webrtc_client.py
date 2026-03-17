#!/usr/bin/env python3
"""
WebRTC test client - C# Agent를 흉내내어 sample/data_pos.txt를 서버에 전송.

사용법:
  cd server && uv run python ../test_webrtc_client.py
  또는:
  cd server && uv run python ../test_webrtc_client.py --delay 1.0 --limit 50
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import click
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("test-client")


async def run_client(
    server_url: str,
    sample_file: str,
    delay: float,
    limit: int,
    client_id: str,
):
    """Connect to hub via WebRTC and send sample data."""

    # Load sample data
    file_path = Path(sample_file)
    if not file_path.exists():
        log.error(f"Sample file not found: {file_path}")
        return

    records = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    total = min(len(records), limit) if limit > 0 else len(records)
    log.info(f"Loaded {len(records)} records, will send {total}")

    # Create WebRTC connection
    config = RTCConfiguration(
        iceServers=[
            RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
        ]
    )
    pc = RTCPeerConnection(configuration=config)
    channel = pc.createDataChannel("pulseai")

    connected = asyncio.Event()
    responses_received = 0

    @channel.on("open")
    def on_open():
        log.info("DataChannel opened!")
        connected.set()

    @channel.on("message")
    def on_message(message):
        nonlocal responses_received
        try:
            data = json.loads(message)
            msg_type = data.get("type", "unknown")
            if msg_type == "welcome":
                log.info(f"  << welcome: client_id={data.get('client_id')}")
            elif msg_type == "hello_ack":
                log.info(f"  << hello_ack: role={data.get('role')}")
            elif msg_type == "data_ack":
                responses_received += 1
            elif msg_type == "anomaly":
                dets = data.get("detections", [])
                health = data.get("health_score", "?")
                engines = set(d.get("engine") for d in dets)
                log.info(f"  << anomaly: {len(dets)} detections, engines={engines}, health={health}")
            elif msg_type == "metrics":
                pass  # broadcast echo, ignore
            else:
                log.debug(f"  << {msg_type}")
        except Exception as e:
            log.warning(f"  << parse error: {e}")

    @channel.on("close")
    def on_close():
        log.info("DataChannel closed")

    # Create offer
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    # Wait for ICE gathering
    while pc.iceGatheringState != "complete":
        await asyncio.sleep(0.1)

    # Send offer to server
    log.info(f"Sending offer to {server_url}/offer?client_id={client_id}&role=agent")
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{server_url}/offer?client_id={client_id}&role=agent",
            json={"sdp": pc.localDescription.sdp, "type": pc.localDescription.type},
        ) as resp:
            if resp.status != 200:
                log.error(f"Offer failed: {resp.status} {await resp.text()}")
                return
            answer = await resp.json()

    await pc.setRemoteDescription(
        RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
    )

    # Wait for channel to open
    log.info("Waiting for DataChannel to open...")
    await asyncio.wait_for(connected.wait(), timeout=10)

    # Send hello
    channel.send(json.dumps({"type": "hello", "role": "agent"}))
    await asyncio.sleep(0.5)

    # Send sample data
    log.info(f"Sending {total} records (delay={delay}s)...")
    sent = 0
    for i, record in enumerate(records[:total]):
        # Adjust timestamp to now
        record["Timestamp"] = datetime.utcnow().isoformat() + "Z"

        msg = {"type": "data", "payload": record, "ts": time.time()}
        channel.send(json.dumps(msg, ensure_ascii=False))
        sent += 1

        if sent % 10 == 0:
            log.info(f"  Sent {sent}/{total} | Responses: {responses_received}")

        await asyncio.sleep(delay)

    log.info(f"Done! Sent: {sent}, Responses: {responses_received}")

    # Wait a bit for remaining responses
    await asyncio.sleep(2)
    log.info(f"Final responses: {responses_received}")

    # Cleanup
    channel.close()
    await pc.close()


@click.command()
@click.option("--server", default="http://localhost:8080", help="Server URL")
@click.option("--file", "sample_file", default="sample/data_pos.txt", help="Sample data file")
@click.option("--delay", default=0.5, type=float, help="Delay between records (seconds)")
@click.option("--limit", default=100, type=int, help="Max records to send (0 = all)")
@click.option("--client-id", default="test-agent-01", help="Client ID")
def main(server, sample_file, delay, limit, client_id):
    """WebRTC test client - sends sample data to PulseAI Hub server."""
    asyncio.run(run_client(server, sample_file, delay, limit, client_id))


if __name__ == "__main__":
    main()
