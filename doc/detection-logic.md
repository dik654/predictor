# PulseAI 이상 탐지 로직 정리

## 전체 구조

데이터 수신(5초 간격) → ECOD(10초 간격) + ARIMA(60초 간격) → Ensemble → InfluxDB 저장 → 대시보드 표시

```
C# Agent → WebRTC DataChannel → Python Server
                                    ├─ ECOD (다변량 이상탐지)
                                    ├─ ARIMA (시계열 예측)
                                    ├─ Ensemble (앙상블)
                                    ├─ Peripheral (주변장치 변화 감지)
                                    └─ InfluxDB → React 대시보드
```

---

## 1. ECOD (Empirical Cumulative Distribution-based Outlier Detection)

14개 차원을 동시에 분석하는 다변량 이상탐지.

### 입력 데이터 (14차원)

| 구분 | 메트릭 | 타입 | 설명 |
|------|--------|------|------|
| 시스템 | CPU | 연속 (0~100%) | CPU 사용률 |
| 시스템 | Memory | 연속 (0~100%) | 메모리 사용률 |
| 시스템 | DiskIO | 연속 | 디스크 I/O |
| 네트워크 | NetworkSent | 연속 (bytes) | 네트워크 송신량 |
| 네트워크 | NetworkRecv | 연속 (bytes) | 네트워크 수신량 |
| 프로세스 | Process | 이진 (0/1) | POS 프로세스 실행 여부 |
| 주변장치 | Dongle | 이진 (0/1) | 보안 동글 연결 여부 |
| 주변장치 | HandScanner | 이진 (0/1) | 핸드스캐너 연결 여부 |
| 주변장치 | PassportReader | 이진 (0/1) | 여권리더기 연결 여부 |
| 주변장치 | 2DScanner | 이진 (0/1) | 2D스캐너 연결 여부 |
| 주변장치 | PhoneCharger | 이진 (0/1) | 폰충전기 연결 여부 |
| 주변장치 | Keyboard | 이진 (0/1) | 키보드 연결 여부 |
| 주변장치 | MSR | 이진 (0/1) | 카드리더기 연결 여부 |
| 상태 | POS_Idle | 이진 (0/1) | POS 유휴 상태 여부 |

### Multivariate Score (종합 점수)

ECOD 모델이 14차원 데이터 전체를 학습한 뒤, 최신 데이터 포인트의 이상 정도를 산출.

- **Score**: 0~1 정규화. `(raw_score - min) / (max - min)`. 학습 데이터 전체에서 이 포인트가 얼마나 극단적인지.
- **Outlier 판정**: ECOD 모델의 `predict()` 결과 (contamination 비율 기반)
- **심각도**:
  - outlier이고 Score > 0.9 → **critical** (신뢰도 0.9)
  - outlier이고 Score > 0.7 → **warning** (신뢰도 0.7)
  - outlier이고 Score ≤ 0.7 → **warning** (신뢰도 0.5)
  - outlier 아님 → **normal** (신뢰도 = 1.0 - score)

### Per-metric Score (개별 메트릭 점수)

#### 연속 메트릭 (CPU, Memory, DiskIO, Network)

- **Score**: 백분위(percentile) 기반. 학습 데이터에서 현재값보다 작은 값의 비율.
  - 예: CPU=85%가 학습 데이터 60개 중 58번째 → Score = 58/60 = 0.97
  - 0 = 가장 낮은 값, 1 = 가장 높은 값
- **심각도**: 절대 임계값 기반
  - CPU ≥ 90% 또는 Memory ≥ 95% → **critical**
  - CPU ≥ 80% 또는 Memory ≥ 85% 또는 Score ≥ 0.95 → **warning**
  - 그 외 → **normal**

| 메트릭 | Warning 임계값 | Critical 임계값 |
|--------|---------------|----------------|
| CPU | 80% | 90% |
| Memory | 85% | 95% |
| DiskIO | 70 | 85 |
| NetworkSent | 50,000 bytes | 100,000 bytes |
| NetworkRecv | 50,000 bytes | 100,000 bytes |

#### 이진 메트릭 (Process, 주변장치)

백분위 계산이 무의미하므로 **상태 변화 기반**으로 판정.

- 학습 데이터의 중앙값(median)을 "평소 상태"로 간주
- **평소 켜져있다가(median=1) 꺼짐(value=0)** → Score=1.0, **critical**. 장비 이탈 의심
- **평소 꺼져있다가(median=0) 켜짐(value=1)** → Score=0.3, **normal**. 복구됨
- **상태 변화 없음** → Score=0.0, **normal**
- **POS_Idle**: 유휴/사용중 표시만, 이상 판정 안 함 (항상 normal)

