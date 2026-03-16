#!/bin/bash
# 서버를 실행하면서 로그를 자동으로 저장합니다

set -e

cd "$(dirname "$0")"

# 로그 파일 이름 생성
TIMESTAMP=$(date +%Y-%m-%d-%H%M%S)
LOG_DIR="logs"
mkdir -p "$LOG_DIR"

# 모드 및 파일 경로 결정
MODE="${1:-sample}"
SAMPLE_FILE="${2:-sample/data_pos.txt}"
LOG_FILE="$LOG_DIR/server-${MODE}-${TIMESTAMP}.log"

echo "🚀 Starting server in $MODE mode"
echo "📝 Log saved to: $LOG_FILE"
echo ""

# 가상 환경 활성화
source server/.venv/bin/activate

# 서버 실행 및 로그 저장
if [ "$MODE" = "sample" ]; then
    python -m webrtc_hub.server --mode sample --file "$SAMPLE_FILE" 2>&1 | tee "$LOG_FILE"
else
    python -m webrtc_hub.server 2>&1 | tee "$LOG_FILE"
fi

echo ""
echo "✅ Server stopped. Log saved to: $LOG_FILE"
