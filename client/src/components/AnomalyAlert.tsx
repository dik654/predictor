interface Detection {
  engine: string;
  metric: string;
  score: number;
  threshold: number;
  arima_predicted?: number;
  arima_deviation?: number;
  severity: string;
}

interface AnomalyData {
  agent_id: string;
  timestamp: string;
  detections: Detection[];
  health_score: number;
}

interface Props {
  anomalies: AnomalyData[];
}

const severityColors: Record<string, string> = {
  critical: '#ef4444',
  warning: '#f59e0b',
  normal: '#22c55e',
};

const severityBg: Record<string, string> = {
  critical: '#fef2f2',
  warning: '#fffbeb',
  normal: '#f0fdf4',
};

export function AnomalyAlert({ anomalies }: Props) {
  if (anomalies.length === 0) {
    return (
      <div style={{
        padding: '20px',
        backgroundColor: '#f0fdf4',
        borderRadius: '8px',
        textAlign: 'center',
        color: '#166534',
      }}>
        ✅ 이상 징후 없음
      </div>
    );
  }

  // Show latest 5 anomalies
  const recentAnomalies = anomalies.slice(-5).reverse();

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
      <h3 style={{ margin: 0, fontSize: '16px', color: '#374151' }}>🚨 이상 탐지 알림</h3>
      {recentAnomalies.map((anomaly, idx) => (
        <div key={idx} style={{
          padding: '12px',
          backgroundColor: '#fff',
          borderRadius: '8px',
          border: '1px solid #e5e7eb',
          boxShadow: '0 1px 3px rgba(0,0,0,0.1)',
        }}>
          <div style={{
            display: 'flex',
            justifyContent: 'space-between',
            marginBottom: '8px',
            fontSize: '12px',
            color: '#6b7280',
          }}>
            <span>{anomaly.agent_id}</span>
            <span>{anomaly.timestamp}</span>
          </div>
          {anomaly.detections.map((d, dIdx) => (
            <div key={dIdx} style={{
              padding: '8px',
              marginTop: '4px',
              backgroundColor: severityBg[d.severity] || severityBg.normal,
              borderRadius: '4px',
              borderLeft: `4px solid ${severityColors[d.severity] || severityColors.normal}`,
            }}>
              <div style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}>
                <span style={{ fontWeight: 600 }}>
                  {d.metric}
                </span>
                <span style={{
                  padding: '2px 8px',
                  borderRadius: '12px',
                  fontSize: '11px',
                  backgroundColor: severityColors[d.severity],
                  color: '#fff',
                  textTransform: 'uppercase',
                }}>
                  {d.severity}
                </span>
              </div>
              <div style={{ fontSize: '13px', marginTop: '4px', color: '#374151' }}>
                <span>엔진: {d.engine.toUpperCase()}</span>
                <span style={{ margin: '0 8px' }}>|</span>
                <span>점수: {d.score.toFixed(2)}</span>
              </div>
              {d.engine === 'arima' && d.arima_predicted !== undefined && (
                <div style={{ fontSize: '12px', marginTop: '4px', color: '#6b7280' }}>
                  예측값: {d.arima_predicted.toFixed(2)} | 잔차: {d.arima_deviation?.toFixed(2)}
                </div>
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
