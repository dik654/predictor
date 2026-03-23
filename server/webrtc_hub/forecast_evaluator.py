"""
PulseAI Lite - Forecast Evaluator
ARIMA 예측값을 장기 ECOD 모델로 평가하여 미래 이상 여부를 판단.

구조:
  ARIMA 예측 (1시간/6시간/12시간/1일/2일)
      ↓
  장기 ECOD 모델 (7일치 실제 데이터로 학습)
      ↓
  "이 예측값 조합이 이 에이전트의 정상 범위 밖인가?"
      ↓
  이진 피처(동글/스캐너) 규칙 기반 점수 합산
      ↓
  예측 신뢰도(predict_tracker 오차율) 반영
      ↓
  최종 severity 판정
"""

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from pyod.models.ecod import ECOD

from . import influx_writer

log = logging.getLogger("forecast_evaluator")

# Long-term ECOD config
LONGTERM_WINDOW_DAYS = 7
LONGTERM_RESOLUTION_MIN = 5  # 5-minute resolution for InfluxDB query
LONGTERM_MIN_SAMPLES = 100   # Minimum data points to train (5분 해상도 × 100 = 약 8시간)
RETRAIN_INTERVAL_SEC = 24 * 3600  # 24시간마다 재학습
RETRAIN_HOUR = 3    # 새벽 3시 30분에 재학습 (사람이 적은 시간대)
RETRAIN_MINUTE = 30

# Severity thresholds for ECOD score (percentile-based)
ECOD_SCORE_WARNING = 0.7
ECOD_SCORE_CRITICAL = 0.9

# Rule-based scoring for peripheral failures (0=실패 시 가산)
# -1(미사용)은 점수 0
PERIPHERAL_SCORES = {
    "dongle": 0.5,           # 동글: 결제 불가 → 치명적
    "hand_scanner": 0.4,     # 핸드스캐너: 상품 스캔 → 결제 핵심
    "2d_scanner": 0.3,       # 2D스캐너: QR/쿠폰 → 결제 관련이지만 핸드보다 낮음
    "passport_reader": 0.1,  # 나머지: 추후 조정
    "phone_charger": 0.1,
    "keyboard": 0.1,
    "msr": 0.1,
}
RULE_SCORE_MULTI_FAIL = 0.7  # 2개 이상 실패 시 최소 점수


@dataclass
class EventState:
    """Latest known state for event-driven data per agent."""
    peripherals: Dict[str, int] = field(default_factory=lambda: {})  # {device_eng: 1/0/-1}
    payment_time_sec: float = 0.0
    payment_updated: float = 0.0


@dataclass
class FeatureContribution:
    """ECOD feature-level contribution to anomaly score."""
    metric: str           # "CPU", "Memory", "DiskIO"
    score: float          # raw feature score
    pct: float            # contribution percentage (0-100)
    predicted_value: float  # predicted metric value

@dataclass
class HorizonEvaluation:
    """Evaluation result for a single forecast horizon."""
    horizon_min: int
    pred_cpu: float
    pred_memory: float
    pred_disk_io: float
    pred_network_sent: float
    pred_network_recv: float
    ecod_score: float           # 0-1, from long-term ECOD
    rule_score: float           # 0-1, from binary feature rules
    combined_score: float       # weighted combination
    reliability: float          # 0-1, from prediction accuracy history
    final_score: float          # combined_score * reliability
    severity: str               # "normal", "warning", "critical"
    is_outlier: bool            # ECOD outlier flag
    prediction_interval: Optional[Dict] = None  # {lo_90, hi_90, lo_95, hi_95}
    feature_contributions: List['FeatureContribution'] = field(default_factory=list)


@dataclass
class ForecastEvaluation:
    """Full forecast evaluation result for an agent."""
    agent_id: str
    timestamp: str
    horizons: List[HorizonEvaluation] = field(default_factory=list)
    overall_severity: str = "normal"
    model_ready: bool = False   # Whether long-term ECOD is available
    data_source: str = "none"   # "influxdb", "buffer", "none"


