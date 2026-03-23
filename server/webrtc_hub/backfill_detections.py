"""
Backfill anomaly_detection from existing metrics in InfluxDB.
Uses batch writes for speed.

Usage:
  uv run python -m webrtc_hub.backfill_detections --bucket pos_metrics
  uv run python -m webrtc_hub.backfill_detections --bucket pos_metrics --also-bucket sample_metrics
"""

import asyncio
import logging
import time as _time
import urllib.request
import click
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Dict, List
from collections import defaultdict
from influxdb_client import Point

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger("backfill_detections")

import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning, module="pyod")
warnings.filterwarnings("ignore", category=UserWarning, module="statsforecast")


def query_all_metrics(client, bucket: str, agent_id: str, days: int = 7) -> List[dict]:
    """Query all metrics and build data points for the detector."""
    from .influx_writer import PERIPHERAL_FIELDS

    query = f'''
    from(bucket: "{bucket}")
      |> range(start: -{days}d)
      |> filter(fn: (r) => r._measurement == "metrics")
      |> filter(fn: (r) => r.agent_id == "{agent_id}")
      |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
      |> group()
      |> sort(columns: ["_time"])
    '''
    tables = client.query_api().query(query)

    metrics_list = []
    for table in tables:
        for record in table.records:
            vals = record.values
            metrics_list.append({
                "timestamp": record.get_time().isoformat(),
                "cpu": float(vals.get("cpu", 0) or 0),
                "memory": float(vals.get("memory", 0) or 0),
                "disk_io": float(vals.get("disk_io", 0) or 0),
                "network_sent": float(vals.get("network_sent_bytes", 0) or 0),
                "network_recv": float(vals.get("network_received_bytes", 0) or 0),
            })

    log.info(f"Loaded {len(metrics_list)} metric time points")

    # Peripheral status
    periph_query = f'''
    from(bucket: "{bucket}")
      |> range(start: -{days}d)
      |> filter(fn: (r) => r._measurement == "peripheral_status")
      |> filter(fn: (r) => r.agent_id == "{agent_id}")
      |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
      |> group()
      |> sort(columns: ["_time"])
    '''
    periph_map: Dict[str, dict] = {}
    try:
        ptables = client.query_api().query(periph_query)
        for table in ptables:
            for record in table.records:
                ts = record.get_time().isoformat()
                vals = record.values
                periph_map[ts] = {pf: int(vals.get(pf, -1) or -1) for pf in PERIPHERAL_FIELDS}
    except Exception as e:
        log.warning(f"No peripheral data: {e}")

    log.info(f"Loaded peripheral status for {len(periph_map)} time points")

    # Merge
    periph_times = sorted(periph_map.keys())
    last_periph: dict = {}

    data_points = []
    for m in metrics_list:
        ts = m["timestamp"]
        for pt in periph_times:
            if pt <= ts:
                last_periph = periph_map[pt]
            else:
                break

        dp = {
            "AgentId": agent_id,
            "Timestamp": ts,
            "CPU": m["cpu"],
            "Memory": m["memory"],
            "DiskIO": m["disk_io"],
            "Network": {"Sent": m["network_sent"], "Recv": m["network_recv"]},
            "Peripherals": last_periph.copy(),
            "Process": {"main": "RUNNING"},
            "Logs": [],
        }
        data_points.append(dp)

    return data_points


def _parse_timestamp(timestamp: str) -> datetime:
    """Parse timestamp to naive UTC datetime."""
    KST_OFFSET = timedelta(hours=9)
    try:
        ts_str = timestamp.rstrip('Z') if timestamp else ""
        ts_dt = datetime.fromisoformat(ts_str)
        if ts_dt.tzinfo is None:
            ts_dt = ts_dt - KST_OFFSET
        else:
            ts_dt = ts_dt.replace(tzinfo=None)
        return ts_dt
    except (ValueError, AttributeError):
        return datetime.utcnow()


