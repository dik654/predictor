#!/usr/bin/env python3
"""Quick test of sample mode to verify _nanos_offset handling"""
import asyncio
import logging
from pathlib import Path
from server.webrtc_hub.influx_writer import init_influx
from server.webrtc_hub.sample_loader import sample_data_generator

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
)
log = logging.getLogger("test_sample_mode")


async def test():
    """Test first 20 records from sample data with _nanos_offset"""
    sample_file = Path("/home/dylan/code/webrtc-hub-uv-sample/sample/data_pos.txt")

    init_influx()
    log.info(f"Testing sample mode with: {sample_file}")

    record_count = 0
    async for data in sample_data_generator(sample_file, loop=False):
        record_count += 1

        # Check if _nanos_offset is present
        if "_nanos_offset" in data:
            log.info(f"Record #{record_count}: {data.get('AgentId')} @ {data.get('Timestamp')} | _nanos_offset={data['_nanos_offset']}")
        else:
            log.warning(f"Record #{record_count}: {data.get('AgentId')} @ {data.get('Timestamp')} | NO _nanos_offset!")

        if record_count >= 20:
            log.info(f"Test complete: Read {record_count} records")
            break


if __name__ == "__main__":
    asyncio.run(test())
