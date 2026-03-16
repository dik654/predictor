#!/bin/bash
#
# run_all.sh — WebRTC Hub 통합 실행 스크립트
#
#   1) InfluxDB        (Docker)
#   2) Python Server   (uv)
#   3) React Client    (Vite dev)
#   4) C# WebRTC Client (webrtc_csharp_client)
#   5) C# POS Agent Sim (pos_agent_sim)
#
# Commands:
#   ./run_all.sh start [sample|live] [options]   # 백그라운드로 전체 실행
#   ./run_all.sh stop                             # 전체 종료
#   ./run_all.sh restart [sample|live] [options]  # 재시작
#   ./run_all.sh status                           # 상태 확인
#   ./run_all.sh [sample|live] [options]          # 포그라운드 실행 (Ctrl+C 종료)
#
# Bucket 모드 (기본: live):
#   sample  →  InfluxDB bucket: sample_metrics
#   live    →  InfluxDB bucket: pos_metrics
#
# Options:
#   --sample-file <path>    샘플 파일 경로 (file 시나리오용, default: sample/data_pos.txt)
#   --scenario <name>       POS Sim 시나리오 (normal|spike|jitter|gradual|gap|file)
#   --interval <sec>        POS Sim 전송 간격 (default: 5)
#   --agent <id>            POS Sim AgentId (default: SIM-POS-XX)
#   --store-code <code>     POS Sim StoreCode (default: SIM01)
#   --store-name <name>     POS Sim StoreName (default: 시뮬레이션 테스트점)
#   --pos-no <no>           POS Sim PosNo (default: 1)
#   --sim <spec>            POS Sim 추가 인스턴스 (반복 가능)
#                           형식: "store-code:store-name:pos-no:scenario[:interval[:agent-id]]"
#                           예)  "V135:GS25역삼홍인점:1:spike"
#                                "V136:GS25강남점:2:gradual:10:V136-POS-02"
#   --port <port>           Server 포트 (default: 8080)
#   --no-influx             InfluxDB 건너뛰기
#   --no-client             React 클라이언트 건너뛰기
#   --no-csharp             C# 클라이언트 모두 건너뛰기
#   --no-pos-sim            POS Agent Sim 건너뛰기
#   --only <component>      특정 컴포넌트만: influx|server|client|csharp-client|pos-sim|csharp
#   -h, --help
#

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$ROOT/.webrtc-hub.pids"
LOG_DIR="$ROOT/logs"

# ── 색상 ──────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m'

# ── 기본 설정 ─────────────────────────────────────────────────────────────────
CMD=""
SAMPLE_FILE="sample/data_pos.txt"
BUCKET="pos_metrics"   # live(기본): pos_metrics  /  sample: sample_metrics
RUN_INFLUX=true
RUN_SERVER=true
RUN_CLIENT=true
RUN_CSHARP_CLIENT=true
RUN_POS_SIM=true
POS_SCENARIO="normal"
POS_INTERVAL="5"
POS_AGENT="V135-POS-03"
POS_STORE_CODE="V135"
POS_STORE_NAME="GS25역삼홍인점"
POS_POS_NO="3"
SIM_CONFIGS=()   # --sim 으로 추가된 인스턴스 스펙 목록
SERVER_HOST="0.0.0.0"
SERVER_PORT="8080"
LOG_LEVEL="INFO"
ONLY=""

# ── 인자 파싱 ─────────────────────────────────────────────────────────────────
# 첫 번째 인자가 커맨드인지 확인
case "${1:-}" in
    start|stop|restart|status) CMD="$1"; shift ;;
esac

# 두 번째 인자가 버킷 모드인지 확인 (sample / live)
case "${1:-}" in
    sample) BUCKET="sample_metrics"; shift ;;
    live)   BUCKET="pos_metrics";    shift ;;
esac

