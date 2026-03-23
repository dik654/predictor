"""
Backfill forecast evaluation from existing arima_forecast + metrics in InfluxDB.

Reads forecast records per time slot, builds forecast vectors,
runs ForecastEvaluator (long-term ECOD), and writes evaluation results.

Usage:
  uv run python -m webrtc_hub.backfill_evaluation --bucket pos_metrics
  uv run python -m webrtc_hub.backfill_evaluation --bucket pos_metrics --also-bucket sample_metrics
"""

import asyncio
import logging
import click
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger("backfill_eval")

# forecast metric -> evaluator key
METRIC_KEY_MAP = {
    "CPU": "cpu",
    "Memory": "memory",
    "DiskIO": "disk_io",
    "NetworkSent": "network_sent",
    "NetworkRecv": "network_recv",
}


def query_forecasts_by_slot(client, bucket: str, agent_id: str, days: int = 7) -> Dict[str, Dict[int, Dict[str, float]]]:
    """
    Query arima_forecast and group by (timestamp, horizon_min).
    Returns: { iso_timestamp: { horizon_min: { "cpu": val, "memory": val, ... } } }
    """
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


def query_peripheral_status(client, bucket: str, agent_id: str, days: int = 7) -> Dict[str, Dict[str, int]]:
    """Query peripheral status by timestamp."""
    from .influx_writer import PERIPHERAL_FIELDS
    query = f'''
    from(bucket: "{bucket}")
      |> range(start: -{days}d)
      |> filter(fn: (r) => r._measurement == "peripheral_status")
      |> filter(fn: (r) => r.agent_id == "{agent_id}")
    '''
    tables = client.query_api().query(query)

    result: Dict[str, Dict[str, int]] = defaultdict(dict)
    for table in tables:
        for record in table.records:
            ts = record.get_time().isoformat()
            field = record.get_field()
            value = record.get_value()
            if field in PERIPHERAL_FIELDS and value is not None:
                result[ts][field] = int(value)

    log.info(f"Loaded peripheral status for {len(result)} time slots")
    return dict(result)


async def run_backfill(bucket: str, also_bucket: str, agent_id: str, days: int):
    from .influx_writer import (
        init_influx, close_influx, write_forecast_evaluation,
        INFLUX_URL, INFLUX_ORG, INFLUX_TOKEN, INFLUX_BUCKET, PERIPHERAL_FIELDS,
    )
    from .forecast_evaluator import ForecastEvaluator
    from influxdb_client import InfluxDBClient
    import webrtc_hub.influx_writer as iw

    init_influx()
    client_obj = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)

    # 1. Load data
    forecast_slots = query_forecasts_by_slot(client_obj, bucket, agent_id, days)
    periph_slots = query_peripheral_status(client_obj, bucket, agent_id, days)

    if not forecast_slots:
        log.error("No forecast data found!")
        client_obj.close()
        close_influx()
        return

    # 2. Initialize evaluator (trains ECOD from InfluxDB historical data)
    fe = ForecastEvaluator()

    # Find closest peripheral data for a given timestamp
    periph_timestamps = sorted(periph_slots.keys())

    def get_nearest_periph(ts_iso: str) -> Dict[str, int]:
        """Find nearest peripheral status."""
        if not periph_timestamps:
            return {}
        # Binary search for closest
        target = ts_iso
        idx = min(range(len(periph_timestamps)), key=lambda i: abs(
            datetime.fromisoformat(periph_timestamps[i]).timestamp() -
            datetime.fromisoformat(target).timestamp()
        )) if periph_timestamps else 0
        return periph_slots.get(periph_timestamps[idx], {})

    # 3. Evaluate each slot and write
    buckets = [bucket]
    if also_bucket:
        buckets.append(also_bucket)

    sorted_slots = sorted(forecast_slots.items())
    total = len(sorted_slots)
    written = 0
    errors = 0

    log.info(f"Evaluating {total} forecast slots...")

    for i, (ts_iso, horizons_data) in enumerate(sorted_slots):
        # Update peripheral state for evaluator
        periph = get_nearest_periph(ts_iso)
        if periph:
            # Convert field names to eng keys used by evaluator
            periph_eng = {}
            for field, value in periph.items():
                periph_eng[field] = value
            fe.update_event(agent_id, {"Peripherals": periph_eng})

        # Run evaluation
        # horizons_data: { 60: {"cpu": 20.5, "memory": 80}, 360: {...}, ... }
        eval_result = fe.evaluate(agent_id, ts_iso, horizons_data)
        eval_dict = fe.to_dict(eval_result)

        # Write to all buckets
        for b in buckets:
            try:
                iw.INFLUX_BUCKET = b
                await write_forecast_evaluation(
                    agent_id=agent_id,
                    timestamp=ts_iso,
                    horizons=eval_dict.get("horizons", []),
                    overall_severity=eval_dict.get("overall_severity", "normal"),
                    model_ready=eval_dict.get("model_ready", False),
                    data_source=eval_dict.get("data_source", "influxdb"),
                    bucket=b,
                )
                written += 1
            except Exception as e:
                errors += 1
                if errors <= 5:
                    log.warning(f"Write error: {e}")

        if (i + 1) % max(1, total // 10) == 0:
            log.info(f"  {i+1}/{total} ({(i+1)*100//total}%)")

    # Restore default bucket
    iw.INFLUX_BUCKET = bucket

    client_obj.close()
    close_influx()
    log.info(f"Done! Wrote {written} evaluations ({errors} errors) across {len(buckets)} bucket(s)")


@click.command()
@click.option("--bucket", default="pos_metrics", help="Source/target InfluxDB bucket")
@click.option("--also-bucket", default="", help="Also write to this bucket")
@click.option("--agent-id", default="V135-POS-03")
@click.option("--days", default=7, type=int, help="How many days back to look")
def main(bucket, also_bucket, agent_id, days):
    """Backfill forecast evaluation from existing forecast + metrics data."""
    log.info(f"Backfilling evaluation: bucket={bucket}, agent={agent_id}, days={days}")
    asyncio.run(run_backfill(bucket, also_bucket, agent_id, days))


if __name__ == "__main__":
    main()
