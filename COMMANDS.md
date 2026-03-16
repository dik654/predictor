# WebRTC Hub — 실행 명령어 정리

## 기본 명령어

```bash
./run_all.sh              # 포그라운드 전체 실행 (Ctrl+C 종료)
./run_all.sh start        # 백그라운드 전체 실행
./run_all.sh stop         # 전체 종료
./run_all.sh restart      # 재시작
./run_all.sh status       # 상태 확인
./run_all.sh -h           # 도움말
```

---

## 서버 모드

```bash
# live 모드 (기본) — C# 시뮬레이터가 데이터를 전송
./run_all.sh start

# sample 모드 — sample/data_pos.txt 파일을 루프 재생
./run_all.sh start sample

# sample 파일 직접 지정
./run_all.sh start sample --sample-file /path/to/data_pos.txt
```

---

## POS 시뮬레이터 옵션

### 시나리오

| 시나리오 | 설명 |
|---|---|
| `normal` | 사인파 기반 안정적 메트릭 (기본) |
| `spike` | 20건마다 CPU 90%+ 스파이크 |
| `gradual` | 메모리 서서히 증가 (누수 패턴) |
| `jitter` | 전송 간격에 랜덤 지터 추가 |
| `gap` | N건 전송 후 M초 오프라인, 반복 |
| `file` | data_pos.txt 읽어서 루프 재생 |

```bash
./run_all.sh start --scenario spike
./run_all.sh start --scenario gradual --interval 10
./run_all.sh start --scenario gap --interval 5
./run_all.sh start --scenario file  # sample/data_pos.txt 사용
```

### 매장 정보

```bash
./run_all.sh start \
  --store-code V135 \
  --store-name GS25역삼홍인점 \
  --pos-no 3 \
  --agent V135-POS-03 \
  --scenario spike
```

---

## 다중 시뮬레이터 (`--sim`)

`--sim "코드:매장명:POS번호:시나리오[:간격[:에이전트ID]]"` 형식으로 반복 지정

```bash
# 3개 매장 동시 실행
./run_all.sh start \
  --sim "V135:GS25역삼홍인점:1:spike" \
  --sim "V136:GS25강남점:2:gradual" \
  --sim "V137:GS25홍대점:1:normal"

# 간격, 에이전트ID 까지 지정
./run_all.sh start \
  --sim "V135:GS25역삼홍인점:1:spike:5:V135-POS-01" \
  --sim "V136:GS25강남점:2:gradual:10:V136-POS-02"
```

> `--sim` 사용 시 `--store-code`, `--scenario` 등 단일 옵션은 무시됨

---

## 컴포넌트 제어

```bash
# 특정 컴포넌트 건너뛰기
./run_all.sh start --no-influx      # InfluxDB 없이
./run_all.sh start --no-client      # React 클라이언트 없이
./run_all.sh start --no-csharp      # C# 클라이언트 모두 없이
./run_all.sh start --no-pos-sim     # POS 시뮬레이터 없이

# 특정 컴포넌트만 실행
./run_all.sh start --only server
./run_all.sh start --only client
./run_all.sh start --only pos-sim
./run_all.sh start --only csharp        # C# 클라이언트 2개 모두
./run_all.sh start --only csharp-client # webrtc_csharp_client 만
./run_all.sh start --only influx
```

---

## 자주 쓰는 조합

```bash
# 개발 중 — InfluxDB 없이 빠르게
./run_all.sh start --no-influx --scenario spike

# sample 모드 + 여러 매장 시뮬레이션
./run_all.sh start sample \
  --sim "V135:GS25역삼홍인점:1:file" \
  --sim "V136:GS25강남점:2:spike"

# 서버 + 시뮬레이터만 (UI 없이)
./run_all.sh start --no-client --scenario gradual

# 재시작 (같은 옵션으로)
./run_all.sh restart --scenario spike --store-code V135
```

---

## 로그 확인

```bash
ls logs/                        # 로그 파일 목록
tail -f logs/server-*.log       # 서버 로그 실시간
tail -f logs/pos-sim-0-*.log    # 시뮬레이터 0번 로그
tail -f logs/pos-sim-1-*.log    # 시뮬레이터 1번 로그
```

---

## 엔드포인트

| 항목 | 주소 |
|---|---|
| Server | http://127.0.0.1:8080 |
| React Client | http://localhost:5173 |
| InfluxDB | http://localhost:8086 |
| Health check | http://127.0.0.1:8080/health |
| 접속 현황 | http://127.0.0.1:8080/who |

python -m webrtc_hub.historical_generator --file sample/data_pos.txt --interval-min 10 --hours 96
