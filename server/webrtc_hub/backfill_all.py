"""
Backfill all computed measurements (detections, evaluation, accuracy)
from existing metrics + forecasts in InfluxDB.

Usage:
  uv run python -m webrtc_hub.backfill_all --days 3
  uv run python -m webrtc_hub.backfill_all --days 3 --bucket pos_metrics --also-bucket sample_metrics
"""

import logging
import time as _time
import urllib.request
import click
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List
from collections import defaultdict
from influxdb_client import Point

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger("backfill_all")

import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning, module="pyod")
warnings.filterwarnings("ignore", category=UserWarning, module="statsforecast")

BATCH_SIZE = 5000

METRIC_KEY_MAP = {
    "CPU": "cpu", "Memory": "memory", "DiskIO": "disk_io",
    "NetworkSent": "network_sent", "NetworkRecv": "network_recv",
}
METRIC_FIELD_MAP = {
    "CPU": "cpu", "Memory": "memory", "DiskIO": "disk_io",
    "NetworkSent": "network_sent_bytes", "NetworkRecv": "network_received_bytes",
}
METRIC_RANGE = {"CPU": 100, "Memory": 100, "DiskIO": 1, "NetworkSent": 2000, "NetworkRecv": 2000}


# ── Helpers ──────────────────────────────────────────────

def _parse_ts(timestamp: str) -> datetime:
    KST = timedelta(hours=9)
    try:
        ts = datetime.fromisoformat(timestamp.rstrip('Z'))
        if ts.tzinfo is None:
            return ts - KST
        return ts.replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()


