import React from 'react';
import { ForecastComparison } from '../components/ForecastComparison';

export function PredictionComparisonImproved() {
  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0f172a', color: '#e2e8f0', display: 'flex', flexDirection: 'column', fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif' }}>
      {/* Header */}
      <header style={{
        backgroundColor: '#1e293b',
        borderBottom: '1px solid #334155',
        padding: '24px 32px'
      }}>
        <h1 style={{ margin: '0 0 6px 0', fontSize: '28px', fontWeight: '700', letterSpacing: '-0.5px' }}>예측 vs 실제 성능</h1>
        <p style={{ margin: 0, fontSize: '13px', color: '#94a3b8', fontWeight: '400' }}>장애 위험도 시계열 분석 - 예측과 실제 데이터 비교</p>
      </header>

      {/* Main Content */}
      <main style={{ flex: 1, padding: '28px 32px', overflow: 'auto' }}>
        {/* Forecast Chart */}
        <div style={{
          backgroundColor: '#1e293b',
          borderRadius: '8px',
          border: '1px solid #334155',
          padding: '20px',
          marginBottom: '24px'
        }}>
          <h3 style={{ margin: '0 0 16px 0', fontSize: '13px', fontWeight: '600', color: '#e2e8f0', textTransform: 'uppercase', letterSpacing: '0.5px' }}>성능 vs 예측</h3>
          <p style={{ margin: '0 0 20px 0', fontSize: '11px', color: '#94a3b8', fontWeight: '400' }}>초록색(실제값) vs 파랑색(예측값) - 벌어지는 폭이 커질수록 성능 저하 신호</p>
          <ForecastComparison />
        </div>

        {/* Interpretation Cards */}
        <div style={{ marginBottom: '24px' }}>
          <h3 style={{ margin: '0 0 12px 0', fontSize: '13px', fontWeight: '600', color: '#e2e8f0', textTransform: 'uppercase', letterSpacing: '0.5px' }}>신호 해석</h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '12px' }}>
            <InterpretationCard
              title="정상 (예측 ≈ 실제)"
              metric="GREEN ≈ BLUE"
              message="모델이 정확한 상태로 예측값을 신뢰할 수 있음"
              action="계획에 예측값 사용"
              color="#10b981"
            />
            <InterpretationCard
              title="성능 급격히 저하"
              metric="GREEN ↘ BLUE"
              message="예측보다 실제 성능이 더 빨리 악화되고 있음"
              action="즉시 조치 필요 - 긴급 대응"
              color="#ef4444"
            />
            <InterpretationCard
              title="성능 호전 (예상 초과)"
              metric="GREEN ↗ BLUE"
              message="예측보다 실제 성능이 더 좋은 상태"
              action="기준 업데이트 - 임계값 조정"
              color="#00ffff"
            />
            <InterpretationCard
              title="높은 변동성"
              metric="GREEN ~~ BLUE"
              message="불안정한 동작 패턴 감지됨"
              action="원인 분석 - 하드웨어 장애 의심"
              color="#ef4444"
            />
          </div>
        </div>

        {/* Failure Timeline - Extended to 2 days */}
        <div style={{ marginBottom: '24px' }}>
          <h3 style={{ margin: '0 0 12px 0', fontSize: '13px', fontWeight: '600', color: '#e2e8f0', textTransform: 'uppercase', letterSpacing: '0.5px' }}>예상 장애 타임라인 (48시간 예측)</h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '12px' }}>
            <TimelineBox window="0~6시간" risk="매우높음" color="#ef4444" probability="45%" recommendation="🚨 즉시 조치 필수" />
            <TimelineBox window="6~12시간" risk="높음" color="#f97316" probability="22%" recommendation="24시간 내 유지보수" />
            <TimelineBox window="12~24시간" risk="중간" color="#f59e0b" probability="18%" recommendation="1일 내 점검 필요" />
            <TimelineBox window="24~48시간" risk="낮음" color="#3b82f6" probability="10%" recommendation="2일 내 정기점검" />
            <TimelineBox window="2일 이상" risk="최소" color="#10b981" probability="5%" recommendation="모니터링 계속" />
          </div>
        </div>

        {/* Metrics Explanation */}
        <div style={{ marginBottom: 0 }}>
          <h3 style={{ margin: '0 0 12px 0', fontSize: '13px', fontWeight: '600', color: '#e2e8f0', textTransform: 'uppercase', letterSpacing: '0.5px' }}>지표 설명</h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '12px' }}>
            <MetricExplanation
              label="현재 측정값"
              example="45.2°C (CPU 온도)"
              meaning="장비에서 현재 실제로 측정된 값"
              context="대부분의 지표에서 낮을수록 좋음"
              color="#00ffff"
            />
            <MetricExplanation
              label="예측값"
              example="52.1°C (30분 후 예측)"
              meaning="30분 후 어떤 값이 될 것으로 예측하는 값"
              context="실제값이 예측값을 먼저 초과하면 곡선이 앞서가고 있다는 신호"
              color="#00ffff"
            />
            <MetricExplanation
              label="정확도 %"
              example="8.3%"
              meaning="예측이 실제 값과 얼마나 다른지를 나타내는 오차율"
              context="<5% 우수 | 5~10% 양호 | 10~20% 보통 | >20% 미흡"
              color="#00ffff"
            />
            <MetricExplanation
              label="예측 윈도우"
              example="30분"
              meaning="얼마나 앞으로 예측하고 있는지를 나타냄"
              context="길수록 불확실성 증가. 30분 = 높은 신뢰도"
              color="#00ffff"
            />
          </div>
        </div>
      </main>
    </div>
  );
}

