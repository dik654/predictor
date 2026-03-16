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
from .influx_writer import init_influx, close_influx, write_metrics, write_forecast, update_forecast_actual
from .predict_tracker import PredictTracker

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

            # Build forecast horizon
            forecast_horizon = []
            warning_threshold = 80.0 if metric_name == "CPU" else 85.0
            critical_threshold = 90.0 if metric_name == "CPU" else 95.0

            for steps, minutes in zip(self.horizon_steps, self.config.horizons_min):
                if steps <= len(all_forecasts):
                    pred_value = float(all_forecasts[steps - 1])
                else:
                    # Extrapolate if needed
                    pred_value = float(all_forecasts[-1])

                # Determine severity
                if pred_value >= critical_threshold:
                    future_severity = "critical"
                elif pred_value >= warning_threshold:
                    future_severity = "warning"
                else:
                    future_severity = "normal"

                forecast_horizon.append({
                    "minutes": minutes,
                    "value": pred_value,
                    "severity": future_severity,
                })

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


def build_time_slots(config: HistoricalConfig) -> List[datetime]:
    """
    Generate target time slots from (now - hours) to now.

    Args:
        config: Historical configuration

    Returns:
        List of datetime objects
    """
    # Floor current time to interval boundary
    now = datetime.utcnow()
    now = now.replace(second=0, microsecond=0)
    minutes_to_floor = now.minute % config.interval_min
    now = now - timedelta(minutes=minutes_to_floor)

    # Start time: shift start back by one interval so that the last slot == now
    start = now - timedelta(hours=config.hours)

    # Generate slots: total_slots + 1 so the last slot is exactly 'now'
    slots = []
    for i in range(config.total_slots + 1):
        ts = start + timedelta(minutes=i * config.interval_min)
        slots.append(ts)

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
):
    """
    Generate historical data and forecasts.

    Args:
        file_path: Path to sample data file
        interval_min: Time interval in minutes
        hours: Total hours of data to generate
        agent_id: Agent ID
        bucket: InfluxDB bucket
        horizons: Comma-separated forecast horizons in minutes
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

    log.info(f"Historical Data Generator")
    log.info(f"  Interval: {config.interval_min} min")
    log.info(f"  Range: {config.hours} hours ({config.total_slots} slots)")
    log.info(f"  Agent: {config.agent_id}")
    log.info(f"  Bucket: {config.bucket}")
    log.info(f"  Horizons: {config.horizons_min} minutes")

    try:
        # Load and aggregate sample data
        windows = load_and_aggregate_sample(Path(file_path), config.interval_min)
        if not windows:
            log.error("No data loaded!")
            return

        # Create detector and tracker
        detector = HistoricalDetector(config)
        tracker = PredictTracker(retention_hours=max(config.horizons_min) // 60 * 3)

        # Calculate actual warmup
        if config.total_slots < 20:
            actual_warmup = min(3, max(1, config.total_slots // 6))
        else:
            actual_warmup = min(config.warmup_slots, max(5, config.total_slots // 3))
        log.info(f"Warmup period: {actual_warmup} slots (out of {config.total_slots})")

        # Use placeholder slots for ARIMA buffer accumulation (relative offsets only)
        placeholder_slots = build_time_slots(config)
        data_points = map_data_to_slots(windows, placeholder_slots, config.agent_id, config)

        # PASS 1: Run all ARIMA/ECOD in memory — no InfluxDB writes yet.
        # This separates CPU-intensive ML work from timestamp assignment so that
        # we can recompute "now" after processing completes, minimising the gap
        # between the last historical slot and live data.
        log.info(f"Pass 1/2: ARIMA/ECOD detection ({config.total_slots} slots)...")
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

            if (i + 1) % max(1, config.total_slots // 10) == 0:
                pct = (i + 1) * 100 // config.total_slots
                log.info(f"  {i+1}/{config.total_slots} ({pct}%)")

        # Recompute fresh timestamps NOW — after all ML work is done.
        # The gap between the last slot and live data is now just InfluxDB write time.
        fresh_slots = build_time_slots(config)
        for i, data_point in enumerate(data_points):
            data_point["Timestamp"] = fresh_slots[i].isoformat() + "Z"
        log.info(f"Timestamps refreshed: {fresh_slots[0]} → {fresh_slots[-1]}")

        # Connect to InfluxDB right before writing (avoids idle-timeout disconnect during Pass 1)
        # Set INFLUX_BUCKET so predict_tracker writes accuracy to the correct bucket
        influx_writer.INFLUX_BUCKET = config.bucket
        log.info(f"Connecting to InfluxDB... (bucket={config.bucket})")
        init_influx()

        # PASS 2: Write metrics and forecasts to InfluxDB with fresh timestamps.
        log.info(f"Pass 2/2: Writing to InfluxDB...")
        total_forecasts = 0
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

            # Write forecasts from stored detection results
            if result and i >= actual_warmup:
                agent_id_val = data_point["AgentId"]
                timestamp_val = data_point["Timestamp"]
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
                # Fire-and-forget: don't await accuracy writes so they don't block the main loop
                asyncio.create_task(tracker.compare_actual_async(
                    agent_id=agent_id_val,
                    timestamp=timestamp_val,
                    raw_metrics=result.raw_metrics,
                ))

            if (i + 1) % max(1, config.total_slots // 10) == 0:
                pct = (i + 1) * 100 // config.total_slots
                log.info(f"  {i+1}/{config.total_slots} ({pct}%) — forecasts: {total_forecasts}")
                await asyncio.sleep(0)  # yield to let pending accuracy tasks run

        # Wait for all pending accuracy writes to complete before closing connection
        log.info("Waiting for accuracy writes to flush...")
        await asyncio.sleep(2)
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        log.info(f"✓ Complete! Total forecasts written: {total_forecasts}")
        log.info(f"  Data spans from {fresh_slots[0]} to {fresh_slots[-1]}")

    except Exception as e:
        log.error(f"Failed: {e}", exc_info=True)

    finally:
        # Close connection
        log.info("Closing InfluxDB connection...")
        close_influx()


@click.command()
@click.option("--file", "sample_file", default="sample/data_pos.txt",
              help="Path to sample data file")
@click.option("--interval-min", default=10, type=int,
              help="Time interval in minutes (default: 10)")
@click.option("--hours", default=96, type=int,
              help="Total hours of data (default: 96 = 4 days)")
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
def cli(sample_file, interval_min, hours, agent_id, bucket, horizons,
        store_code, store_name, pos_no, region_code, region_name):
    """Generate historical metrics and forecasts."""
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
    ))


if __name__ == "__main__":
    cli()
