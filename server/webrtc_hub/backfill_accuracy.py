"""
Backfill accuracy data from existing arima_forecast + metrics in InfluxDB.

Reads forecast records, finds matching actual metrics at (forecast_time + horizon),
calculates error, and writes accuracy records.

Usage:
  uv run python -m webrtc_hub.backfill_accuracy --bucket pos_metrics
  uv run python -m webrtc_hub.backfill_accuracy --bucket pos_metrics --also-bucket sample_metrics
"""

import logging
import click
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger("backfill_accuracy")

# Metric name mapping: forecast tag -> metrics field
METRIC_FIELD_MAP = {
    "CPU": "cpu",
    "Memory": "memory",
    "DiskIO": "disk_io",
    "NetworkSent": "network_sent_bytes",
    "NetworkRecv": "network_received_bytes",
}

TOLERANCE_MIN = 6  # match tolerance in minutes


def query_forecasts(client, bucket: str, agent_id: str, days: int = 7) -> List[dict]:
    """Query all arima_forecast records."""
    query = f'''
    from(bucket: "{bucket}")
      |> range(start: -{days}d)
      |> filter(fn: (r) => r._measurement == "arima_forecast")
      |> filter(fn: (r) => r.agent_id == "{agent_id}")
      |> filter(fn: (r) => r._field == "predicted_value")
    '''
    tables = client.query_api().query(query)
    results = []
    for table in tables:
        for record in table.records:
            results.append({
                "time": record.get_time(),
                "metric": record.values.get("metric", ""),
                "horizon_min": int(record.values.get("horizon_min", 0)),
                "predicted_value": float(record.get_value()),
            })
    log.info(f"Loaded {len(results)} forecast records from {bucket}")
    return results


def query_metrics_map(client, bucket: str, agent_id: str, days: int = 7) -> Dict[str, dict]:
    """Query metrics and build a time-indexed map (rounded to minute)."""
    fields = list(METRIC_FIELD_MAP.values())
    field_filter = " or ".join(f'r._field == "{f}"' for f in fields)
    query = f'''
    from(bucket: "{bucket}")
      |> range(start: -{days}d)
      |> filter(fn: (r) => r._measurement == "metrics")
      |> filter(fn: (r) => r.agent_id == "{agent_id}")
      |> filter(fn: (r) => {field_filter})
    '''
    tables = client.query_api().query(query)

    # Group by timestamp (rounded to minute)
    metrics_map: Dict[str, dict] = {}
    for table in tables:
        for record in table.records:
            ts = record.get_time()
            # Round to minute for matching
            key = ts.replace(second=0, microsecond=0).isoformat()
            if key not in metrics_map:
                metrics_map[key] = {}
            field = record.get_field()
            value = record.get_value()
            if value is not None:
                metrics_map[key][field] = float(value)

    log.info(f"Loaded {len(metrics_map)} metric time points from {bucket}")
    return metrics_map


def match_and_calculate(
    forecasts: List[dict],
    metrics_map: Dict[str, dict],
    tolerance_min: int = TOLERANCE_MIN,
) -> List[dict]:
    """Match forecasts with actuals and calculate accuracy."""
    results = []
    matched = 0
    unmatched = 0

    for fc in forecasts:
        metric_tag = fc["metric"]
        metric_field = METRIC_FIELD_MAP.get(metric_tag)
        if not metric_field:
            continue

        # Expected actual time = forecast_time + horizon
        expected_time = fc["time"] + timedelta(minutes=fc["horizon_min"])

        # Search within tolerance window
        best_match = None
        best_diff = float("inf")

        for offset_min in range(-tolerance_min, tolerance_min + 1):
            candidate = expected_time + timedelta(minutes=offset_min)
            key = candidate.replace(second=0, microsecond=0).isoformat()
            if key in metrics_map and metric_field in metrics_map[key]:
                diff = abs(offset_min)
                if diff < best_diff:
                    best_diff = diff
                    best_match = (key, metrics_map[key][metric_field])

        if best_match:
            actual_value = best_match[1]
            predicted_value = fc["predicted_value"]
            error_pct = abs(actual_value - predicted_value) / actual_value * 100 if actual_value != 0 else 0

            results.append({
                "time": expected_time,  # accuracy record at the actual measurement time
                "metric": metric_tag,
                "horizon_min": fc["horizon_min"],
                "actual_value": actual_value,
                "forecast_value": predicted_value,
                "error_pct": error_pct,
            })
            matched += 1
        else:
            unmatched += 1

    log.info(f"Matched: {matched}, Unmatched: {unmatched}")

    # Stats per metric/horizon
    from collections import defaultdict
    stats = defaultdict(list)
    for r in results:
        stats[(r["metric"], r["horizon_min"])].append(r["error_pct"])
    for (m, h), errors in sorted(stats.items()):
        arr = np.array(errors)
        log.info(f"  {m}/{h}min: {len(arr)} records, mean_error={arr.mean():.1f}%, std={arr.std():.1f}%")

    return results


