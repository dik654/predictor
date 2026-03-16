#!/usr/bin/env python3
"""Test if flush() fix works"""
import asyncio
import logging
from pathlib import Path
from server.webrtc_hub.sample_loader import sample_data_generator, load_all_sample_data
from server.webrtc_hub.influx_writer import write_metrics, init_influx

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
)
log = logging.getLogger("test_flush")

async def test_write():
    """Test writing metrics"""
    init_influx()

    # Write 30 test records
    for i in range(30):
        result = await write_metrics(
            agent_id=f"TEST-{i}",
            timestamp="2026-03-12T02:00:00Z",
            raw_metrics={"CPU": 50.0 + i, "Memory": 70.0 + i, "DiskIO": 10.0 + i}
        )
        if i % 10 == 0:
            log.info(f"Record {i}: {'✓' if result else '✗'}")

    log.info("Test completed")

if __name__ == "__main__":
    asyncio.run(test_write())
