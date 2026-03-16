# PulseAI Lite - 이상 탐지 엔진 가이드

## 개요

PulseAI Lite는 POS 장비의 예지 장애 탐지를 위해 두 가지 이상 탐지 알고리즘을 사용합니다:

1. **ECOD** (Empirical Cumulative Distribution Functions for Outlier Detection)
2. **AutoARIMA** (Auto Regressive Integrated Moving Average)

---

## 1. ECOD (실시간 이상 탐지)

### 1.1 알고리즘 설명

ECOD는 PyOD 라이브러리의 비지도 학습 기반 이상 탐지 알고리즘입니다.

**핵심 원리:**
- 각 feature의 경험적 누적 분포 함수(ECDF)를 계산
- 데이터 포인트가 분포의 극단(tail)에 위치할수록 높은 이상 점수 부여
- 다변량 데이터에서 각 차원의 tail probability를 결합하여 최종 점수 산출

**장점:**
- 학습 데이터에 라벨이 필요 없음 (비지도 학습)
- 계산이 빠름 (O(n log n))
- 해석 가능한 결과 (어떤 feature가 이상인지 파악 가능)

### 1.2 현재 구현

```python
# server.py
ECOD_INTERVAL = 10.0  # 10초마다 실행

# detector.py
ECOD_CONTAMINATION = 0.02  # 상위 2%를 이상치로 간주
WINDOW_SIZE = 60  # 최근 60개 데이터 포인트 사용
```

**적용 메트릭:**
- CPU 사용률 (%)
- Memory 사용률 (%)
- Disk I/O (%)

**실행 조건:**
- 버퍼에 최소 20개 이상의 데이터 포인트가 있을 때
- 마지막 실행으로부터 10초 이상 경과했을 때

### 1.3 출력 형식

```json
{
  "engine": "ecod",
  "metric": "CPU",
  "value": 16.1,
  "score": 0.847,
  "threshold": 15.2,
  "severity": "normal" | "warning" | "critical"
}
```

**severity 기준:**
- `normal`: 이상치로 판정되지 않음
- `warning`: 이상치 + score ≤ 0.9
- `critical`: 이상치 + score > 0.9

---

## 2. AutoARIMA (예측 기반 이상 탐지)

### 2.1 알고리즘 설명

AutoARIMA는 시계열 예측 모델로, 과거 패턴을 학습하여 미래 값을 예측합니다.

**핵심 원리:**
- ARIMA(p, d, q) 모델의 최적 파라미터를 자동으로 찾음
- 예측값과 실제값의 차이(잔차)를 계산
- 잔차가 k×σ (표준편차)를 초과하면 이상으로 판정

**장점:**
- 시계열의 트렌드와 계절성을 반영
- 예측값을 제공하여 "정상 범위"를 알 수 있음
- 점진적 변화도 탐지 가능

### 2.2 현재 구현

```python
# server.py
ARIMA_INTERVAL = 60.0  # 60초마다 실행

# detector.py
ARIMA_HORIZON = 6      # 6스텝(30초) 앞 예측
ARIMA_RESIDUAL_K = 2.5 # 잔차 임계값 = 2.5 * σ
```

**적용 메트릭:**
- CPU 사용률
- Memory 사용률

**실행 조건:**
- 버퍼에 최소 30개 이상의 데이터 포인트가 있을 때
- 마지막 실행으로부터 60초 이상 경과했을 때

### 2.3 출력 형식

```json
{
  "engine": "arima",
  "metric": "CPU",
  "value": 16.1,
  "score": 0.523,
  "threshold": 2.34,
  "forecast": 15.2,
  "residual": 0.9,
  "severity": "normal" | "warning" | "critical"
}
```

**severity 기준:**
- `normal`: residual ≤ threshold
- `warning`: residual > threshold && residual ≤ threshold × 1.5
- `critical`: residual > threshold × 1.5

---

## 3. 데이터 흐름

```
[POS 장비] → [WebRTC DataChannel] → [서버]
                                       ↓
                              [MetricBuffer] (WINDOW_SIZE=60)
                                       ↓
                    ┌──────────────────┴──────────────────┐
                    ↓                                      ↓
              [ECOD 10초마다]                      [AutoARIMA 60초마다]
                    ↓                                      ↓
              [anomaly score]                     [forecast, residual]
                    └──────────────────┬──────────────────┘
                                       ↓
                              [WebSocket broadcast]
                                       ↓
                              [Dashboard 시각화]
```

---

