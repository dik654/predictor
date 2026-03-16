"""
TDD: ForecastEvaluator - 장기 ECOD 기반 예측 평가 테스트

Run from project root:
  cd server && uv run pytest ../test_forecast_evaluator.py -v
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "server"))

import numpy as np
import pytest
from datetime import datetime

from webrtc_hub.forecast_evaluator import (
    ForecastEvaluator,
    EventState,
    LONGTERM_MIN_SAMPLES,
)

np.random.seed(42)


# ── helpers ────────────────────────────────────────────────────────────────────

def make_normal_data(n: int = 200) -> list:
    """Generate n records of normal POS data (CPU 20-50, Mem 50-70, Disk 0.1-0.5)."""
    records = []
    for i in range(n):
        records.append({
            "CPU": 35.0 + np.random.normal(0, 8),
            "Memory": 60.0 + np.random.normal(0, 5),
            "DiskIO": 0.3 + np.random.normal(0, 0.1),
        })
    return records


def feed_buffer(ev: ForecastEvaluator, agent_id: str, records: list):
    """Feed records into evaluator's fallback buffer."""
    for r in records:
        ev.update_fallback_buffer(agent_id, r)


@pytest.fixture
def ev():
    return ForecastEvaluator()


# ── 1. Model Training ─────────────────────────────────────────────────────────


class TestModelTraining:

    def test_no_model_without_data(self, ev):
        """데이터 없으면 모델 생성 실패."""
        assert ev._ensure_model("empty-agent") is False
        assert "empty-agent" not in ev.models

    def test_model_trains_with_enough_buffer_data(self, ev):
        """충분한 버퍼 데이터로 모델 학습 성공."""
        feed_buffer(ev, "agent-1", make_normal_data(LONGTERM_MIN_SAMPLES + 10))
        assert ev._train_model("agent-1") is True
        assert "agent-1" in ev.models

    def test_model_not_trained_below_min_samples(self, ev):
        """최소 샘플 미만이면 학습 실패."""
        feed_buffer(ev, "agent-2", make_normal_data(LONGTERM_MIN_SAMPLES - 1))
        assert ev._train_model("agent-2") is False


# ── 2. Forecast Evaluation ────────────────────────────────────────────────────


class TestForecastEvaluation:

    def test_normal_prediction_scores_low(self, ev):
        """정상 범위 예측값은 낮은 점수."""
        feed_buffer(ev, "pos-1", make_normal_data(200))
        ev._train_model("pos-1")

        forecasts = {
            60: {"cpu": 35.0, "memory": 60.0, "disk_io": 0.3},
            360: {"cpu": 36.0, "memory": 61.0, "disk_io": 0.3},
        }
        result = ev.evaluate("pos-1", "2026-03-15 10:00:00", forecasts)

        assert result.model_ready is True
        assert len(result.horizons) == 2
        for h in result.horizons:
            assert h.severity == "normal"
            assert h.ecod_score < 0.7

    def test_abnormal_prediction_scores_high(self, ev):
        """이상 범위 예측값은 높은 점수."""
        feed_buffer(ev, "pos-2", make_normal_data(200))
        ev._train_model("pos-2")

        # 정상 범위(CPU 20-50)에서 크게 벗어난 값
        forecasts = {
            60: {"cpu": 95.0, "memory": 95.0, "disk_io": 5.0},
        }
        result = ev.evaluate("pos-2", "2026-03-15 10:00:00", forecasts)

        assert result.model_ready is True
        h = result.horizons[0]
        assert h.ecod_score > 0.7
        assert h.severity in ("warning", "critical")

    def test_fallback_when_no_model(self, ev):
        """모델 없으면 고정 임계값 fallback."""
        forecasts = {
            60: {"cpu": 85.0, "memory": 70.0, "disk_io": 0.3},
        }
        result = ev.evaluate("unknown-pos", "2026-03-15 10:00:00", forecasts)

        assert result.model_ready is False
        assert result.data_source == "none"
        assert len(result.horizons) == 1
        # CPU 85 >= 80 → warning in fallback mode
        assert result.horizons[0].severity == "warning"

    def test_empty_forecasts_returns_empty(self, ev):
        """예측 데이터 없으면 빈 결과."""
        result = ev.evaluate("pos-3", "2026-03-15 10:00:00", {})
        assert len(result.horizons) == 0

    def test_all_horizons_evaluated(self, ev):
        """모든 horizon이 평가됨."""
        feed_buffer(ev, "pos-4", make_normal_data(200))
        ev._train_model("pos-4")

        forecasts = {
            60: {"cpu": 40, "memory": 65, "disk_io": 0.3},
            360: {"cpu": 45, "memory": 68, "disk_io": 0.4},
            720: {"cpu": 50, "memory": 70, "disk_io": 0.5},
            1440: {"cpu": 55, "memory": 72, "disk_io": 0.6},
            2880: {"cpu": 60, "memory": 75, "disk_io": 0.7},
        }
        result = ev.evaluate("pos-4", "2026-03-15 10:00:00", forecasts)

        assert len(result.horizons) == 5
        horizon_mins = [h.horizon_min for h in result.horizons]
        assert horizon_mins == [60, 360, 720, 1440, 2880]

    def test_overall_severity_is_worst(self, ev):
        """overall_severity는 가장 나쁜 horizon의 severity."""
        feed_buffer(ev, "pos-5", make_normal_data(200))
        ev._train_model("pos-5")

        forecasts = {
            60: {"cpu": 35, "memory": 60, "disk_io": 0.3},   # normal
            2880: {"cpu": 95, "memory": 95, "disk_io": 5.0},  # critical
        }
        result = ev.evaluate("pos-5", "2026-03-15 10:00:00", forecasts)
        assert result.overall_severity in ("warning", "critical")


