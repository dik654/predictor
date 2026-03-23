"""
Backfill forecast evaluation from existing arima_forecast + metrics in InfluxDB.
Uses batch writes for speed.

Usage:
  uv run python -m webrtc_hub.backfill_evaluation --bucket pos_metrics
  uv run python -m webrtc_hub.backfill_evaluation --bucket pos_metrics --also-bucket sample_metrics
"""

import logging
import time as _time
import urllib.request
import click
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from collections import defaultdict
from influxdb_client import Point

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger("backfill_eval")

METRIC_KEY_MAP = {
    "CPU": "cpu",
    "Memory": "memory",
    "DiskIO": "disk_io",
    "NetworkSent": "network_sent",
    "NetworkRecv": "network_recv",
}


def query_forecasts_by_slot(client, bucket: str, agent_id: str, days: int = 7) -> Dict[str, Dict[int, Dict[str, float]]]:
    query = f'''
    from(bucket: "{bucket}")
      |> range(start: -{days}d)
      |> filter(fn: (r) => r._measurement == "arima_forecast")
      |> filter(fn: (r) => r.agent_id == "{agent_id}")
      |> filter(fn: (r) => r._field == "predicted_value")
    '''
    tables = client.query_api().query(query)

    slots: Dict[str, Dict[int, Dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    count = 0
    for table in tables:
        for record in table.records:
            ts = record.get_time().isoformat()
            metric = record.values.get("metric", "")
            horizon = int(record.values.get("horizon_min", 0))
            value = float(record.get_value())

            key = METRIC_KEY_MAP.get(metric)
            if key and horizon > 0:
                slots[ts][horizon][key] = value
                count += 1

    log.info(f"Loaded {count} forecast records across {len(slots)} time slots")
    return dict(slots)


def _parse_timestamp(timestamp: str) -> datetime:
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


def build_eval_lines(agent_id: str, ts_iso: str, eval_dict: dict) -> List[str]:
    """Build line protocol strings for one evaluation."""
    ts_dt = _parse_timestamp(ts_iso)
    overall_severity = eval_dict.get("overall_severity", "normal")
    data_source = eval_dict.get("data_source", "none")
    model_ready = eval_dict.get("model_ready", False)

    lines = []
    for h in eval_dict.get("horizons", []):
        point = Point("arima_ecod_ensemble_forecast_eval") \
            .tag("agent_id", agent_id) \
            .tag("horizon_min", str(h.get("horizon_min", 0))) \
            .tag("severity", h.get("severity", "normal")) \
            .tag("overall_severity", overall_severity) \
            .tag("data_source", data_source) \
            .field("predicted_cpu", float(h.get("pred_cpu", 0))) \
            .field("predicted_memory", float(h.get("pred_memory", 0))) \
            .field("predicted_disk_io", float(h.get("pred_disk_io", 0))) \
            .field("predicted_network_sent", float(h.get("pred_network_sent", 0))) \
            .field("predicted_network_recv", float(h.get("pred_network_recv", 0))) \
            .field("ecod_score", float(h.get("ecod_score", 0))) \
            .field("rule_score", float(h.get("rule_score", 0))) \
            .field("final_score", float(h.get("final_score", 0))) \
            .field("reliability", float(h.get("reliability", 0))) \
            .field("is_outlier", 1 if h.get("is_outlier") else 0) \
            .field("model_ready", 1 if model_ready else 0)

        for fc in h.get("feature_contributions", []):
            mk = fc.get("metric", "").lower().replace(" ", "_")
            if mk:
                point.field(f"contribution_{mk}_percent", float(fc.get("pct", 0)))
                point.field(f"contribution_{mk}_score", float(fc.get("score", 0)))

        point.time(ts_dt)
        lines.append(point.to_line_protocol())

    return lines


def batch_write(url: str, token: str, org: str, bucket: str, lines: List[str]):
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
    from .influx_writer import init_influx, close_influx, INFLUX_URL, INFLUX_ORG, INFLUX_TOKEN
    from .forecast_evaluator import ForecastEvaluator
    from influxdb_client import InfluxDBClient

    init_influx()
    client_obj = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)

    forecast_slots = query_forecasts_by_slot(client_obj, bucket, agent_id, days)
    client_obj.close()

    if not forecast_slots:
        log.error("No forecast data found!")
        close_influx()
        return

    fe = ForecastEvaluator()

    buckets = [bucket]
    if also_bucket:
        buckets.append(also_bucket)

    sorted_slots = sorted(forecast_slots.items())
    total = len(sorted_slots)
    log.info(f"Evaluating {total} forecast slots...")

    BATCH_SIZE = 3000
    bucket_lines: Dict[str, List[str]] = {b: [] for b in buckets}
    written = 0
    t0 = _time.time()

    for i, (ts_iso, horizons_data) in enumerate(sorted_slots):
        eval_result = fe.evaluate(agent_id, ts_iso, horizons_data)
        eval_dict = fe.to_dict(eval_result)

        lines = build_eval_lines(agent_id, ts_iso, eval_dict)
        for b in buckets:
            bucket_lines[b].extend(lines)
            if len(bucket_lines[b]) >= BATCH_SIZE:
                try:
                    batch_write(INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, b, bucket_lines[b])
                    written += len(bucket_lines[b])
                except Exception as e:
                    log.warning(f"Batch write error ({b}): {e}")
                bucket_lines[b] = []

        if (i + 1) % max(1, total // 10) == 0:
            elapsed = _time.time() - t0
            log.info(f"  {i+1}/{total} ({(i+1)*100//total}%)")

    # Flush remaining
    for b in buckets:
        if bucket_lines[b]:
            try:
                batch_write(INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, b, bucket_lines[b])
                written += len(bucket_lines[b])
            except Exception as e:
                log.warning(f"Final batch write error ({b}): {e}")

    close_influx()
    elapsed = _time.time() - t0
    log.info(f"Done! {written} evaluation points written in {elapsed:.0f}s")


@click.command()
@click.option("--bucket", default="pos_metrics", help="Source/target InfluxDB bucket")
@click.option("--also-bucket", default="", help="Also write to this bucket")
@click.option("--agent-id", default="V135-POS-03")
@click.option("--days", default=7, type=int, help="How many days back to look")
def main(bucket, also_bucket, agent_id, days):
    """Backfill forecast evaluation from existing forecast + metrics data."""
    log.info(f"Backfilling evaluation: bucket={bucket}, agent={agent_id}, days={days}")
    run_backfill(bucket, also_bucket, agent_id, days)


if __name__ == "__main__":
    main()