while [[ $# -gt 0 ]]; do
    case "$1" in
        --sample-file)      SAMPLE_FILE="$2"; shift 2 ;;
        --no-influx)        RUN_INFLUX=false; shift ;;
        --no-client)        RUN_CLIENT=false; shift ;;
        --no-csharp)        RUN_CSHARP_CLIENT=false; RUN_POS_SIM=false; shift ;;
        --no-pos-sim)       RUN_POS_SIM=false; shift ;;
        --scenario)         POS_SCENARIO="$2"; shift 2 ;;
        --interval)         POS_INTERVAL="$2"; shift 2 ;;
        --agent)            POS_AGENT="$2"; shift 2 ;;
        --store-code)       POS_STORE_CODE="$2"; shift 2 ;;
        --store-name)       POS_STORE_NAME="$2"; shift 2 ;;
        --pos-no)           POS_POS_NO="$2"; shift 2 ;;
        --sim)              SIM_CONFIGS+=("$2"); shift 2 ;;
        --port)             SERVER_PORT="$2"; shift 2 ;;
        --log-level)        LOG_LEVEL="$2"; shift 2 ;;
        --debug)            LOG_LEVEL="DEBUG"; shift ;;
        --only)             ONLY="$2"; shift 2 ;;
        -h|--help)
            sed -n '3,30p' "$0" | sed 's/^#//'
            exit 0 ;;
        *)  echo -e "${RED}Unknown option: $1${NC}"; exit 1 ;;
    esac
done

SERVER_URL="http://127.0.0.1:${SERVER_PORT}"

# --only 처리
if [[ -n "$ONLY" ]]; then
    RUN_INFLUX=false; RUN_SERVER=false; RUN_CLIENT=false
    RUN_CSHARP_CLIENT=false; RUN_POS_SIM=false
    case "$ONLY" in
        influx)        RUN_INFLUX=true ;;
        server)        RUN_SERVER=true ;;
        client)        RUN_CLIENT=true ;;
        csharp-client) RUN_CSHARP_CLIENT=true ;;
        pos-sim)       RUN_POS_SIM=true ;;
        csharp)        RUN_CSHARP_CLIENT=true; RUN_POS_SIM=true ;;
        *) echo -e "${RED}Unknown component: $ONLY${NC}"; exit 1 ;;
    esac
fi

# ── docker compose 명령어 감지 ───────────────────────────────────────────────
if docker compose version &>/dev/null 2>&1; then
    DC="docker compose"
elif command -v docker-compose &>/dev/null; then
    DC="docker-compose"
else
    DC="docker compose"  # 없으면 에러는 나중에 check_dep 에서 처리
fi

# ── 헬퍼 ─────────────────────────────────────────────────────────────────────
check_dep() {
    command -v "$1" &>/dev/null || { echo -e "${RED}[ERROR] '$1' not found.${NC}"; return 1; }
}

pid_alive() {
    kill -0 "$1" 2>/dev/null
}

# PID 파일에 label:pid 형식으로 저장
save_pid() {
    echo "$1:$2" >> "$PID_FILE"
}

# ── stop ──────────────────────────────────────────────────────────────────────
do_stop() {
    echo -e "${YELLOW}[STOP] Stopping all processes...${NC}"

    if [[ -f "$PID_FILE" ]]; then
        while IFS=: read -r label pid; do
            if pid_alive "$pid"; then
                echo -e "  Killing ${label} (PID ${pid})..."
                kill "$pid" 2>/dev/null || true
                # 자식 프로세스 그룹도 정리
                pkill -P "$pid" 2>/dev/null || true
            else
                echo -e "  ${label} (PID ${pid}) — already stopped"
            fi
        done < "$PID_FILE"
        rm -f "$PID_FILE"
    else
        echo -e "  ${YELLOW}No PID file found. Trying fallback...${NC}"
        # 포트/이름으로 찾아 종료
        pkill -f "webrtc_hub.server" 2>/dev/null || true
        pkill -f "vite"              2>/dev/null || true
        pkill -f "webrtc_csharp_client" 2>/dev/null || true
        pkill -f "PosAgentSim"       2>/dev/null || true
    fi

    if $RUN_INFLUX || [[ -z "$ONLY" ]]; then
        if $DC -f "$ROOT/docker-compose.yml" ps -q 2>/dev/null | grep -q .; then
            echo -e "  Stopping InfluxDB (Docker)..."
            $DC -f "$ROOT/docker-compose.yml" down 2>/dev/null || true
        fi
    fi

    echo -e "${GREEN}[DONE] All stopped.${NC}"
}

