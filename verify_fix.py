#!/usr/bin/env python3
"""Verify HTTP API fix is working"""
import asyncio
import logging
from pathlib import Path
from server.webrtc_hub.influx_writer import write_metrics, init_influx

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
)
log = logging.getLogger("verify_fix")

async def test():
    """Test 10 writes with HTTP API to sample_metrics bucket"""
    init_influx()

    log.info("Starting 10-record HTTP API write test to sample_metrics...")
    successes = 0

    from datetime import datetime, timedelta
    now_utc = datetime.utcnow()

    for i in range(10):
        # Generate unique timestamps - increment by 1 second each
        unique_timestamp = (now_utc + timedelta(seconds=i)).isoformat() + "Z"

        result = await write_metrics(
            agent_id=f"VERIFY-SAMPLE-{i:02d}",
            timestamp=unique_timestamp,
            raw_metrics={"CPU": 50.0 + i, "Memory": 70.0 + i, "DiskIO": 10.0 + i},
            bucket="sample_metrics"  # Explicitly use sample_metrics bucket
        )
        if result:
            successes += 1
            log.info(f"✓ Record {i}: SUCCESS")
        else:
            log.error(f"✗ Record {i}: FAILED")

    log.info(f"\nTest complete: {successes}/10 records succeeded")
    if successes == 10:
        log.info("✅ HTTP API fix appears to be working!")
    else:
        log.error(f"⚠️  Only {successes}/10 records succeeded")

if __name__ == "__main__":
    asyncio.run(test())