class ForecastEvaluator:
    """
    Evaluates ARIMA forecast values using long-term ECOD model.

    The ECOD model is trained on 7 days of historical actual data,
    representing "what is normal for this agent across all time periods."
    Predicted future values are scored against this model.
    """

    def __init__(self):
        # Long-term ECOD models: agent_id -> ECOD
        self.models: Dict[str, ECOD] = {}
        # Training data stats for score normalization
        self.train_scores: Dict[str, np.ndarray] = {}
        # Cached training data for feature contribution calculation
        self._training_data_cache: Dict[str, np.ndarray] = {}
        # Last retrain timestamp per agent
        self.last_retrain: Dict[str, float] = {}
        # Event states per agent
        self.event_states: Dict[str, EventState] = {}
        # Fallback buffer: agent_id -> deque of [cpu, mem, disk] arrays
        self.fallback_buffers: Dict[str, deque] = {}

    def _ensure_event_state(self, agent_id: str) -> EventState:
        if agent_id not in self.event_states:
            self.event_states[agent_id] = EventState()
        return self.event_states[agent_id]

    def update_event(self, agent_id: str, data: dict) -> None:
        """Update event state from incoming data (peripherals, payment)."""
        state = self._ensure_event_state(agent_id)

        # server.py에서 합성된 Peripherals dict 사용 (영문 변환 완료 상태)
        peripherals = data.get("Peripherals")
        if peripherals:
            state.peripherals.update(peripherals)

    def update_fallback_buffer(self, agent_id: str, data: dict) -> None:
        """Accumulate data in fallback buffer for when InfluxDB is unavailable."""
        if agent_id not in self.fallback_buffers:
            self.fallback_buffers[agent_id] = deque(maxlen=2016)

        network = data.get("Network", {})
        process = data.get("Process", {})
        proc_val = 1 if any(v == "RUNNING" for v in process.values()) else 0
        peripherals = data.get("Peripherals", {})

        has_periph = any(peripherals.get(k, -1) != -1 for k in ["dongle", "hand_scanner", "2d_scanner", "keyboard", "msr"])
        is_idle = 1 if has_periph else 0

        row = [
            data.get("CPU", 0),
            data.get("Memory", 0),
            data.get("DiskIO", 0),
            network.get("Sent", 0),
            network.get("Recv", 0),
            proc_val,
            peripherals.get("dongle", -1),
            peripherals.get("hand_scanner", -1),
            peripherals.get("passport_reader", -1),
            peripherals.get("2d_scanner", -1),
            peripherals.get("phone_charger", -1),
            peripherals.get("keyboard", -1),
            peripherals.get("msr", -1),
            is_idle,
        ]
        self.fallback_buffers[agent_id].append(row)

    def _get_training_data(self, agent_id: str) -> Optional[np.ndarray]:
        """Get training data from InfluxDB or fallback buffer."""
        # Try InfluxDB first
        hist = influx_writer.get_historical_metrics(
            agent_id,
            hours=LONGTERM_WINDOW_DAYS * 24,
            resolution_minutes=LONGTERM_RESOLUTION_MIN,
        )
        if hist is not None and hist["count"] >= LONGTERM_MIN_SAMPLES:
            from . import influx_writer as iw
            cols = [hist["cpu"], hist["memory"], hist["disk_io"],
                    hist.get("network_sent_bytes", np.zeros(hist["count"])),
                    hist.get("network_received_bytes", np.zeros(hist["count"])),
                    hist.get("process", np.zeros(hist["count"]))]
            for pf in iw.PERIPHERAL_FIELDS:
                cols.append(hist.get(pf, np.full(hist["count"], -1.0)))
            # pos_idle: 주변장치 데이터가 있으면 유휴(1), 없으면 비유휴(0)
            periph_cols = [hist.get(pf, np.full(hist["count"], -1.0)) for pf in iw.PERIPHERAL_FIELDS]
            idle_col = np.array([1.0 if any(pc[i] != -1 for pc in periph_cols) else 0.0 for i in range(hist["count"])])
            cols.append(idle_col)
            X = np.column_stack(cols)
            log.info(f"Training data from InfluxDB: {agent_id} ({X.shape[0]} points, {X.shape[1]} features)")
            return X

        # Fallback to buffer
        buf = self.fallback_buffers.get(agent_id)
        if buf and len(buf) >= LONGTERM_MIN_SAMPLES:
            X = np.array(list(buf))
            log.info(f"Training data from buffer: {agent_id} ({X.shape[0]} points)")
            return X

        return None

    def _train_model(self, agent_id: str) -> bool:
        """Train or retrain long-term ECOD model for an agent."""
        X = self._get_training_data(agent_id)
        if X is None:
            return False

        try:
            model = ECOD(contamination=0.05)
            model.fit(X)
            self.models[agent_id] = model

            # Store training scores for percentile-based normalization
            self.train_scores[agent_id] = model.decision_function(X)
            # Cache training data for feature contribution calculation
            self._training_data_cache[agent_id] = X
            self.last_retrain[agent_id] = time.time()

            log.info(
                f"Long-term ECOD trained for {agent_id}: "
                f"{X.shape[0]} samples, {X.shape[1]} features"
            )
            return True
        except Exception as e:
            log.warning(f"Failed to train long-term ECOD for {agent_id}: {e}")
            return False

    def _ensure_model(self, agent_id: str) -> bool:
        """Ensure model exists and is up-to-date.
        - 모델 없음: 즉시 학습 (데이터 부족하면 fallback)
        - 모델 있음: 매일 새벽 3:30에 재학습
        """
        from datetime import datetime, timedelta, timezone
        KST = timezone(timedelta(hours=9))
        now = time.time()
        last = self.last_retrain.get(agent_id, 0)

        if agent_id not in self.models:
            # 최초: 즉시 학습
            return self._train_model(agent_id)

        # 재학습: 24시간 경과 + 새벽 3:30 이후
        if (now - last) >= RETRAIN_INTERVAL_SEC:
            now_kst = datetime.now(KST)
            if now_kst.hour == RETRAIN_HOUR and now_kst.minute >= RETRAIN_MINUTE:
                log.info(f"Scheduled retrain for {agent_id} at {now_kst.strftime('%H:%M')}")
                return self._train_model(agent_id)

        return True

    def _normalize_score(self, agent_id: str, raw_score: float) -> float:
        """Normalize ECOD score to 0-1 using percentile of training scores."""
        train = self.train_scores.get(agent_id)
        if train is None or len(train) == 0:
            return min(1.0, max(0.0, raw_score))

        # Percentile rank: what fraction of training scores is below this score
        percentile = float(np.sum(train < raw_score)) / len(train)
        return percentile

    def _compute_rule_score(self, agent_id: str) -> float:
        """Compute rule-based score from peripheral failures.
        0=실패 → 가산, 1=연결 → 0점, -1=미사용 → 0점
        """
        state = self.event_states.get(agent_id)
        if state is None or not state.peripherals:
            return 0.0

        score = 0.0
        failures = 0

        for device, weight in PERIPHERAL_SCORES.items():
            status = state.peripherals.get(device)
            if status == 0:  # 실패
                score += weight
                failures += 1

        # 2개 이상 실패 시 최소 점수 보장
        if failures >= 2:
            score = max(score, RULE_SCORE_MULTI_FAIL)

        return min(1.0, score)

    def _get_reliability(self, agent_id: str, horizon_min: int) -> float:
        """
        Get prediction reliability from predict_tracker error history.
        Returns 0.1-1.0 (lower = less reliable).
        """
        try:
            from .predict_tracker import tracker
            key = (agent_id, "CPU")
            forecasts = list(tracker.forecasts.get(key, []))

            # Filter matched forecasts for this horizon
            matched = [
                f for f in forecasts
                if f.actual_value is not None
                and f.horizon_min == horizon_min
            ]

            if len(matched) < 3:
                # Not enough error history: default reliability decreases with horizon
                default_by_horizon = {
                    60: 0.8,    # 1시간: 비교적 신뢰
                    360: 0.6,   # 6시간
                    720: 0.5,   # 12시간
                    1440: 0.4,  # 1일
                    2880: 0.3,  # 2일: 낮은 신뢰
                }
                return default_by_horizon.get(horizon_min, 0.5)

            errors = [f.error_pct for f in matched[-20:]]  # Last 20
            mean_error = float(np.mean(errors))

            # reliability = 1.0 - error/100, clamped to [0.1, 1.0]
            reliability = max(0.1, 1.0 - mean_error / 100.0)
            return reliability

        except Exception:
            return 0.7  # Default moderate reliability

    def evaluate(
        self,
        agent_id: str,
        timestamp: str,
        forecasts: Dict[int, Dict[str, float]],
    ) -> ForecastEvaluation:
        """
        Evaluate ARIMA forecast values using long-term ECOD.

        Args:
            agent_id: Agent identifier
            timestamp: Current timestamp
            forecasts: {horizon_min: {"cpu": val, "memory": val, "disk_io": val}}
                       e.g. {60: {"cpu": 72, "memory": 68, "disk_io": 0.4}, ...}

        Returns:
            ForecastEvaluation with per-horizon scores and severities
        """
        result = ForecastEvaluation(
            agent_id=agent_id,
            timestamp=timestamp,
        )

        if not forecasts:
            return result

        # Ensure model is ready
        model_ready = self._ensure_model(agent_id)
        result.model_ready = model_ready

        if not model_ready:
            # Fallback: use fixed thresholds (current behavior)
            result.data_source = "none"
            for horizon_min, preds in sorted(forecasts.items()):
                cpu = preds.get("cpu", 0)
                mem = preds.get("memory", 0)
                disk = preds.get("disk_io", 0)

                # Simple threshold fallback
                if cpu >= 90 or mem >= 95:
                    severity = "critical"
                    score = 0.95
                elif cpu >= 80 or mem >= 85:
                    severity = "warning"
                    score = 0.75
                else:
                    severity = "normal"
                    score = 0.2

                result.horizons.append(HorizonEvaluation(
                    horizon_min=horizon_min,
                    pred_cpu=cpu,
                    pred_memory=mem,
                    pred_disk_io=disk,
                    pred_network_sent=preds.get("network_sent", 0),
                    pred_network_recv=preds.get("network_recv", 0),
                    ecod_score=score,
                    rule_score=self._compute_rule_score(agent_id),
                    combined_score=score,
                    reliability=0.5,
                    final_score=score * 0.5,
                    severity=severity,
                    is_outlier=severity != "normal",
                ))

            result.overall_severity = self._worst_severity(result.horizons)
            return result

        # Use long-term ECOD model
        model = self.models[agent_id]
        result.data_source = "influxdb" if agent_id in self.last_retrain else "buffer"
        rule_score = self._compute_rule_score(agent_id)

        # 예측 평가: 주변장치는 "정상 연결(1)" 가정 — 시스템 메트릭 변화에 집중
        # 주변장치 이상은 rule_score에서 별도 반영
        from . import influx_writer as iw
        periph_values = [1.0] * len(iw.PERIPHERAL_FIELDS)  # 모두 연결 가정
        is_idle = 1.0
        proc = 1.0

        # 학습 데이터 중앙값 (예측값 없는 메트릭은 "평소 수준"으로 → ECOD에 영향 안 줌)
        X_train = self._training_data_cache.get(agent_id)
        train_medians = np.median(X_train, axis=0) if X_train is not None else np.zeros(14)

        for horizon_min, preds in sorted(forecasts.items()):
            cpu = preds.get("cpu") or float(train_medians[0])
            mem = preds.get("memory") or float(train_medians[1])
            disk = preds.get("disk_io") or float(train_medians[2])
            net_sent = preds.get("network_sent") or float(train_medians[3])
            net_recv = preds.get("network_recv") or float(train_medians[4])

            # 14차원 벡터: ARIMA 예측(연속) + 현재 상태(이산) + 유휴 여부
            feature_names = ["CPU", "Memory", "DiskIO", "NetworkSent", "NetworkRecv", "Process",
                             "Dongle", "HandScanner", "PassportReader", "2DScanner", "PhoneCharger", "Keyboard", "MSR",
                             "POS_Idle"]
            feature_values = [cpu, mem, disk, net_sent, net_recv, proc] + periph_values + [is_idle]
            point = np.array([feature_values])
            feature_contribs: List[FeatureContribution] = []
            try:
                raw_score = float(model.decision_function(point)[0])
                is_outlier = bool(model.predict(point)[0] == 1)

                # Feature contribution: percentile rank of each predicted value
                # against training data — how extreme is each feature?
                X_train = self._training_data_cache.get(agent_id)
                if X_train is not None:
                    per_feature_scores = []
                    for i in range(len(feature_names)):
                        col = X_train[:, i]
                        pct = float(np.sum(col < feature_values[i])) / len(col)
                        feat_score = abs(pct - 0.5) * 2  # 0=median, 1=extreme
                        per_feature_scores.append(feat_score)

                    total = sum(per_feature_scores) or 1.0
                    for i, name in enumerate(feature_names):
                        feature_contribs.append(FeatureContribution(
                            metric=name,
                            score=round(per_feature_scores[i], 4),
                            pct=round((per_feature_scores[i] / total) * 100, 1),
                            predicted_value=feature_values[i],
                        ))
                    feature_contribs.sort(key=lambda x: x.score, reverse=True)

            except Exception as e:
                log.warning(f"ECOD evaluation failed for {agent_id}/{horizon_min}min: {e}")
                raw_score = 0.0
                is_outlier = False

            ecod_score = self._normalize_score(agent_id, raw_score)

            # Combine ECOD score with rule-based score
            # ECOD weight: 0.8, Rule weight: 0.2
            combined = 0.8 * ecod_score + 0.2 * rule_score

            # Apply reliability
            reliability = self._get_reliability(agent_id, horizon_min)
            final_score = combined * reliability

            # Determine severity
            if final_score >= ECOD_SCORE_CRITICAL or (is_outlier and ecod_score >= 0.85):
                severity = "critical"
            elif final_score >= ECOD_SCORE_WARNING or (is_outlier and ecod_score >= 0.65):
                severity = "warning"
            else:
                severity = "normal"

            # Prediction interval from preds (if provided by ARIMA)
            pi = None
            if "lo_90" in preds:
                pi = {
                    "lo_90": preds["lo_90"],
                    "hi_90": preds["hi_90"],
                    "lo_95": preds.get("lo_95"),
                    "hi_95": preds.get("hi_95"),
                }

            result.horizons.append(HorizonEvaluation(
                horizon_min=horizon_min,
                pred_cpu=cpu,
                pred_memory=mem,
                pred_disk_io=disk,
                pred_network_sent=net_sent,
                pred_network_recv=net_recv,
                ecod_score=ecod_score,
                rule_score=rule_score,
                combined_score=combined,
                reliability=reliability,
                final_score=final_score,
                severity=severity,
                is_outlier=is_outlier,
                prediction_interval=pi,
                feature_contributions=feature_contribs,
            ))

        result.overall_severity = self._worst_severity(result.horizons)
        return result

    def _worst_severity(self, horizons: List[HorizonEvaluation]) -> str:
        if any(h.severity == "critical" for h in horizons):
            return "critical"
        if any(h.severity == "warning" for h in horizons):
            return "warning"
        return "normal"

    def to_dict(self, result: ForecastEvaluation) -> dict:
        """Convert ForecastEvaluation to dict for JSON serialization."""
        return {
            "type": "forecast_evaluation",
            "agent_id": result.agent_id,
            "timestamp": result.timestamp,
            "overall_severity": result.overall_severity,
            "model_ready": result.model_ready,
            "data_source": result.data_source,
            "horizons": [
                {
                    "horizon_min": h.horizon_min,
                    "horizon_label": self._horizon_label(h.horizon_min),
                    "pred_cpu": round(h.pred_cpu, 2),
                    "pred_memory": round(h.pred_memory, 2),
                    "pred_disk_io": round(h.pred_disk_io, 3),
                    "pred_network_sent": round(h.pred_network_sent, 0),
                    "pred_network_recv": round(h.pred_network_recv, 0),
                    "ecod_score": round(h.ecod_score, 3),
                    "rule_score": round(h.rule_score, 3),
                    "final_score": round(h.final_score, 3),
                    "reliability": round(h.reliability, 2),
                    "severity": h.severity,
                    "is_outlier": h.is_outlier,
                    "prediction_interval": h.prediction_interval,
                    "feature_contributions": [
                        {
                            "metric": fc.metric,
                            "score": fc.score,
                            "pct": fc.pct,
                            "predicted_value": fc.predicted_value,
                        }
                        for fc in h.feature_contributions
                    ],
                }
                for h in result.horizons
            ],
        }

    def _horizon_label(self, minutes: int) -> str:
        if minutes < 60:
            return f"{minutes}분 후"
        elif minutes < 1440:
            return f"{minutes // 60}시간 후"
        else:
            return f"{minutes // 1440}일 후"


# Global evaluator instance
evaluator = ForecastEvaluator()