# ── 3. Event State ─────────────────────────────────────────────────────────────


class TestEventState:

    def test_dongle_disconnect_updates_state(self, ev):
        """동글 끊김 이벤트가 상태에 반영."""
        ev.update_event("pos-e1", {
            "Logs": [{
                "BodyType": "주변장치 체크",
                "KeyValues": {"동글이": "실패", "스캐너-핸드스캐너": "연결"},
            }]
        })
        state = ev.event_states["pos-e1"]
        assert state.dongle == 0
        assert state.scanner_hand == 1

    def test_payment_time_updates(self, ev):
        """결제 소요 시간 이벤트가 상태에 반영."""
        ev.update_event("pos-e2", {
            "Logs": [{
                "BodyType": "결제",
                "KeyValues": {"소요시간": 8.5, "결과": "성공"},
            }]
        })
        state = ev.event_states["pos-e2"]
        assert state.payment_time_sec == 8.5

    def test_rule_score_increases_with_failures(self, ev):
        """주변장치 장애가 많을수록 rule_score 증가."""
        ev.update_event("pos-e3", {
            "Logs": [{
                "BodyType": "주변장치 체크",
                "KeyValues": {
                    "동글이": "실패",
                    "스캐너-핸드스캐너": "실패",
                    "스캐너-2D스캐너": "실패",
                },
            }]
        })
        score = ev._compute_rule_score("pos-e3")
        assert score >= 0.7  # Multiple failures → high score

    def test_no_events_returns_zero_rule_score(self, ev):
        """이벤트 데이터 없으면 rule_score = 0."""
        assert ev._compute_rule_score("nonexistent") == 0.0


# ── 4. Serialization ──────────────────────────────────────────────────────────


class TestSerialization:

    def test_to_dict_structure(self, ev):
        """to_dict 결과가 올바른 구조."""
        feed_buffer(ev, "pos-s1", make_normal_data(200))
        ev._train_model("pos-s1")

        forecasts = {60: {"cpu": 40, "memory": 65, "disk_io": 0.3}}
        result = ev.evaluate("pos-s1", "2026-03-15 10:00:00", forecasts)
        d = ev.to_dict(result)

        assert d["type"] == "forecast_evaluation"
        assert d["agent_id"] == "pos-s1"
        assert "overall_severity" in d
        assert "model_ready" in d
        assert len(d["horizons"]) == 1

        h = d["horizons"][0]
        assert "horizon_min" in h
        assert "horizon_label" in h
        assert "pred_cpu" in h
        assert "ecod_score" in h
        assert "final_score" in h
        assert "severity" in h
        assert "reliability" in h

    def test_horizon_label_format(self, ev):
        """horizon_label이 한국어로 올바르게 포맷."""
        assert ev._horizon_label(60) == "1시간 후"
        assert ev._horizon_label(360) == "6시간 후"
        assert ev._horizon_label(1440) == "1일 후"
        assert ev._horizon_label(2880) == "2일 후"
        assert ev._horizon_label(30) == "30분 후"


# ── 5. Score Normalization ────────────────────────────────────────────────────


class TestScoreNormalization:

    def test_normal_value_normalizes_low(self, ev):
        """정상값은 정규화 후 낮은 점수."""
        feed_buffer(ev, "pos-n1", make_normal_data(200))
        ev._train_model("pos-n1")

        model = ev.models["pos-n1"]
        normal_point = np.array([[35, 60, 0.3]])
        raw = float(model.decision_function(normal_point)[0])
        normalized = ev._normalize_score("pos-n1", raw)
        assert normalized < 0.7

    def test_extreme_value_normalizes_high(self, ev):
        """극단값은 정규화 후 높은 점수."""
        feed_buffer(ev, "pos-n2", make_normal_data(200))
        ev._train_model("pos-n2")

        model = ev.models["pos-n2"]
        extreme_point = np.array([[99, 99, 10.0]])
        raw = float(model.decision_function(extreme_point)[0])
        normalized = ev._normalize_score("pos-n2", raw)
        assert normalized > 0.8
