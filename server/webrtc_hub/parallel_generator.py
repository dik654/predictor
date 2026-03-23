"""
Parallel Historical Generator — ARIMA를 병렬 처리하여 빠르게 생성.
ECOD는 순차(이전 데이터 의존), ARIMA 5개 메트릭은 ThreadPoolExecutor로 병렬.
"""

import asyncio
import csv
import json
import logging
import time as _time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import click
import numpy as np
import pandas as pd
from pyod.models.ecod import ECOD
from statsforecast import StatsForecast
from statsforecast.models import AutoARIMA

from .detector import (
    EnhancedAnomalyDetector, AnomalyResult, DetectionResult,
    WINDOW_SIZE, MIN_SAMPLES_ECOD, MIN_SAMPLES_ARIMA,
    ARIMA_RESIDUAL_K, ARIMA_SEASON_LENGTH,
    ECOD_WEIGHT, ARIMA_WEIGHT,
)
from . import influx_writer
from .influx_writer import init_influx, close_influx, write_metrics, write_forecast, write_forecast_evaluation, write_peripheral_status, DEVICE_NAME_MAP, get_last_metric_time
from .predict_tracker import PredictTracker
from .forecast_evaluator import ForecastEvaluator

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger("parallel_gen")

# Suppress noisy warnings
import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning, module="pyod")
warnings.filterwarnings("ignore", category=UserWarning, module="statsforecast")


# ── Time-of-day profiles ─────────────────────────────────────────────────────
_HOUR_PROFILE = {
    0: (0.20, 0.05), 1: (0.15, 0.03), 2: (0.15, 0.02), 3: (0.15, 0.02),
    4: (0.18, 0.03), 5: (0.25, 0.05), 6: (0.40, 0.15), 7: (0.55, 0.25),
    8: (0.70, 0.40), 9: (0.85, 0.60), 10: (0.95, 0.80), 11: (1.00, 0.90),
    12: (1.10, 1.00), 13: (1.05, 0.95), 14: (0.95, 0.85), 15: (0.90, 0.80),
    16: (0.90, 0.80), 17: (0.95, 0.85), 18: (1.05, 0.95), 19: (1.00, 0.90),
    20: (0.90, 0.80), 21: (0.75, 0.60), 22: (0.50, 0.30), 23: (0.30, 0.10),
}


def load_base_data(file_path: Path, interval_min: int) -> List[dict]:
    """Load from CSV or JSON, aggregate into windows."""
    if file_path.suffix == '.csv':
        return _load_csv(file_path, interval_min)
    return _load_json(file_path, interval_min)


def _load_csv(file_path: Path, interval_min: int) -> List[dict]:
    records = []
    with open(file_path) as f:
        for row in csv.DictReader(f):
            try:
                records.append({
                    "ts": datetime.fromisoformat(row["_time"].rstrip("Z")),
                    "CPU": float(row.get("cpu", 0) or 0),
                    "Memory": float(row.get("memory", 0) or 0),
                    "DiskIO": float(row.get("disk_io", 0) or 0),
                    "Network": {
                        "Sent": int(float(row.get("network_sent_bytes", 0) or 0)),
                        "Recv": int(float(row.get("network_received_bytes", 0) or 0)),
                    },
                })
            except Exception:
                continue
    return _aggregate(records, interval_min)


def _load_json(file_path: Path, interval_min: int) -> List[dict]:
    records = []
    with open(file_path) as f:
        for line in f:
            try:
                r = json.loads(line.strip())
                if "CPU" not in r or r.get("CPU") is None:
                    continue
                ts_str = r["Timestamp"]
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S") if " " in ts_str else datetime.fromisoformat(ts_str.rstrip("Z"))
                records.append({
                    "ts": ts,
                    "CPU": float(r.get("CPU", 0)),
                    "Memory": float(r.get("Memory", 0)),
                    "DiskIO": float(r.get("DiskIO", 0)),
                    "Network": {
                        "Sent": int(r.get("Network", {}).get("Sent", 0)),
                        "Recv": int(r.get("Network", {}).get("Recv", 0)),
                    },
                })
            except Exception:
                continue
    return _aggregate(records, interval_min)


