import { useMemo } from 'react';

interface Detection {
  engine: string;
  metric: string;
  value: number;
  score: number;
  severity: string;
  confidence?: number;
  details?: string;
  forecast?: number;
  forecast_horizon?: Array<{
    minutes: number;
    value: number;
    severity: string;
  }>;
}

interface Props {
  detections: Detection[];
  healthScore: number;
}

export function StatusInsightCard({ detections, healthScore }: Props) {
  // ECOD 분석 결과 해설 (항상 표시)
  const ecodInsights = useMemo(() => {
    const ecodResults = detections.filter(d => d.engine === 'ecod');
    const multivariate = ecodResults.find(d => d.metric === 'Multivariate');
    const metrics = ecodResults.filter(d => d.metric !== 'Multivariate');
    
    // 데이터 없을 때 기본값
    if (!multivariate) {
      return {
        status: 'normal' as const,
        message: '데이터 수집 중...',
        score: 0,
        details: [],
      };
    }
    
    const warnings = metrics.filter(d => d.severity === 'warning' || d.severity === 'critical');
    
    let status: 'normal' | 'warning' | 'critical' = 'normal';
    let message = '모든 시스템 지표가 정상 범위입니다.';
    
    if (multivariate.severity === 'critical') {
      status = 'critical';
      message = '시스템 전반에 심각한 이상이 감지되었습니다!';
    } else if (multivariate.severity === 'warning' || warnings.length > 0) {
      status = 'warning';
      const warningMetrics = warnings.map(w => w.metric).join(', ');
      message = warningMetrics 
        ? `${warningMetrics} 지표에서 이상 징후가 감지되었습니다.`
        : '일부 지표에서 이상 징후가 감지되었습니다.';
    }
    
    return {
      status,
      message,
      score: multivariate.score,
      details: metrics.map(m => ({
        metric: m.metric,
        value: m.value,
        severity: m.severity,
      })),
    };
  }, [detections]);

  // ARIMA 미래 예측 경보
  const arimaForecasts = useMemo(() => {
    const arimaResults = detections.filter(d => d.engine === 'arima' && d.forecast_horizon);
    
    const alerts: Array<{
      metric: string;
      minutes: number;
      value: number;
      severity: string;
    }> = [];
    
    arimaResults.forEach(result => {
      result.forecast_horizon?.forEach(horizon => {
        if (horizon.severity === 'warning' || horizon.severity === 'critical') {
          alerts.push({
            metric: result.metric,
            minutes: horizon.minutes,
            value: horizon.value,
            severity: horizon.severity,
          });
        }
      });
    });
    
    // Sort by severity (critical first) then by time
    alerts.sort((a, b) => {
      if (a.severity === 'critical' && b.severity !== 'critical') return -1;
      if (b.severity === 'critical' && a.severity !== 'critical') return 1;
      return a.minutes - b.minutes;
    });
    
    return alerts.slice(0, 3); // Top 3 alerts
  }, [detections]);

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'critical': return { bg: '#7f1d1d', border: '#ef4444', text: '#fca5a5' };
      case 'warning': return { bg: '#713f12', border: '#f59e0b', text: '#fcd34d' };
      default: return { bg: '#14532d', border: '#22c55e', text: '#86efac' };
    }
  };

  const formatMinutes = (minutes: number) => {
    if (minutes >= 60) {
      const hours = Math.floor(minutes / 60);
      const mins = minutes % 60;
      return mins > 0 ? `${hours}시간 ${mins}분` : `${hours}시간`;
    }
    return `${minutes}분`;
  };

  return (
    <div style={{
      backgroundColor: '#1e293b',
      borderRadius: '12px',
      padding: '16px',
      height: '100%',
      display: 'flex',
      flexDirection: 'column',
      gap: '16px',
    }}>
      {/* Header */}
      <div style={{ 
        display: 'flex', 
        alignItems: 'center', 
        justifyContent: 'space-between',
      }}>
        <h3 style={{ margin: 0, fontSize: '14px', color: '#94a3b8' }}>
          🎯 시스템 상태 분석
        </h3>
        {/* <div style={{
          padding: '4px 12px',
          borderRadius: '12px',
          backgroundColor: healthScore >= 80 ? '#14532d' : healthScore >= 50 ? '#713f12' : '#7f1d1d',
          fontSize: '14px',
          fontWeight: 'bold',
        }}>
          ❤️ {healthScore}
        </div> */}
      </div>

      {/* ECOD Current Status */}
      {ecodInsights && (
        <div style={{
          backgroundColor: getStatusColor(ecodInsights.status).bg,
          border: `1px solid ${getStatusColor(ecodInsights.status).border}`,
          borderRadius: '8px',
          padding: '12px',
        }}>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            marginBottom: '8px',
          }}>
            <span style={{ fontSize: '18px' }}>
              {ecodInsights.status === 'critical' ? '🚨' : ecodInsights.status === 'warning' ? '⚠️' : '✅'}
            </span>
            <span style={{ 
              fontSize: '13px', 
              fontWeight: 'bold',
              color: getStatusColor(ecodInsights.status).text,
            }}>
              현재 상태
            </span>
          </div>
          <p style={{ 
            margin: '0 0 8px', 
            fontSize: '12px',
            color: '#e2e8f0',
            lineHeight: '1.4',
          }}>
            {ecodInsights.message}
          </p>
          {/* Metric breakdown */}
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            {ecodInsights.details.map((d, i) => (
              <span key={i} style={{
                padding: '2px 8px',
                borderRadius: '4px',
                fontSize: '10px',
                backgroundColor: d.severity === 'warning' ? '#854d0e' : '#166534',
                color: d.severity === 'warning' ? '#fef3c7' : '#dcfce7',
              }}>
                {d.metric}: {typeof d.value === 'number' ? d.value.toFixed(1) : d.value}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* ARIMA Prediction Alerts */}
      <div style={{ flex: 1 }}>
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          marginBottom: '8px',
        }}>
          <span style={{ fontSize: '16px' }}>🔮</span>
          <span style={{ fontSize: '12px', color: '#94a3b8' }}>예측 경보</span>
        </div>
        
        {arimaForecasts.length === 0 ? (
          <div style={{
            backgroundColor: '#0f172a',
            borderRadius: '8px',
            padding: '12px',
            textAlign: 'center',
          }}>
            <span style={{ fontSize: '20px' }}>😊</span>
            <p style={{ 
              margin: '8px 0 0', 
              fontSize: '11px', 
              color: '#64748b',
            }}>
              향후 2시간 내 예상되는 문제 없음
            </p>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {arimaForecasts.map((alert, i) => {
              const colors = getStatusColor(alert.severity);
              return (
                <div key={i} style={{
                  backgroundColor: colors.bg,
                  border: `1px solid ${colors.border}`,
                  borderRadius: '8px',
                  padding: '10px 12px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '10px',
                }}>
                  <span style={{ fontSize: '16px' }}>
                    {alert.severity === 'critical' ? '🔴' : '🟡'}
                  </span>
                  <div style={{ flex: 1 }}>
                    <div style={{ 
                      fontSize: '12px', 
                      fontWeight: 'bold',
                      color: colors.text,
                    }}>
                      {formatMinutes(alert.minutes)} 후 {alert.metric} {alert.severity === 'critical' ? '위험' : '주의'}
                    </div>
                    <div style={{ 
                      fontSize: '10px', 
                      color: '#94a3b8',
                      marginTop: '2px',
                    }}>
                      예측값: {alert.value.toFixed(1)}%
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
