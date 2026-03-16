# PulseAI Lite Backend

**ECOD + AutoARIMA 기반 POS 예지 장애 탐지 시스템**

---

## 1. 문제 정의 (Problem)

편의점 POS 단말은 다음과 같은 시계열 시스템 지표를 지속적으로 생성합니다.

- CPU 사용률
- Memory 사용률
- Disk I/O
- Network TX/RX
- Process 상태

하지만 실제 장애는:

- 갑작스러운 spike
- 서서히 증가하는 메모리 누수
- 계절성/시간대 패턴 붕괴
- 복합 지표 동시 이상

즉, 단순 threshold 방식으로는 조기 탐지가 어렵습니다.

---

## 2. 솔루션 전략

PulseAI는 **이상 감지(Detection)** 와 **예측 기반 탐지(Forecast-based Anomaly)** 를 결합합니다.

### 두 가지 핵심 엔진

#### ① ECOD (실시간 이상 감지)

- 분포 기반 비지도 알고리즘
- 튜닝 거의 없음
- 엣지 장비에서 실행 가능
- 1차 이상 의심 탐지

> "지금 이상한가?"

#### ② AutoARIMA (미래 예측 기반 이상 탐지)

- 정상 패턴을 학습
- 미래를 예측
- 실제값과 예측값의 오차 분석

> "앞으로 이상해질 가능성이 있는가?"

---

## 3. 전체 아키텍처

```
[POS Edge]
   ├── ECOD (실시간 분포 기반 이상 감지)
   └── Feature Aggregation (15s → window)

        ↓

[Central / Gateway]
   └── AutoARIMA (예측 기반 이상 판단)
```

---

## 4. 동작 방식

### Step 1 — ECOD

- 최근 60~90분 정상 분포 기반
- 상위 98 percentile 이상값 탐지
- sustain 조건 적용 (6/10 rule)

→ 즉각적 이상 알림

### Step 2 — AutoARIMA

- CPU / Memory / Disk 시계열 학습
- 계절성/추세 자동 탐색
- 미래 N-step forecast
- 잔차(residual) 기반 이상 판단

**이상 조건:**

```
|Actual - Forecast| > k × σ(residual)
```

→ 서서히 악화되는 메모리 누수 탐지 가능

---

## 5. 왜 이 조합인가?

### ECOD 장점

- 가볍다
- 라벨 필요 없다
- 엣지 적합

### AutoARIMA 장점

- 통계적으로 해석 가능
- 딥러닝 없이도 충분히 강력
- GPU 필요 없음
- 중앙 서버 비용 최소화

---

## 6. 차별점

| 구분 | 방식 |
|------|------|
| 기존 방식 | Threshold 기반 모니터링 |
| PulseAI | 분포 기반 실시간 이상 감지 + 예측 기반 미래 이상 탐지 |

**Reactive + Predictive 동시 수행**

---

## 7. 기대 효과

- 장애 10~30분 사전 탐지
- 메모리 누수 조기 경보
- 점포별 건강 점수 산출
- 다운타임 감소
- 유지보수 비용 절감

---

## 8. 기술 스택

| 구분 | 기술 |
|------|------|
| Language | Python |
| API | FastAPI |
| 이상 감지 | PyOD (ECOD) |
| 시계열 예측 | StatsForecast (AutoARIMA) |
| 에이전트 | POS Agent |

---

> **한 줄 요약:** PulseAI는 ECOD로 현재 이상을 감지하고, AutoARIMA로 미래 이상을 예측하는 경량 예지 정비 시스템이다.
