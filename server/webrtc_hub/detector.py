"""
PulseAI Lite - Enhanced Anomaly Detection Engine
ECOD (다변량 분석) + AutoARIMA (모델 캐싱) + 앙상블 탐지
"""

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

import numpy as np
import pandas as pd
from pyod.models.ecod import ECOD
from statsforecast import StatsForecast
from statsforecast.models import AutoARIMA

log = logging.getLogger("detector")

# Configuration
WINDOW_SIZE = 60  # 60 data points (~5 minutes at 5s intervals)
MIN_SAMPLES_ECOD = 20  # Minimum samples for ECOD
MIN_SAMPLES_ARIMA = 30  # Minimum samples for ARIMA

# Dynamic contamination based on data characteristics
BASE_CONTAMINATION = 0.05  # Start with 5%

# ARIMA settings
ARIMA_SEASON_LENGTH = 12
ARIMA_RESIDUAL_K = 2.5

# Ensemble weights
ECOD_WEIGHT = 0.6
ARIMA_WEIGHT = 0.4

# Peripheral device failure tracking
PERIPHERAL_FAILURE_THRESHOLD = 3  # Alert after N consecutive failures


@dataclass
class MetricBuffer:
    """Circular buffer for time series data."""
    cpu: deque = field(default_factory=lambda: deque(maxlen=WINDOW_SIZE))
    memory: deque = field(default_factory=lambda: deque(maxlen=WINDOW_SIZE))
    disk_io: deque = field(default_factory=lambda: deque(maxlen=WINDOW_SIZE))
    network_sent: deque = field(default_factory=lambda: deque(maxlen=WINDOW_SIZE))
    network_recv: deque = field(default_factory=lambda: deque(maxlen=WINDOW_SIZE))
    process_status: deque = field(default_factory=lambda: deque(maxlen=WINDOW_SIZE))
    # 주변장치 상태 (1=연결, 0=실패, -1=미사용/데이터없음)
    periph_dongle: deque = field(default_factory=lambda: deque(maxlen=WINDOW_SIZE))
    periph_hand_scanner: deque = field(default_factory=lambda: deque(maxlen=WINDOW_SIZE))
    periph_passport_reader: deque = field(default_factory=lambda: deque(maxlen=WINDOW_SIZE))
    periph_2d_scanner: deque = field(default_factory=lambda: deque(maxlen=WINDOW_SIZE))
    periph_phone_charger: deque = field(default_factory=lambda: deque(maxlen=WINDOW_SIZE))
    periph_keyboard: deque = field(default_factory=lambda: deque(maxlen=WINDOW_SIZE))
    periph_msr: deque = field(default_factory=lambda: deque(maxlen=WINDOW_SIZE))
    pos_idle: deque = field(default_factory=lambda: deque(maxlen=WINDOW_SIZE))  # 1=유휴, 0=비유휴
    timestamps: deque = field(default_factory=lambda: deque(maxlen=WINDOW_SIZE))


@dataclass
class PeripheralState:
    """Track peripheral device states."""
    failure_counts: Dict[str, int] = field(default_factory=dict)
    last_states: Dict[str, str] = field(default_factory=dict)


@dataclass
class AnomalyResult:
    """Single anomaly detection result."""
    engine: str  # "ecod", "arima", "ensemble", "peripheral"
    metric: str
    value: float
    score: float = 0.0
    threshold: float = 0.0
    forecast: Optional[float] = None
    residual: Optional[float] = None
    severity: str = "normal"
    confidence: float = 0.0  # 0-1, how confident in this detection
    details: Optional[str] = None
    # Multi-step forecast for ARIMA
    forecast_horizon: Optional[List[Dict[str, Any]]] = None  # [{minutes, value, severity}]


@dataclass
class DetectionResult:
    """Full detection result for a data point."""
    agent_id: str
    timestamp: str
    detections: List[AnomalyResult] = field(default_factory=list)
    health_score: int = 100
    raw_metrics: Dict[str, float] = field(default_factory=dict)
    ensemble_score: float = 0.0


