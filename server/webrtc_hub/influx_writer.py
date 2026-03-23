"""
InfluxDB writer for PulseAI metrics and forecasts.
Stores raw metrics, ARIMA forecasts, and prediction accuracy.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
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


KST_OFFSET = timedelta(hours=9)

# 한글 → 영문 매핑
DEVICE_NAME_MAP = {
    "동글이": "dongle",
    "스캐너-핸드스캐너": "hand_scanner",
    "여권리더기": "passport_reader",
    "스캐너-2D스캐너": "2d_scanner",
    "휴대폰충전기": "phone_charger",
    "키보드": "keyboard",
    "MSR": "msr",
}
DEVICE_STATUS_MAP = {"연결": 1, "실패": 0, "미사용": -1}

BODY_TYPE_MAP = {
    "주변장치 체크": "peripheral_check",
    "승인 처리시간": "payment_approval_time",
    "승인 처리결과": "payment_approval_result",
    "영수증프린터 커버상태": "receipt_printer_cover",
    "영수증 용지상태": "receipt_paper_status",
}

# 주변장치 영문 필드 목록 (ECOD feature용)
PERIPHERAL_FIELDS = ["dongle", "hand_scanner", "passport_reader", "2d_scanner", "phone_charger", "keyboard", "msr"]


def _parse_timestamp(timestamp: str) -> datetime:
    """C# 에이전트 타임스탬프(KST, timezone-naive)를 UTC datetime으로 변환."""
    try:
        ts_str = timestamp.rstrip('Z') if timestamp else ""
        ts_dt = datetime.fromisoformat(ts_str)
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt - KST_OFFSET
        return ts_dt
    except (ValueError, AttributeError):
        log.warning(f"Failed to parse timestamp '{timestamp}', using current time")
        return datetime.utcnow()


