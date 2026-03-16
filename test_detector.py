"""
TDD: EnhancedAnomalyDetector - real-world interval scenarios

Run from project root (server venv):
  cd server && uv run pytest ../test_detector.py -v

Scenarios covered:
  1. _get_interval_seconds  — 정상/지터/패킷 누락/재연결/샘플모드 fallback
  2. _run_cached_arima      — 최소 샘플, 다양한 간격, freq 변경 시 재학습
  3. detect() pipeline      — 5s/10min/20min, 샘플모드, CPU 스파이크, 재연결, 간격 중간 변경
  4. batch_arima_forecast   — 데이터 부족, 다양한 간격, sub-interval extrapolation
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "server"))

import numpy as np
import pytest
from datetime import datetime, timedelta

from webrtc_hub.detector import (
    EnhancedAnomalyDetector,
    batch_arima_forecast,
    MIN_SAMPLES_ARIMA,
    MIN_SAMPLES_ECOD,
)

np.random.seed(42)


# ── helpers ────────────────────────────────────────────────────────────────────


def make_record(agent_id: str, ts, cpu=50.0, memory=60.0, disk_io=0.5) -> dict:
    ts_str = ts.strftime("%Y-%m-%d %H:%M:%S") if isinstance(ts, datetime) else ts
    return {
        "AgentId": agent_id,
        "Timestamp": ts_str,
        "CPU": float(cpu),
        "Memory": float(memory),
        "DiskIO": float(disk_io),
        "Network": {"Sent": 1000, "Recv": 500},
    }


def make_sequence(
    agent_id: str,
    n: int,
    interval_sec: float,
    start: datetime = None,
    cpu_fn=None,
) -> list:
    """n개의 레코드를 interval_sec 간격으로 생성."""
    start = start or datetime(2026, 3, 15, 10, 0, 0)
    return [
        make_record(
            agent_id,
            start + timedelta(seconds=i * interval_sec),
            cpu=cpu_fn(i) if cpu_fn else 50.0 + np.sin(i * 0.3) * 5,
        )
        for i in range(n)
    ]


def feed(det: EnhancedAnomalyDetector, records: list) -> None:
    for r in records:
        det._update_buffer(r["AgentId"], r)


# ── fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def det():
    return EnhancedAnomalyDetector()


# ── 1. _get_interval_seconds ───────────────────────────────────────────────────


class TestGetIntervalSeconds:

    def test_default_no_buffer(self, det):
        """버퍼 없으면 기본값 5.0."""
        assert det._get_interval_seconds("unknown") == 5.0

    def test_single_entry_fallback(self, det):
        """타임스탬프 1개 → 비교 불가 → 5.0."""
        det._update_buffer("a", make_record("a", datetime(2026, 3, 15, 10, 0, 0)))
        assert det._get_interval_seconds("a") == 5.0

    @pytest.mark.parametrize("interval_sec,expected", [
        (5,    5.0),
        (60,   60.0),
        (600,  600.0),
        (1200, 1200.0),
    ])
    def test_regular_interval(self, det, interval_sec, expected):
        """일정 간격 데이터는 10% 오차 내로 감지."""
        agent = f"regular-{interval_sec}"
        feed(det, make_sequence(agent, 12, interval_sec))
        result = det._get_interval_seconds(agent)
        assert abs(result - expected) <= expected * 0.1

    def test_jitter_around_5s(self, det):
        """±2s 지터가 있어도 ~5s로 감지 (3.5~6.5s 허용)."""
        agent = "jitter"
        base = datetime(2026, 3, 15, 10, 0, 0)
        offsets = [0, 4, 9, 13, 18, 23, 27, 32, 37, 42]
        for off in offsets:
            det._update_buffer(agent, make_record(agent, base + timedelta(seconds=off)))
        assert 3.5 < det._get_interval_seconds(agent) < 6.5

    def test_one_missing_packet_median_stable(self, det):
        """패킷 1개 누락(10s gap, 나머지 5s) → median은 5s 유지."""
        agent = "drop"
        base = datetime(2026, 3, 15, 10, 0, 0)
        offsets = [0, 5, 10, 20, 25, 30, 35, 40, 45, 50]  # t=15 누락
        for off in offsets:
            det._update_buffer(agent, make_record(agent, base + timedelta(seconds=off)))
        assert abs(det._get_interval_seconds(agent) - 5.0) < 2.0

    def test_sub_second_sample_mode_fallback(self, det):
        """샘플 replay 모드: 타임스탬프 전부 동일 → sub-second → fallback 5.0."""
        agent = "sample-mode"
        ts = "2026-03-15 10:00:00"
        for _ in range(10):
            det._update_buffer(agent, make_record(agent, ts))
        assert det._get_interval_seconds(agent) == 5.0

    def test_reconnect_large_gap_median_stable(self, det):
        """30분 오프라인 후 재연결: 9구간 중 1개만 1800s, 나머지 5s → median=5s."""
        agent = "reconnect"
        base = datetime(2026, 3, 15, 10, 0, 0)
        pre  = [base + timedelta(seconds=i * 5) for i in range(5)]
        post = [base + timedelta(minutes=30) + timedelta(seconds=i * 5) for i in range(5)]
        for ts in pre + post:
            det._update_buffer(agent, make_record(agent, ts))
        assert abs(det._get_interval_seconds(agent) - 5.0) < 2.0


# ── 2. _run_cached_arima ───────────────────────────────────────────────────────


class TestRunCachedArima:

    def test_returns_none_below_min_samples(self, det):
        """MIN_SAMPLES_ARIMA 미만이면 None 반환."""
        agent = "few"
        feed(det, make_sequence(agent, MIN_SAMPLES_ARIMA - 1, 5))
        result = det._run_cached_arima(agent, "CPU", det.buffers[agent].cpu)
        assert result is None

    def test_runs_with_enough_samples(self, det):
        """충분한 샘플 → AnomalyResult 반환, forecast_horizon 포함."""
        agent = "enough"
        feed(det, make_sequence(agent, MIN_SAMPLES_ARIMA + 5, 5))
        result = det._run_cached_arima(agent, "CPU", det.buffers[agent].cpu)
        assert result is not None
        assert result.engine == "arima"
        assert result.forecast_horizon is not None

    @pytest.mark.parametrize("interval_sec", [5, 60, 600, 1200])
    def test_forecast_horizon_always_in_target_minutes(self, det, interval_sec):
        """간격과 무관하게 forecast_horizon의 minutes는 항상 [60,360,720,1440,2880]."""
        agent = f"horizon-{interval_sec}"
        feed(det, make_sequence(agent, MIN_SAMPLES_ARIMA + 5, interval_sec))
        result = det._run_cached_arima(agent, "CPU", det.buffers[agent].cpu)
        assert result is not None
        minutes = [h["minutes"] for h in result.forecast_horizon]
        assert minutes == [60, 360, 720, 1440, 2880]

    def test_freq_change_triggers_retrain(self, det):
        """
        데이터 간격이 5s → 10min으로 바뀌면 모델을 재학습해야 한다.
        arima_models에 {metric}__freq 키로 현재 freq를 저장하고
        변경 시 retrain이 트리거되어야 함.
        """
        agent = "freq-change"

        # Phase 1: 5s 데이터
        feed(det, make_sequence(agent, MIN_SAMPLES_ARIMA + 5, 5,
                                start=datetime(2026, 3, 15, 8, 0, 0)))
        det._run_cached_arima(agent, "CPU", det.buffers[agent].cpu)
        assert det.arima_models[agent].get("CPU__freq") == "5s"

        # Phase 2: 버퍼 초기화 후 10min 데이터
        det.buffers[agent].timestamps.clear()
        det.buffers[agent].cpu.clear()
        feed(det, make_sequence(agent, MIN_SAMPLES_ARIMA + 5, 600,
                                start=datetime(2026, 3, 15, 10, 0, 0)))
        det._run_cached_arima(agent, "CPU", det.buffers[agent].cpu)
        assert det.arima_models[agent].get("CPU__freq") == "600s"

    def test_same_freq_does_not_retrain_unnecessarily(self, det):
        """
        freq가 같으면 100포인트마다 외에 불필요한 재학습 없어야 함.
        (캐시 키가 존재하고 같은 freq면 need_retrain=False)
        """
        agent = "no-retrain"
        feed(det, make_sequence(agent, MIN_SAMPLES_ARIMA + 5, 5))
        # 첫 호출: 학습
        r1 = det._run_cached_arima(agent, "CPU", det.buffers[agent].cpu)
        # 두 번째 호출: freq 동일, 100 배수 아님 → 캐시 사용 (크래시 없이 동작)
        r2 = det._run_cached_arima(agent, "CPU", det.buffers[agent].cpu)
        assert r1 is not None
        assert r2 is not None


# ── 3. Full detect() pipeline ──────────────────────────────────────────────────


class TestDetectorPipeline:

    def test_ecod_and_arima_both_run_with_35_records_5s(self, det):
        """5s 간격 35개 → ECOD와 ARIMA 모두 실행됨."""
        agent = "full-5s"
        ecod_ran = arima_ran = False
        for r in make_sequence(agent, 35, 5):
            result = det.detect(r)
            for d in result.detections:
                ecod_ran |= (d.engine == "ecod")
                arima_ran |= (d.engine == "arima")
        assert ecod_ran, "ECOD should have run"
        assert arima_ran, "ARIMA should have run"

    def test_arima_does_not_run_below_min_samples(self, det):
        """MIN_SAMPLES_ARIMA 미만: ARIMA는 실행되지 않아야 함."""
        agent = "below-min"
        arima_ran = False
        for r in make_sequence(agent, MIN_SAMPLES_ARIMA - 1, 5):
            for d in det.detect(r).detections:
                arima_ran |= (d.engine == "arima")
        assert not arima_ran

    @pytest.mark.parametrize("interval_sec", [5, 600, 1200])
    def test_pipeline_no_crash_various_intervals(self, det, interval_sec):
        """5s/10min/20min 간격 모두 크래시 없이 35개 처리."""
        agent = f"pipeline-{interval_sec}"
        completed = 0
        for r in make_sequence(agent, 35, interval_sec):
            det.detect(r)
            completed += 1
        assert completed == 35

    def test_sample_mode_all_same_timestamp_no_crash(self, det):
        """샘플 replay 모드: 타임스탬프 전부 동일 → sub-second fallback, 크래시 없음."""
        agent = "sample-ts"
        ts = "2026-03-15 10:00:00"
        completed = 0
        for i in range(35):
            det.detect(make_record(agent, ts, cpu=50.0 + i * 0.1))
            completed += 1
        assert completed == 35

    def test_agent_reconnect_after_30min_gap_no_crash(self, det):
        """30분 오프라인 후 재연결: 크래시 없이 계속 동작."""
        agent = "gap-agent"
        base = datetime(2026, 3, 15, 10, 0, 0)
        pre  = make_sequence(agent, 35, 5, start=base)
        post = make_sequence(agent, 10, 5, start=base + timedelta(minutes=30))
        for r in pre + post:
            det.detect(r)
        final = det.detect(make_record(agent, base + timedelta(minutes=31)))
        assert final is not None

    def test_cpu_spike_reduces_health_score(self, det):
        """베이스라인 30% 이후 95% 스파이크 → health_score 감소 또는 anomaly 감지."""
        agent = "spike"
        base = datetime(2026, 3, 15, 10, 0, 0)
        for r in make_sequence(agent, 30, 5, start=base, cpu_fn=lambda i: 30.0):
            det.detect(r)
        spike = make_record(agent, base + timedelta(seconds=150), cpu=95.0)
        result = det.detect(spike)
        assert result.health_score < 100 or any(
            d.severity in ("warning", "critical") for d in result.detections
        )

    def test_freq_change_mid_stream_no_crash(self, det):
        """35개 5s → 10개 10min 순서로 들어와도 크래시 없이 처리."""
        agent = "mid-change"
        base = datetime(2026, 3, 15, 8, 0, 0)
        phase1 = make_sequence(agent, 35, 5, start=base)
        phase2 = make_sequence(agent, 10, 600, start=base + timedelta(hours=1))
        for r in phase1 + phase2:
            det.detect(r)  # must not raise

    def test_multiple_agents_independent(self, det):
        """여러 AgentId가 섞여 들어와도 서로 간섭 없이 독립적으로 처리."""
        agents = ["agent-A", "agent-B", "agent-C"]
        base = datetime(2026, 3, 15, 10, 0, 0)
        records = []
        for ag in agents:
            records += make_sequence(ag, 35, 5, start=base)

        completed = {ag: 0 for ag in agents}
        for r in records:
            det.detect(r)
            completed[r["AgentId"]] += 1

        for ag in agents:
            assert completed[ag] == 35


# ── 4. batch_arima_forecast ────────────────────────────────────────────────────


class TestBatchArimaForecast:

    def test_error_on_insufficient_data(self):
        """MIN_SAMPLES_ARIMA 미만 데이터 → error 키 반환."""
        data = make_sequence("b", MIN_SAMPLES_ARIMA - 1, 5)
        result = batch_arima_forecast(data)
        assert "error" in result

    @pytest.mark.parametrize("interval_sec", [5, 600])
    def test_forecast_points_present(self, interval_sec):
        """5s / 10min 간격 모두 cpu/memory 4개 예측 포인트 반환."""
        data = make_sequence("b", 50, interval_sec,
                             cpu_fn=lambda i: 50 + 10 * np.sin(i * 0.4))
        result = batch_arima_forecast(data)
        assert len(result["cpu"]) == 4
        assert len(result["memory"]) == 4

    def test_forecast_minutes_keys_correct(self):
        """예측 포인트의 minutes 키: [10, 30, 60, 120]."""
        data = make_sequence("b", 50, 5,
                             cpu_fn=lambda i: 50 + 10 * np.sin(i * 0.4))
        result = batch_arima_forecast(data)
        minutes = [p["minutes"] for p in result["cpu"]]
        assert minutes == [10, 30, 60, 120]

    def test_severity_present_in_all_points(self):
        """모든 예측 포인트에 severity 키 존재."""
        data = make_sequence("b", 50, 5,
                             cpu_fn=lambda i: 85.0 + np.random.normal(0, 0.5))
        result = batch_arima_forecast(data)
        for point in result["cpu"]:
            assert "severity" in point
            assert point["severity"] in ("normal", "warning", "critical")

    def test_sub_interval_20min_no_crash_and_4_points(self):
        """
        20min 간격: 10min/30min이 sub-interval 또는 경계 → 모두 extrapolate 처리,
        크래시 없이 4개 포인트, 값은 0~100 범위 내.
        """
        data = make_sequence("b", 50, 1200,
                             cpu_fn=lambda i: 50 + 5 * np.sin(i * 0.3))
        result = batch_arima_forecast(data)
        assert len(result["cpu"]) == 4
        for point in result["cpu"]:
            assert 0.0 <= point["value"] <= 100.0

    def test_current_values_present(self):
        """current_cpu, current_memory 키 존재."""
        data = make_sequence("b", 50, 5)
        result = batch_arima_forecast(data)
        assert "current_cpu" in result
        assert "current_memory" in result
