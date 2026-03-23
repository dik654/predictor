"""
Historical Data Generator - Generates past timeseries data from sample data
for testing ECOD, ARIMA forecasts with configurable intervals and ranges.
"""

import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

import click
import numpy as np
import pandas as pd
from statsforecast import StatsForecast
from statsforecast.models import AutoARIMA

from .detector import EnhancedAnomalyDetector, AnomalyResult
from . import influx_writer
from .influx_writer import init_influx, close_influx, write_metrics, write_forecast, update_forecast_actual, write_forecast_evaluation
from .predict_tracker import PredictTracker
from .forecast_evaluator import ForecastEvaluator

log = logging.getLogger("historical_gen")
log.setLevel(logging.INFO)
if not log.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    log.addHandler(handler)


@dataclass
class HistoricalConfig:
    """Configuration for historical data generation."""
    interval_min: int = 10  # Minutes between data points
    hours: int = 96  # Total hours of historical data (4 days)
    agent_id: str = "V135-POS-03"
    bucket: str = "pos_metrics"
    warmup_slots: int = 30  # Slots before ARIMA/ECOD start
    horizons_min: List[int] = None  # Forecast horizons in minutes
    arima_retrain_interval: int = 20  # Retrain ARIMA every N slots
    # StoreInfo — must match C# simulator defaults to share the same InfluxDB series
    store_code: str = "V135"
    store_name: str = "GS25역삼홍인점"
    pos_no: str = "3"
    region_code: str = "16"
    region_name: str = "2부문"

    def __post_init__(self):
        if self.horizons_min is None:
            self.horizons_min = [30, 60, 360, 720, 1440, 2880]

    @property
    def total_slots(self) -> int:
        """Calculate total number of slots."""
        return int(self.hours * 60 / self.interval_min)