def write_accuracy_records(client, bucket: str, agent_id: str, records: List[dict]):
    """Write accuracy records to InfluxDB."""
    import urllib.request
    from influxdb_client import Point

    INFLUX_URL = client.url
    INFLUX_ORG = client.org
    token = client.token

    lines = []
    for r in records:
        point = Point("accuracy") \
            .tag("agent_id", agent_id) \
            .tag("metric", r["metric"]) \
            .tag("horizon_min", str(r["horizon_min"])) \
            .field("actual_value", float(r["actual_value"])) \
            .field("forecast_value", float(r["forecast_value"])) \
            .field("error_percent", float(r["error_pct"])) \
            .field("within_3sigma", 1 if r["error_pct"] <= 3.0 else 0) \
            .time(r["time"])
        lines.append(point.to_line_protocol())

    # Write in batches of 500
    batch_size = 500
    written = 0
    for i in range(0, len(lines), batch_size):
        batch = lines[i:i + batch_size]
        body = "\n".join(batch).encode("utf-8")
        write_url = f"{INFLUX_URL}/api/v2/write?org={INFLUX_ORG}&bucket={bucket}"
        req = urllib.request.Request(
            write_url, data=body,
            headers={
                "Authorization": f"Token {token}",
                "Content-Type": "text/plain; charset=utf-8",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status in [200, 204]:
                    written += len(batch)
        except Exception as e:
            log.error(f"Write batch failed: {e}")

    log.info(f"Wrote {written}/{len(lines)} accuracy records to {bucket}")


@click.command()
@click.option("--bucket", default="pos_metrics", help="Source/target InfluxDB bucket")
@click.option("--also-bucket", default="", help="Also write accuracy to this bucket")
@click.option("--agent-id", default="V135-POS-03")
@click.option("--days", default=7, type=int, help="How many days back to look")
def main(bucket, also_bucket, agent_id, days):
    """Backfill accuracy from existing forecast + metrics data."""
    from .influx_writer import init_influx, close_influx, INFLUX_URL, INFLUX_ORG, INFLUX_TOKEN
    from influxdb_client import InfluxDBClient

    init_influx()
    client_obj = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)

    log.info(f"Backfilling accuracy: bucket={bucket}, agent={agent_id}, days={days}")

    # 1. Load forecasts and metrics
    forecasts = query_forecasts(client_obj, bucket, agent_id, days)
    metrics_map = query_metrics_map(client_obj, bucket, agent_id, days)

    if not forecasts:
        log.error("No forecast data found!")
        client_obj.close()
        close_influx()
        return

    if not metrics_map:
        log.error("No metrics data found!")
        client_obj.close()
        close_influx()
        return

    # 2. Match and calculate
    results = match_and_calculate(forecasts, metrics_map)

    if not results:
        log.error("No matches found!")
        client_obj.close()
        close_influx()
        return

    # 3. Write to bucket(s)
    buckets = [bucket]
    if also_bucket:
        buckets.append(also_bucket)

    for b in buckets:
        write_accuracy_records(client_obj, b, agent_id, results)

    client_obj.close()
    close_influx()
    log.info("Done!")


if __name__ == "__main__":
    main()