def _apply_store_tags(point: Point, store_info: Dict) -> Point:
    """모든 measurement에 동일한 StoreInfo 태그를 적용하는 공통 헬퍼."""
    if store_info:
        point.tag("store_code", str(store_info.get("StoreCode", "")))
        point.tag("store_name", str(store_info.get("StoreName", "")))
        point.tag("zip_code", str(store_info.get("ZipCode", "")))
        point.tag("address", str(store_info.get("Address", "")))
        point.tag("region_code", str(store_info.get("RegionCode", "")))
        point.tag("region_name", str(store_info.get("RegionName", "")))
        point.tag("pos_no", str(store_info.get("PosNo", "")))
    return point


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
    # Reconnection is handled inside _write() (runs in thread pool)
    # to avoid blocking the asyncio event loop.

    try:
        # Parse timestamp string to datetime object
        ts_dt = _parse_timestamp(timestamp)

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
            _apply_store_tags(point, full_data.get("StoreInfo", {}))

        # Add core metrics as fields
        point.field("cpu", float(raw_metrics.get("CPU", 0))) \
            .field("memory", float(raw_metrics.get("Memory", 0))) \
            .field("disk_io", float(raw_metrics.get("DiskIO", 0)))

        # Add network metrics if available
        if full_data:
            network = full_data.get("Network", {})
            if network:
                point.field("network_sent_bytes", int(network.get("Sent", 0)))
                point.field("network_received_bytes", int(network.get("Recv", 0)))

        # Process status → metrics에 숫자로 포함 (1=running, 0=stopped)
        if full_data:
            process = full_data.get("Process", {})
            if process:
                _proc_status_map = {"RUNNING": 1, "STOPPED": 0}
                for proc_name, proc_status in process.items():
                    point.field(f"process_{proc_name}", _proc_status_map.get(str(proc_status).upper(), 0))

        extra_points = []

        # FileVersions → separate measurement
        if full_data:
            file_versions = full_data.get("FileVersions", [])
            if file_versions:
                fv_point = Point("file_versions") \
                    .tag("agent_id", agent_id)
                _apply_store_tags(fv_point, full_data.get("StoreInfo", {}))
                for fv in file_versions:
                    file_name = fv.get("FileName", "")
                    file_version = fv.get("FileVersion", "")
                    if file_name and file_version:
                        fv_point.field(file_name, file_version)
                fv_point.time(ts_to_use)
                extra_points.append(fv_point)

        point.time(ts_to_use)

        # Run blocking write in thread pool to avoid blocking event loop
        def _write():
            global client, write_api, _last_reconnect
            try:
                log.debug(f"[WRITE] Starting write_metrics for {agent_id} to bucket={bucket}")
                log.debug(f"[WRITE] Data: cpu={raw_metrics.get('CPU'):.1f}, mem={raw_metrics.get('Memory'):.1f}, disk={raw_metrics.get('DiskIO'):.1f}")

                # Periodic reconnection (runs in thread pool, not event loop)
                import time
                now = time.time()
                if now - _last_reconnect > 60:
                    log.debug("Periodic write_api reconnection (in executor)")
                    client = None
                    write_api = None
                    _last_reconnect = now

                if not client or not write_api:
                    log.info("InfluxDB not connected. Reconnecting in executor...")
                    init_influx()

                if write_api is None:
                    log.error("[WRITE] Failed to initialize write_api")
                    return False

                # Use Point.to_line_protocol() which handles timestamps correctly
                # Combine metrics point with extra points (file_versions, process_status)
                all_lines = [point.to_line_protocol()]
                for ep in extra_points:
                    all_lines.append(ep.to_line_protocol())
                line_protocol_str = "\n".join(all_lines)

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
    store_info: Dict = None,
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
        ts_dt = _parse_timestamp(timestamp)

        point = Point("arima_forecast") \
            .tag("agent_id", agent_id) \
            .tag("metric", metric) \
            .tag("horizon_min", str(horizon_min))

        _apply_store_tags(point, store_info)

        point.field("predicted_value", float(predicted_value)) \
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
    store_info: Dict = None,
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
    # Reconnection handled inside executor to avoid blocking event loop.

    try:
        point = Point("accuracy") \
            .tag("agent_id", agent_id) \
            .tag("metric", metric)

        if store_info:
            point.tag("store_code", str(store_info.get("StoreCode", "")))
            point.tag("pos_no", str(store_info.get("PosNo", "")))
            point.tag("region_code", str(store_info.get("RegionCode", "")))

        point \
            .tag("horizon_min", str(horizon_min)) \
            .field("actual_value", float(actual_value)) \
            .field("forecast_value", float(forecast_value)) \
            .field("error_percent", float(error_pct)) \
            .field("within_3sigma", 1 if error_pct <= 3.0 else 0)

        def _write_accuracy():
            global client, write_api, _last_reconnect
            import time
            now = time.time()
            if now - _last_reconnect > 60:
                client = None
                write_api = None
                _last_reconnect = now
            if not client or not write_api:
                log.info("InfluxDB reconnecting in accuracy executor...")
                init_influx()
            if not write_api:
                return False
            line = point.to_line_protocol()
            import urllib.request, urllib.error
            write_url = f"{INFLUX_URL}/api/v2/write?org={INFLUX_ORG}&bucket={bucket}"
            req = urllib.request.Request(
                write_url, data=line.encode('utf-8'),
                headers={"Authorization": f"Token {INFLUX_TOKEN}", "Content-Type": "text/plain; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status not in [200, 204]:
                    return False
            return True

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(_write_executor, _write_accuracy)
        return True
    except Exception as e:
        if "shutdown" not in str(e).lower():
            log.warning(f"Failed to update forecast accuracy: {e}")
        return False


def get_latest_peripheral_status(
    agent_id: str,
    bucket: str | None = None,
) -> Dict:
    """Query latest peripheral device status with disconnection time.
    Returns {device: {"status": 1/0/-1, "since": "ISO timestamp" or null}}
    """
    target_bucket = bucket if bucket is not None else INFLUX_BUCKET
    if not client:
        return {}
    try:
        query_api = client.query_api()

        # 1. 최신 상태 가져오기
        query = f'''
        from(bucket: "{target_bucket}")
          |> range(start: -24h)
          |> filter(fn: (r) => r._measurement == "peripheral_status")
          |> filter(fn: (r) => r.agent_id == "{agent_id}")
          |> last()
        '''
        tables = query_api.query(query)
        latest: Dict = {}
        for table in tables:
            for record in table.records:
                field = record.get_field()
                value = record.get_value()
                if field in PERIPHERAL_FIELDS and value is not None:
                    latest[field] = int(value)

        # 2. 실패(0) 장치의 마지막 연결(1) 시점 조회
        result: Dict = {}
        for device in PERIPHERAL_FIELDS:
            status = latest.get(device)
            if status is None:
                result[device] = {"status": None, "since": None}
                continue

            since = None
            if status == 0:
                # 마지막으로 연결(1)이었던 시점 = 끊어진 시점
                since_query = f'''
                from(bucket: "{target_bucket}")
                  |> range(start: -7d)
                  |> filter(fn: (r) => r._measurement == "peripheral_status")
                  |> filter(fn: (r) => r.agent_id == "{agent_id}")
                  |> filter(fn: (r) => r._field == "{device}" and r._value == 1)
                  |> last()
                '''
                try:
                    since_tables = query_api.query(since_query)
                    for t in since_tables:
                        for r in t.records:
                            since = str(r.get_time())
                except Exception:
                    pass

            result[device] = {"status": status, "since": since}

        return result
    except Exception as e:
        log.warning(f"Failed to query peripheral status: {e}")
        return {}


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
          |> range(start: -7d)
          |> filter(fn: (r) => r._measurement == "accuracy")
          |> filter(fn: (r) => r.agent_id == "{agent_id}")
          |> filter(fn: (r) => r.metric == "{metric}")
          |> filter(fn: (r) => r.horizon_min == "{horizon_min}")
          |> tail(n: {limit})
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
                        "error_percent": None,
                    }

                # Accumulate values for each field
                if field == "actual_value" and value is not None:
                    records_map[ts]["actual_value"] = float(value)
                elif field == "forecast_value" and value is not None:
                    records_map[ts]["forecast_value"] = float(value)
                elif field == "error_percent" and value is not None:
                    records_map[ts]["error_percent"] = float(value)
                    errors.append(float(value))
                elif field == "within_3sigma" and value == 1:
                    within_3sigma_count += 1

        # Convert map to list, only include records with error_pct
        data["records"] = [r for r in records_map.values() if r["error_percent"] is not None]

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


def get_historical_metrics(
    agent_id: str,
    hours: int = 168,
    resolution_minutes: int = 5,
    bucket: str | None = None,
) -> Optional[Dict]:
    """
    Query historical metrics from InfluxDB for long-term ECOD training.

    Args:
        agent_id: Agent identifier
        hours: How many hours of history to fetch (default: 168 = 7 days)
        resolution_minutes: Downsample resolution in minutes (default: 5)
        bucket: InfluxDB bucket name

    Returns:
        Dict with 'cpu', 'memory', 'disk_io' numpy arrays, or None if unavailable
    """
    target_bucket = bucket if bucket is not None else INFLUX_BUCKET

    if not client:
        log.debug("InfluxDB not connected, cannot fetch historical metrics")
        return None

    try:
        import numpy as np

        # metrics measurement에서 연속값 가져오기
        all_metric_fields = ["cpu", "memory", "disk_io", "network_sent_bytes", "network_received_bytes"]
        # process_ 접두사 필드도 포함
        field_filter = " or ".join(f'r._field == "{f}"' for f in all_metric_fields)
        field_filter += ' or r._field =~ /^process_/'

        query = f'''
        from(bucket: "{target_bucket}")
          |> range(start: -{hours}h)
          |> filter(fn: (r) => r._measurement == "metrics")
          |> filter(fn: (r) => r.agent_id == "{agent_id}")
          |> filter(fn: (r) => {field_filter})
          |> aggregateWindow(every: {resolution_minutes}m, fn: mean, createEmpty: false)
          |> sort(columns: ["_time"], desc: false)
        '''

        query_api = client.query_api()
        tables = query_api.query(query)

        # Collect values by field
        fields: Dict[str, list] = {f: [] for f in all_metric_fields}
        process_fields: Dict[str, list] = {}
        for table in tables:
            for record in table.records:
                field = record.get_field()
                value = record.get_value()
                if value is None:
                    continue
                if field in fields:
                    fields[field].append(float(value))
                elif field.startswith("process_"):
                    process_fields.setdefault(field, []).append(float(value))

        # peripheral_status measurement에서 주변장치 이력 가져오기
        periph_query = f'''
        from(bucket: "{target_bucket}")
          |> range(start: -{hours}h)
          |> filter(fn: (r) => r._measurement == "peripheral_status")
          |> filter(fn: (r) => r.agent_id == "{agent_id}")
          |> aggregateWindow(every: {resolution_minutes}m, fn: last, createEmpty: true)
          |> fill(usePrevious: true)
          |> sort(columns: ["_time"], desc: false)
        '''
        periph_fields: Dict[str, list] = {f: [] for f in PERIPHERAL_FIELDS}
        try:
            periph_tables = query_api.query(periph_query)
            for table in periph_tables:
                for record in table.records:
                    field = record.get_field()
                    value = record.get_value()
                    if field in periph_fields:
                        periph_fields[field].append(float(value) if value is not None else -1)
        except Exception as pe:
            log.debug(f"No peripheral history for {agent_id}: {pe}")

        # Ensure core fields have data and same length
        core_fields = ["cpu", "memory", "disk_io"]
        min_len = min(len(fields[f]) for f in core_fields) if all(fields[f] for f in core_fields) else 0
        if min_len < 20:
            log.debug(f"Not enough historical data for {agent_id}: {min_len} points")
            return None

        result: Dict = {
            "cpu": np.array(fields["cpu"][:min_len]),
            "memory": np.array(fields["memory"][:min_len]),
            "disk_io": np.array(fields["disk_io"][:min_len]),
            "count": min_len,
        }

        # 추가 필드: 길이 맞추기 (부족하면 0으로 패딩)
        for f in ["network_sent_bytes", "network_received_bytes"]:
            arr = fields.get(f, [])
            if len(arr) >= min_len:
                result[f] = np.array(arr[:min_len])
            else:
                result[f] = np.zeros(min_len)

        # process 필드 (첫 번째 process_ 필드만 사용)
        if process_fields:
            first_proc = list(process_fields.values())[0]
            result["process"] = np.array(first_proc[:min_len]) if len(first_proc) >= min_len else np.zeros(min_len)
        else:
            result["process"] = np.zeros(min_len)

        # peripheral 필드
        for pf in PERIPHERAL_FIELDS:
            arr = periph_fields.get(pf, [])
            if len(arr) >= min_len:
                result[pf] = np.array(arr[:min_len])
            else:
                result[pf] = np.full(min_len, -1.0)

        log.info(f"Fetched {min_len} historical points for {agent_id} ({hours}h, {resolution_minutes}m resolution, {len(result)-1} features)")
        return result

    except Exception as e:
        log.warning(f"Failed to fetch historical metrics for {agent_id}: {e}")
        return None


async def write_forecast_evaluation(
    agent_id: str,
    timestamp: str,
    horizons: list,
    overall_severity: str,
    model_ready: bool,
    data_source: str,
    bucket: str = INFLUX_BUCKET,
    store_info: Dict = None,
) -> bool:
    """
    Write forecast evaluation results to InfluxDB for persistence.
    Each horizon is stored as a separate point.
    """
    global client, write_api

    if not client or not write_api:
        init_influx()
        if not client or not write_api:
            return False

    try:
        ts_dt = _parse_timestamp(timestamp)

        points = []
        for h in horizons:
            point = Point("arima_ecod_ensemble_forecast_eval") \
                .tag("agent_id", agent_id) \
                .tag("horizon_min", str(h.get("horizon_min", 0)))

            _apply_store_tags(point, store_info)

            point \
                .tag("severity", h.get("severity", "normal")) \
                .tag("overall_severity", overall_severity) \
                .tag("data_source", data_source) \
                .field("predicted_cpu", float(h.get("predicted_cpu", 0))) \
                .field("predicted_memory", float(h.get("predicted_memory", 0))) \
                .field("predicted_disk_io", float(h.get("predicted_disk_io", 0))) \
                .field("ecod_score", float(h.get("ecod_score", 0))) \
                .field("rule_score", float(h.get("rule_score", 0))) \
                .field("final_score", float(h.get("final_score", 0))) \
                .field("reliability", float(h.get("reliability", 0))) \
                .field("is_outlier", 1 if h.get("is_outlier") else 0) \
                .field("model_ready", 1 if model_ready else 0) \
                .time(ts_dt)

            # Feature contributions (flattened)
            for fc in h.get("feature_contributions", []):
                metric_key = fc.get("metric", "").lower().replace(" ", "_")
                if metric_key:
                    point.field(f"contribution_{metric_key}_percent", float(fc.get("pct", 0)))
                    point.field(f"contribution_{metric_key}_score", float(fc.get("score", 0)))
            points.append(point)

        def _write():
            for p in points:
                line = p.to_line_protocol()
                write_url = f"{INFLUX_URL}/api/v2/write?org={INFLUX_ORG}&bucket={bucket}"
                req = urllib.request.Request(
                    write_url,
                    data=line.encode('utf-8'),
                    headers={
                        "Authorization": f"Token {INFLUX_TOKEN}",
                        "Content-Type": "text/plain; charset=utf-8",
                    },
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        if resp.status not in [200, 204]:
                            return False
                except urllib.error.HTTPError:
                    return False
            return True

        loop = asyncio.get_event_loop()
        return await asyncio.wait_for(loop.run_in_executor(_write_executor, _write), timeout=15.0)

    except Exception as e:
        if "shutdown" not in str(e).lower():
            log.warning(f"Failed to write forecast evaluation: {e}")
        return False


def get_latest_forecast_evaluation(
    agent_id: str,
    bucket: str | None = None,
) -> Optional[Dict]:
    """
    Query the latest forecast evaluation from InfluxDB.
    Uses last() to get the most recent value per horizon per field.

    Returns:
        Dict matching ForecastEvaluation format, or None if unavailable
    """
    target_bucket = bucket if bucket is not None else INFLUX_BUCKET

    if not client:
        return None

    try:
        # Use last() to get only the most recent value per field per horizon
        query = f'''
        from(bucket: "{target_bucket}")
          |> range(start: -7d)
          |> filter(fn: (r) => r._measurement == "arima_ecod_ensemble_forecast_eval")
          |> filter(fn: (r) => r.agent_id == "{agent_id}")
          |> last()
        '''

        query_api = client.query_api()
        tables = query_api.query(query)

        # Group by horizon_min
        horizons_map: Dict[str, Dict] = {}
        latest_time = None
        overall_severity = "normal"

        for table in tables:
            for record in table.records:
                h_min = record.values.get("horizon_min", "0")
                field = record.get_field()
                value = record.get_value()
                ts = str(record.get_time())

                if latest_time is None or ts > latest_time:
                    latest_time = ts

                # severity, overall_severity, data_source are now tags
                rec_severity = record.values.get("severity", "normal")
                rec_overall = record.values.get("overall_severity", "normal")
                if rec_overall != "normal":
                    overall_severity = rec_overall

                if h_min not in horizons_map:
                    horizons_map[h_min] = {
                        "horizon_min": int(h_min),
                        "severity": rec_severity,
                        "data_source": record.values.get("data_source", "none"),
                    }

                h = horizons_map[h_min]
                if field == "predicted_cpu" and value is not None:
                    h["predicted_cpu"] = float(value)
                elif field == "predicted_memory" and value is not None:
                    h["predicted_memory"] = float(value)
                elif field == "predicted_disk_io" and value is not None:
                    h["predicted_disk_io"] = float(value)
                elif field == "ecod_score" and value is not None:
                    h["ecod_score"] = float(value)
                elif field == "rule_score" and value is not None:
                    h["rule_score"] = float(value)
                elif field == "final_score" and value is not None:
                    h["final_score"] = float(value)
                elif field == "reliability" and value is not None:
                    h["reliability"] = float(value)
                elif field == "is_outlier":
                    h["is_outlier"] = bool(value)
                elif field == "model_ready":
                    h["model_ready"] = bool(value)
                elif field.startswith("contribution_") and field.endswith("_percent") and value is not None:
                    metric_key = field[len("contribution_"):-len("_percent")]
                    metric_name = {"cpu": "CPU", "memory": "Memory", "disk_io": "DiskIO", "networksent": "NetworkSent", "networkrecv": "NetworkRecv", "process": "Process", "dongle": "Dongle", "hand_scanner": "HandScanner", "passport_reader": "PassportReader", "2d_scanner": "2DScanner", "phone_charger": "PhoneCharger", "keyboard": "Keyboard", "msr": "MSR", "pos_idle": "POS_Idle"}.get(metric_key, metric_key)
                    if "feature_contributions" not in h:
                        h["feature_contributions"] = {}
                    if metric_name not in h["feature_contributions"]:
                        h["feature_contributions"][metric_name] = {"metric": metric_name, "pct": 0, "score": 0, "predicted_value": 0}
                    h["feature_contributions"][metric_name]["pct"] = float(value)
                elif field.startswith("contribution_") and field.endswith("_score") and value is not None:
                    metric_key = field[len("contribution_"):-len("_score")]
                    metric_name = {"cpu": "CPU", "memory": "Memory", "disk_io": "DiskIO", "networksent": "NetworkSent", "networkrecv": "NetworkRecv", "process": "Process", "dongle": "Dongle", "hand_scanner": "HandScanner", "passport_reader": "PassportReader", "2d_scanner": "2DScanner", "phone_charger": "PhoneCharger", "keyboard": "Keyboard", "msr": "MSR", "pos_idle": "POS_Idle"}.get(metric_key, metric_key)
                    if "feature_contributions" not in h:
                        h["feature_contributions"] = {}
                    if metric_name not in h["feature_contributions"]:
                        h["feature_contributions"][metric_name] = {"metric": metric_name, "pct": 0, "score": 0, "predicted_value": 0}
                    h["feature_contributions"][metric_name]["score"] = float(value)

        if not horizons_map:
            return None

        # Build labels
        def _label(minutes: int) -> str:
            if minutes < 60:
                return f"{minutes}분 후"
            elif minutes < 1440:
                return f"{minutes // 60}시간 후"
            return f"{minutes // 1440}일 후"

        horizons = sorted(horizons_map.values(), key=lambda x: x.get("horizon_min", 0))
        for h in horizons:
            h["horizon_label"] = _label(h.get("horizon_min", 0))
            h.setdefault("predicted_cpu", 0)
            h.setdefault("predicted_memory", 0)
            h.setdefault("predicted_disk_io", 0)
            h.setdefault("ecod_score", 0)
            h.setdefault("rule_score", 0)
            h.setdefault("final_score", 0)
            h.setdefault("reliability", 0.5)
            h.setdefault("is_outlier", False)

            # Convert feature_contributions dict → sorted list + fill predicted_value
            fc_dict = h.pop("feature_contributions", {})
            if fc_dict:
                fc_list = sorted(fc_dict.values(), key=lambda x: x.get("score", 0), reverse=True)
                # Fill predicted_value from horizon data
                for fc in fc_list:
                    if fc["metric"] == "CPU":
                        fc["predicted_value"] = h["predicted_cpu"]
                    elif fc["metric"] == "Memory":
                        fc["predicted_value"] = h["predicted_memory"]
                    elif fc["metric"] == "DiskIO":
                        fc["predicted_value"] = h["predicted_disk_io"]
                h["feature_contributions"] = fc_list
            else:
                h["feature_contributions"] = []

        model_ready = any(h.get("model_ready") for h in horizons)
        data_source = next((h.get("data_source", "none") for h in horizons if h.get("data_source")), "none")

        return {
            "type": "forecast_evaluation",
            "agent_id": agent_id,
            "timestamp": latest_time or "",
            "overall_severity": overall_severity,
            "model_ready": model_ready,
            "data_source": data_source,
            "horizons": horizons,
        }

    except Exception as e:
        log.warning(f"Failed to query forecast evaluation: {e}")
        return None


async def write_peripheral_status(
    agent_id: str,
    timestamp: str,
    peripherals: Dict[str, str],
    bucket: str = INFLUX_BUCKET,
    store_info: Dict = None,
) -> bool:
    """
    Write peripheral device status to InfluxDB.

    Measurement: 'peripheral_status'
    Tags: agent_id, store_code, pos_no
    Fields: device name → status value (1=연결, 0=실패, -1=미사용)
    """
    global client, write_api

    if not client or not write_api:
        init_influx()
        if not client or not write_api:
            return False

    if not peripherals:
        return True

    try:
        ts_dt = _parse_timestamp(timestamp)

        point = Point("peripheral_status") \
            .tag("agent_id", agent_id)
        _apply_store_tags(point, store_info)

        for device, status in peripherals.items():
            field_name = DEVICE_NAME_MAP.get(device, device)
            point.field(field_name, DEVICE_STATUS_MAP.get(status, -1))

        point.time(ts_dt)

        def _write():
            line = point.to_line_protocol()
            write_url = f"{INFLUX_URL}/api/v2/write?org={INFLUX_ORG}&bucket={bucket}"
            req = urllib.request.Request(
                write_url,
                data=line.encode('utf-8'),
                headers={
                    "Authorization": f"Token {INFLUX_TOKEN}",
                    "Content-Type": "text/plain; charset=utf-8",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status not in [200, 204]:
                        return False
            except urllib.error.HTTPError:
                return False
            return True

        loop = asyncio.get_event_loop()
        return await asyncio.wait_for(loop.run_in_executor(_write_executor, _write), timeout=10.0)

    except Exception as e:
        if "shutdown" not in str(e).lower():
            log.warning(f"Failed to write peripheral status: {e}")
        return False


async def write_log_entry(
    agent_id: str,
    timestamp: str,
    body_type: str,
    key_values: Dict[str, str],
    bucket: str = INFLUX_BUCKET,
    store_info: Dict = None,
) -> bool:
    """
    Write non-peripheral log entries to InfluxDB.
    BodyType: 승인 처리시간, 승인 처리결과, 영수증프린터 커버상태, 영수증 용지상태
    """
    global client, write_api

    if not client or not write_api:
        init_influx()
        if not client or not write_api:
            return False

    if not key_values:
        return True

    try:
        ts_dt = _parse_timestamp(timestamp)

        point = Point("pos_logs") \
            .tag("agent_id", agent_id) \
            .tag("body_type", BODY_TYPE_MAP.get(body_type, body_type))
        _apply_store_tags(point, store_info)

        for k, v in key_values.items():
            field_name = DEVICE_NAME_MAP.get(k, k)
            # 숫자로 변환 가능하면 숫자로 저장
            try:
                point.field(field_name, float(v))
            except (ValueError, TypeError):
                point.field(field_name, DEVICE_STATUS_MAP.get(v, 0))

        point.time(ts_dt)

        def _write():
            line = point.to_line_protocol()
            write_url = f"{INFLUX_URL}/api/v2/write?org={INFLUX_ORG}&bucket={bucket}"
            req = urllib.request.Request(
                write_url,
                data=line.encode('utf-8'),
                headers={
                    "Authorization": f"Token {INFLUX_TOKEN}",
                    "Content-Type": "text/plain; charset=utf-8",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status not in [200, 204]:
                        return False
            except urllib.error.HTTPError:
                return False
            return True

        loop = asyncio.get_event_loop()
        return await asyncio.wait_for(loop.run_in_executor(_write_executor, _write), timeout=10.0)

    except Exception as e:
        if "shutdown" not in str(e).lower():
            log.warning(f"Failed to write log entry: {e}")
        return False


async def write_detection(
    agent_id: str,
    timestamp: str,
    engine: str,
    metric: str,
    value: float,
    score: float,
    threshold: float,
    severity: str,
    confidence: float,
    forecast: float | None = None,
    residual: float | None = None,
    details: str | None = None,
    bucket: str = INFLUX_BUCKET,
    store_info: Dict = None,
) -> bool:
    """
    Write a single anomaly detection result to InfluxDB.

    Measurement: 'detection'
    Tags: agent_id, engine, metric, severity
    Fields: value, score, threshold, confidence, forecast, residual, details
    """
    global client, write_api

    if not client or not write_api:
        init_influx()
        if not client or not write_api:
            return False

    try:
        ts_dt = _parse_timestamp(timestamp)

        point = Point("anomaly_detection") \
            .tag("agent_id", agent_id) \
            .tag("engine", engine) \
            .tag("metric", metric) \
            .tag("severity", severity)

        _apply_store_tags(point, store_info)

        point.field("score", float(score)) \
            .field("threshold", float(threshold)) \
            .field("confidence", float(confidence)) \
            .field("actual_value", float(value))

        if forecast is not None:
            point.field("arima_predicted", float(forecast))
        if residual is not None:
            point.field("arima_deviation", float(residual))
        if details:
            point.tag("details", str(details))

        point.time(ts_dt)

        def _write():
            line = point.to_line_protocol()
            write_url = f"{INFLUX_URL}/api/v2/write?org={INFLUX_ORG}&bucket={bucket}"
            req = urllib.request.Request(
                write_url,
                data=line.encode('utf-8'),
                headers={
                    "Authorization": f"Token {INFLUX_TOKEN}",
                    "Content-Type": "text/plain; charset=utf-8",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status not in [200, 204]:
                        return False
            except urllib.error.HTTPError:
                return False
            return True

        loop = asyncio.get_event_loop()
        return await asyncio.wait_for(loop.run_in_executor(_write_executor, _write), timeout=10.0)

    except Exception as e:
        if "shutdown" not in str(e).lower():
            log.warning(f"Failed to write detection: {e}")
        return False


def get_recent_metrics(
    agent_id: str,
    limit: int = 100,
    bucket: str | None = None,
) -> list:
    """
    Query recent raw metrics from InfluxDB.

    Returns list of dicts with timestamp, cpu, memory, disk_io, network_sent, network_received.
    """
    target_bucket = bucket if bucket is not None else INFLUX_BUCKET

    if not client:
        return []

    try:
        query = f'''
        from(bucket: "{target_bucket}")
          |> range(start: -1d)
          |> filter(fn: (r) => r._measurement == "metrics")
          |> filter(fn: (r) => r.agent_id == "{agent_id}")
          |> filter(fn: (r) => r._field == "cpu" or r._field == "memory" or r._field == "disk_io"
              or r._field == "network_sent_bytes" or r._field == "network_received_bytes")
          |> tail(n: {limit})
        '''

        query_api = client.query_api()
        tables = query_api.query(query)

        records_map: dict = {}
        for table in tables:
            for record in table.records:
                ts = str(record.get_time())
                field = record.get_field()
                value = record.get_value()

                if ts not in records_map:
                    records_map[ts] = {
                        "timestamp": ts,
                        "agent_id": agent_id,
                        "cpu": 0, "memory": 0, "disk_io": 0,
                        "network_sent_bytes": 0, "network_received_bytes": 0,
                    }

                if field in ("cpu", "memory", "disk_io", "network_sent_bytes", "network_received_bytes") and value is not None:
                    records_map[ts][field] = float(value)

        records = sorted(records_map.values(), key=lambda x: x["timestamp"])
        log.debug(f"get_recent_metrics: {agent_id} -> {len(records)} records")
        return records

    except Exception as e:
        log.warning(f"Failed to query recent metrics: {e}")
        return []


def get_recent_detections(
    agent_id: str,
    limit: int = 200,
    bucket: str | None = None,
) -> list:
    """
    Query recent anomaly detection results from InfluxDB.

    Returns list of dicts with timestamp, engine, metric, value, score, threshold,
    severity, confidence, forecast, residual, details.
    """
    target_bucket = bucket if bucket is not None else INFLUX_BUCKET

    if not client:
        return []

    try:
        query = f'''
        from(bucket: "{target_bucket}")
          |> range(start: -1d)
          |> filter(fn: (r) => r._measurement == "anomaly_detection")
          |> filter(fn: (r) => r.agent_id == "{agent_id}")
          |> tail(n: {limit})
        '''

        query_api = client.query_api()
        tables = query_api.query(query)

        # Group by (timestamp, engine, metric) to reassemble fields
        records_map: dict = {}
        for table in tables:
            for record in table.records:
                ts = str(record.get_time())
                engine = record.values.get("engine", "")
                metric = record.values.get("metric", "")
                severity = record.values.get("severity", "normal")
                field = record.get_field()
                value = record.get_value()

                key = f"{ts}|{engine}|{metric}"
                if key not in records_map:
                    records_map[key] = {
                        "timestamp": ts,
                        "engine": engine,
                        "metric": metric,
                        "severity": severity,
                        "score": 0, "threshold": 0, "confidence": 0,
                        "actual_value": None, "arima_predicted": None, "arima_deviation": None,
                        "details": record.values.get("details"),
                    }

                rec = records_map[key]
                if field == "score" and value is not None:
                    rec["score"] = float(value)
                elif field == "threshold" and value is not None:
                    rec["threshold"] = float(value)
                elif field == "confidence" and value is not None:
                    rec["confidence"] = float(value)
                elif field == "arima_predicted" and value is not None:
                    rec["arima_predicted"] = float(value)
                elif field == "arima_deviation" and value is not None:
                    rec["arima_deviation"] = float(value)
                elif field == "actual_value" and value is not None:
                    rec["actual_value"] = float(value)

        records = sorted(records_map.values(), key=lambda x: x["timestamp"])
        log.debug(f"get_recent_detections: {agent_id} -> {len(records)} records")
        return records

    except Exception as e:
        log.warning(f"Failed to query recent detections: {e}")
        return []


def get_last_metric_time(
    agent_id: str,
    bucket: str | None = None,
) -> str | None:
    """Query the earliest 'last' timestamp across metrics and peripheral_status.
    Returns the earlier of the two so resume fills all gaps."""
    target_bucket = bucket if bucket is not None else INFLUX_BUCKET
    if not client:
        return None

    timestamps = []
    for measurement, field in [("metrics", "cpu"), ("peripheral_status", "dongle")]:
        try:
            query = f'''
            from(bucket: "{target_bucket}")
              |> range(start: -30d)
              |> filter(fn: (r) => r._measurement == "{measurement}")
              |> filter(fn: (r) => r.agent_id == "{agent_id}")
              |> filter(fn: (r) => r._field == "{field}")
              |> last()
            '''
            tables = client.query_api().query(query)
            for table in tables:
                for record in table.records:
                    timestamps.append(str(record.get_time()))
        except Exception as e:
            log.warning(f"Failed to query last {measurement} time: {e}")

    if not timestamps:
        return None
    # Return the earliest so no measurement has gaps
    timestamps.sort()
    log.info(f"Resume timestamps — metrics: {timestamps}")
    return timestamps[0]


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
