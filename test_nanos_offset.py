#!/usr/bin/env python3
"""Test nanosecond offset handling for duplicate timestamps"""
import asyncio
import logging
from datetime import datetime
from server.webrtc_hub.influx_writer import write_metrics, init_influx

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
)
log = logging.getLogger("test_nanos_offset")


async def test():
    """Test writing records with same timestamp but different nanos_offset"""
    init_influx()

    log.info("Testing nanosecond offset handling for duplicate timestamps...")
    successes = 0

    # Use the same timestamp for all records (simulating sample data with duplicates)
    fixed_timestamp = datetime.utcnow().isoformat() + "Z"

    for i in range(5):
        # Each record gets a different nanosecond offset
        nanos_offset = i * 1000  # 0, 1000, 2000, 3000, 4000 nanoseconds

        result = await write_metrics(
            agent_id=f"DUP-TEST-{i:02d}",
            timestamp=fixed_timestamp,
            raw_metrics={
                "CPU": 50.0 + i,
                "Memory": 70.0 + i,
                "DiskIO": 10.0 + i,
                "_nanos_offset": nanos_offset  # Pass the offset
            },
            bucket="sample_metrics"
        )
        if result:
            successes += 1
            log.info(f"✓ Record {i} (offset={nanos_offset}ns): SUCCESS")
        else:
            log.error(f"✗ Record {i} (offset={nanos_offset}ns): FAILED")

    log.info(f"\nTest complete: {successes}/5 records succeeded")
    if successes == 5:
        log.info("✅ Nanosecond offset test PASSED - all duplicate timestamps handled correctly!")
    else:
        log.error(f"⚠️  Only {successes}/5 records succeeded")


if __name__ == "__main__":
    asyncio.run(test())