# ── status ────────────────────────────────────────────────────────────────────
do_status() {
    echo -e "${CYAN}── WebRTC Hub Status ──────────────────────${NC}"
    if [[ ! -f "$PID_FILE" ]]; then
        echo -e "  ${YELLOW}Not running (no PID file).${NC}"
    else
        while IFS=: read -r label pid; do
            if pid_alive "$pid"; then
                echo -e "  ${GREEN}[RUNNING]${NC} ${label} (PID ${pid})"
            else
                echo -e "  ${RED}[STOPPED]${NC} ${label} (PID ${pid})"
            fi
        done < "$PID_FILE"
    fi

    # InfluxDB
    if curl -sf http://localhost:8086/health >/dev/null 2>&1; then
        echo -e "  ${GREEN}[RUNNING]${NC} InfluxDB (http://localhost:8086)"
    else
        echo -e "  ${RED}[STOPPED]${NC} InfluxDB"
    fi

    # Server health
    if curl -sf "${SERVER_URL}/health" >/dev/null 2>&1; then
        echo -e "  ${GREEN}[HEALTHY]${NC} Server (${SERVER_URL})"
    fi
    echo ""
}

# ── start (핵심 실행 로직) ────────────────────────────────────────────────────
do_start() {
    local background="$1"   # true = start 커맨드, false = 포그라운드

    # 이미 실행 중이면 경고
    if [[ -f "$PID_FILE" ]] && [[ "$background" == "true" ]]; then
        echo -e "${YELLOW}[WARN] PID file exists. Use 'restart' or 'stop' first.${NC}"
        do_status
        exit 1
    fi

    rm -f "$PID_FILE"

    # 의존성 체크
    $RUN_SERVER  && { check_dep uv || check_dep python3; }
    $RUN_CLIENT  && check_dep npm
    { $RUN_CSHARP_CLIENT || $RUN_POS_SIM; } && check_dep dotnet
    $RUN_INFLUX  && check_dep docker

    mkdir -p "$LOG_DIR"
    local TS
    TS=$(date +%Y%m%d-%H%M%S)

    echo -e "${CYAN}============================================${NC}"
    echo -e "${CYAN}  WebRTC Hub — Starting${NC}"
    echo -e "${CYAN}============================================${NC}"
    echo ""

    # ── 1. InfluxDB ─────────────────────────────────────────────────────────
    if $RUN_INFLUX; then
        echo -e "${MAGENTA}[1/5] InfluxDB (Docker)...${NC}"
        $DC -f "$ROOT/docker-compose.yml" up -d
        echo -n "  Waiting"
        for i in $(seq 1 30); do
            curl -sf http://localhost:8086/health >/dev/null 2>&1 && { echo -e " ${GREEN}OK${NC}"; break; }
            echo -n "."; sleep 1
            [[ $i -eq 30 ]] && echo -e " ${YELLOW}(timeout, continuing)${NC}"
        done

        # sample_metrics 버킷이 없으면 자동 생성 (docker exec 사용)
        local _token="pulseai-token-12345"
        local _org="pulseai"
        for _bucket in sample_metrics pos_metrics; do
            # 버킷 존재 여부 확인 (HTTP API)
            local _exists
            _exists=$(curl -sf \
                -H "Authorization: Token ${_token}" \
                "http://localhost:8086/api/v2/buckets?org=${_org}&name=${_bucket}" 2>/dev/null \
                | grep -c '"name"' || true)
            if [[ "$_exists" -eq 0 ]]; then
                echo -e "  Creating bucket: ${CYAN}${_bucket}${NC}..."
                $DC -f "$ROOT/docker-compose.yml" exec -T influxdb \
                    influx bucket create \
                        --name "$_bucket" \
                        --org "$_org" \
                        --retention 30d \
                        --token "$_token" \
                    2>/dev/null || true
            fi
        done
        echo ""
    else
        echo -e "${YELLOW}[1/5] InfluxDB — skipped${NC}"
    fi

    # ── 2. Python Server ────────────────────────────────────────────────────
    if $RUN_SERVER; then
        local server_log="$LOG_DIR/server-${TS}.log"
        echo -e "${GREEN}[2/5] Python Server (port=${SERVER_PORT})...${NC}"

        # 포트 점유 시 기존 프로세스 정리
        if lsof -ti :"$SERVER_PORT" >/dev/null 2>&1; then
            echo -e "  ${YELLOW}Port ${SERVER_PORT} in use — killing old process...${NC}"
            lsof -ti :"$SERVER_PORT" | xargs kill 2>/dev/null || true
            sleep 1
        fi

        local server_args=(python -m webrtc_hub.server --host "$SERVER_HOST" --port "$SERVER_PORT" --log-level "$LOG_LEVEL" --bucket "$BUCKET")

        if [[ "$background" == "true" ]]; then
            (cd "$ROOT/server"; uv run "${server_args[@]}" >> "$server_log" 2>&1) &
        else
            (cd "$ROOT/server"; uv run "${server_args[@]}" 2>&1 | tee "$server_log") &
        fi
        local server_pid=$!
        save_pid "server" "$server_pid"
        echo -e "  PID=${server_pid} | Log: ${server_log}"

        echo -n "  Waiting"
        for i in $(seq 1 20); do
            curl -sf "${SERVER_URL}/health" >/dev/null 2>&1 && { echo -e " ${GREEN}OK${NC}"; break; }
            echo -n "."; sleep 1
            [[ $i -eq 20 ]] && echo -e " ${YELLOW}(still starting...)${NC}"
        done
        echo ""
    else
        echo -e "${YELLOW}[2/5] Python Server — skipped${NC}"
    fi

    # ── 3. React Client ─────────────────────────────────────────────────────
    if $RUN_CLIENT; then
        local client_log="$LOG_DIR/client-${TS}.log"
        echo -e "${BLUE}[3/5] React Client (Vite)...${NC}"

        if [[ "$background" == "true" ]]; then
            # stdin을 /dev/null로 리다이렉트 — Vite의 인터랙티브 stdin 읽기 방지
            (cd "$ROOT/client"; [[ ! -d node_modules ]] && npm install; npx vite --host < /dev/null >> "$client_log" 2>&1) &
        else
            (cd "$ROOT/client"; [[ ! -d node_modules ]] && npm install; npx vite --host 2>&1 | tee "$client_log") &
        fi
        local client_pid=$!
        save_pid "client" "$client_pid"
        echo -e "  PID=${client_pid} | Log: ${client_log}"
        echo -e "  URL: ${CYAN}http://localhost:5173${NC}"
        echo ""
    else
        echo -e "${YELLOW}[3/5] React Client — skipped${NC}"
    fi

    # ── 4. C# WebRTC Client ─────────────────────────────────────────────────
    if $RUN_CSHARP_CLIENT; then
        local csharp_log="$LOG_DIR/csharp-client-${TS}.log"
        echo -e "${MAGENTA}[4/5] C# WebRTC Client...${NC}"

        if [[ "$background" == "true" ]]; then
            (cd "$ROOT/webrtc_csharp_client"; dotnet run -- "$SERVER_URL" >> "$csharp_log" 2>&1) &
        else
            (cd "$ROOT/webrtc_csharp_client"; dotnet run -- "$SERVER_URL" 2>&1 | tee "$csharp_log") &
        fi
        local csharp_pid=$!
        save_pid "csharp-client" "$csharp_pid"
        echo -e "  PID=${csharp_pid} | Log: ${csharp_log}"
        echo ""
    else
        echo -e "${YELLOW}[4/5] C# WebRTC Client — skipped${NC}"
    fi

    # ── 5. C# POS Agent Sim ─────────────────────────────────────────────────
    if $RUN_POS_SIM; then
        # --sim 이 하나라도 있으면 다중 모드, 없으면 단일 모드
        if [[ ${#SIM_CONFIGS[@]} -gt 0 ]]; then
            echo -e "${RED}[5/5] C# POS Agent Sim — ${#SIM_CONFIGS[@]}개 인스턴스${NC}"
        else
            echo -e "${RED}[5/5] C# POS Agent Sim (scenario=${POS_SCENARIO}, store=${POS_STORE_CODE}, pos=${POS_POS_NO})${NC}"
        fi

        # 단일 인스턴스 헬퍼
        launch_sim() {
            local idx="$1" code="$2" name="$3" pos="$4" scenario="$5" interval="$6" agent="$7"
            local sim_log="$LOG_DIR/pos-sim-${idx}-${TS}.log"
            local sim_args=(--url "$SERVER_URL"
                            --scenario "$scenario"
                            --interval "$interval"
                            --store-code "$code"
                            --store-name "$name"
                            --pos-no "$pos")
            [[ -n "$agent" ]]            && sim_args+=(--agent "$agent")
            [[ "$scenario" == "file" ]]  && sim_args+=(--file "$ROOT/$SAMPLE_FILE")

            if [[ "$background" == "true" ]]; then
                (cd "$ROOT/pos_agent_sim"; dotnet run -- "${sim_args[@]}" >> "$sim_log" 2>&1) &
            else
                (cd "$ROOT/pos_agent_sim"; dotnet run -- "${sim_args[@]}" 2>&1 | tee "$sim_log") &
            fi
            local pid=$!
            save_pid "pos-sim-${idx}" "$pid"
            echo -e "  [${idx}] PID=${pid} store=${code} pos=${pos} scenario=${scenario} | Log: ${sim_log}"
        }

        if [[ ${#SIM_CONFIGS[@]} -gt 0 ]]; then
            # 다중 모드: --sim 으로 지정된 각 스펙 실행
            local idx=0
            for spec in "${SIM_CONFIGS[@]}"; do
                # 형식: code:name:pos:scenario[:interval[:agent]]
                IFS=: read -r s_code s_name s_pos s_scenario s_interval s_agent <<< "$spec"
                launch_sim "$idx" \
                    "${s_code:-SIM0${idx}}" \
                    "${s_name:-시뮬레이션}" \
                    "${s_pos:-1}" \
                    "${s_scenario:-normal}" \
                    "${s_interval:-$POS_INTERVAL}" \
                    "${s_agent:-}"
                idx=$((idx + 1))
            done
        else
            # 단일 모드: 기존 옵션 사용
            launch_sim "0" \
                "$POS_STORE_CODE" "$POS_STORE_NAME" "$POS_POS_NO" \
                "$POS_SCENARIO" "$POS_INTERVAL" "$POS_AGENT"
        fi
        echo ""
    else
        echo -e "${YELLOW}[5/5] C# POS Agent Sim — skipped${NC}"
    fi

    # ── Summary ─────────────────────────────────────────────────────────────
    echo -e "${CYAN}============================================${NC}"
    echo -e "  Server : ${GREEN}${SERVER_URL}${NC}"
    $RUN_CLIENT && echo -e "  Client : ${BLUE}http://localhost:5173${NC}"
    $RUN_INFLUX && echo -e "  Influx : ${MAGENTA}http://localhost:8086${NC}"
    echo -e "  Logs   : ${LOG_DIR}/"
    echo -e "${CYAN}============================================${NC}"
    echo ""

    if [[ "$background" == "true" ]]; then
        echo -e "${GREEN}Started in background.${NC}"
        echo -e "  Stop    : ${YELLOW}./run_all.sh stop${NC}"
        echo -e "  Restart : ${YELLOW}./run_all.sh restart${NC}"
        echo -e "  Status  : ${YELLOW}./run_all.sh status${NC}"
    else
        # 포그라운드: Ctrl+C 로 전체 종료
        echo -e "${YELLOW}Running in foreground. Press Ctrl+C to stop all.${NC}"

        _fg_cleanup() {
            trap '' SIGINT SIGTERM  # 재진입 방지
            echo ""
            echo -e "${YELLOW}[STOP] Caught signal, shutting down...${NC}"
            if [[ -f "$PID_FILE" ]]; then
                while IFS=: read -r label pid; do
                    pid_alive "$pid" && kill "$pid" 2>/dev/null || true
                    pkill -P "$pid" 2>/dev/null || true
                done < "$PID_FILE"
                rm -f "$PID_FILE"
            fi
            if $RUN_INFLUX; then
                $DC -f "$ROOT/docker-compose.yml" down 2>/dev/null || true
            fi
            echo -e "${GREEN}[DONE] All stopped.${NC}"
            exit 0
        }
        trap '_fg_cleanup' SIGINT SIGTERM

        # set -e 가 활성화된 상태에서 wait 가 SIGINT 로 중단되면
        # 트랩이 실행되기 전에 스크립트가 종료될 수 있으므로 비활성화
        set +e
        wait
    fi
}

# ── 커맨드 분기 ───────────────────────────────────────────────────────────────
case "$CMD" in
    start)
        do_start true
        ;;
    stop)
        do_stop
        ;;
    restart)
        do_stop
        sleep 1
        do_start true
        ;;
    status)
        do_status
        ;;
    "")
        # 커맨드 없음 = 포그라운드 모드
        do_start false
        ;;
esac
