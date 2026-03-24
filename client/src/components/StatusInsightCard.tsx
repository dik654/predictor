import { useMemo } from 'react';
import { Crosshair, AlertOctagon, AlertTriangle, CheckCircle, Eye } from 'lucide-react';

interface Detection {
  engine: string;
  metric: string;
  score: number;
  severity: string;
  confidence?: number;
  details?: string;
  arima_predicted?: number;
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
    // 같은 metric이 여러 ECOD 사이클에서 올 수 있으므로 최신 값만 사용
    const latestByMetric = new Map<string, typeof ecodResults[0]>();
    for (const d of ecodResults) {
      latestByMetric.set(d.metric, d);
    }
    const multivariate = latestByMetric.get('Multivariate');
    const metrics = [...latestByMetric.values()].filter(d => d.metric !== 'Multivariate');
    
    // 데이터 없을 때 기본값
    if (!multivariate) {
      return {
        status: 'normal' as const,
        message: '데이터 수집 중...',
        score: 0,
        details: [],
      };
    }
    
    // score 기준으로 판단 — 시스템 메트릭만 (바 표시와 동일 기준)
    const SYSTEM_METRICS = ['CPU', 'Memory', 'DiskIO', 'NetworkSent', 'NetworkRecv'];
    const sysMetrics = metrics.filter(d => SYSTEM_METRICS.includes(d.metric));
    const criticalMetrics = sysMetrics.filter(d => d.score >= 0.8);
    const warningMetrics = sysMetrics.filter(d => d.score >= 0.5 && d.score < 0.8);

    let status: 'normal' | 'warning' | 'critical' = 'normal';
    let message = '모든 시스템 지표가 정상 범위입니다.';

    if (criticalMetrics.length > 0 || multivariate.severity === 'critical') {
      status = 'critical';
      const names = criticalMetrics.map(w => w.metric).join(', ');
      message = `${names || '복합 지표'}에서 이상이 감지되었습니다!`;
    } else if (warningMetrics.length > 0 || multivariate.severity === 'warning') {
      status = 'warning';
      const names = warningMetrics.map(w => w.metric).join(', ');
      message = `${names || '일부 지표'}에서 주의가 필요합니다.`;
    }
    
    return {
      status,
      message,
      score: multivariate.score,
      details: metrics
        .filter(m => ['CPU', 'Memory', 'DiskIO', 'NetworkSent', 'NetworkRecv'].includes(m.metric))
        .map(m => ({
          metric: m.metric,
          score: m.score,
          severity: m.severity,
          description: m.details || '',
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
      backgroundColor: '#111827',
      border: '1px solid #1f2937',
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
        <h3 style={{ margin: 0, fontSize: '14px', color: '#cbd5e1', display: 'flex', alignItems: 'center', gap: '6px' }}>
          <Crosshair size={14} /> 시스템 상태 분석
        </h3>
      </div>
      <div style={{ fontSize: '11px', color: '#475569', lineHeight: '1.5' }}>
        최근 수집된 데이터 기반 분석. 짧은 구간에서는 일시적 변동으로 수치가 튈 수 있으며, 지속적인 추세를 함께 확인하세요.
      </div>

      {/* ECOD Current Status */}
      {ecodInsights && (
        <div style={{
          backgroundColor: getStatusColor(ecodInsights.status).bg,
          border: `1px solid ${getStatusColor(ecodInsights.status).border}`,
          borderRadius: '8px',
          padding: '12px',
          transition: 'all 0.3s ease',
        }}>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            marginBottom: '8px',
          }}>
            <span style={{ fontSize: '18px', display: 'flex', alignItems: 'center', color: getStatusColor(ecodInsights.status).text }}>
              {ecodInsights.status === 'critical' ? <AlertOctagon size={18} /> : ecodInsights.status === 'warning' ? <AlertTriangle size={18} /> : <CheckCircle size={18} />}
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
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {ecodInsights.details.map((d, i) => {
              const barColor = d.score >= 0.8 ? '#ef4444' : d.score >= 0.5 ? '#f59e0b' : '#22c55e';
              const labelColor = d.score >= 0.8 ? '#fca5a5' : d.score >= 0.5 ? '#fcd34d' : '#86efac';
              const labelText = d.score >= 0.8 ? '이상 감지' : d.score >= 0.5 ? '주의 필요' : '정상';
              return (
                <div key={i} style={{ marginBottom: '2px' }}>
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    fontSize: '11px',
                  }}>
                    <span style={{ color: '#e2e8f0', minWidth: '80px', flexShrink: 0 }}>{d.metric}</span>
                    <div style={{
                      flex: 1,
                      height: '6px',
                      backgroundColor: 'rgba(255,255,255,0.1)',
                      borderRadius: '3px',
                      overflow: 'hidden',
                    }}>
                      <div style={{
                        width: `${Math.min(d.score * 100, 100)}%`,
                        height: '100%',
                        backgroundColor: barColor,
                        borderRadius: '3px',
                        transition: 'width 0.3s ease',
                      }} />
                    </div>
                    <span style={{ color: labelColor, minWidth: '52px', textAlign: 'right', flexShrink: 0, fontSize: '11px', fontWeight: 600 }}>
                      {labelText}
                    </span>
                  </div>
                  {d.description && (
                    <div style={{ fontSize: '11px', color: '#cbd5e1', paddingLeft: '88px', marginTop: '1px' }}>
                      {d.description}
                    </div>
                  )}
                </div>
              );
            })}
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
          <span style={{ display: 'flex', alignItems: 'center', color: '#cbd5e1' }}><Eye size={14} /></span>
          <span style={{ fontSize: '12px', color: '#cbd5e1' }}>예측 경보</span>
        </div>
        
        {arimaForecasts.length === 0 ? (
          <div style={{
            backgroundColor: '#0f172a',
            borderRadius: '8px',
            padding: '12px',
            textAlign: 'center',
          }}>
            <span style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#22c55e' }}><CheckCircle size={20} /></span>
            <p style={{
              margin: '8px 0 0',
              fontSize: '12px',
              color: '#cbd5e1',
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
                  <span style={{ display: 'flex', alignItems: 'center' }}>
                    {alert.severity === 'critical'
                      ? <span style={{width:8,height:8,borderRadius:'50%',backgroundColor:'#ef4444',display:'inline-block'}} />
                      : <span style={{width:8,height:8,borderRadius:'50%',backgroundColor:'#f59e0b',display:'inline-block'}} />}
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
                      fontSize: '11px',
                      color: '#cbd5e1',
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
