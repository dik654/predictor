"""
Sample data loader for PulseAI Lite.
Reads data_pos.txt and replays at configurable speed.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncIterator, Callable, List

log = logging.getLogger("sample-loader")

# Configurable delay between records (seconds)
SAMPLE_DELAY = 0.5  # 0.5s = 2 records per second


def load_all_sample_data(file_path: Path) -> List[dict]:
    """
    Load all sample data at once for batch processing.
    Adjusts timestamps to current time while preserving time intervals.

    Args:
        file_path: Path to data_pos.txt

    Returns:
        List of all data points with adjusted timestamps
    """
    if not file_path.exists():
        log.error(f"Sample file not found: {file_path}")
        return []

    data_list = []
    first_timestamp = None
    time_offset = None

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)

                # Calculate time offset from first record
                if first_timestamp is None:
                    first_timestamp = data.get("Timestamp")
                    # Parse timestamp - handles "2025-12-11 15:05:26" format (assumed local time)
                    orig_ts = datetime.strptime(first_timestamp, "%Y-%m-%d %H:%M:%S")
                    # Use UTC now (as naive datetime) to match strptime result
                    time_offset = datetime.utcnow() - orig_ts

                # Adjust timestamp to current UTC time
                if "Timestamp" in data and time_offset is not None:
                    orig_ts = datetime.strptime(data["Timestamp"], "%Y-%m-%d %H:%M:%S")
                    adjusted_ts = orig_ts + time_offset
                    # Format as ISO with Z for UTC
                    data["Timestamp"] = adjusted_ts.isoformat() + "Z"
                    # Log first few timestamps for verification
                    if len(data_list) < 3:
                        log.info(f"[TIMESTAMP] Original: {orig_ts} → Adjusted: {adjusted_ts.isoformat()}Z (offset: {time_offset})")

                data_list.append(data)
            except (json.JSONDecodeError, ValueError) as e:
                log.warning(f"Error processing line: {e}")
                continue

    log.info(f"Loaded {len(data_list)} records from {file_path}")
    return data_list


async def load_sample_file(
    file_path: Path,
    on_data: Callable[[dict], None],
    loop: bool = False,
) -> None:
    """
    Load sample file and replay at fixed speed.
    
    Args:
        file_path: Path to data_pos.txt
        on_data: Callback function for each data point
        loop: Whether to loop the file
    """
    if not file_path.exists():
        log.error(f"Sample file not found: {file_path}")
        return

    log.info(f"Loading sample file: {file_path}")
    
    while True:
        line_count = 0
        
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as e:
                    log.warning(f"Invalid JSON at line {line_count}: {e}")
                    continue
                
                # Fixed delay for consistent playback
                await asyncio.sleep(SAMPLE_DELAY)
                
                # Call the data handler
                on_data(data)
                line_count += 1
                
                if line_count % 100 == 0:
                    log.info(f"Processed {line_count} records")
        
        log.info(f"Finished processing {line_count} records")
        
        if not loop:
            break
        
        log.info("Looping sample file...")
        await asyncio.sleep(1)


async def sample_data_generator(
    file_path: Path,
    loop: bool = False,
    fast_initial_load: bool = True,
    sample_historical: bool = True,
    historical_sample_interval: int = 120,
) -> AsyncIterator[dict]:
    """
    Async generator that yields data points at fixed speed.
    Maps sample file time range to current time window (first record = now - sample_duration).

    Args:
        file_path: Path to data_pos.txt
        loop: Whether to loop the file
        fast_initial_load: If True, load entire file first without delay for quick data accumulation,
                          then continue with SAMPLE_DELAY. Good for testing 48h forecasts.
        sample_historical: If True, sample historical data during fast_initial_load phase to speed up loading
        historical_sample_interval: Load every Nth record during fast_initial_load (e.g., 120 = ~10min intervals for 5s data)

    Yields:
        dict: Parsed data point with adjusted timestamp
    """
    if not file_path.exists():
        log.error(f"Sample file not found: {file_path}")
        return

    log.info(f"Loading sample file: {file_path}")

    # Use fixed baseline time (when generator started)
    # This ensures all loops use the same time window
    baseline_time = datetime.utcnow()
    log.info(f"Baseline time: {baseline_time.isoformat()}Z (all loops will use this reference)")

    # Track cumulative time across all loops
    first_record_ts = None  # First record in current loop (used for current loop duration)
    last_record_ts = None  # Last record in current loop
    full_48h_loaded = False  # Flag: 48 hours of data accumulated
    cumulative_hours = 0.0  # Cumulative hours across all loops

    # Calculate sample file time span (first to last record)
    sample_start = None
    sample_end = None
    is_reverse_order = False
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
            if lines:
                first_data = json.loads(lines[0])
                last_data = json.loads(lines[-1])
                sample_start = datetime.strptime(first_data.get("Timestamp"), "%Y-%m-%d %H:%M:%S")
                sample_end = datetime.strptime(last_data.get("Timestamp"), "%Y-%m-%d %H:%M:%S")
                sample_duration = sample_end - sample_start

                # Check if file is in reverse order (last record is older than first)
                if sample_duration.total_seconds() < 0:
                    is_reverse_order = True
                    log.warning(f"⚠️ Sample file is in REVERSE chronological order!")
                    log.warning(f"   First record: {sample_start} (latest)")
                    log.warning(f"   Last record: {sample_end} (oldest)")
                    # Get correct duration
                    sample_duration = sample_start - sample_end
                    log.info(f"Sample file spans {sample_duration.total_seconds() / 3600:.1f} hours (reverse order)")
                else:
                    log.info(f"Sample file spans {sample_duration.total_seconds() / 3600:.1f} hours (normal order: {sample_start} to {sample_end})")
    except Exception as e:
        log.warning(f"Could not calculate sample file duration: {e}")
        sample_duration = None

    loop_count = 0

    while True:
        loop_count += 1
        line_count = 0
        first_timestamp = None
        time_offset = None
        loop_first_record_ts = None  # Track first record in this loop for duration calc
        loop_last_record_ts = None   # Track last record in this loop for duration calc

        try:
            log.debug(f"Generator: Opening file {file_path} (loop iteration #{loop_count})")
            with open(file_path, "r", encoding="utf-8") as f:
                log.debug(f"Generator: File opened successfully, starting line iteration")
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    line_count += 1

                    # Skip records if we're in fast_initial_load phase and sampling is enabled
                    if fast_initial_load and sample_historical and not full_48h_loaded:
                        # Only process every Nth record to speed up historical data loading
                        if line_count % historical_sample_interval != 0:
                            continue
                        log.debug(f"[LOOP#{loop_count}] Processing sampled record #{line_count} (every {historical_sample_interval}th record)")

                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Calculate time offset from first record of this loop iteration
                    if first_timestamp is None:
                        first_timestamp = data.get("Timestamp")
                        # Parse timestamp - handles "2025-12-11 15:05:26" format (assumed local time)
                        try:
                            orig_ts = datetime.strptime(first_timestamp, "%Y-%m-%d %H:%M:%S")
                            # IMPORTANT: Map the FIRST record to CURRENT time NOW
                            # This ensures fresh timestamps, not 90 days in the past
                            current_utc = datetime.utcnow()
                            time_offset = current_utc - orig_ts
                            log.info(f"[LOOP#{loop_count}] Time offset calculated (first record → NOW): {time_offset}")
                        except ValueError as ve:
                            log.error(f"Failed to parse timestamp '{first_timestamp}': {ve}")
                            continue

                    # Adjust timestamp to current time (local timezone)
                    if "Timestamp" in data:
                        try:
                            # Ignore sample file timestamps - use current UTC time NOW for all records
                            # This ensures all sample data is recorded at present time
                            adjusted_ts = datetime.utcnow()
                            data["Timestamp"] = adjusted_ts.isoformat() + "Z"

                            # Store nanosecond offset separately to ensure unique timestamps
                            # Include loop_count to avoid duplicate offsets across file iterations
                            # Each record gets a unique offset: (loop_count * 100000) + (line_count * 1000) nanoseconds
                            # This ensures records from different loop iterations have different offsets
                            data["_nanos_offset"] = (loop_count * 10000000) + (line_count * 1000)

                            # Log every 100 records to verify _nanos_offset is being set
                            if line_count % 100 == 0:
                                log.info(f"[LOOP#{loop_count}] Record #{line_count}: {data.get('AgentId')} @ {data.get('Timestamp')} with _nanos_offset={data['_nanos_offset']}")
                        except ValueError as ve:
                            log.error(f"Failed to parse record timestamp: {ve}")
                            continue

                    # Apply delay based on whether 48h of data is accumulated
                    # Until 48 hours are loaded: no delay (fast accumulation)
                    # After 48 hours loaded: use SAMPLE_DELAY (realistic playback)
                    if fast_initial_load and not full_48h_loaded:
                        # Still accumulating: load fast
                        await asyncio.sleep(0)  # Yield to event loop but don't delay
                    else:
                        # 48+ hours loaded: use normal SAMPLE_DELAY for realistic playback
                        await asyncio.sleep(SAMPLE_DELAY)

                    # Track timestamps for loop duration calculation
                    if fast_initial_load and not full_48h_loaded and data.get("Timestamp"):
                        try:
                            current_ts = datetime.fromisoformat(data.get("Timestamp", "").rstrip("Z"))
                            if loop_first_record_ts is None:
                                loop_first_record_ts = current_ts
                            loop_last_record_ts = current_ts  # Update on every record
                        except Exception as e:
                            log.debug(f"Error parsing timestamp: {e}")

                    try:
                        log.debug(f"Generator: About to yield record {line_count + 1}")
                        yield data
                        log.debug(f"Generator: Successfully yielded record {line_count + 1}, continuing")
                    except GeneratorExit:
                        # Client disconnected, clean shutdown
                        log.info(f"Generator closed by GeneratorExit after {line_count} records")
                        raise
                    except Exception as e:
                        log.error(f"Error while yielding data at record {line_count}: {e}", exc_info=True)
                        raise

                    line_count += 1

                log.debug(f"Generator: File reading completed, yielded {line_count} records in this iteration")
        except GeneratorExit:
            # Normal generator close
            log.info(f"Generator closed by consumer after {line_count} records")
            pass
        except Exception as e:
            log.error(f"Error in sample_data_generator at record {line_count}: {e}", exc_info=True)
            break

        log.info(f"Finished processing {line_count} records from file")

        # Calculate cumulative time from this loop
        if fast_initial_load and not full_48h_loaded and loop_first_record_ts and loop_last_record_ts:
            # Use absolute value to handle both normal and reverse-order files
            loop_duration = abs((loop_last_record_ts - loop_first_record_ts).total_seconds() / 3600)
            cumulative_hours += loop_duration
            log.info(f"[LOOP#{loop_count}] Duration: {loop_duration:.1f}h | Cumulative: {cumulative_hours:.1f}h")

            if cumulative_hours >= 48:
                full_48h_loaded = True
                status = f"with {historical_sample_interval}x sampling" if sample_historical else "full resolution"
                log.info(f"✅ 48-hour window LOADED ({cumulative_hours:.1f}h accumulated) {status}, switching to SAMPLE_DELAY")

        if not loop:
            break

        log.info("Looping sample file...")
        await asyncio.sleep(1)