## 4. 현재 한계점 및 개선 방안

### 4.1 ECOD 개선

| 현재 한계 | 개선 방안 |
|-----------|-----------|
| 단변량 분석 (메트릭별 독립) | 다변량 ECOD로 메트릭 간 상관관계 고려 |
| 고정 contamination (2%) | 동적 contamination 또는 adaptive threshold |
| 시간 패턴 미반영 | 시간대별 기준선(baseline) 적용 |

**구현 제안: 다변량 ECOD**
```python
# 현재: 메트릭별 독립 분석
for metric in ['CPU', 'Memory', 'DiskIO']:
    ecod.fit(metric_data)

# 개선: 다변량 동시 분석
combined = np.column_stack([cpu, memory, disk_io])
ecod.fit(combined)  # 메트릭 간 상관관계 반영
```

### 4.2 AutoARIMA 개선

| 현재 한계 | 개선 방안 |
|-----------|-----------|
| 매번 전체 재학습 (느림) | 온라인 학습 또는 모델 캐싱 |
| 단기 예측만 (6스텝) | 장기 예측 + 트렌드 분석 |
| 계절성 고정 (season_length=12) | 자동 계절성 탐지 |

**구현 제안: 모델 캐싱**
```python
# 현재: 매 실행마다 새로 학습
sf = StatsForecast(models=[AutoARIMA()])
sf.fit(df)

# 개선: 모델 재사용
if agent_id not in self.arima_models:
    self.arima_models[agent_id] = {}
    
if metric not in self.arima_models[agent_id]:
    # 첫 학습
    sf.fit(df)
    self.arima_models[agent_id][metric] = sf
else:
    # 기존 모델로 예측만
    sf = self.arima_models[agent_id][metric]
```

### 4.3 하이브리드 접근

**앙상블 탐지:**
```python
def ensemble_detect(data):
    ecod_score = run_ecod(data)
    arima_residual = run_arima(data)
    
    # 가중 평균
    combined_score = 0.6 * ecod_score + 0.4 * normalize(arima_residual)
    
    # 둘 다 이상이면 신뢰도 높음
    if ecod_score > threshold and arima_residual > threshold:
        return "high_confidence_anomaly"
```

---

## 5. 권장 설정

### 5.1 실시간 모니터링 (현재)

```python
ECOD_INTERVAL = 10   # 빠른 탐지
ARIMA_INTERVAL = 60  # 예측 품질 유지
WINDOW_SIZE = 60     # 5분 히스토리
```

### 5.2 고정밀 탐지 (느리지만 정확)

```python
ECOD_INTERVAL = 30
ARIMA_INTERVAL = 120
WINDOW_SIZE = 120    # 10분 히스토리
ECOD_CONTAMINATION = 0.01  # 상위 1%만 이상
```

### 5.3 빠른 반응 (민감)

```python
ECOD_INTERVAL = 5
ARIMA_INTERVAL = 30
WINDOW_SIZE = 30     # 2.5분 히스토리
ECOD_CONTAMINATION = 0.05  # 상위 5% 이상
```

---

## 6. 추가 고려사항

### 6.1 주변장치 로그 분석

현재 POS 데이터에는 주변장치 체크 로그가 포함되어 있습니다:
```json
{
  "Logs": [
    {"BodyType": "주변장치 체크", "KeyValues": {"동글이": "연결", "스캐너-2D스캐너": "실패"}}
  ]
}
```

**활용 방안:**
- 주변장치 상태를 categorical feature로 변환
- "실패" 빈도가 높아지면 경고
- 특정 장치의 연속 실패 패턴 탐지

### 6.2 네트워크 메트릭

현재 Network Sent/Recv 데이터도 수집 중:
```json
{"Network": {"Sent": 176557, "Recv": 8932}}
```

**활용 방안:**
- 비정상적인 트래픽 급증 탐지
- Sent/Recv 비율 모니터링
- 네트워크 지연과 다른 메트릭의 상관관계 분석

---

## 7. 참고 자료

- [ECOD Paper](https://arxiv.org/abs/2201.00382) - ECOD: Unsupervised Outlier Detection Using Empirical Cumulative Distribution Functions
- [PyOD Documentation](https://pyod.readthedocs.io/)
- [StatsForecast Documentation](https://nixtla.github.io/statsforecast/)
- [AutoARIMA Explained](https://otexts.com/fpp3/arima-r.html)

---

*문서 작성: 2026-02-20*
*버전: PulseAI Lite v0.2.0*