> 미사용/미연결 장비: 평소부터 꺼져있으면(median=0) "평소 상태"로 인식. 계속 꺼져있어도 Score=0, normal. 평소 연결된 장비가 갑자기 끊길 때만 이상으로 잡음.

### 신뢰도 (Confidence)

학습 데이터 양에 따라 결정:

| 학습 데이터 | 신뢰도 |
|------------|--------|
| 20건 미만 | 0.4 (낮음) |
| 20~60건 | 0.7 (보통) |
| 60건 이상 | 0.9 (높음) |

---

## 2. ARIMA (AutoARIMA 시계열 예측)

5개 연속 메트릭(CPU, Memory, DiskIO, NetworkSent, NetworkRecv)에 대해 미래값 예측 및 현재값과의 괴리 탐지.

### 현재값 이상 탐지

- AutoARIMA 모델로 다음 값 예측 → 실제값과의 차이(residual) 계산
- **Threshold**: `2.5 × std(과거 residual 이력)` (적응형, 최소 0.1)
- **Score**: `residual / threshold`
- **심각도**:
  - residual > threshold × 1.5 → **critical** (신뢰도: min(0.95, score/2))
  - residual > threshold → **warning** (신뢰도: min(0.8, score/2))
  - 그 외 → **normal** (신뢰도: 1.0 - score)

### 미래 예측 (Forecast Horizon)

여러 시간대의 미래값을 예측하고 임계값 초과 시 경고:

| Horizon | 의미 |
|---------|------|
| 30분 | 30분 후 예측값 |
| 60분 | 1시간 후 |
| 360분 | 6시간 후 |
| 720분 | 12시간 후 |
| 1440분 | 24시간 후 |
| 2880분 | 48시간 후 |

각 예측값에 대해:
- 예측값 ≥ critical 임계값 → **critical**
- 예측값 ≥ warning 임계값 → **warning**
- 그 외 → **normal**

---

## 3. Ensemble (앙상블)

ECOD와 ARIMA 결과를 가중 평균으로 합산.

- **조건**: ECOD와 ARIMA 결과가 둘 다 있을 때만 생성
- **계산**: `Score = ECOD평균 × 0.6 + ARIMA평균 × 0.4`
  - 각 평균 = 해당 엔진 detection들의 `score × confidence` 평균
- **심각도**:
  - Score > 0.8 → **critical**
  - Score > 0.5 → **warning**
  - 그 외 → **normal**
- **신뢰도**: Score > 0.7 → 0.9, 그 외 → 0.7

---

## 4. Peripheral (주변장치 변화 감지)

ECOD와 별개로, 주변장치 상태 변화를 직접 감지하는 별도 엔진.

- 주변장치 상태가 변할 때(1→0 또는 0→1) 이벤트 기록
- 연속 실패 3회 이상부터 로그 출력, 이후 100회 단위

---

## 5. Health Score (건강 점수)

모든 detection 결과를 종합한 0~100 점수.

- 시작값: 100
- critical detection 1건당: `-20 × confidence`
- warning detection 1건당: `-10 × confidence`
- 최소 0, 최대 100

---

## 실행 주기

| 엔진 | 주기 | 비고 |
|------|------|------|
| 데이터 수신 | 5초 | C# Agent → WebRTC |
| ECOD | 10초 | 다변량 14차원 분석 |
| ARIMA | 60초 | 5개 메트릭 시계열 예측 |
| Ensemble | ECOD+ARIMA 동시 실행 시 | 가중 평균 |
| Peripheral | 매 수신마다 | 상태 변화 감지 |

---

## 대시보드 표시

| 항목 | 데이터 소스 | 설명 |
|------|------------|------|
| ECOD 차트 | anomaly_detection (engine=ecod) | 종합 + 개별 메트릭 Score 추이 |
| 시스템 상태 카드 | 최근 ECOD 결과 | CPU/Memory/DiskIO 상태 + 이진 메트릭 상태 |
| ARIMA 차트 | anomaly_detection (engine=arima) | 예측값 vs 실제값 |
| 예측 경보 | ARIMA forecast_horizon | 미래 위험 예측 알림 |
| 주변장치 카드 | peripheral_status | 장비별 연결 상태 |
