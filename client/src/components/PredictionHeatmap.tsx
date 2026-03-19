interface ForecastHorizon {
  minutes: number;
  value: number;
  severity: string;
}

interface Props {
  cpuForecasts: ForecastHorizon[];
  memoryForecasts: ForecastHorizon[];
  currentCpu: number;
  currentMemory: number;
}

export function PredictionHeatmap({ cpuForecasts, memoryForecasts, currentCpu, currentMemory }: Props) {
  const timeLabels = ['현재', '+30분', '+1시간', '+2시간'];
  
  const getSeverityStyle = (severity: string, value: number) => {
    if (severity === 'critical' || value >= 90) {
      return { bg: '#7f1d1d', border: '#ef4444', text: '#fca5a5', icon: '🔴' };
    } else if (severity === 'warning' || value >= 80) {
      return { bg: '#713f12', border: '#f59e0b', text: '#fcd34d', icon: '🟡' };
    }
    return { bg: '#14532d', border: '#22c55e', text: '#86efac', icon: '🟢' };
  };

  // Build timeline data
  const cpuTimeline = [
    { minutes: 0, value: currentCpu, severity: currentCpu >= 90 ? 'critical' : currentCpu >= 80 ? 'warning' : 'normal' },
    ...cpuForecasts,
  ];
  
  const memTimeline = [
    { minutes: 0, value: currentMemory, severity: currentMemory >= 95 ? 'critical' : currentMemory >= 85 ? 'warning' : 'normal' },
    ...memoryForecasts,
  ];

  return (
    <div style={{
      backgroundColor: '#1e293b',
      borderRadius: '12px',
      padding: '16px',
      marginBottom: '20px',
    }}>
      <h3 style={{ margin: '0 0 16px', fontSize: '14px', color: '#94a3b8' }}>
        🌡️ 예측 히트맵 타임라인
      </h3>
      
      {/* Time labels */}
      <div style={{ display: 'grid', gridTemplateColumns: '80px repeat(4, 1fr)', gap: '8px', marginBottom: '8px' }}>
        <div></div>
        {timeLabels.map((label, i) => (
          <div key={i} style={{ 
            textAlign: 'center', 
            fontSize: '11px', 
            color: '#94a3b8',
            fontWeight: i === 0 ? 'bold' : 'normal',
          }}>
            {label}
          </div>
        ))}
      </div>
      
      {/* CPU Row */}
      <div style={{ display: 'grid', gridTemplateColumns: '80px repeat(4, 1fr)', gap: '8px', marginBottom: '8px' }}>
        <div style={{ 
          display: 'flex', 
          alignItems: 'center', 
          fontSize: '12px', 
          color: '#94a3b8',
          fontWeight: 'bold',
        }}>
          💻 CPU
        </div>
        {cpuTimeline.map((point, i) => {
          const style = getSeverityStyle(point.severity, point.value);
          return (
            <div
              key={i}
              style={{
                backgroundColor: style.bg,
                border: `1px solid ${style.border}`,
                borderRadius: '8px',
                padding: '8px',
                textAlign: 'center',
                transition: 'transform 0.2s',
              }}
            >
              <div style={{ fontSize: '14px', marginBottom: '2px' }}>{style.icon}</div>
              <div style={{ fontSize: '14px', fontWeight: 'bold', color: style.text }}>
                {point.value.toFixed(0)}%
              </div>
            </div>
          );
        })}
      </div>
      
      {/* Memory Row */}
      <div style={{ display: 'grid', gridTemplateColumns: '80px repeat(4, 1fr)', gap: '8px' }}>
        <div style={{ 
          display: 'flex', 
          alignItems: 'center', 
          fontSize: '12px', 
          color: '#94a3b8',
          fontWeight: 'bold',
        }}>
          🧠 Memory
        </div>
        {memTimeline.map((point, i) => {
          const style = getSeverityStyle(point.severity, point.value);
          return (
            <div
              key={i}
              style={{
                backgroundColor: style.bg,
                border: `1px solid ${style.border}`,
                borderRadius: '8px',
                padding: '8px',
                textAlign: 'center',
                transition: 'transform 0.2s',
              }}
            >
              <div style={{ fontSize: '14px', marginBottom: '2px' }}>{style.icon}</div>
              <div style={{ fontSize: '14px', fontWeight: 'bold', color: style.text }}>
                {point.value.toFixed(0)}%
              </div>
            </div>
          );
        })}
      </div>
      
      {/* Legend */}
      <div style={{ 
        display: 'flex', 
        justifyContent: 'center', 
        gap: '16px', 
        marginTop: '12px',
        fontSize: '11px',
        color: '#94a3b8',
      }}>
        <span>🟢 정상 (&lt;80%)</span>
        <span>🟡 주의 (80-90%)</span>
        <span>🔴 위험 (&gt;90%)</span>
      </div>
    </div>
  );
}
