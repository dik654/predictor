"""
Prediction tracker for comparing predicted vs actual values.
Stores forecasts and matches them with actual values received later.
"""

import asyncio
import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from . import influx_writer

log = logging.getLogger("predict_tracker")


@dataclass
class Forecast:
    """Stored forecast record."""
    agent_id: str
    metric: str
    predicted_at: datetime
    horizon_min: int
    predicted_value: float
    actual_at: Optional[datetime] = None
    actual_value: Optional[float] = None
    error_pct: Optional[float] = None


class PredictTracker:
    """Track predictions and match with actual values for accuracy calculation."""

    def __init__(self, retention_hours: int = 24):
        self.retention_hours = retention_hours
        # (agent_id, metric) -> deque of Forecast
        # maxlen must fit: num_horizons × total_slots (e.g. 6 × 577 = 3462 for 96h/10min)
        self.forecasts: Dict[Tuple[str, str], deque] = defaultdict(
            lambda: deque(maxlen=10000)
        )

    def record(
        self,
        agent_id: str,
        metric: str,
        predicted_at: str,  # ISO timestamp
        horizon_min: int,
        predicted_value: float,
    ):
        """
        Record a new forecast.

        Args:
            agent_id: Agent identifier
            metric: "CPU" or "Memory"
            predicted_at: ISO timestamp when forecast was made
            horizon_min: Minutes into future (30, 60, 120)
            predicted_value: Predicted value
        """
        try:
            predicted_dt = self._parse_timestamp(predicted_at)
            key = (agent_id, metric)

            forecast = Forecast(
                agent_id=agent_id,
                metric=metric,
                predicted_at=predicted_dt,
                horizon_min=horizon_min,
                predicted_value=predicted_value,
            )

            self.forecasts[key].append(forecast)
            log.debug(
                f"Recorded forecast: {agent_id}/{metric} "
                f"{horizon_min}min @ {predicted_at}"
            )
        except Exception as e:
            log.warning(f"Failed to record forecast: {e}")

    def compare_actual(
        self,
        agent_id: str,
        timestamp: str,  # Current time
        raw_metrics: Dict,
    ):
        """
        Compare actual values with stored forecasts (synchronous version for sample mode).
        Matches forecasts by horizon (predicted_at + horizon_min ~= current_time).

        Args:
            agent_id: Agent identifier
            timestamp: Current ISO timestamp
            raw_metrics: {"CPU": float, "Memory": float, ...}
        """
        # Synchronous version - used in sample mode where we can await
        import asyncio
        try:
            current_dt = self._parse_timestamp(timestamp)

            for metric in ["CPU", "Memory", "DiskIO", "NetworkSent", "NetworkRecv"]:
                if metric not in raw_metrics:
                    continue

                actual_value = float(raw_metrics[metric])
                key = (agent_id, metric)

                # Find forecasts that should match this actual value
                if key not in self.forecasts:
                    continue

                forecasts_to_check = list(self.forecasts[key])
                for forecast in forecasts_to_check:
                    if forecast.actual_at is not None:
                        continue  # Already matched

                    expected_time = forecast.predicted_at + timedelta(
                        minutes=forecast.horizon_min
                    )
                    time_diff = abs((current_dt - expected_time).total_seconds())

                    # Allow 5 minute tolerance for matching (sample data can have timing variance)
                    if time_diff <= 300:
                        error_pct = abs(
                            (actual_value - forecast.predicted_value) / actual_value * 100
                        ) if actual_value != 0 else 0

                        forecast.actual_at = current_dt
                        forecast.actual_value = actual_value
                        forecast.error_pct = error_pct

                        # Schedule InfluxDB update without blocking
                        log.info(f"📊 Matched {metric}/{forecast.horizon_min}min: actual={actual_value:.1f}, forecast={forecast.predicted_value:.1f}, error={error_pct:.1f}%")
                        asyncio.create_task(
                            influx_writer.update_forecast_actual(
                                agent_id=agent_id,
                                metric=metric,
                                horizon_min=forecast.horizon_min,
                                actual_value=actual_value,
                                forecast_value=forecast.predicted_value,
                                error_pct=error_pct,
                                bucket=influx_writer.INFLUX_BUCKET,
                                timestamp=timestamp,
                            )
                        )

                        log.info(
                            f"Matched forecast: {agent_id}/{metric} "
                            f"{forecast.horizon_min}min "
                            f"pred={forecast.predicted_value:.2f} "
                            f"actual={actual_value:.2f} "
                            f"error={error_pct:.2f}%"
                        )

            # Clean up old forecasts
            self._cleanup_old(current_dt)

        except Exception as e:
            log.warning(f"Failed to compare actual: {e}")

    async def compare_actual_async(
        self,
        agent_id: str,
        timestamp: str,  # Current time
        raw_metrics: Dict,
    ):
        """
        Compare actual values with stored forecasts (async version for live mode).
        Matches forecasts by horizon (predicted_at + horizon_min ~= current_time).

        Args:
            agent_id: Agent identifier
            timestamp: Current ISO timestamp
            raw_metrics: {"CPU": float, "Memory": float, ...}
        """
        try:
            current_dt = self._parse_timestamp(timestamp)

            for metric in ["CPU", "Memory", "DiskIO", "NetworkSent", "NetworkRecv"]:
                if metric not in raw_metrics:
                    continue

                actual_value = float(raw_metrics[metric])
                key = (agent_id, metric)

                # Find forecasts that should match this actual value
                if key not in self.forecasts:
                    continue

                forecasts_to_check = list(self.forecasts[key])
                log.debug(f"🔍 Checking {len(forecasts_to_check)} forecasts for {key} at {current_dt}")

                for forecast in forecasts_to_check:
                    if forecast.actual_at is not None:
                        continue  # Already matched

                    expected_time = forecast.predicted_at + timedelta(
                        minutes=forecast.horizon_min
                    )
                    time_diff = abs((current_dt - expected_time).total_seconds())

                    log.debug(f"  {metric}/{forecast.horizon_min}min: expected={expected_time}, diff={time_diff:.1f}s")

                    # Allow 5 minute tolerance for matching (sample data can have timing variance)
                    if time_diff <= 300:
                        error_pct = abs(
                            (actual_value - forecast.predicted_value) / actual_value * 100
                        ) if actual_value != 0 else 0

                        forecast.actual_at = current_dt
                        forecast.actual_value = actual_value
                        forecast.error_pct = error_pct

                        # Await InfluxDB update
                        log.info(f"📊 Matched {metric}/{forecast.horizon_min}min: actual={actual_value:.1f}, forecast={forecast.predicted_value:.1f}, error={error_pct:.1f}%")
                        await influx_writer.update_forecast_actual(
                            agent_id=agent_id,
                            metric=metric,
                            horizon_min=forecast.horizon_min,
                            actual_value=actual_value,
                            forecast_value=forecast.predicted_value,
                            error_pct=error_pct,
                            bucket=influx_writer.INFLUX_BUCKET,
                            timestamp=timestamp,
                        )

                        log.info(
                            f"Matched forecast: {agent_id}/{metric} "
                            f"{forecast.horizon_min}min "
                            f"pred={forecast.predicted_value:.2f} "
                            f"actual={actual_value:.2f} "
                            f"error={error_pct:.2f}%"
                        )

            # Clean up old forecasts
            self._cleanup_old(current_dt)

        except Exception as e:
            log.warning(f"Failed to compare actual async: {e}")

    def _cleanup_old(self, current_dt: datetime):
        """Remove forecasts older than retention period."""
        cutoff = current_dt - timedelta(hours=self.retention_hours)

        for key in list(self.forecasts.keys()):
            forecasts = self.forecasts[key]
            while forecasts and forecasts[0].predicted_at < cutoff:
                forecasts.popleft()

    def _parse_timestamp(self, timestamp: str) -> datetime:
        """Parse ISO 8601 timestamp."""
        # Remove trailing Z if present
        ts = timestamp.rstrip('Z') if timestamp else ""

        # Try common formats
        for fmt in [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ]:
            try:
                return datetime.strptime(ts, fmt)
            except ValueError:
                continue

        # Fallback to current time
        log.warning(f"Could not parse timestamp: {timestamp}")
        return datetime.now()

    def get_summary(self, agent_id: str, metric: str) -> Dict:
        """
        Get accuracy summary for a metric.

        Returns:
            Dict with recent forecasts and accuracy stats
        """
        key = (agent_id, metric)
        forecasts = list(self.forecasts.get(key, []))

        matched = [f for f in forecasts if f.actual_value is not None]
        if not matched:
            return {"count": 0, "mean_error_pct": None, "records": []}

        import numpy as np
        errors = [f.error_pct for f in matched]

        return {
            "count": len(matched),
            "mean_error_pct": float(np.mean(errors)),
            "std_error": float(np.std(errors)),
            "min_error_pct": float(np.min(errors)),
            "max_error_pct": float(np.max(errors)),
            "records": [
                {
                    "predicted_at": f.predicted_at.isoformat(),
                    "horizon_min": f.horizon_min,
                    "predicted_value": f.predicted_value,
                    "actual_value": f.actual_value,
                    "error_pct": f.error_pct,
                }
                for f in matched[-20:]  # Last 20 matches
            ],
        }


# Global tracker instance
tracker = PredictTracker()