def _aggregate(records: list, interval_min: int) -> List[dict]:
    windows: Dict[datetime, list] = {}
    for r in records:
        ts = r["ts"]
        key = ts.replace(second=0, microsecond=0, minute=(ts.minute // interval_min) * interval_min)
        windows.setdefault(key, []).append(r)

    result = []
    for key in sorted(windows):
        recs = windows[key]
        result.append({
            "CPU": float(np.mean([r["CPU"] for r in recs])),
            "Memory": float(np.mean([r["Memory"] for r in recs])),
            "DiskIO": float(np.mean([r["DiskIO"] for r in recs])),
            "Network": {
                "Sent": int(np.mean([r["Network"]["Sent"] for r in recs])),
                "Recv": int(np.mean([r["Network"]["Recv"] for r in recs])),
            },
        })
    log.info(f"Loaded {len(records)} records -> {len(result)} windows ({interval_min}min)")
    return result


def synthesize(base: List[dict], slots: List[datetime], agent_id: str,
               store_info: dict, seed: int = 42) -> List[dict]:
    """Generate synthetic data with time variation + noise + events."""
    rng = np.random.default_rng(seed)
    BLEND = min(6, len(slots))
    last = base[-1]
    points = []

    for i, ts in enumerate(slots):
        b = base[i % len(base)]
        cm, nm = _HOUR_PROFILE.get(ts.hour, (0.8, 0.5))

        cpu = float(np.clip(b["CPU"] * cm + rng.normal(0, b["CPU"] * 0.08), 1, 100))
        mem = float(np.clip(b["Memory"] + rng.normal(0, 1.5), 30, 98))
        disk = float(np.clip(b["DiskIO"] * cm + rng.normal(0, max(b["DiskIO"] * 0.1, 0.01)), 0, 100))
        sent = int(max(0, b["Network"]["Sent"] * nm + rng.normal(0, max(b["Network"]["Sent"] * 0.15, 10))))
        recv = int(max(0, b["Network"]["Recv"] * nm + rng.normal(0, max(b["Network"]["Recv"] * 0.15, 10))))

        if i < BLEND:
            a = i / BLEND
            cpu = last["CPU"] * (1 - a) + cpu * a
            mem = last["Memory"] * (1 - a) + mem * a
            disk = last["DiskIO"] * (1 - a) + disk * a

        points.append({
            "AgentId": agent_id,
            "Timestamp": ts.isoformat() + "Z",
            "CPU": round(cpu, 1), "Memory": round(mem, 1), "DiskIO": round(disk, 2),
            "Network": {"Sent": sent, "Recv": recv},
            "Process": {"GSRTL.CVS.POS.Shell": 1},
            "Peripherals": {
                "dongle": 1, "hand_scanner": 1, "keyboard": 1, "msr": 1,
                "2d_scanner": 0, "phone_charger": 0, "passport_reader": -1,
            },
            "StoreInfo": store_info,
            "_nanos_offset": 0,
        })

    # Inject events
    n = len(points)
    if n > 50:
        for _ in range(max(1, n // 36)):
            s = int(rng.integers(10, max(11, n - 40)))
            etype = rng.choice(["cpu_spike", "mem_rise", "net_surge", "idle"])
            if etype == "cpu_spike":
                for j in range(int(rng.integers(2, 6))):
                    if s + j >= n: break
                    points[s + j]["CPU"] = round(float(np.clip(
                        points[s + j]["CPU"] + rng.uniform(40, 60) * np.sin(j / 4 * np.pi), 0, 100)), 1)
            elif etype == "mem_rise":
                target = rng.uniform(85, 96)
                dur = int(rng.integers(15, 30))
                for j in range(dur):
                    if s + j >= n: break
                    p = j / max(dur - 1, 1)
                    points[s + j]["Memory"] = round(float(np.clip(
                        points[s + j]["Memory"] + (target - points[s + j]["Memory"]) * p, 30, 98)), 1)
            elif etype == "net_surge":
                m = rng.uniform(5, 15)
                for j in range(int(rng.integers(3, 8))):
                    if s + j >= n: break
                    points[s + j]["Network"]["Sent"] = int(points[s + j]["Network"]["Sent"] * m)
                    points[s + j]["Network"]["Recv"] = int(points[s + j]["Network"]["Recv"] * m)
            elif etype == "idle":
                for j in range(int(rng.integers(6, 12))):
                    if s + j >= n: break
                    points[s + j]["CPU"] = round(float(rng.uniform(1.0, 3.0)), 1)

    log.info(f"Synthesized {len(points)} data points")
    return points


# ── Parallel ARIMA ────────────────────────────────────────────────────────────

def _run_single_arima(
    metric_name: str, values: np.ndarray, freq: str, season_length: int,
    residual_history: list, residual_k: float,
) -> Optional[AnomalyResult]:
    """Run ARIMA for one metric (called in thread pool)."""
    if len(values) < MIN_SAMPLES_ARIMA:
        return None
    try:
        df = pd.DataFrame({
            "unique_id": "agent",
            "ds": pd.date_range(end=pd.Timestamp.now(), periods=len(values), freq=freq),
            "y": values,
        })
        sf = StatsForecast(
            models=[AutoARIMA(season_length=season_length)],
            freq=freq,
        )
        sf.fit(df)
        forecast_1 = sf.predict(h=1)
        forecast_value = float(forecast_1["AutoARIMA"].values[0])
        actual_value = float(values[-1])
        residual = abs(actual_value - forecast_value)

        residual_history.append(residual)
        rh = np.array(residual_history[-WINDOW_SIZE:])
        if len(rh) > 5:
            threshold = float(residual_k * np.std(rh))
            threshold = max(threshold, 0.1)
        else:
            threshold = float(np.mean(rh) * 2) if len(rh) > 0 else 1.0

        score = residual / max(threshold, 0.01)

        if residual > threshold * 1.5:
            severity, confidence = "critical", min(0.95, score / 2)
        elif residual > threshold:
            severity, confidence = "warning", min(0.8, score / 2)
        else:
            severity, confidence = "normal", 1.0 - min(0.9, score)

        # Multi-step forecast (simplified: just 1-step for speed)
        forecast_horizon = []
        for h_min in [30, 60, 360]:
            steps = max(1, h_min * 60 // 600)  # 10min interval
            try:
                fdf = sf.predict(h=steps)
                pred = float(np.clip(fdf["AutoARIMA"].values[-1], 0, 100))
            except Exception:
                pred = forecast_value
            fh_severity = "normal"
            _thresholds = {"CPU": (80, 90), "Memory": (85, 95), "DiskIO": (70, 85)}
            w, c = _thresholds.get(metric_name, (70, 85))
            if pred >= c: fh_severity = "critical"
            elif pred >= w: fh_severity = "warning"
            forecast_horizon.append({"minutes": h_min, "value": pred, "severity": fh_severity})

        return AnomalyResult(
            engine="arima", metric=metric_name,
            value=actual_value, score=float(score), threshold=threshold,
            forecast=forecast_value, residual=float(residual),
            severity=severity, confidence=confidence,
            details=f"예측={forecast_value:.1f}, 실제={actual_value:.1f}, 차이={residual:.1f}",
            forecast_horizon=forecast_horizon,
        )
    except Exception as e:
        return None


class ParallelDetector:
    """ECOD sequential + ARIMA parallel detector."""

    def __init__(self, interval_min: int = 10, n_workers: int = 5):
        self.interval_min = interval_min
        self.freq = f"{interval_min}min"
        self.season_length = max(1, 60 // interval_min)
        self.n_workers = n_workers
        self.executor = ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="arima")

        # Buffers per metric
        self.buffers: Dict[str, deque] = {
            m: deque(maxlen=WINDOW_SIZE)
            for m in ["CPU", "Memory", "DiskIO", "NetworkSent", "NetworkRecv"]
        }
        self.residuals: Dict[str, list] = {m: [] for m in self.buffers}

        # ECOD
        self.ecod_X: List[list] = []
        self.ecod_model: Optional[ECOD] = None

    def detect(self, data_point: dict, run_ecod: bool, run_arima: bool) -> DetectionResult:
        """Run detection with parallel ARIMA."""
        agent_id = data_point.get("AgentId", "unknown")
        timestamp = data_point.get("Timestamp", "")

        cpu = data_point.get("CPU", 0)
        mem = data_point.get("Memory", 0)
        disk = data_point.get("DiskIO", 0)
        net_sent = data_point.get("Network", {}).get("Sent", 0)
        net_recv = data_point.get("Network", {}).get("Recv", 0)

        self.buffers["CPU"].append(cpu)
        self.buffers["Memory"].append(mem)
        self.buffers["DiskIO"].append(disk)
        self.buffers["NetworkSent"].append(net_sent)
        self.buffers["NetworkRecv"].append(net_recv)

        raw_metrics = {"CPU": cpu, "Memory": mem, "DiskIO": disk,
                       "NetworkSent": net_sent, "NetworkRecv": net_recv}

        detections: List[AnomalyResult] = []

        # 1. ECOD (sequential — needs accumulated data)
        if run_ecod:
            self.ecod_X.append([cpu, mem, disk, net_sent, net_recv])
            if len(self.ecod_X) >= MIN_SAMPLES_ECOD:
                try:
                    X = np.array(self.ecod_X)
                    model = ECOD(contamination=0.05)
                    model.fit(X)
                    self.ecod_model = model

                    latest = X[-1].reshape(1, -1)
                    score = model.decision_function(latest)[0]
                    all_scores = model.decision_function(X)
                    score_norm = (score - all_scores.min()) / (all_scores.max() - all_scores.min() + 1e-10)
                    score_norm = max(0.0, min(1.0, score_norm))
                    is_outlier = model.predict(latest)[0] == 1

                    if is_outlier and score_norm > 0.9:
                        sev, conf = "critical", 0.9
                    elif is_outlier:
                        sev, conf = "warning", 0.7
                    else:
                        sev, conf = "normal", 1.0 - score_norm

                    detections.append(AnomalyResult(
                        engine="ecod", metric="Multivariate",
                        value=float(score), score=float(score_norm),
                        threshold=0.05, severity=sev, confidence=conf,
                        details=f"CPU={cpu:.1f}%, Mem={mem:.1f}%, Disk={disk:.2f}",
                    ))

                    # Per-metric scores
                    n_samples = len(X)
                    data_conf = 0.4 if n_samples < 20 else (0.7 if n_samples < 60 else 0.9)
                    _thresholds = {"CPU": (80, 90), "Memory": (85, 95), "DiskIO": (70, 85),
                                   "NetworkSent": (50000, 100000), "NetworkRecv": (50000, 100000)}
                    for col_i, (name, value) in enumerate(zip(
                        ["CPU", "Memory", "DiskIO", "NetworkSent", "NetworkRecv"],
                        [cpu, mem, disk, net_sent, net_recv]
                    )):
                        col_data = X[:, col_i]
                        percentile = np.sum(col_data < value) / len(col_data)
                        p95 = float(np.percentile(col_data, 95))
                        w_th, c_th = _thresholds.get(name, (70, 85))
                        if value >= c_th:
                            m_sev = "critical"
                        elif value >= w_th or percentile >= 0.95:
                            m_sev = "warning"
                        else:
                            m_sev = "normal"
                        unit = '%' if name in ('CPU', 'Memory') else ''
                        val_s = f"{value:.1f}" if name not in ('NetworkSent', 'NetworkRecv') else f"{value:.0f}"
                        pct_rank = percentile * 100
                        det = f"{name} {val_s}{unit} (상위 {100-pct_rank:.0f}%)"
                        if m_sev != "normal":
                            det = f"{name} {val_s}{unit} — 상위 {100-pct_rank:.0f}% (p95={p95:.1f})"
                        detections.append(AnomalyResult(
                            engine="ecod", metric=name, value=float(value),
                            score=float(percentile), threshold=p95,
                            severity=m_sev, confidence=data_conf, details=det,
                        ))
                except Exception as e:
                    log.debug(f"ECOD failed: {e}")

        # 2. ARIMA (parallel — 5 metrics simultaneously)
        if run_arima and len(self.buffers["CPU"]) >= MIN_SAMPLES_ARIMA:
            futures = {}
            for metric_name in ["CPU", "Memory", "DiskIO", "NetworkSent", "NetworkRecv"]:
                values = np.array(self.buffers[metric_name])
                fut = self.executor.submit(
                    _run_single_arima,
                    metric_name, values, self.freq, self.season_length,
                    self.residuals[metric_name], ARIMA_RESIDUAL_K,
                )
                futures[fut] = metric_name

            for fut in as_completed(futures):
                result = fut.result()
                if result:
                    detections.append(result)

        # 3. Ensemble
        ecod_scores = [d.score * d.confidence for d in detections if d.engine == "ecod"]
        arima_scores = [d.score * d.confidence for d in detections if d.engine == "arima"]
        if ecod_scores and arima_scores:
            ens = ECOD_WEIGHT * np.mean(ecod_scores) + ARIMA_WEIGHT * np.mean(arima_scores)
            ens_sev = "critical" if ens > 0.8 else ("warning" if ens > 0.5 else "normal")
            detections.append(AnomalyResult(
                engine="ensemble", metric="Combined",
                value=float(ens), score=float(ens), threshold=0.5,
                severity=ens_sev, confidence=0.9 if ens > 0.7 else 0.7,
                details=f"ECOD×0.6 + ARIMA×0.4",
            ))

        # Health score
        health = 100
        for d in detections:
            if d.severity == "critical": health -= int(20 * d.confidence)
            elif d.severity == "warning": health -= int(10 * d.confidence)
        health = max(0, min(100, health))

        return DetectionResult(
            agent_id=agent_id, timestamp=timestamp,
            detections=detections, health_score=health,
            raw_metrics=raw_metrics, ensemble_score=0,
        )

    def shutdown(self):
        self.executor.shutdown(wait=False)


async def _write_to_bucket(dp, result, bucket, aid, ts_val, tracker, fe, forecasts_by_horizon, total_forecasts, total_evals):
    """Write metrics + forecasts + evaluations to a single bucket."""
    try:
        raw = {"CPU": dp["CPU"], "Memory": dp["Memory"], "DiskIO": dp["DiskIO"], "_nanos_offset": 0}
        await write_metrics(agent_id=dp["AgentId"], timestamp=dp["Timestamp"],
                            raw_metrics=raw, bucket=bucket, full_data=dp)
    except Exception as e:
        log.warning(f"Metrics write error ({bucket}): {e}")

    # Write peripheral status
    if dp.get("Peripherals"):
        try:
            # Convert eng keys to Korean for write_peripheral_status
            _reverse_map = {v: k for k, v in DEVICE_NAME_MAP.items()}
            periph_kr = {_reverse_map.get(k, k): ("연결" if v == 1 else ("실패" if v == 0 else "미사용"))
                         for k, v in dp["Peripherals"].items()}
            await write_peripheral_status(
                agent_id=dp["AgentId"], timestamp=dp["Timestamp"],
                peripherals=periph_kr, bucket=bucket,
                store_info=dp.get("StoreInfo", {}),
            )
        except Exception as e:
            log.warning(f"Peripheral write error ({bucket}): {e}")

    if result:
        for det in result.detections:
            if det.engine == "arima" and det.forecast_horizon:
                for fh in det.forecast_horizon:
                    try:
                        await write_forecast(agent_id=aid, timestamp=ts_val,
                                             metric=det.metric, horizon_min=fh["minutes"],
                                             predicted_value=fh["value"], bucket=bucket)
                        total_forecasts[0] += 1
                    except Exception:
                        pass

        if forecasts_by_horizon:
            eval_result = fe.evaluate(aid, ts_val, forecasts_by_horizon)
            eval_dict = fe.to_dict(eval_result)
            try:
                await write_forecast_evaluation(
                    agent_id=aid, timestamp=ts_val,
                    horizons=eval_dict.get("horizons", []),
                    overall_severity=eval_dict.get("overall_severity", "normal"),
                    model_ready=eval_dict.get("model_ready", False),
                    data_source=eval_dict.get("data_source", "none"),
                    bucket=bucket,
                )
                total_evals[0] += 1
            except Exception:
                pass


async def main(
    file_path: str, interval_min: int, hours: int,
    agent_id: str, bucket: str, start_after: str,
    store_code: str, store_name: str, pos_no: str,
    region_code: str, region_name: str,
    seed: int, workers: int, also_bucket: str = "",
):
    store_info = {
        "StoreCode": store_code, "StoreName": store_name,
        "PosNo": pos_no, "RegionCode": region_code, "RegionName": region_name,
    }

    # Parse start_after
    start_after_dt = None
    if start_after:
        try:
            parsed = datetime.fromisoformat(start_after.rstrip("Z"))
            start_after_dt = parsed.replace(tzinfo=None)  # normalize to naive UTC
        except ValueError:
            log.error(f"Invalid --start-after: {start_after}")
            return

    # Load base data
    base = load_base_data(Path(file_path), interval_min)
    if not base:
        log.error("No base data loaded!")
        return

    # Build time slots (UTC)
    now = datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0)
    now = now - timedelta(minutes=now.minute % interval_min)

    if start_after_dt:
        sa = start_after_dt.replace(second=0, microsecond=0)
        start = sa - timedelta(minutes=sa.minute % interval_min) + timedelta(minutes=interval_min)
    else:
        start = now - timedelta(hours=hours)

    slots = []
    ts = start
    while ts <= now:
        slots.append(ts)
        ts += timedelta(minutes=interval_min)

    if not slots:
        log.error(f"No slots! start={start} (UTC), now={now} (UTC)")
        return

    total = len(slots)
    log.info(f"Parallel Generator: {total} slots, {workers} ARIMA workers")
    log.info(f"  Range: {slots[0]} → {slots[-1]} (UTC)")
    log.info(f"  Base patterns: {len(base)}")

    # Synthesize
    data_points = synthesize(base, slots, agent_id, store_info, seed)

    # Connect to InfluxDB
    buckets = [bucket]
    if also_bucket:
        buckets.append(also_bucket)
        log.info(f"Writing to both buckets: {buckets}")
    influx_writer.INFLUX_BUCKET = bucket
    init_influx()

    # Detector
    detector = ParallelDetector(interval_min=interval_min, n_workers=workers)
    tracker = PredictTracker(retention_hours=24)
    fe = ForecastEvaluator()
    total_forecasts = [0]  # mutable for helper
    total_evals = [0]
    warmup = min(30, max(5, total // 3))

    log.info(f"Single-pass: detect (parallel ARIMA) + write ({total} slots, warmup={warmup})...")
    t0 = _time.time()

    for i, dp in enumerate(data_points):
        # Detection
        result = None
        try:
            run_ecod = (i >= warmup)
            run_arima = (i >= warmup)
            result = detector.detect(dp, run_ecod=run_ecod, run_arima=run_arima)
        except Exception as e:
            log.error(f"Detection error slot {i}: {e}")

        # Build forecast horizon map
        aid = dp["AgentId"]
        ts_val = dp["Timestamp"]
        forecasts_by_horizon: dict = {}
        if result and i >= warmup:
            for det in result.detections:
                if det.engine == "arima" and det.forecast_horizon:
                    for fh in det.forecast_horizon:
                        tracker.record(aid, det.metric, ts_val, fh["minutes"], fh["value"])
                        h_min = fh["minutes"]
                        if h_min not in forecasts_by_horizon:
                            forecasts_by_horizon[h_min] = {}
                        mk = det.metric.lower()
                        if mk == "diskio": mk = "disk_io"
                        forecasts_by_horizon[h_min][mk] = fh["value"]

            fe.update_event(aid, dp)
            fe.update_fallback_buffer(aid, dp)

        # Write to all buckets
        for b in buckets:
            await _write_to_bucket(
                dp, result if i >= warmup else None,
                b, aid, ts_val, tracker, fe, forecasts_by_horizon,
                total_forecasts, total_evals,
            )

        if result and i >= warmup:
            asyncio.create_task(tracker.compare_actual_async(
                agent_id=aid, timestamp=ts_val, raw_metrics=result.raw_metrics))

        if (i + 1) % max(1, total // 20) == 0:
            elapsed = _time.time() - t0
            pct = (i + 1) * 100 // total
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            eta = (total - i - 1) / rate if rate > 0 else 0
            log.info(f"  {i+1}/{total} ({pct}%) — {rate:.1f} slots/s, ETA {eta:.0f}s — forecasts: {total_forecasts[0]}, evals: {total_evals[0]}")
            await asyncio.sleep(0)

    # Flush
    log.info("Flushing pending writes...")
    await asyncio.sleep(2)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    elapsed = _time.time() - t0
    log.info(f"Done! {total} slots in {elapsed:.0f}s ({total/elapsed:.1f} slots/s)")
    log.info(f"  Forecasts: {total_forecasts[0]}, Evaluations: {total_evals[0]}")
    log.info(f"  {data_points[0]['Timestamp']} → {data_points[-1]['Timestamp']}")

    detector.shutdown()
    close_influx()


@click.command()
@click.option("--file", "file_path", required=True, help="CSV or JSON base data file")
@click.option("--interval-min", default=10, type=int)
@click.option("--hours", default=168, type=int, help="Hours of data (default 168=7days, ignored if --start-after)")
@click.option("--agent-id", default="V135-POS-03")
@click.option("--bucket", default="pos_metrics")
@click.option("--start-after", default="", help="UTC datetime, e.g. 2026-03-16T00:00:00")
@click.option("--store-code", default="V135")
@click.option("--store-name", default="GS25역삼홍인점")
@click.option("--pos-no", default="3")
@click.option("--region-code", default="16")
@click.option("--region-name", default="2부문")
@click.option("--seed", default=42, type=int)
@click.option("--workers", default=5, type=int, help="ARIMA parallel workers (default: 5)")
@click.option("--also-bucket", default="", help="Also write to this bucket (e.g. sample_metrics)")
@click.option("--resume", is_flag=True, default=False, help="Resume from last written timestamp in InfluxDB")
def cli(file_path, interval_min, hours, agent_id, bucket, start_after,
        store_code, store_name, pos_no, region_code, region_name, seed, workers, also_bucket, resume):
    """Parallel historical generator: ARIMA runs in thread pool for speed."""
    if resume:
        init_influx()
        last_ts = get_last_metric_time(agent_id, bucket)
        # close_influx()를 호출하지 않음 — main()에서 재사용
        if last_ts:
            log.info(f"Resume: last metric at {last_ts}, generating from there to now")
            start_after = last_ts
        else:
            log.warning("Resume: no existing data found, falling back to --hours")
    asyncio.run(main(
        file_path=file_path, interval_min=interval_min, hours=hours,
        agent_id=agent_id, bucket=bucket, start_after=start_after,
        store_code=store_code, store_name=store_name, pos_no=pos_no,
        region_code=region_code, region_name=region_name,
        seed=seed, workers=workers, also_bucket=also_bucket,
    ))


if __name__ == "__main__":
    cli()