function InterpretationCard({ title, metric, message, action, color }: { title: string; metric: string; message: string; action: string; color: string }) {
  return (
    <div style={{
      backgroundColor: '#1e293b',
      borderRadius: '8px',
      border: `1px solid ${color}40`,
      borderLeft: `3px solid ${color}`,
      padding: '14px'
    }}>
      <h4 style={{ margin: '0 0 4px 0', fontSize: '12px', fontWeight: '600', color, letterSpacing: '0.5px', textTransform: 'uppercase' }}>
        {title}
      </h4>
      <p style={{ margin: '4px 0 8px 0', fontSize: '11px', fontWeight: '500', color: '#e2e8f0' }}>
        {metric}
      </p>
      <p style={{ margin: '0 0 10px 0', fontSize: '11px', color: '#94a3b8', fontWeight: '400' }}>
        {message}
      </p>
      <p style={{ margin: 0, fontSize: '10px', color, fontWeight: '600', letterSpacing: '0.5px', textTransform: 'uppercase' }}>
        → {action}
      </p>
    </div>
  );
}

function TimelineBox({ window, risk, color, probability, recommendation }: { window: string; risk: string; color: string; probability: string; recommendation: string }) {
  return (
    <div style={{
      backgroundColor: '#1e293b',
      borderRadius: '8px',
      border: `1px solid ${color}40`,
      borderTop: `3px solid ${color}`,
      padding: '14px'
    }}>
      <p style={{ margin: '0 0 8px 0', fontSize: '10px', fontWeight: '600', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
        {window}
      </p>
      <p style={{ margin: '8px 0 8px 0', fontSize: '28px', fontWeight: '700', color, letterSpacing: '-0.5px' }}>
        {probability}
      </p>
      <p style={{ margin: '0 0 10px 0', fontSize: '10px', fontWeight: '600', color, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
        {risk} 위험도
      </p>
      <p style={{ margin: 0, fontSize: '11px', color: '#94a3b8', lineHeight: '1.4', fontWeight: '400' }}>
        {recommendation}
      </p>
    </div>
  );
}

function MetricExplanation({ label, example, meaning, context, color }: { label: string; example: string; meaning: string; context: string; color: string }) {
  return (
    <div style={{
      backgroundColor: '#1e293b',
      borderRadius: '8px',
      border: `1px solid ${color}40`,
      padding: '14px'
    }}>
      <h4 style={{ margin: '0 0 8px 0', fontSize: '11px', fontWeight: '600', color: '#e2e8f0', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
        {label}
      </h4>
      <p style={{ margin: '0 0 6px 0', fontSize: '12px', color, fontFamily: 'monospace', fontWeight: '600' }}>
        {example}
      </p>
      <p style={{ margin: '0 0 8px 0', fontSize: '11px', color: '#94a3b8', fontWeight: '400' }}>
        {meaning}
      </p>
      <p style={{ margin: 0, fontSize: '10px', color: '#94a3b8', fontStyle: 'italic', fontWeight: '400' }}>
        💡 {context}
      </p>
    </div>
  );
}
