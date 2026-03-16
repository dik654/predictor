import React from 'react';
import { ForecastComparison } from '../components/ForecastComparison';

export function PredictionComparison() {
  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0f172a', color: '#e2e8f0', padding: '20px' }}>
      {/* Header */}
      <header style={{
        backgroundColor: '#1e293b',
        borderRadius: '12px',
        padding: '16px 24px',
        marginBottom: '20px',
      }}>
        <div>
          <h1 style={{ margin: 0, fontSize: '24px' }}>📊 예측 vs 실제 분석</h1>
          <p style={{ margin: '4px 0 0', fontSize: '14px', color: '#94a3b8' }}>
            실시간 예측값과 실제 측정값의 비교 분석
          </p>
        </div>
      </header>

      {/* Main Content */}
      <ForecastComparison />

      {/* Info Section */}
      <div style={{
        backgroundColor: '#1e293b',
        borderRadius: '12px',
        padding: '20px',
        marginTop: '20px',
      }}>
        <h3 style={{ margin: '0 0 12px', fontSize: '16px', color: '#e2e8f0' }}>
          💡 정보
        </h3>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
          <div>
            <h4 style={{ margin: '0 0 8px', fontSize: '14px', color: '#22c55e' }}>
              📈 실제 측정값
            </h4>
            <p style={{ margin: 0, fontSize: '12px', color: '#94a3b8', lineHeight: '1.6' }}>
              POS 기기에서 실시간으로 수집된 CPU, Memory, DiskIO 메트릭의 측정값입니다. 최근 3개의 측정 데이터가 표시됩니다.
            </p>
          </div>
          <div>
            <h4 style={{ margin: '0 0 8px', fontSize: '14px', color: '#8b5cf6' }}>
              🔮 예측값
            </h4>
            <p style={{ margin: 0, fontSize: '12px', color: '#94a3b8', lineHeight: '1.6' }}>
              AutoARIMA 모델이 생성한 30분, 1시간, 2시간 후의 예측값입니다. 최근 2개의 예측 데이터가 표시됩니다.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