def batch_write(url, token, org, bucket, lines):
    if not lines:
        return
    body = "\n".join(lines).encode("utf-8")
    req = urllib.request.Request(
        f"{url}/api/v2/write?org={org}&bucket={bucket}",
        data=body,
        headers={"Authorization": f"Token {token}", "Content-Type": "text/plain; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        pass


def delete_measurement(url, token, org, bucket, measurement):
    body = f'{{"start":"2020-01-01T00:00:00Z","stop":"2030-01-01T00:00:00Z","predicate":"_measurement=\\"{measurement}\\""}}'.encode()
    req = urllib.request.Request(
        f"{url}/api/v2/delete?org={org}&bucket={bucket}",
        data=body,
        headers={"Authorization": f"Token {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30):
            pass
    except Exception as e:
        log.warning(f"Delete {measurement} from {bucket}: {e}")


# ── Data Loading ─────────────────────────────────────────

def load_metrics(client, bucket, agent_id, days):
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
    metrics = []
    for table in tables:
        for r in table.records:
            v = r.values
            metrics.append({
                "ts": r.get_time().isoformat(),
                "cpu": float(v.get("cpu", 0) or 0),
                "mem": float(v.get("memory", 0) or 0),
                "disk": float(v.get("disk_io", 0) or 0),
                "net_s": float(v.get("network_sent_bytes", 0) or 0),
                "net_r": float(v.get("network_received_bytes", 0) or 0),
            })
    log.info(f"[load] {len(metrics)} metric points")

    # Peripherals
    pq = f'''
    from(bucket: "{bucket}")
      |> range(start: -{days}d)
      |> filter(fn: (r) => r._measurement == "peripheral_status")
      |> filter(fn: (r) => r.agent_id == "{agent_id}")
      |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
      |> group()
      |> sort(columns: ["_time"])
    '''
    periph_map = {}
    try:
        for table in client.query_api().query(pq):
            for r in table.records:
                ts = r.get_time().isoformat()
                periph_map[ts] = {pf: int(r.values.get(pf, -1) or -1) for pf in PERIPHERAL_FIELDS}
    except Exception:
        pass
    log.info(f"[load] {len(periph_map)} peripheral points")

    # Forecasts
    fq = f'''
    from(bucket: "{bucket}")
      |> range(start: -{days}d)
      |> filter(fn: (r) => r._measurement == "arima_forecast")
      |> filter(fn: (r) => r.agent_id == "{agent_id}")
      |> filter(fn: (r) => r._field == "predicted_value")
    '''
    forecast_slots = defaultdict(lambda: defaultdict(dict))
    fc_count = 0
    for table in client.query_api().query(fq):
        for r in table.records:
            ts = r.get_time().isoformat()
            metric = r.values.get("metric", "")
            horizon = int(r.values.get("horizon_min", 0))
            key = METRIC_KEY_MAP.get(metric)
            if key and horizon > 0:
                forecast_slots[ts][horizon][key] = float(r.get_value())
                fc_count += 1
    log.info(f"[load] {fc_count} forecast records across {len(forecast_slots)} slots")

    # Metrics map for accuracy matching
    metrics_map = {}
    for m in metrics:
        key = datetime.fromisoformat(m["ts"]).replace(second=0, microsecond=0).isoformat()
        metrics_map[key] = {
            "cpu": m["cpu"], "memory": m["mem"], "disk_io": m["disk"],
            "network_sent_bytes": m["net_s"], "network_received_bytes": m["net_r"],
        }

    return metrics, periph_map, dict(forecast_slots), metrics_map


# ── Line Builders ────────────────────────────────────────

def detection_line(agent_id, ts, det):
    ts_dt = _parse_ts(ts)
    p = Point("anomaly_detection") \
        .tag("agent_id", agent_id).tag("engine", det.engine) \
        .tag("metric", det.metric).tag("severity", det.severity)
    if det.details:
        p.tag("details", str(det.details)[:256])
    p.field("score", float(det.score)).field("threshold", float(det.threshold)) \
        .field("confidence", float(det.confidence)).field("actual_value", float(det.value))
    if det.forecast is not None:
        p.field("arima_predicted", float(det.forecast))
    if det.residual is not None:
        p.field("arima_deviation", float(det.residual))
    p.time(ts_dt)
    return p.to_line_protocol()


def evaluation_lines(agent_id, ts, eval_dict):
    ts_dt = _parse_ts(ts)
    sev = eval_dict.get("overall_severity", "normal")
    ds = eval_dict.get("data_source", "none")
    mr = eval_dict.get("model_ready", False)
    lines = []
    for h in eval_dict.get("horizons", []):
        p = Point("arima_ecod_ensemble_forecast_eval") \
            .tag("agent_id", agent_id) \
            .tag("horizon_min", str(h.get("horizon_min", 0))) \
            .tag("severity", h.get("severity", "normal")) \
            .tag("overall_severity", sev).tag("data_source", ds) \
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
            .field("model_ready", 1 if mr else 0)
        for fc in h.get("feature_contributions", []):
            mk = fc.get("metric", "").lower().replace(" ", "_")
            if mk:
                p.field(f"contribution_{mk}_percent", float(fc.get("pct", 0)))
                p.field(f"contribution_{mk}_score", float(fc.get("score", 0)))
        p.time(ts_dt)
        lines.append(p.to_line_protocol())
    return lines


def accuracy_line(agent_id, time_dt, metric, horizon, actual, forecast, error):
    p = Point("accuracy") \
        .tag("agent_id", agent_id).tag("metric", metric) \
        .tag("horizon_min", str(horizon)) \
        .field("actual_value", float(actual)) \
        .field("forecast_value", float(forecast)) \
        .field("error_percent", float(error)) \
        .field("within_3sigma", 1 if error <= 3.0 else 0) \
        .time(time_dt)
    return p.to_line_protocol()


# ── Main ─────────────────────────────────────────────────

def run(bucket, also_bucket, agent_id, days):
    from .influx_writer import init_influx, close_influx, INFLUX_URL, INFLUX_ORG, INFLUX_TOKEN
    from .detector import EnhancedAnomalyDetector
    from .forecast_evaluator import ForecastEvaluator
    from influxdb_client import InfluxDBClient

    init_influx()
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    URL, ORG, TOKEN = INFLUX_URL, INFLUX_ORG, INFLUX_TOKEN

    buckets = [bucket]
    if also_bucket:
        buckets.append(also_bucket)

    # Delete existing computed data
    log.info("Deleting existing computed data...")
    for m in ["anomaly_detection", "arima_ecod_ensemble_forecast_eval", "accuracy"]:
        for b in buckets:
            delete_measurement(URL, TOKEN, ORG, b, m)

    # Load
    metrics, periph_map, forecast_slots, metrics_map = load_metrics(client, bucket, agent_id, days)
    client.close()

    if not metrics:
        log.error("No data!")
        close_influx()
        return

    # ── Phase 1: Detections ──
    log.info("=== Phase 1: Detections ===")
    detector = EnhancedAnomalyDetector()
    periph_times = sorted(periph_map.keys())
    last_periph = {}
    total = len(metrics)
    warmup = min(30, max(5, total // 3))
    det_count = 0
    t0 = _time.time()
    buf: Dict[str, List[str]] = {b: [] for b in buckets}

    for i, m in enumerate(metrics):
        for pt in periph_times:
            if pt <= m["ts"]:
                last_periph = periph_map[pt]
            else:
                break

        dp = {
            "AgentId": agent_id, "Timestamp": m["ts"],
            "CPU": m["cpu"], "Memory": m["mem"], "DiskIO": m["disk"],
            "Network": {"Sent": m["net_s"], "Recv": m["net_r"]},
            "Peripherals": last_periph.copy(),
            "Process": {"main": "RUNNING"}, "Logs": [],
        }

        try:
            result = detector.detect(dp, run_ecod=(i >= warmup), run_arima=(i >= warmup))
        except Exception:
            continue

        if result and result.detections:
            for det in result.detections:
                line = detection_line(agent_id, m["ts"], det)
                for b in buckets:
                    buf[b].append(line)
                det_count += 1

        for b in buckets:
            if len(buf[b]) >= BATCH_SIZE:
                batch_write(URL, TOKEN, ORG, b, buf[b])
                buf[b] = []

        if (i + 1) % max(1, total // 10) == 0:
            log.info(f"  detections: {i+1}/{total} ({(i+1)*100//total}%) — {det_count} written")

    for b in buckets:
        if buf[b]:
            batch_write(URL, TOKEN, ORG, b, buf[b])
            buf[b] = []
    log.info(f"  detections done: {det_count} in {_time.time()-t0:.0f}s")

    # ── Phase 2: Evaluation ──
    log.info("=== Phase 2: Evaluation ===")
    fe = ForecastEvaluator()
    eval_count = 0
    t1 = _time.time()
    sorted_fc = sorted(forecast_slots.items())

    for i, (ts, horizons_data) in enumerate(sorted_fc):
        ev = fe.evaluate(agent_id, ts, horizons_data)
        lines = evaluation_lines(agent_id, ts, fe.to_dict(ev))
        for b in buckets:
            buf[b].extend(lines)
            if len(buf[b]) >= BATCH_SIZE:
                batch_write(URL, TOKEN, ORG, b, buf[b])
                buf[b] = []
        eval_count += len(lines)

        if (i + 1) % max(1, len(sorted_fc) // 5) == 0:
            log.info(f"  evaluation: {i+1}/{len(sorted_fc)} ({(i+1)*100//len(sorted_fc)}%)")

    for b in buckets:
        if buf[b]:
            batch_write(URL, TOKEN, ORG, b, buf[b])
            buf[b] = []
    log.info(f"  evaluation done: {eval_count} in {_time.time()-t1:.0f}s")

    # ── Phase 3: Accuracy ──
    log.info("=== Phase 3: Accuracy ===")
    acc_count = 0
    t2 = _time.time()
    TOLERANCE_MIN = 6

    all_forecasts = []
    for ts, horizons in forecast_slots.items():
        fc_time = datetime.fromisoformat(ts)
        for horizon, preds in horizons.items():
            for metric_tag, key in METRIC_KEY_MAP.items():
                if key in preds:
                    all_forecasts.append({
                        "time": fc_time, "metric": metric_tag,
                        "horizon": horizon, "predicted": preds[key],
                    })

    for fc in all_forecasts:
        field = METRIC_FIELD_MAP.get(fc["metric"])
        if not field:
            continue
        expected = fc["time"] + timedelta(minutes=fc["horizon"])
        best = None
        for off in range(-TOLERANCE_MIN, TOLERANCE_MIN + 1):
            c = expected + timedelta(minutes=off)
            k = c.replace(second=0, microsecond=0).isoformat()
            if k in metrics_map and field in metrics_map[k]:
                if best is None or abs(off) < best[0]:
                    best = (abs(off), metrics_map[k][field])
        if best:
            actual = best[1]
            vrange = METRIC_RANGE.get(fc["metric"], 100)
            error = abs(actual - fc["predicted"]) / vrange * 100
            line = accuracy_line(agent_id, expected, fc["metric"], fc["horizon"], actual, fc["predicted"], error)
            for b in buckets:
                buf[b].append(line)
            acc_count += 1

            for b in buckets:
                if len(buf[b]) >= BATCH_SIZE:
                    batch_write(URL, TOKEN, ORG, b, buf[b])
                    buf[b] = []

    for b in buckets:
        if buf[b]:
            batch_write(URL, TOKEN, ORG, b, buf[b])

    log.info(f"  accuracy done: {acc_count} in {_time.time()-t2:.0f}s")
    close_influx()
    total_time = _time.time() - t0
    log.info(f"=== All done! detections={det_count}, evaluations={eval_count}, accuracy={acc_count} in {total_time:.0f}s ===")


@click.command()
@click.option("--bucket", default="pos_metrics")
@click.option("--also-bucket", default="")
@click.option("--agent-id", default="V135-POS-03")
@click.option("--days", default=3, type=int)
def main(bucket, also_bucket, agent_id, days):
    """Backfill all: detections + evaluation + accuracy."""
    run(bucket, also_bucket, agent_id, days)


if __name__ == "__main__":
    main()