class EnhancedAnomalyDetector:
    """
    PulseAI Lite Enhanced Anomaly Detector
    
    Improvements:
    - Multivariate ECOD (considers metric correlations)
    - ARIMA model caching (faster predictions)
    - Ensemble detection (combined confidence)
    - Peripheral device monitoring
    - Dynamic threshold adjustment
    """
    
    def __init__(self):
        self.buffers: Dict[str, MetricBuffer] = {}
        self.peripheral_states: Dict[str, PeripheralState] = {}
        
        # Model caching
        self.ecod_models: Dict[str, ECOD] = {}  # agent_id -> model
        self.arima_models: Dict[str, Dict[str, StatsForecast]] = {}  # agent_id -> {metric -> model}
        self.arima_residuals: Dict[str, Dict[str, deque]] = {}
        
        # Adaptive thresholds
        self.score_history: Dict[str, deque] = {}  # For dynamic threshold
        
    def _ensure_buffer(self, agent_id: str) -> MetricBuffer:
        """Ensure buffer exists for agent."""
        if agent_id not in self.buffers:
            self.buffers[agent_id] = MetricBuffer()
            self.peripheral_states[agent_id] = PeripheralState()
            self.arima_models[agent_id] = {}
            self.arima_residuals[agent_id] = {}
            self.score_history[agent_id] = deque(maxlen=100)
        return self.buffers[agent_id]
    
    def _update_buffer(self, agent_id: str, data: dict) -> None:
        """Update metric buffer with new data."""
        buf = self._ensure_buffer(agent_id)
        
        buf.cpu.append(data.get("CPU", 0))
        buf.memory.append(data.get("Memory", 0))
        buf.disk_io.append(data.get("DiskIO", 0))

        network = data.get("Network", {})
        buf.network_sent.append(network.get("Sent", 0))
        buf.network_recv.append(network.get("Recv", 0))

        # Process status: 하나라도 RUNNING이면 1, 아니면 0
        process = data.get("Process", {})
        proc_val = 1 if any(v == "RUNNING" for v in process.values()) else 0
        buf.process_status.append(proc_val)

        # 주변장치 상태 (Peripherals dict from server.py buffer)
        periph = data.get("Peripherals", {})
        buf.periph_dongle.append(periph.get("dongle", -1))
        buf.periph_hand_scanner.append(periph.get("hand_scanner", -1))
        buf.periph_passport_reader.append(periph.get("passport_reader", -1))
        buf.periph_2d_scanner.append(periph.get("2d_scanner", -1))
        buf.periph_phone_charger.append(periph.get("phone_charger", -1))
        buf.periph_keyboard.append(periph.get("keyboard", -1))
        buf.periph_msr.append(periph.get("msr", -1))

        # POS 유휴 상태 판단: Process=RUNNING + 주변장치 데이터 없음(-1) = 비유휴(0)
        has_periph_data = any(periph.get(k, -1) != -1 for k in ["dongle", "hand_scanner", "2d_scanner", "keyboard", "msr"])
        is_idle = 1 if has_periph_data else 0
        buf.pos_idle.append(is_idle)

        buf.timestamps.append(data.get("Timestamp", ""))
    
    def _get_dynamic_contamination(self, agent_id: str) -> float:
        """Calculate dynamic contamination based on recent score history."""
        if agent_id not in self.score_history or len(self.score_history[agent_id]) < 10:
            return BASE_CONTAMINATION
        
        scores = list(self.score_history[agent_id])
        # If many recent anomalies, increase sensitivity
        high_scores = sum(1 for s in scores if s > 0.7)
        ratio = high_scores / len(scores)
        
        # Adjust contamination: more anomalies -> lower contamination (more strict)
        if ratio > 0.3:
            return max(0.01, BASE_CONTAMINATION - 0.02)
        elif ratio < 0.05:
            return min(0.10, BASE_CONTAMINATION + 0.02)
        return BASE_CONTAMINATION
    
    def _run_multivariate_ecod(self, agent_id: str) -> List[AnomalyResult]:
        """Run multivariate ECOD on all metrics simultaneously."""
        buf = self.buffers.get(agent_id)
        if not buf or len(buf.cpu) < MIN_SAMPLES_ECOD:
            return []
        
        results = []
        
        try:
            # Combine metrics into multivariate array (13 features)
            cpu_arr = np.array(buf.cpu)
            mem_arr = np.array(buf.memory)
            disk_arr = np.array(buf.disk_io)
            net_sent_arr = np.array(buf.network_sent)
            net_recv_arr = np.array(buf.network_recv)
            proc_arr = np.array(buf.process_status)
            p_dongle = np.array(buf.periph_dongle)
            p_hand_scanner = np.array(buf.periph_hand_scanner)
            p_passport = np.array(buf.periph_passport_reader)
            p_2d_scanner = np.array(buf.periph_2d_scanner)
            p_phone_charger = np.array(buf.periph_phone_charger)
            p_keyboard = np.array(buf.periph_keyboard)
            p_msr = np.array(buf.periph_msr)
            idle_arr = np.array(buf.pos_idle)

            # Multivariate data matrix (14 features)
            X = np.column_stack([cpu_arr, mem_arr, disk_arr, net_sent_arr, net_recv_arr, proc_arr,
                                 p_dongle, p_hand_scanner, p_passport, p_2d_scanner, p_phone_charger, p_keyboard, p_msr,
                                 idle_arr])
            
            # Dynamic contamination
            contamination = self._get_dynamic_contamination(agent_id)
            
            # Train ECOD model
            model = ECOD(contamination=contamination)
            model.fit(X)
            
            # Cache model
            self.ecod_models[agent_id] = model
            
            # Get scores for latest point
            latest = X[-1].reshape(1, -1)
            score = model.decision_function(latest)[0]
            is_outlier = model.predict(latest)[0] == 1
            
            # Normalize score to 0-1 range
            all_scores = model.decision_function(X)
            score_normalized = (score - all_scores.min()) / (all_scores.max() - all_scores.min() + 1e-10)
            score_normalized = max(0.0, min(1.0, score_normalized))  # 0~1 클램핑
            
            # Track score history
            self.score_history[agent_id].append(score_normalized)
            
            # Determine severity based on normalized score
            if is_outlier:
                if score_normalized > 0.9:
                    severity = "critical"
                    confidence = 0.9
                elif score_normalized > 0.7:
                    severity = "warning"
                    confidence = 0.7
                else:
                    severity = "warning"
                    confidence = 0.5
            else:
                severity = "normal"
                confidence = 1.0 - score_normalized
            
            # Create result for multivariate analysis
            periph_ok = sum(1 for v in [p_dongle[-1], p_hand_scanner[-1], p_passport[-1], p_2d_scanner[-1], p_phone_charger[-1], p_keyboard[-1], p_msr[-1]] if v == 1)
            periph_total = 7
            idle_str = '유휴' if idle_arr[-1] else '비유휴'
            vals_summary = f"CPU={cpu_arr[-1]:.1f}%, Mem={mem_arr[-1]:.1f}%, Disk={disk_arr[-1]:.2f}, NetSent={net_sent_arr[-1]:.0f}, NetRecv={net_recv_arr[-1]:.0f}, Proc={'ON' if proc_arr[-1] else 'OFF'}, Periph={periph_ok}/{periph_total}, POS={idle_str}"
            if severity == "critical":
                multi_details = f"복합 지표 이상 심각 (score={score_normalized:.2f}). {vals_summary}"
            elif severity == "warning":
                multi_details = f"복합 지표 이상 감지 (score={score_normalized:.2f}). {vals_summary}"
            else:
                multi_details = vals_summary
            results.append(AnomalyResult(
                engine="ecod",
                metric="Multivariate",
                value=float(score),
                score=float(score_normalized),
                threshold=float(contamination),
                severity=severity,
                confidence=confidence,
                details=multi_details,
            ))
            
            # Per-metric breakdown: 전체 14차원 (차트용)
            # 이산값의 개별 점수는 StatusInsightCard에서 필터링
            metric_names = ["CPU", "Memory", "DiskIO", "NetworkSent", "NetworkRecv", "Process",
                            "Dongle", "HandScanner", "PassportReader", "2DScanner", "PhoneCharger", "Keyboard", "MSR", "POS_Idle"]
            metric_values = [cpu_arr[-1], mem_arr[-1], disk_arr[-1], net_sent_arr[-1], net_recv_arr[-1], proc_arr[-1],
                             p_dongle[-1], p_hand_scanner[-1], p_passport[-1], p_2d_scanner[-1], p_phone_charger[-1], p_keyboard[-1], p_msr[-1],
                             idle_arr[-1]]
            
            # Binary metrics: state-change based scoring
            _binary_metrics = {"Process", "Dongle", "HandScanner", "PassportReader",
                               "2DScanner", "PhoneCharger", "Keyboard", "MSR", "POS_Idle"}
            # Thresholds for continuous metrics
            _continuous_thresholds = {
                "CPU": (80.0, 90.0),       # warning, critical
                "Memory": (85.0, 95.0),
                "DiskIO": (70.0, 85.0),
                "NetworkSent": (50000.0, 100000.0),
                "NetworkRecv": (50000.0, 100000.0),
            }
            # Data sample count for confidence calculation
            n_samples = len(X)
            if n_samples < 20:
                data_confidence = 0.4
            elif n_samples < 60:
                data_confidence = 0.7
            else:
                data_confidence = 0.9

            for i, (name, value) in enumerate(zip(metric_names, metric_values)):
                metric_data = X[:, i]

                if name in _binary_metrics:
                    # Binary: score based on state, not percentile
                    # Normal state = 1 (running/connected), abnormal = 0
                    most_common = float(np.median(metric_data))
                    if name == "POS_Idle":
                        # POS_Idle: 0=busy(normal during business), 1=idle
                        metric_score = 0.0
                        metric_severity = "normal"
                        state_str = "유휴" if value == 1 else "사용중"
                        metric_details = f"{name}: {state_str}"
                    elif value == 0 and most_common == 1:
                        # Was running, now stopped → abnormal
                        metric_score = 1.0
                        metric_severity = "critical"
                        metric_details = f"{name} 비정상 (꺼짐)"
                    elif value == 1 and most_common == 0:
                        # Was stopped, now running → recovered
                        metric_score = 0.3
                        metric_severity = "normal"
                        metric_details = f"{name} 복구됨 (켜짐)"
                    else:
                        # Status unchanged
                        metric_score = 0.0
                        metric_severity = "normal"
                        state_str = "정상" if value == 1 else "꺼짐"
                        metric_details = f"{name}: {state_str}"

                    results.append(AnomalyResult(
                        engine="ecod",
                        metric=name,
                        value=float(value),
                        score=float(metric_score),
                        threshold=0.5,
                        severity=metric_severity,
                        confidence=data_confidence,
                        details=metric_details,
                    ))
                else:
                    # Continuous: percentile-based scoring + threshold check
                    percentile = np.sum(metric_data < value) / len(metric_data)
                    metric_score = percentile  # 0=lowest, 1=highest in history
                    p95 = float(np.percentile(metric_data, 95))
                    pct_rank = percentile * 100

                    # Severity from absolute thresholds
                    warn_th, crit_th = _continuous_thresholds.get(name, (70.0, 85.0))
                    if value >= crit_th:
                        metric_severity = "critical"
                    elif value >= warn_th or metric_score >= 0.95:
                        metric_severity = "warning"
                    else:
                        metric_severity = "normal"

                    unit = '%' if name in ('CPU', 'Memory') else ('bytes' if name.startswith('Network') else '')
                    val_str = f"{value:.0f}" if name in ('NetworkSent', 'NetworkRecv') else f"{value:.1f}"
                    if metric_severity in ("warning", "critical"):
                        metric_details = f"{name} {val_str}{unit} — 상위 {100 - pct_rank:.0f}% (p95={p95:.1f})"
                    else:
                        metric_details = f"{name} {val_str}{unit} (상위 {100 - pct_rank:.0f}%)"

                    results.append(AnomalyResult(
                        engine="ecod",
                        metric=name,
                        value=float(value),
                        score=float(metric_score),
                        threshold=p95,
                        severity=metric_severity,
                        confidence=data_confidence,
                        details=metric_details,
                    ))
            
        except Exception as e:
            log.warning(f"Multivariate ECOD failed: {e}")
        
        return results
    
    def _get_interval_seconds(self, agent_id: str) -> float:
        """Estimate actual data interval in seconds from buffer timestamps."""
        buf = self.buffers.get(agent_id)
        if not buf or len(buf.timestamps) < 2:
            return 5.0

        ts_list = []
        for ts in list(buf.timestamps)[-10:]:
            try:
                ts_list.append(pd.Timestamp(ts))
            except Exception:
                continue

        if len(ts_list) < 2:
            return 5.0

        intervals = []
        for i in range(len(ts_list) - 1):
            diff = (ts_list[i + 1] - ts_list[i]).total_seconds()
            if diff >= 1.0:  # sub-second = sample replay artifact, ignore
                intervals.append(diff)

        if not intervals:
            return 5.0

        return float(np.median(intervals))

    def _multi_resolution_forecast(
        self, agent_id: str, metric_name: str, arr: np.ndarray, interval_sec: float
    ) -> list:
        """
        Multi-resolution forecasting to avoid step explosion.

        Short-term (1hr, 6hr): use 5-min resolution → max 72 steps
        Long-term (12hr, 1day, 2day): use 1-hr resolution → max 48 steps

        Returns list of {minutes, value} dicts.
        """
        # Resolution configs: (target_resolution_sec, horizons_minutes)
        RESOLUTIONS = [
            (300, [60, 360]),        # 5min resolution → 1hr(12 steps), 6hr(72 steps)
            (3600, [720, 1440, 2880]),  # 1hr resolution → 12hr(12), 1day(24), 2day(48)
        ]

        results = []

        for res_sec, horizons in RESOLUTIONS:
            # Resample data if needed
            if interval_sec < res_sec:
                # Downsample: group every N points and take mean
                factor = max(1, int(res_sec / interval_sec))
                n_full = (len(arr) // factor) * factor
                if n_full < MIN_SAMPLES_ARIMA * factor:
                    # Not enough data for this resolution, use raw with capped steps
                    resampled = arr
                    actual_res = interval_sec
                else:
                    resampled = arr[-n_full:].reshape(-1, factor).mean(axis=1)
                    actual_res = res_sec
            else:
                # Data is already coarser than target resolution
                resampled = arr
                actual_res = interval_sec

            if len(resampled) < MIN_SAMPLES_ARIMA:
                # Fallback: use last known value
                for m in horizons:
                    results.append({"minutes": m, "value": float(arr[-1])})
                continue

            try:
                res_freq = f"{int(actual_res)}s"
                res_df = pd.DataFrame({
                    "unique_id": agent_id,
                    "ds": pd.date_range(
                        end=pd.Timestamp.now(), periods=len(resampled), freq=res_freq
                    ),
                    "y": resampled,
                })

                # Cache key includes resolution
                cache_key = f"{metric_name}__{int(actual_res)}s"
                cached_freq = self.arima_models[agent_id].get(f"{cache_key}__freq")
                need_retrain = (
                    cache_key not in self.arima_models[agent_id]
                    or cached_freq != res_freq
                    or len(resampled) % 100 == 0
                )

                if need_retrain:
                    res_sf = StatsForecast(
                        models=[AutoARIMA(season_length=min(ARIMA_SEASON_LENGTH, len(resampled) // 3))],
                        freq=res_freq,
                    )
                    res_sf.fit(res_df)
                    self.arima_models[agent_id][cache_key] = res_sf
                    self.arima_models[agent_id][f"{cache_key}__freq"] = res_freq
                    log.info(f"Multi-res ARIMA trained: {agent_id}/{metric_name} res={int(actual_res)}s")
                else:
                    res_sf = self.arima_models[agent_id][cache_key]
                    res_sf.fit(res_df)

                # Calculate max steps needed
                max_steps = max(max(1, int(m * 60 / actual_res)) for m in horizons)
                forecast_df = res_sf.predict(h=max_steps)
                forecasts = forecast_df["AutoARIMA"].values

                for m in horizons:
                    steps = max(1, int(m * 60 / actual_res))
                    idx = min(steps - 1, len(forecasts) - 1)
                    pred = float(forecasts[idx])
                    # Clamp to reasonable range
                    pred = max(0, min(100, pred))
                    res_label = f"{int(actual_res // 60)}분" if actual_res >= 60 else f"{int(actual_res)}초"
                    results.append({
                        "minutes": m,
                        "value": pred,
                        "resolution": res_label,
                        "steps": steps,
                    })

            except Exception as e:
                log.warning(f"Multi-res forecast failed ({int(actual_res)}s): {e}")
                for m in horizons:
                    results.append({"minutes": m, "value": float(arr[-1])})

        # Sort by horizon
        results.sort(key=lambda x: x["minutes"])
        return results

    def _run_cached_arima(self, agent_id: str, metric_name: str, values: deque) -> Optional[AnomalyResult]:
        """Run AutoARIMA with model caching for faster predictions."""
        if len(values) < MIN_SAMPLES_ARIMA:
            return None

        try:
            arr = np.array(values)

            # Detect actual data interval
            interval_sec = self._get_interval_seconds(agent_id)
            freq = f"{int(interval_sec)}s"

            # Prepare data
            df = pd.DataFrame({
                "unique_id": agent_id,
                "ds": pd.date_range(end=pd.Timestamp.now(), periods=len(arr), freq=freq),
                "y": arr,
            })

            # Check if we need to (re)train
            cached_freq = self.arima_models[agent_id].get(f"{metric_name}__freq")
            need_retrain = (
                metric_name not in self.arima_models[agent_id]
                or cached_freq != freq          # interval changed → must retrain
                or len(values) % 100 == 0       # periodic refresh
            )

            if need_retrain:
                # Train new model
                sf = StatsForecast(
                    models=[AutoARIMA(season_length=ARIMA_SEASON_LENGTH)],
                    freq=freq,
                )
                sf.fit(df)
                self.arima_models[agent_id][metric_name] = sf
                self.arima_models[agent_id][f"{metric_name}__freq"] = freq
                log.info(f"ARIMA model trained for {agent_id}/{metric_name} (interval={interval_sec:.1f}s)")
            else:
                sf = self.arima_models[agent_id][metric_name]
                # Update with new data (partial fit simulation)
                sf.fit(df)

            # Current (1-step) forecast for residual analysis
            forecast_1step = sf.predict(h=1)
            forecast_value = float(forecast_1step["AutoARIMA"].values[0])

            # Calculate residual
            actual_value = float(arr[-1])
            residual = abs(actual_value - forecast_value)

            # Track residuals for adaptive threshold
            if metric_name not in self.arima_residuals[agent_id]:
                self.arima_residuals[agent_id][metric_name] = deque(maxlen=WINDOW_SIZE)
            self.arima_residuals[agent_id][metric_name].append(residual)

            # Calculate adaptive threshold
            residual_history = np.array(self.arima_residuals[agent_id][metric_name])
            if len(residual_history) > 5:
                threshold = float(ARIMA_RESIDUAL_K * np.std(residual_history))
                threshold = max(threshold, 0.1)  # Minimum threshold
            else:
                threshold = float(np.mean(residual_history) * 2) if len(residual_history) > 0 else 1.0

            # Calculate normalized score
            score = residual / max(threshold, 0.01)

            # Determine severity with confidence
            if residual > threshold * 1.5:
                severity = "critical"
                confidence = min(0.95, score / 2)
            elif residual > threshold:
                severity = "warning"
                confidence = min(0.8, score / 2)
            else:
                severity = "normal"
                confidence = 1.0 - min(0.9, score)

            # Build multi-step forecast horizon using multi-resolution
            forecast_horizon = self._multi_resolution_forecast(
                agent_id, metric_name, arr, interval_sec
            )
            _forecast_thresholds = {
                "CPU": (80.0, 90.0),
                "Memory": (85.0, 95.0),
                "DiskIO": (70.0, 85.0),
                "NetworkSent": (50000.0, 100000.0),
                "NetworkRecv": (50000.0, 100000.0),
            }
            warning_threshold, critical_threshold = _forecast_thresholds.get(metric_name, (70.0, 85.0))

            for fh in forecast_horizon:
                pred_value = fh["value"]
                if pred_value >= critical_threshold:
                    fh["severity"] = "critical"
                elif pred_value >= warning_threshold:
                    fh["severity"] = "warning"
                else:
                    fh["severity"] = "normal"
            
            if severity == "critical":
                arima_details = f"예측({forecast_value:.1f})과 실제({actual_value:.1f}) 차이 {residual:.1f} — 임계값({threshold:.1f})의 1.5배 초과"
            elif severity == "warning":
                arima_details = f"예측({forecast_value:.1f})과 실제({actual_value:.1f}) 차이 {residual:.1f} — 임계값({threshold:.1f}) 초과"
            else:
                arima_details = f"예측={forecast_value:.1f}, 실제={actual_value:.1f}, 차이={residual:.1f}"
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
                details=arima_details,
                forecast_horizon=forecast_horizon,
            )
            
        except Exception as e:
            log.warning(f"Cached AutoARIMA failed for {metric_name}: {e}")
            return None
    
    def _check_peripherals(self, agent_id: str, data: dict) -> List[AnomalyResult]:
        """Monitor peripheral device status from logs."""
        results = []
        logs = data.get("Logs", [])
        
        if not logs:
            return results
        
        state = self.peripheral_states[agent_id]
        
        for log_entry in logs:
            if log_entry.get("BodyType") == "주변장치 체크":
                key_values = log_entry.get("KeyValues", {})
                
                for device, status in key_values.items():
                    prev_status = state.last_states.get(device)
                    state.last_states[device] = status
                    
                    if status == "실패":
                        state.failure_counts[device] = state.failure_counts.get(device, 0) + 1
                        
                        # Alert on consecutive failures
                        if state.failure_counts[device] >= PERIPHERAL_FAILURE_THRESHOLD:
                            results.append(AnomalyResult(
                                engine="peripheral",
                                metric=device,
                                value=float(state.failure_counts[device]),
                                score=min(1.0, state.failure_counts[device] / 10),
                                threshold=float(PERIPHERAL_FAILURE_THRESHOLD),
                                severity="critical" if state.failure_counts[device] >= 5 else "warning",
                                confidence=0.95,
                                details=f"{device} 연속 {state.failure_counts[device]}회 실패"
                            ))
                    elif status == "연결":
                        # Reset failure count on success
                        if device in state.failure_counts and state.failure_counts[device] > 0:
                            log.info(f"Peripheral {device} recovered after {state.failure_counts[device]} failures")
                        state.failure_counts[device] = 0
        
        return results
    
    def _calculate_ensemble_score(self, detections: List[AnomalyResult]) -> Tuple[float, str]:
        """Calculate ensemble score from ECOD and ARIMA results."""
        ecod_scores = [d.score * d.confidence for d in detections if d.engine == "ecod"]
        arima_scores = [d.score * d.confidence for d in detections if d.engine == "arima"]
        
        ecod_avg = np.mean(ecod_scores) if ecod_scores else 0
        arima_avg = np.mean(arima_scores) if arima_scores else 0
        
        # Weighted ensemble
        if ecod_scores and arima_scores:
            ensemble = ECOD_WEIGHT * ecod_avg + ARIMA_WEIGHT * arima_avg
        elif ecod_scores:
            ensemble = ecod_avg
        elif arima_scores:
            ensemble = arima_avg
        else:
            ensemble = 0
        
        # Determine overall severity
        if ensemble > 0.8:
            severity = "critical"
        elif ensemble > 0.5:
            severity = "warning"
        else:
            severity = "normal"
        
        return float(ensemble), severity
    
    def detect(self, data: dict, run_ecod: bool = True, run_arima: bool = True) -> DetectionResult:
        """
        Run enhanced anomaly detection on incoming data.
        
        Args:
            data: POS metric data point
            run_ecod: Whether to run ECOD this cycle
            run_arima: Whether to run ARIMA this cycle
            
        Returns:
            DetectionResult with anomalies, ensemble score, and health score
        """
        agent_id = data.get("AgentId", "unknown")
        timestamp = data.get("Timestamp", "")
        
        # Update buffer
        self._update_buffer(agent_id, data)
        buf = self.buffers[agent_id]
        
        detections: List[AnomalyResult] = []
        
        # Raw metrics for client
        raw_metrics = {
            "CPU": data.get("CPU", 0),
            "Memory": data.get("Memory", 0),
            "DiskIO": data.get("DiskIO", 0),
            "NetworkSent": data.get("Network", {}).get("Sent", 0),
            "NetworkRecv": data.get("Network", {}).get("Recv", 0),
        }
        
        # 1. Multivariate ECOD
        if run_ecod:
            ecod_results = self._run_multivariate_ecod(agent_id)
            detections.extend(ecod_results)
        
        # 2. Cached AutoARIMA
        if run_arima:
            for metric_name, values in [("CPU", buf.cpu), ("Memory", buf.memory), ("DiskIO", buf.disk_io),
                                         ("NetworkSent", buf.network_sent), ("NetworkRecv", buf.network_recv)]:
                result = self._run_cached_arima(agent_id, metric_name, values)
                if result:
                    detections.append(result)
        
        # 3. Peripheral monitoring
        peripheral_results = self._check_peripherals(agent_id, data)
        detections.extend(peripheral_results)
        
        # 4. Calculate ensemble score
        ensemble_score, ensemble_severity = self._calculate_ensemble_score(detections)
        
        # Add ensemble result if we have both ECOD and ARIMA
        ecod_count = sum(1 for d in detections if d.engine == "ecod")
        arima_count = sum(1 for d in detections if d.engine == "arima")
        
        if ecod_count > 0 and arima_count > 0:
            detections.append(AnomalyResult(
                engine="ensemble",
                metric="Combined",
                value=ensemble_score,
                score=ensemble_score,
                threshold=0.5,
                severity=ensemble_severity,
                confidence=0.9 if ensemble_score > 0.7 else 0.7,
                details=f"ECOD weight={ECOD_WEIGHT}, ARIMA weight={ARIMA_WEIGHT}"
            ))
        
        # 5. Calculate health score
        health_score = 100
        for d in detections:
            if d.severity == "critical":
                health_score -= int(20 * d.confidence)
            elif d.severity == "warning":
                health_score -= int(10 * d.confidence)
        health_score = max(0, min(100, health_score))
        
        return DetectionResult(
            agent_id=agent_id,
            timestamp=timestamp,
            detections=detections,
            health_score=health_score,
            raw_metrics=raw_metrics,
            ensemble_score=ensemble_score,
        )
    
    def to_dict(self, result: DetectionResult) -> dict:
        """Convert DetectionResult to dict for JSON serialization."""
        return {
            "type": "anomaly",
            "agent_id": result.agent_id,
            "timestamp": result.timestamp,
            "detections": [
                {
                    "engine": d.engine,
                    "metric": d.metric,
                    "score": d.score,
                    "threshold": d.threshold,
                    "arima_predicted": d.forecast,
                    "arima_deviation": d.residual,
                    "severity": d.severity,
                    "confidence": d.confidence,
                    "details": d.details,
                    "forecast_horizon": d.forecast_horizon,  # Multi-step predictions
                }
                for d in result.detections
            ],
            "health_score": result.health_score,
            "ensemble_score": result.ensemble_score,
            "raw_metrics": result.raw_metrics,
        }


# Global detector instance
detector = EnhancedAnomalyDetector()


def batch_arima_forecast(data_list: List[dict], forecast_hours: int = 2) -> dict:
    """
    Run ARIMA on entire sample data and generate future forecast.
    
    Args:
        data_list: List of all sample data points
        forecast_hours: Hours to forecast into future
        
    Returns:
        dict with CPU and Memory forecasts
    """
    if len(data_list) < MIN_SAMPLES_ARIMA:
        log.warning(f"Not enough data for ARIMA: {len(data_list)} < {MIN_SAMPLES_ARIMA}")
        return {"cpu": [], "memory": [], "error": "Not enough data"}
    
    # Sample data if too large (keep last 500 points for speed)
    max_samples = 500
    if len(data_list) > max_samples:
        # Take evenly spaced samples + last 100 points
        step = len(data_list) // (max_samples - 100)
        sampled = data_list[::step][:max_samples - 100] + data_list[-100:]
        log.info(f"Sampled {len(data_list)} -> {len(sampled)} records for ARIMA")
        data_list = sampled
    
    # Detect actual data interval from timestamps
    interval_sec = 5.0  # default
    ts_list = []
    for d in data_list[:20]:
        try:
            ts_list.append(pd.Timestamp(d.get("Timestamp", "")))
        except Exception:
            continue
    if len(ts_list) >= 2:
        diffs = [
            (ts_list[i + 1] - ts_list[i]).total_seconds()
            for i in range(len(ts_list) - 1)
            if (ts_list[i + 1] - ts_list[i]).total_seconds() >= 1.0
        ]
        if diffs:
            interval_sec = float(np.median(diffs))
    freq = f"{int(interval_sec)}s"

    log.info(f"Running batch ARIMA forecast on {len(data_list)} records (interval={interval_sec:.1f}s, freq={freq})...")

    # Extract metrics
    cpu_values = [d.get("CPU", 0) for d in data_list]
    memory_values = [d.get("Memory", 0) for d in data_list]
    disk_io_values = [d.get("DiskIO", 0) for d in data_list]

    # Forecast horizon: 30 minutes worth of steps (capped for speed), extrapolate beyond
    horizon_anchor_minutes = 30
    horizon = max(1, int(horizon_anchor_minutes * 60 / interval_sec))

    result = {"cpu": [], "memory": [], "disk_io": []}

    for metric_name, values in [("cpu", cpu_values), ("memory", memory_values), ("disk_io", disk_io_values)]:
        try:
            arr = np.array(values)

            # Prepare data for StatsForecast
            df = pd.DataFrame({
                "unique_id": "batch",
                "ds": pd.date_range(end=pd.Timestamp.now(), periods=len(arr), freq=freq),
                "y": arr,
            })

            # Train model
            sf = StatsForecast(
                models=[AutoARIMA(season_length=ARIMA_SEASON_LENGTH)],
                freq=freq,
            )
            sf.fit(df)

            # Forecast
            forecast_df = sf.predict(h=horizon)
            forecasts = forecast_df["AutoARIMA"].values

            # Sample at 10min, 30min (ARIMA), extrapolate 1hr, 2hr
            last_forecast = float(forecasts[-1])
            trend = (last_forecast - float(arr[-1])) / horizon_anchor_minutes  # trend per minute

            forecast_minutes = [10, 30, 60, 120]
            sample_points = []
            for minutes in forecast_minutes:
                steps = int(minutes * 60 / interval_sec)
                extrapolate = steps < 1 or steps > horizon   # sub-interval or beyond horizon
                sample_points.append({
                    "minutes": minutes,
                    "index": min(steps - 1, horizon - 1) if not extrapolate else None,
                    "extrapolate": extrapolate,
                })
            
            metric_forecasts = []
            for point in sample_points:
                if point["extrapolate"]:
                    # Extrapolate using trend
                    value = last_forecast + trend * (point["minutes"] - 30)
                    value = max(0, min(100, value))  # Clamp to 0-100
                else:
                    idx = min(point["index"], len(forecasts) - 1)
                    value = float(forecasts[idx])
                
                # Determine severity based on metric type
                if metric_name == "cpu":
                    warning_threshold = 80.0
                    critical_threshold = 90.0
                elif metric_name == "disk_io":
                    # DiskIO can spike suddenly, use lower thresholds
                    warning_threshold = 70.0
                    critical_threshold = 85.0
                else:  # memory
                    warning_threshold = 85.0
                    critical_threshold = 95.0
                
                if value >= critical_threshold:
                    severity = "critical"
                elif value >= warning_threshold:
                    severity = "warning"
                else:
                    severity = "normal"
                
                metric_forecasts.append({
                    "minutes": point["minutes"],
                    "value": round(value, 2),
                    "severity": severity,
                })
            
            result[metric_name] = metric_forecasts
            log.info(f"ARIMA {metric_name} forecast: {metric_forecasts}")
            
        except Exception as e:
            log.error(f"Batch ARIMA failed for {metric_name}: {e}")
            result[metric_name] = []
    
    # Add current values (last data point)
    result["current_cpu"] = round(cpu_values[-1], 2) if cpu_values else 0
    result["current_memory"] = round(memory_values[-1], 2) if memory_values else 0
    
    return result