def build_line(agent_id: str, ts_val: str, det) -> str:
    """Build a single line protocol string for a detection."""
    ts_dt = _parse_timestamp(ts_val)
    point = Point("anomaly_detection") \
        .tag("agent_id", agent_id) \
        .tag("engine", det.engine) \
        .tag("metric", det.metric) \
        .tag("severity", det.severity)

    if det.details:
        point.tag("details", str(det.details)[:256])

    point.field("score", float(det.score)) \
        .field("threshold", float(det.threshold)) \
        .field("confidence", float(det.confidence)) \
        .field("actual_value", float(det.value))

    if det.forecast is not None:
        point.field("arima_predicted", float(det.forecast))
    if det.residual is not None:
        point.field("arima_deviation", float(det.residual))

    point.time(ts_dt)
    return point.to_line_protocol()


def batch_write(url: str, token: str, org: str, bucket: str, lines: List[str]):
    """Write batch of line protocol strings."""
    body = "\n".join(lines).encode("utf-8")
    write_url = f"{url}/api/v2/write?org={org}&bucket={bucket}"
    req = urllib.request.Request(
        write_url, data=body,
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "text/plain; charset=utf-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status in [200, 204]


def run_backfill(bucket: str, also_bucket: str, agent_id: str, days: int):
    from .influx_writer import INFLUX_URL, INFLUX_ORG, INFLUX_TOKEN, init_influx, close_influx
    from .detector import EnhancedAnomalyDetector
    from influxdb_client import InfluxDBClient

    init_influx()
    client_obj = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)

    data_points = query_all_metrics(client_obj, bucket, agent_id, days)
    client_obj.close()

    if not data_points:
        log.error("No metrics data found!")
        close_influx()
        return

    total = len(data_points)
    warmup = min(30, max(5, total // 3))
    log.info(f"Running detector on {total} data points (warmup={warmup})...")

    detector = EnhancedAnomalyDetector()
    buckets = [bucket]
    if also_bucket:
        buckets.append(also_bucket)

    BATCH_SIZE = 5000
    total_detections = 0
    t0 = _time.time()

    # Collect all lines per bucket
    bucket_lines: Dict[str, List[str]] = {b: [] for b in buckets}

    for i, dp in enumerate(data_points):
        run_ecod = (i >= warmup)
        run_arima = (i >= warmup)

        try:
            result = detector.detect(dp, run_ecod=run_ecod, run_arima=run_arima)
        except Exception as e:
            if i % 100 == 0:
                log.warning(f"Detection error at slot {i}: {e}")
            continue

        if not result or not result.detections:
            continue

        ts_val = dp["Timestamp"]
        for det in result.detections:
            line = build_line(agent_id, ts_val, det)
            for b in buckets:
                bucket_lines[b].append(line)
            total_detections += 1

        # Flush batch periodically
        for b in buckets:
            if len(bucket_lines[b]) >= BATCH_SIZE:
                try:
                    batch_write(INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, b, bucket_lines[b])
                except Exception as e:
                    log.warning(f"Batch write error ({b}): {e}")
                bucket_lines[b] = []

        if (i + 1) % max(1, total // 20) == 0:
            elapsed = _time.time() - t0
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            log.info(f"  {i+1}/{total} ({(i+1)*100//total}%) — {rate:.1f} slots/s — detections: {total_detections}")

    # Flush remaining
    for b in buckets:
        if bucket_lines[b]:
            try:
                batch_write(INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, b, bucket_lines[b])
            except Exception as e:
                log.warning(f"Final batch write error ({b}): {e}")

    close_influx()
    elapsed = _time.time() - t0
    log.info(f"Done! {total_detections} detections written in {elapsed:.0f}s")


@click.command()
@click.option("--bucket", default="pos_metrics", help="Source/target InfluxDB bucket")
@click.option("--also-bucket", default="", help="Also write to this bucket")
@click.option("--agent-id", default="V135-POS-03")
@click.option("--days", default=7, type=int, help="How many days back to look")
def main(bucket, also_bucket, agent_id, days):
    """Backfill anomaly detections from existing metrics data."""
    log.info(f"Backfilling detections: bucket={bucket}, agent={agent_id}, days={days}")
    run_backfill(bucket, also_bucket, agent_id, days)


if __name__ == "__main__":
    main()