class HistoricalDetector(EnhancedAnomalyDetector):
    """ARIMA detector adapted for configurable time intervals."""

    def __init__(self, config: HistoricalConfig):
        super().__init__()
        self.config = config
        self.interval_min = config.interval_min

        # For historical data, use more lenient minimums
        # Adjust based on total dataset size
        if config.total_slots < 10:
            self.min_samples_arima = 3
            self.min_samples_ecod = 2
        elif config.total_slots < 30:
            self.min_samples_arima = 5
            self.min_samples_ecod = 3
        else:
            self.min_samples_arima = 10
            self.min_samples_ecod = 5

        # Calculate ARIMA parameters based on interval
        self._calculate_arima_params()
        log.info(f"Historical detector initialized: min_samples_arima={self.min_samples_arima}, "
                f"min_samples_ecod={self.min_samples_ecod}")

    def _calculate_arima_params(self):
        """Calculate ARIMA parameters based on interval."""
        # Frequency string for pandas
        if self.interval_min == 1:
            self.freq = "1min"
        else:
            self.freq = f"{self.interval_min}min"

        # Season length: choose appropriate cycle based on interval
        # Goal: ~1 hour seasonality for stability
        if self.interval_min <= 5:
            self.season_length = 12  # 12 * 5min = 1 hour
        elif self.interval_min <= 10:
            self.season_length = 6   # 6 * 10min = 1 hour
        elif self.interval_min <= 15:
            self.season_length = 4   # 4 * 15min = 1 hour
        elif self.interval_min <= 30:
            self.season_length = 2   # 2 * 30min = 1 hour
        else:
            self.season_length = 1   # No seasonality for very long intervals

        # Horizon steps at this interval
        # Round up to ensure at least 1 step (avoid 0-step horizons)
        self.horizon_steps = [max(1, (h + self.interval_min - 1) // self.interval_min)
                             for h in self.config.horizons_min]
        self.max_h = max(self.horizon_steps)

        log.info(f"ARIMA params: freq={self.freq}, season={self.season_length}, "
                f"horizons={self.config.horizons_min} -> steps={self.horizon_steps}")

    def _run_cached_arima(self, agent_id: str, metric_name: str, values: deque) -> Optional[AnomalyResult]:
        """Override ARIMA with configurable interval parameters."""
        from .detector import ARIMA_RESIDUAL_K, WINDOW_SIZE

        if len(values) < self.min_samples_arima:
            log.debug(f"Not enough samples for ARIMA: {len(values)} < {self.min_samples_arima}")
            return None

        try:
            arr = np.array(values)

            # Prepare data with custom frequency
            df = pd.DataFrame({
                "unique_id": agent_id,
                "ds": pd.date_range(end=pd.Timestamp.now(), periods=len(arr), freq=self.freq),
                "y": arr,
            })

            # Check if we need to retrain
            need_retrain = False
            if metric_name not in self.arima_models[agent_id]:
                need_retrain = True
            else:
                # Retrain periodically
                if len(values) % self.config.arima_retrain_interval == 0:
                    need_retrain = True

            if need_retrain:
                sf = StatsForecast(
                    models=[AutoARIMA(season_length=self.season_length)],
                    freq=self.freq,
                )
                sf.fit(df)
                self.arima_models[agent_id][metric_name] = sf
                log.debug(f"ARIMA trained: {agent_id}/{metric_name} (season={self.season_length})")
            else:
                sf = self.arima_models[agent_id][metric_name]
                sf.fit(df)

            # Forecast all horizons in one call
            forecast_df = sf.predict(h=self.max_h)
            all_forecasts = forecast_df["AutoARIMA"].values

            # Current forecast
            forecast_value = float(all_forecasts[0])
            actual_value = float(arr[-1])
            residual = abs(actual_value - forecast_value)

            # Track residuals
            if metric_name not in self.arima_residuals[agent_id]:
                self.arima_residuals[agent_id][metric_name] = deque(maxlen=WINDOW_SIZE)
            self.arima_residuals[agent_id][metric_name].append(residual)

            # Calculate threshold
            residual_history = np.array(self.arima_residuals[agent_id][metric_name])
            if len(residual_history) > 5:
                threshold = float(ARIMA_RESIDUAL_K * np.std(residual_history))
                threshold = max(threshold, 0.1)
            else:
                threshold = float(np.mean(residual_history) * 2) if len(residual_history) > 0 else 1.0

            # Score
            score = residual / max(threshold, 0.01)

            # Severity
            if residual > threshold * 1.5:
                severity = "critical"
                confidence = min(0.95, score / 2)
            elif residual > threshold:
                severity = "warning"
                confidence = min(0.8, score / 2)
            else:
                severity = "normal"
                confidence = 1.0 - min(0.9, score)

            # Build forecast horizon using multi-resolution
            interval_sec = self.interval_min * 60
            forecast_horizon = self._multi_resolution_forecast(
                agent_id, metric_name, arr, interval_sec
            )

            if metric_name == "CPU":
                warning_threshold, critical_threshold = 80.0, 90.0
            elif metric_name == "Memory":
                warning_threshold, critical_threshold = 85.0, 95.0
            else:  # DiskIO
                warning_threshold, critical_threshold = 70.0, 85.0

            for fh in forecast_horizon:
                pred_value = fh["value"]
                if pred_value >= critical_threshold:
                    fh["severity"] = "critical"
                elif pred_value >= warning_threshold:
                    fh["severity"] = "warning"
                else:
                    fh["severity"] = "normal"

            return AnomalyResult(
                engine="arima",
                metric=metric_name,
                value=actual_value,
                score=float(score),
                threshold=threshold,
                forecast=forecast_value,
                residual=float(residual),
                severity=severity,
                confidence=confidence,
                details=f"Predicted: {forecast_value:.2f}, Actual: {actual_value:.2f}",
                forecast_horizon=forecast_horizon,
            )

        except Exception as e:
            log.warning(f"ARIMA failed for {metric_name}: {e}")
            return None


def load_and_aggregate_sample(file_path: Path, interval_min: int) -> List[dict]:
    """
    Load sample data and aggregate into time windows.

    Args:
        file_path: Path to data_pos.txt
        interval_min: Aggregation interval in minutes

    Returns:
        List of aggregated window dicts
    """
    log.info(f"Loading sample data from {file_path}...")

    records = []
    with open(file_path, 'r') as f:
        for line in f:
            try:
                records.append(json.loads(line.strip()))
            except json.JSONDecodeError:
                continue

    # Filter to records with CPU (skip logs-only records)
    cpu_records = [r for r in records if "CPU" in r and r.get("CPU") is not None]
    log.info(f"Loaded {len(records)} total records, {len(cpu_records)} with CPU metrics")

    # Parse timestamps and group by interval
    windows = {}
    for record in cpu_records:
        try:
            ts_str = record["Timestamp"]
            # Try parsing "2025-12-11 15:05:26" format
            if " " in ts_str:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            else:
                ts = datetime.fromisoformat(ts_str.rstrip('Z'))

            # Round down to interval
            window_key = ts.replace(second=0, microsecond=0, minute=(ts.minute // interval_min) * interval_min)

            if window_key not in windows:
                windows[window_key] = []
            windows[window_key].append(record)
        except Exception as e:
            log.warning(f"Failed to parse record: {e}")
            continue

    # Aggregate each window
    aggregated = []
    for ts_key in sorted(windows.keys()):
        recs = windows[ts_key]

        # Calculate averages
        cpus = [r.get("CPU", 0) for r in recs if r.get("CPU") is not None]
        mems = [r.get("Memory", 0) for r in recs if r.get("Memory") is not None]
        disks = [r.get("DiskIO", 0) for r in recs if r.get("DiskIO") is not None]

        sent_list = [r.get("Network", {}).get("Sent", 0) for r in recs]
        recv_list = [r.get("Network", {}).get("Recv", 0) for r in recs]

        aggregated.append({
            "ts": ts_key,
            "CPU": float(np.mean(cpus)) if cpus else 50.0,
            "Memory": float(np.mean(mems)) if mems else 50.0,
            "DiskIO": float(np.mean(disks)) if disks else 0.1,
            "Network": {
                "Sent": int(np.mean(sent_list)) if sent_list else 0,
                "Recv": int(np.mean(recv_list)) if recv_list else 0,
            }
        })

    log.info(f"Aggregated into {len(aggregated)} windows at {interval_min}min intervals")
    return aggregated


def load_from_influxdb(agent_id: str, bucket: str, hours: int = 72) -> List[dict]:
    """
    Load raw metrics from InfluxDB as base pattern data.

    Returns:
        List of dicts with keys: ts, CPU, Memory, DiskIO, Network:{Sent,Recv}
    """
    from influxdb_client import InfluxDBClient
    from . import influx_writer as iw

    log.info(f"Loading from InfluxDB: agent={agent_id}, bucket={bucket}, range={hours}h")
    ic = InfluxDBClient(url=iw.INFLUX_URL, token=iw.INFLUX_TOKEN, org=iw.INFLUX_ORG)
    query_api = ic.query_api()

    query = f'''
    from(bucket: "{bucket}")
      |> range(start: -{hours}h)
      |> filter(fn: (r) => r._measurement == "metrics")
      |> filter(fn: (r) => r.agent_id == "{agent_id}")
      |> filter(fn: (r) => r._field == "cpu" or r._field == "memory" or r._field == "disk_io"
          or r._field == "network_sent_bytes" or r._field == "network_received_bytes")
      |> sort(columns: ["_time"], desc: false)
    '''

    records_map = {}
    tables = query_api.query(query)
    for table in tables:
        for record in table.records:
            ts = record.get_time()
            field = record.get_field()
            value = record.get_value()
            if ts not in records_map:
                records_map[ts] = {"ts": ts, "CPU": 0, "Memory": 0, "DiskIO": 0,
                                   "Network": {"Sent": 0, "Recv": 0}}
            if value is None:
                continue
            if field == "cpu":
                records_map[ts]["CPU"] = float(value)
            elif field == "memory":
                records_map[ts]["Memory"] = float(value)
            elif field == "disk_io":
                records_map[ts]["DiskIO"] = float(value)
            elif field == "network_sent_bytes":
                records_map[ts]["Network"]["Sent"] = int(value)
            elif field == "network_received_bytes":
                records_map[ts]["Network"]["Recv"] = int(value)

    # Load latest peripheral status
    periph_query = f'''
    from(bucket: "{bucket}")
      |> range(start: -{hours}h)
      |> filter(fn: (r) => r._measurement == "peripheral_status")
      |> filter(fn: (r) => r.agent_id == "{agent_id}")
      |> last()
    '''
    peripherals = {}
    try:
        periph_tables = query_api.query(periph_query)
        for table in periph_tables:
            for record in table.records:
                peripherals[record.get_field()] = int(record.get_value()) if record.get_value() is not None else 1
    except Exception:
        pass

    # Load latest process status
    process_query = f'''
    from(bucket: "{bucket}")
      |> range(start: -{hours}h)
      |> filter(fn: (r) => r._measurement == "metrics")
      |> filter(fn: (r) => r.agent_id == "{agent_id}")
      |> filter(fn: (r) => r._field =~ /^process_/)
      |> last()
    '''
    process = {}
    try:
        proc_tables = query_api.query(process_query)
        for table in proc_tables:
            for record in table.records:
                field = record.get_field()
                # process_XXX -> XXX
                name = field.replace("process_", "")
                process[name] = int(record.get_value()) if record.get_value() is not None else 1
    except Exception:
        pass

    ic.close()
    result = sorted(records_map.values(), key=lambda x: x["ts"])

    # Attach peripheral/process to all records so synthesize_data can carry them
    if peripherals or process:
        for rec in result:
            rec["Peripherals"] = peripherals
            rec["Process"] = process
        log.info(f"Attached peripherals={list(peripherals.keys())}, process={list(process.keys())}")

    log.info(f"Loaded {len(result)} records from InfluxDB")
    return result


# ── Time-of-day profiles ─────────────────────────────────────────────────────
# Multiplier applied to base metrics per hour-of-day (KST).
# 0=midnight, 6=early morning, 9=open, 22=close
_HOUR_PROFILE = {
    #  hour: (cpu_mult, net_mult)
    0: (0.20, 0.05), 1: (0.15, 0.03), 2: (0.15, 0.02), 3: (0.15, 0.02),
    4: (0.18, 0.03), 5: (0.25, 0.05), 6: (0.40, 0.15), 7: (0.55, 0.25),
    8: (0.70, 0.40), 9: (0.85, 0.60), 10: (0.95, 0.80), 11: (1.00, 0.90),
    12: (1.10, 1.00), 13: (1.05, 0.95), 14: (0.95, 0.85), 15: (0.90, 0.80),
    16: (0.90, 0.80), 17: (0.95, 0.85), 18: (1.05, 0.95), 19: (1.00, 0.90),
    20: (0.90, 0.80), 21: (0.75, 0.60), 22: (0.50, 0.30), 23: (0.30, 0.10),
}


def _apply_time_profile(base: dict, ts: datetime, rng: np.random.Generator) -> dict:
    """
    Apply time-of-day variation + random noise to a base data point.

    Args:
        base: Base data dict with CPU, Memory, DiskIO, Network
        ts: Target timestamp (assumed KST or local)
        rng: numpy random generator for reproducibility
    """
    hour = ts.hour
    cpu_m, net_m = _HOUR_PROFILE.get(hour, (0.8, 0.5))

    # Base values
    cpu_base = base["CPU"]
    mem_base = base["Memory"]
    disk_base = base["DiskIO"]
    net_sent = base["Network"]["Sent"]
    net_recv = base["Network"]["Recv"]

    # Apply multiplier + noise
    cpu = cpu_base * cpu_m + rng.normal(0, cpu_base * 0.08)
    cpu = float(np.clip(cpu, 1.0, 100.0))

    # Memory is sticky — slow drift, not multiplied
    mem = mem_base + rng.normal(0, 1.5)
    mem = float(np.clip(mem, 30.0, 98.0))

    disk = disk_base * cpu_m + rng.normal(0, max(disk_base * 0.1, 0.01))
    disk = float(np.clip(disk, 0.0, 100.0))

    sent = int(max(0, net_sent * net_m + rng.normal(0, max(net_sent * 0.15, 10))))
    recv = int(max(0, net_recv * net_m + rng.normal(0, max(net_recv * 0.15, 10))))

    return {
        "CPU": round(cpu, 1),
        "Memory": round(mem, 1),
        "DiskIO": round(disk, 2),
        "Network": {"Sent": sent, "Recv": recv},
    }


# ── Event injection ──────────────────────────────────────────────────────────

def _inject_events(data_points: List[dict], rng: np.random.Generator) -> int:
    """
    Inject realistic anomaly events into the data stream.

    Events:
      - CPU spike (short burst, 2-5 points)
      - Memory gradual rise (20-40 points, ~3-7 hours at 10min interval)
      - Network surge (3-8 points)
      - Idle period (CPU/Network drop to near-zero, 6-12 points)

    Returns number of events injected.
    """
    n = len(data_points)
    if n < 50:
        return 0

    events_injected = 0

    # Roughly 1 event per 6 hours of data
    # At 10min interval: 6h = 36 slots
    num_events = max(1, n // 36)
    num_events = min(num_events, 8)  # cap

    event_types = ["cpu_spike", "memory_rise", "network_surge", "idle_period"]

    for _ in range(num_events):
        etype = rng.choice(event_types)
        start = rng.integers(10, max(11, n - 50))

        if etype == "cpu_spike":
            duration = rng.integers(2, 6)
            peak = rng.uniform(70, 95)
            for j in range(duration):
                idx = start + j
                if idx >= n:
                    break
                dp = data_points[idx]
                # Bell curve shape
                t = j / max(duration - 1, 1)
                factor = np.sin(t * np.pi)
                dp["CPU"] = round(float(np.clip(dp["CPU"] + (peak - dp["CPU"]) * factor, 0, 100)), 1)
                dp["DiskIO"] = round(float(np.clip(dp["DiskIO"] * (1 + factor * 2), 0, 100)), 2)
            events_injected += 1

        elif etype == "memory_rise":
            duration = rng.integers(20, 40)
            target = rng.uniform(85, 96)
            for j in range(duration):
                idx = start + j
                if idx >= n:
                    break
                dp = data_points[idx]
                progress = j / max(duration - 1, 1)
                dp["Memory"] = round(float(np.clip(
                    dp["Memory"] + (target - dp["Memory"]) * progress, 30, 98
                )), 1)
            events_injected += 1

        elif etype == "network_surge":
            duration = rng.integers(3, 9)
            multiplier = rng.uniform(5, 20)
            for j in range(duration):
                idx = start + j
                if idx >= n:
                    break
                dp = data_points[idx]
                dp["Network"]["Sent"] = int(dp["Network"]["Sent"] * multiplier)
                dp["Network"]["Recv"] = int(dp["Network"]["Recv"] * multiplier)
            events_injected += 1

        elif etype == "idle_period":
            duration = rng.integers(6, 13)
            for j in range(duration):
                idx = start + j
                if idx >= n:
                    break
                dp = data_points[idx]
                dp["CPU"] = round(float(rng.uniform(1.0, 3.0)), 1)
                dp["DiskIO"] = round(float(rng.uniform(0.0, 0.05)), 2)
                dp["Network"]["Sent"] = int(rng.uniform(0, 20))
                dp["Network"]["Recv"] = int(rng.uniform(0, 10))
            events_injected += 1

    return events_injected


def synthesize_data(
    base_records: List[dict],
    slots: List[datetime],
    agent_id: str,
    config: "HistoricalConfig",
    seed: int = 42,
) -> List[dict]:
    """
    Generate realistic synthetic data by applying time-of-day profiles,
    noise, and event injection to base pattern records.

    The first BLEND_WINDOW points smoothly transition from the last base record
    to the synthesized values, ensuring continuity with existing data.

    Args:
        base_records: Source records (from file or InfluxDB)
        slots: Target time slots to fill
        agent_id: Agent ID
        config: Historical configuration
        seed: Random seed for reproducibility

    Returns:
        List of data points ready for detection and InfluxDB write
    """
    rng = np.random.default_rng(seed)
    BLEND_WINDOW = min(6, len(slots))  # ~1 hour at 10min interval

    # Last known values for smooth transition
    last_rec = base_records[-1]
    prev_cpu = last_rec["CPU"]
    prev_mem = last_rec["Memory"]
    prev_disk = last_rec["DiskIO"]
    prev_sent = last_rec["Network"]["Sent"]
    prev_recv = last_rec["Network"]["Recv"]

    # Carry forward peripheral/process status from base data
    peripherals = last_rec.get("Peripherals", {})
    process = last_rec.get("Process", {})

    data_points = []
    for i, ts in enumerate(slots):
        base = base_records[i % len(base_records)]
        varied = _apply_time_profile(base, ts, rng)

        # Smooth blend: first few points transition from last real data
        if i < BLEND_WINDOW:
            alpha = i / BLEND_WINDOW  # 0 → 1
            varied["CPU"] = round(prev_cpu * (1 - alpha) + varied["CPU"] * alpha, 1)
            varied["Memory"] = round(prev_mem * (1 - alpha) + varied["Memory"] * alpha, 1)
            varied["DiskIO"] = round(prev_disk * (1 - alpha) + varied["DiskIO"] * alpha, 2)
            varied["Network"]["Sent"] = int(prev_sent * (1 - alpha) + varied["Network"]["Sent"] * alpha)
            varied["Network"]["Recv"] = int(prev_recv * (1 - alpha) + varied["Network"]["Recv"] * alpha)

        dp = {
            "AgentId": agent_id,
            "Timestamp": ts.isoformat() + "Z",
            "CPU": varied["CPU"],
            "Memory": varied["Memory"],
            "DiskIO": varied["DiskIO"],
            "Network": varied["Network"],
            "StoreInfo": {
                "StoreCode": config.store_code,
                "StoreName": config.store_name,
                "PosNo": config.pos_no,
                "RegionCode": config.region_code,
                "RegionName": config.region_name,
            },
            "_slot_index": i,
            "_nanos_offset": 0,
        }
        # Attach peripheral/process status (carry forward from last real data)
        if peripherals:
            dp["Peripherals"] = dict(peripherals)
        if process:
            dp["Process"] = {k: v for k, v in process.items()}
        data_points.append(dp)

    # Inject anomaly events (skip blend window)
    num_events = _inject_events(data_points, rng)
    log.info(f"Synthesized {len(data_points)} points from {len(base_records)} base records, "
             f"{num_events} events injected, blend={BLEND_WINDOW} points")

    return data_points


def build_time_slots(config: HistoricalConfig, start_after: Optional[datetime] = None) -> List[datetime]:
    """
    Generate target time slots.

    If start_after is given, generates slots from start_after to now.
    Otherwise, generates from (now - hours) to now.

    Args:
        config: Historical configuration
        start_after: If set, fill from this time to now (ignores config.hours)

    Returns:
        List of datetime objects
    """
    # Floor current time to interval boundary
    now = datetime.utcnow()
    now = now.replace(second=0, microsecond=0)
    minutes_to_floor = now.minute % config.interval_min
    now = now - timedelta(minutes=minutes_to_floor)

    if start_after:
        # Ensure naive datetime (strip timezone if present)
        sa = start_after
        if hasattr(sa, 'tzinfo') and sa.tzinfo:
            sa = sa.replace(tzinfo=None)
        # Floor to interval boundary, then advance one interval
        start = sa.replace(second=0, microsecond=0)
        sa_floor = start.minute % config.interval_min
        start = start - timedelta(minutes=sa_floor) + timedelta(minutes=config.interval_min)
        log.info(f"build_time_slots: start_after={sa} -> start={start}, now={now}")
    else:
        start = now - timedelta(hours=config.hours)

    # Generate slots from start to now
    slots = []
    ts = start
    while ts <= now:
        slots.append(ts)
        ts += timedelta(minutes=config.interval_min)

    if not slots:
        log.error(f"No slots generated! start={start}, now={now}")
        return slots

    log.info(f"Generated {len(slots)} time slots from {slots[0]} to {slots[-1]}")
    return slots


def map_data_to_slots(windows: List[dict], slots: List[datetime], agent_id: str,
                      config: "HistoricalConfig" = None) -> List[dict]:
    """
    Cycle aggregated windows to fill target slots.

    Args:
        windows: Aggregated window data
        slots: Target time slots
        agent_id: Agent ID

    Returns:
        List of data points with generated timestamps
    """
    result = []
    for i, ts in enumerate(slots):
        window = windows[i % len(windows)]

        # Create data point
        data_point = {
            "AgentId": agent_id,
            "Timestamp": ts.isoformat() + "Z",
            "CPU": window["CPU"],
            "Memory": window["Memory"],
            "DiskIO": window["DiskIO"],
            "Network": window["Network"],
            "StoreInfo": {
                "StoreCode": config.store_code if config else "V135",
                "StoreName": config.store_name if config else "GS25역삼홍인점",
                "PosNo":     config.pos_no     if config else "3",
                "RegionCode": config.region_code if config else "99",
                "RegionName": config.region_name if config else "시뮬레이션 부문",
            },
            "_slot_index": i,
            "_nanos_offset": 0,  # 0 = exact boundary; ensures re-runs overwrite via InfluxDB dedup
        }
        result.append(data_point)

    log.info(f"Mapped {len(windows)} windows to {len(result)} data points")
    return result


async def run_detection_and_forecast(
    detector: HistoricalDetector,
    data_point: dict,
    slot_index: int,
    config: HistoricalConfig,
    tracker: PredictTracker,
) -> int:
    """
    Run ECOD/ARIMA detection and write forecasts.

    Args:
        detector: HistoricalDetector instance
        data_point: Current data point
        slot_index: Current slot index
        config: Historical configuration
        tracker: PredictTracker instance

    Returns:
        Number of forecasts written
    """
    forecast_count = 0

    try:
        # Run detection
        result = detector.detect(data_point, run_ecod=True, run_arima=True)

        agent_id = data_point["AgentId"]
        timestamp = data_point["Timestamp"]

        # Process ARIMA results
        arima_count = 0
        for detection in result.detections:
            if detection.engine == "arima":
                arima_count += 1

                if detection.forecast_horizon:
                    metric = detection.metric

                    # Write forecast for each horizon
                    for horizon_info in detection.forecast_horizon:
                        horizon_min = horizon_info["minutes"]
                        pred_value = horizon_info["value"]

                        try:
                            await write_forecast(
                                agent_id=agent_id,
                                timestamp=timestamp,
                                metric=metric,
                                horizon_min=horizon_min,
                                predicted_value=pred_value,
                                bucket=config.bucket,
                            )

                            # Record in tracker
                            tracker.record(agent_id, metric, timestamp, horizon_min, pred_value)
                            forecast_count += 1
                        except Exception as e:
                            log.warning(f"Failed to write forecast: {e}")

        # Compare actuals with past forecasts
        # tracker.compare_actual_async() uses default bucket (sample_metrics)
        try:
            await tracker.compare_actual_async(
                agent_id=agent_id,
                timestamp=timestamp,
                raw_metrics=result.raw_metrics,
            )
        except Exception as e:
            log.warning(f"Failed to compare actuals: {e}")

    except Exception as e:
        log.error(f"Detection failed for slot {slot_index}: {e}")

    return forecast_count


async def main(
    file_path: str,
    interval_min: int = 10,
    hours: int = 96,
    agent_id: str = "V135-POS-03",
    bucket: str = "pos_metrics",
    horizons: str = "30,60,360,720,1440,2880",
    store_code: str = "V135",
    store_name: str = "GS25역삼홍인점",
    pos_no: str = "3",
    region_code: str = "16",
    region_name: str = "2부문",
    from_influx: bool = False,
    start_after: str = "",
    seed: int = 42,
):
    """
    Generate historical data and forecasts.

    Args:
        file_path: Path to sample data file (ignored if from_influx=True)
        interval_min: Time interval in minutes
        hours: Total hours of data to generate (ignored if start_after is set)
        agent_id: Agent ID
        bucket: InfluxDB bucket
        horizons: Comma-separated forecast horizons in minutes
        from_influx: Load base data from InfluxDB instead of file
        start_after: ISO datetime string — fill from this time to now (e.g. "2026-03-20T18:50:00")
        seed: Random seed for reproducibility
    """
    # Parse horizons
    horizons_min = [int(h.strip()) for h in horizons.split(",")]

    # Create config
    config = HistoricalConfig(
        interval_min=interval_min,
        hours=hours,
        agent_id=agent_id,
        bucket=bucket,
        horizons_min=horizons_min,
        store_code=store_code,
        store_name=store_name,
        pos_no=pos_no,
        region_code=region_code,
        region_name=region_name,
    )

    # Parse start_after
    start_after_dt: Optional[datetime] = None
    if start_after:
        try:
            start_after_dt = datetime.fromisoformat(start_after.rstrip("Z"))
        except ValueError:
            log.error(f"Invalid --start-after format: {start_after}. Use ISO format like 2026-03-20T18:50:00")
            return

    log.info(f"Historical Data Generator")
    log.info(f"  Interval: {config.interval_min} min")
    log.info(f"  Source: {'InfluxDB' if from_influx else file_path}")
    if start_after_dt:
        log.info(f"  Range: {start_after_dt} → now")
    else:
        log.info(f"  Range: {config.hours} hours")
    log.info(f"  Agent: {config.agent_id}")
    log.info(f"  Bucket: {config.bucket}")
    log.info(f"  Horizons: {config.horizons_min} minutes")
    log.info(f"  Seed: {seed}")

    try:
        # Load base data — combine file + InfluxDB for richer patterns
        windows = []

        # Source 1: InfluxDB real data
        if from_influx:
            base_records = load_from_influxdb(agent_id, bucket, hours=72)
            if base_records:
                # Auto-detect start_after from last record if not explicitly set
                if not start_after_dt:
                    last_ts = base_records[-1]["ts"]
                    if hasattr(last_ts, 'tzinfo') and last_ts.tzinfo:
                        start_after_dt = last_ts.replace(tzinfo=None)
                    else:
                        start_after_dt = last_ts
                    log.info(f"Auto-detected last data at {start_after_dt}, will fill from there")
                for rec in base_records:
                    windows.append({
                        "CPU": rec["CPU"], "Memory": rec["Memory"],
                        "DiskIO": rec["DiskIO"], "Network": rec["Network"],
                    })
                log.info(f"Loaded {len(base_records)} records from InfluxDB")
            else:
                log.warning("No data in InfluxDB, falling back to file only")

        # Source 2: Sample file (always load if exists, to enrich pattern pool)
        file_windows = []
        sample_path = Path(file_path)
        if sample_path.exists():
            file_windows = load_and_aggregate_sample(sample_path, config.interval_min)
            log.info(f"Loaded {len(file_windows)} windows from {file_path}")

        # Merge: interleave file and influx data for pattern diversity
        if windows and file_windows:
            # Shuffle-merge: alternate between sources
            merged = []
            max_len = max(len(windows), len(file_windows))
            for i in range(max_len):
                if i < len(windows):
                    merged.append(windows[i])
                if i < len(file_windows):
                    merged.append(file_windows[i])
            windows = merged
            log.info(f"Merged {len(windows)} total base patterns (InfluxDB + file)")
        elif file_windows:
            windows = file_windows

        if not windows:
            log.error("No data loaded from any source!")
            return

        # Create detector and tracker
        detector = HistoricalDetector(config)
        tracker = PredictTracker(retention_hours=max(config.horizons_min) // 60 * 3)

        # Build time slots
        placeholder_slots = build_time_slots(config, start_after=start_after_dt)
        if not placeholder_slots:
            log.error("No time slots to fill!")
            return
        total_slots = len(placeholder_slots)

        # Calculate actual warmup
        if total_slots < 20:
            actual_warmup = min(3, max(1, total_slots // 6))
        else:
            actual_warmup = min(config.warmup_slots, max(5, total_slots // 3))
        log.info(f"Warmup period: {actual_warmup} slots (out of {total_slots})")

        # Generate data: synthesize with time-of-day variation + events
        data_points = synthesize_data(windows, placeholder_slots, config.agent_id, config, seed=seed)

        # PASS 1: Run all ARIMA/ECOD in memory — no InfluxDB writes yet.
        # This separates CPU-intensive ML work from timestamp assignment so that
        # we can recompute "now" after processing completes, minimising the gap
        # between the last historical slot and live data.
        log.info(f"Pass 1/2: ARIMA/ECOD detection ({total_slots} slots)...")
        detection_results = []
        for i, data_point in enumerate(data_points):
            try:
                if i >= actual_warmup:
                    result = detector.detect(data_point, run_ecod=True, run_arima=True)
                else:
                    result = detector.detect(data_point, run_ecod=False, run_arima=False)
                detection_results.append(result)
            except Exception as e:
                log.error(f"Detection error for slot {i}: {e}")
                detection_results.append(None)

            if (i + 1) % max(1, total_slots // 10) == 0:
                pct = (i + 1) * 100 // total_slots
                log.info(f"  {i+1}/{total_slots} ({pct}%)")

        # Recompute fresh timestamps NOW — after all ML work is done.
        # The gap between the last slot and live data is now just InfluxDB write time.
        if not start_after_dt:
            # Only recompute timestamps if not filling a specific gap
            fresh_slots = build_time_slots(config)
            for i, data_point in enumerate(data_points):
                if i < len(fresh_slots):
                    data_point["Timestamp"] = fresh_slots[i].isoformat() + "Z"
            log.info(f"Timestamps refreshed: {fresh_slots[0]} → {fresh_slots[-1]}")
        else:
            log.info(f"Using fixed timestamps: {data_points[0]['Timestamp']} → {data_points[-1]['Timestamp']}")

        # Connect to InfluxDB right before writing (avoids idle-timeout disconnect during Pass 1)
        # Set INFLUX_BUCKET so predict_tracker writes accuracy to the correct bucket
        influx_writer.INFLUX_BUCKET = config.bucket
        log.info(f"Connecting to InfluxDB... (bucket={config.bucket})")
        init_influx()

        # PASS 2: Write metrics and forecasts to InfluxDB with fresh timestamps.
        log.info(f"Pass 2/2: Writing to InfluxDB...")
        total_forecasts = 0
        total_evaluations = 0
        fe = ForecastEvaluator()

        # Pre-populate ForecastEvaluator with training data from data_points
        # so ECOD model + feature contributions work from the start
        _train_X = np.array([[dp["CPU"], dp["Memory"], dp["DiskIO"]] for dp in data_points])
        if len(_train_X) >= 10:
            from pyod.models.ecod import ECOD as _ECOD
            _ecod = _ECOD(contamination=0.05)
            _ecod.fit(_train_X)
            fe.models[config.agent_id] = _ecod
            fe.train_scores[config.agent_id] = _ecod.decision_function(_train_X)
            fe._training_data_cache[config.agent_id] = _train_X
            import time as _time
            fe.last_retrain[config.agent_id] = _time.time()
            log.info(f"Pre-trained ForecastEvaluator ECOD with {len(_train_X)} points")

        for i, (data_point, result) in enumerate(zip(data_points, detection_results)):
            # Write metrics
            try:
                raw_metrics = {
                    "CPU": data_point["CPU"],
                    "Memory": data_point["Memory"],
                    "DiskIO": data_point["DiskIO"],
                    "_nanos_offset": data_point["_nanos_offset"],
                }
                success = await write_metrics(
                    agent_id=data_point["AgentId"],
                    timestamp=data_point["Timestamp"],
                    raw_metrics=raw_metrics,
                    bucket=config.bucket,
                    full_data=data_point,
                )
                if not success:
                    log.warning(f"Failed to write metrics for slot {i}")
            except Exception as e:
                log.warning(f"Metrics write error for slot {i}: {e}")

            # Write forecasts and forecast evaluations
            if result and i >= actual_warmup:
                agent_id_val = data_point["AgentId"]
                timestamp_val = data_point["Timestamp"]

                # Collect ARIMA predictions per horizon for evaluation
                forecasts_by_horizon: dict = {}

                for detection in result.detections:
                    if detection.engine == "arima" and detection.forecast_horizon:
                        for horizon_info in detection.forecast_horizon:
                            try:
                                await write_forecast(
                                    agent_id=agent_id_val,
                                    timestamp=timestamp_val,
                                    metric=detection.metric,
                                    horizon_min=horizon_info["minutes"],
                                    predicted_value=horizon_info["value"],
                                    bucket=config.bucket,
                                )
                                tracker.record(agent_id_val, detection.metric, timestamp_val,
                                               horizon_info["minutes"], horizon_info["value"])
                                total_forecasts += 1
                            except Exception as e:
                                log.warning(f"Failed to write forecast: {e}")

                            # Collect for forecast evaluation
                            h_min = horizon_info["minutes"]
                            if h_min not in forecasts_by_horizon:
                                forecasts_by_horizon[h_min] = {}
                            metric_key = detection.metric.lower()
                            if metric_key == "diskio":
                                metric_key = "disk_io"
                            forecasts_by_horizon[h_min][metric_key] = horizon_info["value"]

                # Run forecast evaluation and write to InfluxDB
                if forecasts_by_horizon:
                    fe.update_event(agent_id_val, data_point)
                    fe.update_fallback_buffer(agent_id_val, data_point)
                    eval_result = fe.evaluate(agent_id_val, timestamp_val, forecasts_by_horizon)
                    eval_dict = fe.to_dict(eval_result)

                    try:
                        await write_forecast_evaluation(
                            agent_id=agent_id_val,
                            timestamp=timestamp_val,
                            horizons=eval_dict.get("horizons", []),
                            overall_severity=eval_dict.get("overall_severity", "normal"),
                            model_ready=eval_dict.get("model_ready", False),
                            data_source=eval_dict.get("data_source", "none"),
                            bucket=config.bucket,
                        )
                        total_evaluations += 1
                    except Exception as e:
                        log.warning(f"Failed to write forecast evaluation: {e}")

                # Fire-and-forget: don't await accuracy writes so they don't block the main loop
                asyncio.create_task(tracker.compare_actual_async(
                    agent_id=agent_id_val,
                    timestamp=timestamp_val,
                    raw_metrics=result.raw_metrics,
                ))

            if (i + 1) % max(1, total_slots // 10) == 0:
                pct = (i + 1) * 100 // total_slots
                log.info(f"  {i+1}/{total_slots} ({pct}%) — forecasts: {total_forecasts}, evaluations: {total_evaluations}")
                await asyncio.sleep(0)  # yield to let pending accuracy tasks run

        # Wait for all pending accuracy writes to complete before closing connection
        log.info("Waiting for accuracy writes to flush...")
        await asyncio.sleep(2)
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        first_ts = data_points[0]["Timestamp"] if data_points else "?"
        last_ts = data_points[-1]["Timestamp"] if data_points else "?"
        log.info(f"✓ Complete! Forecasts: {total_forecasts}, Evaluations: {total_evaluations}")
        log.info(f"  Data spans from {first_ts} to {last_ts}")

    except Exception as e:
        log.error(f"Failed: {e}", exc_info=True)

    finally:
        # Close connection
        log.info("Closing InfluxDB connection...")
        close_influx()


@click.command()
@click.option("--file", "sample_file", default="sample/data_pos.txt",
              help="Path to sample data file (ignored if --from-influx)")
@click.option("--interval-min", default=10, type=int,
              help="Time interval in minutes (default: 10)")
@click.option("--hours", default=96, type=int,
              help="Total hours of data (default: 96, ignored if --start-after)")
@click.option("--agent-id", default="V135-POS-03",
              help="Agent ID (default: V135-POS-03)")
@click.option("--bucket", default="pos_metrics",
              help="InfluxDB bucket (default: pos_metrics)")
@click.option("--horizons", default="30,60,360,720,1440,2880",
              help="Forecast horizons in minutes, comma-separated")
@click.option("--store-code", default="V135", help="Store code (default: V135)")
@click.option("--store-name", default="GS25역삼홍인점", help="Store name")
@click.option("--pos-no", default="3", help="POS number (default: 3)")
@click.option("--region-code", default="16", help="Region code (default: 16)")
@click.option("--region-name", default="2부문", help="Region name")
@click.option("--from-influx", is_flag=True, default=False,
              help="Load base data from InfluxDB instead of file")
@click.option("--start-after", default="",
              help="Fill data from this time to now (ISO format, e.g. 2026-03-20T18:50:00)")
@click.option("--seed", default=42, type=int,
              help="Random seed for reproducibility (default: 42)")
def cli(sample_file, interval_min, hours, agent_id, bucket, horizons,
        store_code, store_name, pos_no, region_code, region_name,
        from_influx, start_after, seed):
    """Generate historical metrics and forecasts with realistic time-of-day variation."""
    asyncio.run(main(
        file_path=sample_file,
        interval_min=interval_min,
        hours=hours,
        agent_id=agent_id,
        bucket=bucket,
        horizons=horizons,
        store_code=store_code,
        store_name=store_name,
        pos_no=pos_no,
        region_code=region_code,
        region_name=region_name,
        from_influx=from_influx,
        start_after=start_after,
        seed=seed,
    ))


if __name__ == "__main__":
    cli()
