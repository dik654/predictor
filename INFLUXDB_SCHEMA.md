# InfluxDB Schema — 관계형 DB 관점 매핑

## 개념 매핑

| InfluxDB         | RDBMS              | 이 프로젝트 값           |
|------------------|--------------------|-----------------------|
| Bucket           | Database / Schema  | `pos_metrics` (운영) / `sample_metrics` (샘플) |
| Measurement      | Table              | 아래 6개 테이블 참조     |
| Tag              | 인덱스된 컬럼        | WHERE 절 검색용         |
| Field            | 일반 컬럼           | 실제 값 저장용           |
| `_time`          | PRIMARY KEY        | 자동 생성 타임스탬프      |

---

## 테이블 목록

### 1. `metrics` — 원시 시스템 메트릭

POS 단말기에서 수집되는 실시간 시스템 리소스 데이터.

| 구분  | 컬럼명               | 타입     | 설명                        |
|-------|----------------------|----------|-----------------------------|
| Tag   | `agent_id`           | string   | POS 에이전트 ID (예: V135-POS-03) |
| Tag   | `store_code`         | string   | 매장 코드                    |
| Tag   | `store_name`         | string   | 매장 이름                    |
| Tag   | `region_code`        | string   | 지역 코드                    |
| Tag   | `region_name`        | string   | 지역 이름                    |
| Tag   | `pos_no`             | string   | POS 번호                    |
| Field | `cpu`                | float    | CPU 사용률 (%)               |
| Field | `memory`             | float    | 메모리 사용률 (%)             |
| Field | `disk_io`            | float    | 디스크 I/O                   |
| Field | `network_sent`       | int      | 네트워크 송신 바이트           |
| Field | `network_recv`       | int      | 네트워크 수신 바이트           |
| Field | `pos_process_status` | int      | POS 프로세스 상태 (1=RUNNING) |

---

### 2. `detection` — 이상 탐지 결과

ECOD / ARIMA / Ensemble 엔진의 이상 탐지 결과.

| 구분  | 컬럼명        | 타입     | 설명                                    |
|-------|--------------|----------|-----------------------------------------|
| Tag   | `agent_id`   | string   | POS 에이전트 ID                          |
| Tag   | `engine`     | string   | 탐지 엔진 (ecod / arima / ensemble / peripheral) |
| Tag   | `metric`     | string   | 메트릭 이름 (CPU / Memory / DiskIO 등)    |
| Tag   | `severity`   | string   | 심각도 (normal / warning / critical)      |
| Field | `value`      | float    | 현재 값                                  |
| Field | `score`      | float    | 이상 점수                                |
| Field | `threshold`  | float    | 임계값                                   |
| Field | `confidence` | float    | 신뢰도 (0~1)                             |
| Field | `forecast`   | float    | 예측값 (ARIMA 전용, nullable)             |
| Field | `residual`   | float    | 잔차 (ARIMA 전용, nullable)               |
| Field | `details`    | string   | 상세 설명 (nullable)                      |

---

### 3. `forecast` — ARIMA 예측값

AutoARIMA 모델이 생성한 미래 시점 예측.

| 구분  | 컬럼명             | 타입     | 설명                       |
|-------|--------------------|----------|----------------------------|
| Tag   | `agent_id`         | string   | POS 에이전트 ID             |
| Tag   | `metric`           | string   | 메트릭 이름                  |
| Tag   | `horizon_min`      | string   | 예측 시점 (분 단위, 예: "30") |
| Field | `predicted_value`  | float    | 예측된 값                    |
| Field | `horizon_minutes`  | int      | 예측 시점 (분, 정수)          |

---

### 4. `accuracy` — 예측 정확도 검증

예측값과 실제값을 비교한 정확도 측정.

