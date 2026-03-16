"""
InfluxDB writer for PulseAI metrics and forecasts.
Stores raw metrics, ARIMA forecasts, and prediction accuracy.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Dict, Optional
import urllib.request
import urllib.error
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS, WritesRetry

log = logging.getLogger("influx_writer")

# InfluxDB connection
INFLUX_URL = "http://localhost:8086"
INFLUX_TOKEN = "pulseai-token-12345"
INFLUX_ORG = "pulseai"
INFLUX_BUCKET = "pos_metrics"

client: Optional[InfluxDBClient] = None
write_api = None
_last_reconnect = 0  # Track last reconnection time

# HTTP error rate limiting: {error_key: last_log_time}
import time as _time
_http_error_last_logged: Dict[str, float] = {}
_HTTP_ERROR_INTERVAL = 300.0  # 같은 오류는 5분에 한 번만 출력

# Thread pool for blocking InfluxDB writes
# Increased from 5 to 20 to 50 to handle concurrent writes without queue buildup
_write_executor = ThreadPoolExecutor(max_workers=50, thread_name_prefix="influx-write")
log.info("ThreadPoolExecutor initialized with 50 workers for InfluxDB writes")


def init_influx():
    """Initialize InfluxDB connection."""
    global client, write_api, _last_reconnect
    import time
    try:
        log.debug(f"[INFLUX] Initializing connection to {INFLUX_URL}")
        client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
        log.debug(f"[INFLUX] Client created")

        # Use SYNCHRONOUS write type for immediate writes without batching
        # write_precision="ns" for nanosecond precision
        log.debug(f"[INFLUX] Creating write_api with SYNCHRONOUS mode")
        write_api = client.write_api(write_type=SYNCHRONOUS, write_precision="ns")
        log.debug(f"[INFLUX] write_api created successfully")

        # Verify write_api is SYNCHRONOUS by checking its type
        log.debug(f"[INFLUX] write_api type: {type(write_api)}")
        log.debug(f"[INFLUX] write_api class: {write_api.__class__.__name__}")

        # Test connection
        log.debug(f"[INFLUX] Testing connection with ping()")
        client.ping()
        _last_reconnect = time.time()  # Reset timer so write_metrics won't immediately re-reset
        log.info(f"✓ InfluxDB connected successfully (SYNCHRONOUS mode, no batching)")
    except Exception as e:
        log.error(f"✗ InfluxDB connection failed: {type(e).__name__}: {e}", exc_info=True)
        client = None
        write_api = None


def get_executor_status():
    """Get thread pool executor status."""
    try:
        # Try to get queue size
        queue_size = _write_executor._work_queue.qsize() if hasattr(_write_executor, '_work_queue') else "unknown"
        return f"executor_queue_size={queue_size}"
    except Exception as e:
        return f"executor_status_unavailable"


async def write_metrics(agent_id: str, timestamp: str, raw_metrics: Dict, bucket: str = INFLUX_BUCKET, full_data: Dict = None) -> bool:
    """
    Write raw metrics (CPU, Memory, DiskIO) and store info to InfluxDB.

    Args:
        agent_id: Agent identifier
        timestamp: ISO timestamp string (e.g., "2026-03-11T15:05:26Z")
        raw_metrics: {"CPU": float, "Memory": float, "DiskIO": float, ...}
        bucket: InfluxDB bucket name (default: pos_metrics)
        full_data: Original data dict containing StoreInfo, Network, Process, etc.

    Returns:
        True if successful, False otherwise
    """
    import time
    global client, write_api, _last_reconnect

    # Reconnect every 60 seconds to avoid stale write_api state
    now = time.time()
    if now - _last_reconnect > 60:
        log.debug("Periodic write_api reconnection")
        client = None
        write_api = None
        _last_reconnect = now

    if not client or not write_api:
        log.warning(f"InfluxDB not connected (client={client is not None}, api={write_api is not None}). Reconnecting...")
        # Try to reconnect if connection was closed
        init_influx()
        if not client or not write_api:
            log.error(f"Reconnection failed. Skipping write.")
            return False
        log.info(f"Reconnection successful")

    try:
        # Parse timestamp string to datetime object
        try:
            ts_str = timestamp.rstrip('Z') if timestamp else ""
            ts_dt = datetime.fromisoformat(ts_str)
            # Log timestamp for diagnostics
            if timestamp and "T" in timestamp:
                log.debug(f"[TS-CHECK] {agent_id[:15]}: {timestamp}")
        except (ValueError, AttributeError) as te:
            log.warning(f"Failed to parse timestamp '{timestamp}', using current time: {te}")
            ts_dt = datetime.utcnow()

        # Get nanosecond offset if provided (from sample_loader)
        # This ensures unique timestamps even when records have the same datetime
        nanos_offset = None
        if isinstance(raw_metrics, dict):
            nanos_offset = raw_metrics.pop("_nanos_offset", None)

        # Adjust timestamp with offset if provided
        ts_to_use = ts_dt
        if nanos_offset is not None and nanos_offset > 0:
            # Add nanoseconds as microseconds (divide by 1000)
            # This maintains uniqueness for duplicate timestamps
            ts_to_use = ts_dt + timedelta(microseconds=nanos_offset/1000)

        point = Point("metrics") \
            .tag("agent_id", agent_id)

        # Extract and add store information as tags if available
        if full_data:
            store_info = full_data.get("StoreInfo", {})
            if store_info:
                point.tag("store_code", str(store_info.get("StoreCode", "")))
                point.tag("store_name", str(store_info.get("StoreName", "")))
                point.tag("region_code", str(store_info.get("RegionCode", "")))
                point.tag("region_name", str(store_info.get("RegionName", "")))
                point.tag("pos_no", str(store_info.get("PosNo", "")))

        # Add core metrics as fields
        point.field("cpu", float(raw_metrics.get("CPU", 0))) \
            .field("memory", float(raw_metrics.get("Memory", 0))) \
            .field("disk_io", float(raw_metrics.get("DiskIO", 0)))

        # Add network metrics if available
        if full_data:
            network = full_data.get("Network", {})
            if network:
                point.field("network_sent", int(network.get("Sent", 0)))
                point.field("network_recv", int(network.get("Recv", 0)))

        # Add process status if available
        if full_data:
            process = full_data.get("Process", {})
            if process:
                # Store main POS process status
                pos_status = process.get("GSRTL.CVS.POS.Shell", "")
                if pos_status:
                    point.field("pos_process_status", 1 if pos_status == "RUNNING" else 0)

        point.time(ts_to_use)

        # Run blocking write in thread pool to avoid blocking event loop
        def _write():
            global client, write_api
            try:
                log.debug(f"[WRITE] Starting write_metrics for {agent_id} to bucket={bucket}")
                log.debug(f"[WRITE] Data: cpu={raw_metrics.get('CPU'):.1f}, mem={raw_metrics.get('Memory'):.1f}, disk={raw_metrics.get('DiskIO'):.1f}")

                # Use global write_api if available, otherwise initialize
                if write_api is None:
                    log.debug("[WRITE] write_api is None, initializing")
                    init_influx()

                if write_api is None:
                    log.error("[WRITE] Failed to initialize write_api")
                    return False

                # Use Point.to_line_protocol() which handles timestamps correctly
                line_protocol_str = point.to_line_protocol()

                # Log detailed data being written
                cpu_val = raw_metrics.get('CPU', 0)
                mem_val = raw_metrics.get('Memory', 0)
                disk_val = raw_metrics.get('DiskIO', 0)

                log.debug(f"[WRITE-DATA] agent={agent_id} | ts={timestamp} | offset={nanos_offset}ns | cpu={cpu_val:.1f} mem={mem_val:.1f} disk={disk_val:.2f}")
                log.debug(f"[WRITE-PROTOCOL] bucket={bucket} | {line_protocol_str}")

                # Use HTTP API directly for reliable writes (proven to work)
                write_url = f"{INFLUX_URL}/api/v2/write?org={INFLUX_ORG}&bucket={bucket}"

                req = urllib.request.Request(
                    write_url,
                    data=line_protocol_str.encode('utf-8'),
                    headers={
                        "Authorization": f"Token {INFLUX_TOKEN}",
                        "Content-Type": "text/plain; charset=utf-8"
                    },
                    method="POST"
                )

                try:
                    with urllib.request.urlopen(req, timeout=30) as response:
                        response_status = response.status
                        # Always read response body to properly close connection
                        response_body = response.read().decode('utf-8') if response_status != 204 else ""

                        if response_status not in [200, 204]:
                            log.error(f"[HTTP-ERROR] Unexpected status {response_status}: {response_body}")
                            return False
                except urllib.error.HTTPError as http_err:
                    error_body = http_err.read().decode('utf-8') if hasattr(http_err, 'read') else str(http_err)
                    err_key = f"{http_err.code}:{bucket}"
                    now = _time.time()
                    if now - _http_error_last_logged.get(err_key, 0) >= _HTTP_ERROR_INTERVAL:
                        _http_error_last_logged[err_key] = now
                        log.error(f"[HTTP-ERROR] {http_err.code} - {error_body[:200]}")
                    return False

                # Success! Log complete write info
                log.debug(f"✓ [WRITE-SUCCESS] agent={agent_id} bucket={bucket} ts={timestamp} offset={nanos_offset}ns | cpu={cpu_val:.1f} mem={mem_val:.1f} disk={disk_val:.2f}")
                return True
            except Exception as write_err:
                log.error(f"✗ Write FAILED in executor for {agent_id}: {type(write_err).__name__}: {write_err}", exc_info=True)
                # Reset connection on error
                client = None
                write_api = None
                raise

        log.debug(f"[WRITE] Starting executor task for {agent_id}")
        loop = asyncio.get_event_loop()
        queue_size = _write_executor._work_queue.qsize() if hasattr(_write_executor, '_work_queue') else "unknown"
        if queue_size != "unknown" and queue_size > 10:
            log.warning(f"[WRITE] Executor queue size: {queue_size} (getting backed up)")
        try:
            # Increased timeout from 10s to 30s to allow for InfluxDB latency
            result = await asyncio.wait_for(loop.run_in_executor(_write_executor, _write), timeout=30.0)
            log.debug(f"[WRITE] Executor returned: {result}")
            return result
        except asyncio.TimeoutError:
            log.error(f"✗ Write TIMEOUT after 30s for {agent_id} - Executor queue may be overloaded")
            queue_size = _write_executor._work_queue.qsize() if hasattr(_write_executor, '_work_queue') else "unknown"
            log.error(f"  Queue size at timeout: {queue_size}")
            client = None
            write_api = None
            return False
        except Exception as executor_err:
            log.error(f"✗ Executor error for {agent_id}: {type(executor_err).__name__}: {executor_err}", exc_info=True)
            return False
    except Exception as e:
        # Silently fail if this is a shutdown error
        if "shutdown" in str(e).lower():
            return False
        log.error(f"Failed to write metrics to {bucket}: {type(e).__name__}: {e}", exc_info=True)

        # Always reset connection on error - reconnect next time
        client = None
        write_api = None
        log.warning(f"Resetting InfluxDB connection due to error")

        return False


async def write_forecast(
    agent_id: str,
    timestamp: str,
    metric: str,
    horizon_min: int,
    predicted_value: float,
    bucket: str = INFLUX_BUCKET,
) -> bool:
    """
    Write ARIMA forecast to InfluxDB.

    Args:
        agent_id: Agent identifier
        timestamp: Prediction timestamp string (when forecast was made, e.g., "2026-03-11T15:05:26Z")
        metric: "CPU" or "Memory"
        horizon_min: Minutes into future (30, 60, 120)
        predicted_value: Predicted metric value
        bucket: InfluxDB bucket name (default: pos_metrics)

    Returns:
        True if successful, False otherwise
    """
    global client, write_api

    if not client or not write_api:
        # Try to reconnect if connection was closed
        init_influx()
        if not client or not write_api:
            return False

    try:
        # Parse timestamp string to datetime object
        try:
            ts_str = timestamp.rstrip('Z') if timestamp else ""
            ts_dt = datetime.fromisoformat(ts_str)
        except (ValueError, AttributeError) as te:
            log.warning(f"Failed to parse forecast timestamp '{timestamp}', using current time: {te}")
            ts_dt = datetime.utcnow()

        point = Point("forecast") \
            .tag("agent_id", agent_id) \
            .tag("metric", metric) \
            .tag("horizon_min", str(horizon_min)) \
            .field("predicted_value", float(predicted_value)) \
            .field("horizon_minutes", int(horizon_min)) \
            .time(ts_dt)

        # Run blocking write in thread pool to avoid blocking event loop
        def _write():
            try:
                write_api.write(bucket=bucket, record=point)
                write_api.flush()
                log.debug(f"Writing forecast: {agent_id}/{metric} +{horizon_min}min = {predicted_value:.2f}")
                return True
            except Exception as write_err:
                log.error(f"✗ Forecast FAILED: {agent_id}/{metric} +{horizon_min}min: {type(write_err).__name__}: {write_err}", exc_info=True)
                raise

        loop = asyncio.get_event_loop()
        try:
            result = await asyncio.wait_for(loop.run_in_executor(_write_executor, _write), timeout=10.0)
            return result
        except asyncio.TimeoutError:
            log.error(f"✗ Forecast write TIMEOUT after 10s for {agent_id}/{metric}")
            return False
        except Exception as executor_err:
            log.error(f"✗ Executor error in forecast: {type(executor_err).__name__}: {executor_err}", exc_info=True)
            return False
    except Exception as e:
        if "shutdown" not in str(e).lower():
            log.error(f"Failed to write forecast: {e}")

        # Only reset connection on connection-related errors
        if any(err in str(type(e).__name__).lower() for err in ["connection", "timeout", "refused"]):
            log.warning(f"Connection error in forecast write, will attempt reconnect next time")
            client = None
            write_api = None
        else:
            # For other errors, keep connection alive
            log.debug(f"Non-connection error in forecast write, keeping connection")

        return False


async def update_forecast_actual(
    agent_id: str,
    metric: str,
    horizon_min: int,
    actual_value: float,
    forecast_value: float,
    error_pct: float,
    bucket: str = INFLUX_BUCKET,
) -> bool:
    """
    Update forecast with actual value and error percentage.

    Args:
        agent_id: Agent identifier
        metric: "CPU" or "Memory"
        horizon_min: Minutes horizon
        actual_value: Actual measured value
        forecast_value: Predicted value
        error_pct: |predicted - actual| / actual * 100
        bucket: InfluxDB bucket name (default: pos_metrics)

    Returns:
        True if successful, False otherwise
    """
    global client, write_api

    if not client or not write_api:
        # Try to reconnect if connection was closed
        init_influx()
        if not client or not write_api:
            return False

    try:
        point = Point("accuracy") \
            .tag("agent_id", agent_id) \
            .tag("metric", metric) \
            .tag("horizon_min", str(horizon_min)) \
            .field("actual_value", float(actual_value)) \
            .field("forecast_value", float(forecast_value)) \
            .field("error_pct", float(error_pct)) \
            .field("within_3sigma", 1 if error_pct <= 3.0 else 0)

        # Run blocking write in thread pool to avoid blocking event loop
        def _write_accuracy():
            write_api.write(bucket=bucket, record=point)
            write_api.flush()

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(_write_executor, _write_accuracy)
        return True
    except Exception as e:
        if "shutdown" not in str(e).lower():
            log.error(f"Failed to update forecast accuracy: {e}")
        # Reset connection on error to try reconnect next time
        client = None
        write_api = None
        return False


def get_latest_accuracy(
    agent_id: str,
    metric: str,
    horizon_min: int,
    limit: int = 100,
    bucket: str | None = None,
) -> Dict:
    """
    Query latest accuracy data for a metric.

    Args:
        agent_id: Agent identifier
        metric: "CPU" or "Memory"
        horizon_min: Minutes horizon
        limit: Max records to return

    Returns:
        Dict with accuracy statistics
    """
    target_bucket = bucket if bucket is not None else INFLUX_BUCKET
    log.info(f"🔍 get_latest_accuracy called: agent={agent_id}, metric={metric}, horizon={horizon_min}, bucket={target_bucket}")

    if not client:
        log.error("❌ InfluxDB client not connected")
        return {}

    try:
        query = f"""
        from(bucket: "{target_bucket}")
          |> range(start: -72h)
          |> filter(fn: (r) => r._measurement == "accuracy")
          |> filter(fn: (r) => r.agent_id == "{agent_id}")
          |> filter(fn: (r) => r.metric == "{metric}")
          |> filter(fn: (r) => r.horizon_min == "{horizon_min}")
          |> sort(columns: ["_time"], desc: true)
          |> limit(n: {limit})
          |> sort(columns: ["_time"], desc: false)
        """

        query_api = client.query_api()
        tables = query_api.query(query)

        data = {"records": [], "stats": {}}
        errors = []
        within_3sigma_count = 0

        # Build a map to accumulate fields per timestamp
        records_map = {}

        for table in tables:
            for record in table.records:
                ts = str(record.get_time())
                field = record.get_field()
                value = record.values.get("_value")

                if ts not in records_map:
                    records_map[ts] = {
                        "timestamp": ts,
                        "horizon_min": horizon_min,
                        "actual_value": None,
                        "forecast_value": None,
                        "error_pct": None,
                    }

                # Accumulate values for each field
                if field == "actual_value" and value is not None:
                    records_map[ts]["actual_value"] = float(value)
                elif field == "forecast_value" and value is not None:
                    records_map[ts]["forecast_value"] = float(value)
                elif field == "error_pct" and value is not None:
                    records_map[ts]["error_pct"] = float(value)
                    errors.append(float(value))
                elif field == "within_3sigma" and value == 1:
                    within_3sigma_count += 1

        # Convert map to list, only include records with error_pct
        data["records"] = [r for r in records_map.values() if r["error_pct"] is not None]

        log.info(f"✅ Query accuracy: agent={agent_id}, metric={metric}, horizon={horizon_min} -> {len(data['records'])} records with error_pct")

        if errors:
            import numpy as np
            errors = np.array(errors)
            data["stats"] = {
                "count": len(errors),
                "mean_error_pct": float(np.mean(errors)),
                "std_error": float(np.std(errors)),
                "min_error_pct": float(np.min(errors)),
                "max_error_pct": float(np.max(errors)),
                "within_3sigma_pct": (within_3sigma_count / len(errors) * 100) if errors.size > 0 else 0,
            }

        return data
    except Exception as e:
        log.warning(f"Failed to query accuracy: {e}")
        return {}


def close_influx():
    """Close InfluxDB connection and flush pending writes."""
    global client, _write_executor
    if client:
        try:
            # Wait for pending executor tasks to complete before closing
            # Note: timeout parameter not available in Python 3.10, so we omit it
            _write_executor.shutdown(wait=True)
        except Exception as e:
            log.warning(f"Error shutting down executor: {e}")

        try:
            client.close()
            log.info("InfluxDB connection closed")
        except Exception as e:
            log.warning(f"Error closing InfluxDB: {e}")
        finally:
            client = None
