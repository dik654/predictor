"""
Backfill anomaly_detection from existing metrics in InfluxDB.

Reads metrics, runs ECOD + ARIMA detector on each time slot,
and writes detection results back to InfluxDB.

Usage:
  uv run python -m webrtc_hub.backfill_detections --bucket pos_metrics
  uv run python -m webrtc_hub.backfill_detections --bucket pos_metrics --also-bucket sample_metrics
"""

import asyncio
import logging
import time as _time
import click
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Dict, List
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger("backfill_detections")

import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning, module="pyod")
warnings.filterwarnings("ignore", category=UserWarning, module="statsforecast")


def query_all_metrics(client, bucket: str, agent_id: str, days: int = 7) -> List[dict]:
    """Query all metrics and build data points for the detector."""
    from .influx_writer import PERIPHERAL_FIELDS

    # Metrics
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

    # Merge: build detector-compatible data points
    periph_times = sorted(periph_map.keys())
    last_periph: dict = {}

    data_points = []
    for m in metrics_list:
        ts = m["timestamp"]

        # Find closest peripheral data
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


async def run_backfill(bucket: str, also_bucket: str, agent_id: str, days: int):
    from .influx_writer import (
        init_influx, close_influx, write_detection,
        INFLUX_URL, INFLUX_ORG, INFLUX_TOKEN,
    )
    from .detector import EnhancedAnomalyDetector, MIN_SAMPLES_ECOD
    from influxdb_client import InfluxDBClient
    import webrtc_hub.influx_writer as iw

    init_influx()
    client_obj = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)

    # Load data
    data_points = query_all_metrics(client_obj, bucket, agent_id, days)
    if not data_points:
        log.error("No metrics data found!")
        client_obj.close()
        close_influx()
        return

    total = len(data_points)
    warmup = min(30, max(5, total // 3))
    log.info(f"Running detector on {total} data points (warmup={warmup})...")

    # Run detector
    detector = EnhancedAnomalyDetector()
    buckets = [bucket]
    if also_bucket:
        buckets.append(also_bucket)

    total_detections = 0
    t0 = _time.time()

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
            for b in buckets:
                try:
                    await write_detection(
                        agent_id=agent_id, timestamp=ts_val,
                        engine=det.engine, metric=det.metric,
                        value=det.value, score=det.score,
                        threshold=det.threshold, severity=det.severity,
                        confidence=det.confidence,
                        forecast=det.forecast, residual=det.residual,
                        details=det.details, bucket=b,
                    )
                    total_detections += 1
                except Exception:
                    pass

        if (i + 1) % max(1, total // 20) == 0:
            elapsed = _time.time() - t0
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            log.info(f"  {i+1}/{total} ({(i+1)*100//total}%) — {rate:.1f} slots/s — detections: {total_detections}")

    client_obj.close()
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
    asyncio.run(run_backfill(bucket, also_bucket, agent_id, days))


if __name__ == "__main__":
    main()