| 구분  | 컬럼명            | 타입     | 설명                                |
|-------|-------------------|----------|-------------------------------------|
| Tag   | `agent_id`        | string   | POS 에이전트 ID                      |
| Tag   | `metric`          | string   | 메트릭 이름 (CPU / Memory)            |
| Tag   | `horizon_min`     | string   | 예측 시점 (분 단위)                   |
| Field | `actual_value`    | float    | 실제 측정값                           |
| Field | `forecast_value`  | float    | 예측했던 값                           |
| Field | `error_pct`       | float    | 오차율 (%)                           |
| Field | `within_3sigma`   | int      | 3σ 이내 여부 (1=이내, 0=초과)          |

---

### 5. `forecast_evaluation` — 예측 종합 평가

ARIMA 예측 + ECOD 이상 점수를 결합한 종합 위험 평가.

| 구분  | 컬럼명                  | 타입     | 설명                          |
|-------|------------------------|----------|-------------------------------|
| Tag   | `agent_id`             | string   | POS 에이전트 ID                |
| Tag   | `horizon_min`          | string   | 예측 시점 (분 단위)             |
| Field | `severity`             | string   | 해당 시점 심각도                |
| Field | `overall_severity`     | string   | 전체 종합 심각도                |
| Field | `pred_cpu`             | float    | CPU 예측값                     |
| Field | `pred_memory`          | float    | Memory 예측값                  |
| Field | `pred_disk_io`         | float    | DiskIO 예측값                  |
| Field | `ecod_score`           | float    | ECOD 이상 점수                 |
| Field | `rule_score`           | float    | 규칙 기반 점수                  |
| Field | `final_score`          | float    | 최종 종합 점수                  |
| Field | `reliability`          | float    | 예측 신뢰도                    |
| Field | `is_outlier`           | int      | 이상치 여부 (1/0)               |
| Field | `model_ready`          | int      | 모델 준비 여부 (1/0)            |
| Field | `data_source`          | string   | 데이터 소스                     |
| Field | `contrib_{metric}_pct` | float    | 메트릭별 기여도 (%)              |
| Field | `contrib_{metric}_score` | float  | 메트릭별 기여 점수               |

---

### 6. `peripheral_status` — 주변장치 상태

POS 주변장치(프린터, 바코드 스캐너 등) 연결 상태.

| 구분  | 컬럼명          | 타입     | 설명                                     |
|-------|----------------|----------|------------------------------------------|
| Tag   | `agent_id`     | string   | POS 에이전트 ID                           |
| Field | `{장치명}`      | int      | 상태 코드 (0=Disconnected, 1=OK, 2=Error) |
| Field | `{장치명}_raw`  | string   | 원본 상태 문자열                           |

---

## RDBMS와의 주요 차이점

| 항목            | RDBMS                      | InfluxDB                         |
|-----------------|----------------------------|----------------------------------|
| JOIN            | 지원 (INNER, LEFT, ...)     | 미지원 — 앱에서 합쳐야 함           |
| 스키마 정의      | `CREATE TABLE` 필수         | 데이터 쓰면 자동 생성               |
| 인덱스          | 수동 생성                    | Tag = 자동 인덱스                  |
| 기본 정렬       | 없음 (ORDER BY 필요)         | `_time` 기준 자동 정렬              |
| 데이터 보존      | 수동 관리                    | Retention Policy로 자동 만료       |
| 쿼리 언어        | SQL                         | Flux (함수형 파이프라인)            |

## Flux 쿼리 예시 (SQL 대응)

```sql
-- SQL
SELECT cpu, memory, disk_io FROM metrics
WHERE agent_id = 'V135-POS-03' AND time > NOW() - INTERVAL 1 HOUR
ORDER BY time DESC LIMIT 100;
```

```flux
// Flux
from(bucket: "pos_metrics")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "metrics" and r.agent_id == "V135-POS-03")
  |> filter(fn: (r) => r._field == "cpu" or r._field == "memory" or r._field == "disk_io")
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: 100)
```
