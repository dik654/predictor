#!/bin/bash
# 저장된 로그를 분석하고 통계를 보여줍니다

LATEST_LOG=$(ls -t logs/server-*.log 2>/dev/null | head -1)

if [ -z "$LATEST_LOG" ]; then
    echo "❌ No server logs found in logs/ directory"
    exit 1
fi

echo "📊 Analyzing: $LATEST_LOG"
echo ""

# 통계 추출
echo "=== 📈 Key Statistics ==="
TOTAL_RECORDS=$(grep -c "^\[LOOP\].*Start processing" "$LATEST_LOG" 2>/dev/null || echo "N/A")
ARIMA_RECORDS=$(grep -c "All [1-9].*forecasts written" "$LATEST_LOG" 2>/dev/null || echo "0")
FORECASTS=$(grep "All [0-9].*forecasts written" "$LATEST_LOG" 2>/dev/null | awk '{sum+=$7} END {print sum}' || echo "0")

echo "Total records processed: $TOTAL_RECORDS"
echo "Records with ARIMA forecasts: $ARIMA_RECORDS"
echo "Total forecasts written: $FORECASTS"
echo ""

# 48시간 누적 상태
echo "=== ⏱️ 48-Hour Accumulation ==="
CUMULATIVE=$(grep "Cumulative.*h" "$LATEST_LOG" | tail -1 || echo "Not found")
if [ ! -z "$CUMULATIVE" ]; then
    echo "$CUMULATIVE"
fi
echo ""

# ARIMA 샘플
echo "=== 🔮 ARIMA Forecast Samples ==="
grep "ARIMA.*value=.*forecast=" "$LATEST_LOG" | head -5
echo ""

# 에러 및 경고
echo "=== ⚠️ Errors and Warnings ==="
ERROR_COUNT=$(grep -i "error\|exception" "$LATEST_LOG" | grep -v "No forecasts" | wc -l)
echo "Errors/Exceptions: $ERROR_COUNT"
if [ "$ERROR_COUNT" -gt 0 ]; then
    grep -i "error\|exception" "$LATEST_LOG" | grep -v "No forecasts" | head -3
fi
echo ""

# InfluxDB 쓰기 성공
echo "=== 💾 InfluxDB Writes ==="
WRITE_SUCCESS=$(grep -c "WRITE-SUCCESS" "$LATEST_LOG" || echo "0")
echo "Successful writes: $WRITE_SUCCESS"
echo ""

# 마지막 상태
echo "=== 📌 Final Status ==="
tail -10 "$LATEST_LOG" | grep -E "finished|stopped|closed|Looping"
