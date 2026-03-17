import { useMemo } from 'react';

interface Detection {
  timestamp: string;
  engine: string;
  metric: string;
  value: number;
  score: number;
  threshold: number;
  severity: string;
  confidence: number;
  forecast?: number;
  residual?: number;
  details?: string;
}

interface Props {
  detections: Detection[];
}

export function AnomalyDiagnosis({ detections }: Props) {
  const diagnosis = useMemo(() => {
    if (detections.length === 0) return null;

    // 최근 타임스탬프 그룹 찾기 (같은 시점의 탐지 결과)
    const latestTs = detections[detections.length - 1]?.timestamp;
    if (!latestTs) return null;

    const latest = detections.filter(d => d.timestamp === latestTs);

    // ECOD feature별 점수
    const ecodMultivariate = latest.find(d => d.engine === 'ecod' && d.metric === 'Multivariate');
    const ecodFeatures = latest
      .filter(d => d.engine === 'ecod' && d.metric !== 'Multivariate')
      .sort((a, b) => b.score - a.score);

    // ARIMA 잔차
    const arimaResults = latest
      .filter(d => d.engine === 'arima')
      .sort((a, b) => (b.residual ?? 0) - (a.residual ?? 0));

    // ECOD feature 기여도 계산 (%)
    const totalEcodScore = ecodFeatures.reduce((sum, d) => sum + d.score, 0) || 1;
    const ecodContributions = ecodFeatures.map(d => ({
      metric: d.metric,
      score: d.score,
      pct: (d.score / totalEcodScore) * 100,
      severity: d.severity,
    }));

    // 전체 심각도 판정
    const overallSeverity = ecodMultivariate?.severity
      || (ecodFeatures.some(d => d.severity === 'critical') ? 'critical'
        : ecodFeatures.some(d => d.severity === 'warning') ? 'warning'
          : 'normal');

    // 주요 원인 요약
    const topCause = ecodContributions[0];

    return {
      timestamp: latestTs,
      overallScore: ecodMultivariate?.score ?? 0,
      overallSeverity,
      ecodContributions,
      arimaResults,
      topCause,
    };
  }, [detections]);

  if (!diagnosis) {
    return (
      <div style={{
        backgroundColor: '#1e293b', borderRadius: 12, padding: 32,
        textAlign: 'center', color: '#64748b',
      }}>
        탐지 데이터 대기 중...
      </div>
    );
  }

  const severityConfig = {
    critical: { color: '#ef4444', bg: '#450a0a', border: '#991b1b', label: '이상 감지', emoji: '🔴' },
    warning: { color: '#f59e0b', bg: '#451a03', border: '#92400e', label: '주의 관찰', emoji: '🟡' },
    normal: { color: '#22c55e', bg: '#052e16', border: '#166534', label: '정상', emoji: '🟢' },
  };
  const sev = severityConfig[diagnosis.overallSeverity as keyof typeof severityConfig] || severityConfig.normal;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* 상단: 전체 상태 요약 */}
      <div style={{
        backgroundColor: sev.bg, border: `1px solid ${sev.border}`,
        borderRadius: 12, padding: '16px 20px',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ fontSize: 28 }}>{sev.emoji}</span>
            <div>
              <div style={{ fontSize: 18, fontWeight: 700, color: sev.color }}>
                {sev.label}
                {diagnosis.topCause && diagnosis.overallSeverity !== 'normal' && (
                  <span style={{ fontSize: 14, fontWeight: 400, color: '#94a3b8', marginLeft: 8 }}>
                    — {diagnosis.topCause.metric}이(가) 주요 원인 ({diagnosis.topCause.pct.toFixed(0)}%)
                  </span>
                )}
              </div>
              <div style={{ fontSize: 12, color: '#64748b', marginTop: 4 }}>
                {new Date(diagnosis.timestamp).toLocaleString('ko-KR')}
              </div>
            </div>
          </div>
          <div style={{
            fontSize: 24, fontWeight: 700, color: sev.color,
          }}>
            {(diagnosis.overallScore * 100).toFixed(0)}%
          </div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        {/* 좌: ECOD Feature별 점수 분해 */}
        <div style={{
          backgroundColor: '#1e293b', borderRadius: 12, padding: 16,
        }}>
          <h4 style={{ margin: '0 0 12px', fontSize: 14, color: '#e2e8f0' }}>
            ECOD Feature 기여도
          </h4>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {diagnosis.ecodContributions.map(c => (
              <div key={c.metric}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
                  <span style={{ color: '#e2e8f0', fontWeight: 600 }}>{c.metric}</span>
                  <span style={{ color: c.severity === 'warning' ? '#f59e0b' : c.severity === 'critical' ? '#ef4444' : '#94a3b8' }}>
                    {c.pct.toFixed(0)}% ({c.score.toFixed(3)})
                  </span>
                </div>
                <div style={{
                  height: 8, backgroundColor: '#0f172a', borderRadius: 4, overflow: 'hidden',
                }}>
                  <div style={{
                    height: '100%',
                    width: `${Math.min(c.pct, 100)}%`,
                    backgroundColor: c.pct >= 50 ? '#ef4444' : c.pct >= 30 ? '#f59e0b' : '#3b82f6',
                    borderRadius: 4,
                    transition: 'width 0.3s ease',
                  }} />
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* 우: ARIMA 잔차 분석 */}
        <div style={{
          backgroundColor: '#1e293b', borderRadius: 12, padding: 16,
        }}>
          <h4 style={{ margin: '0 0 12px', fontSize: 14, color: '#e2e8f0' }}>
            ARIMA 예측 잔차
          </h4>
          {diagnosis.arimaResults.length === 0 ? (
            <div style={{ color: '#64748b', fontSize: 12, textAlign: 'center', padding: 20 }}>
              ARIMA 데이터 수집 중...
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {diagnosis.arimaResults.map(a => {
                const residualPct = a.threshold > 0 ? ((a.residual ?? 0) / a.threshold) * 100 : 0;
                const isOver = (a.residual ?? 0) > a.threshold;
                return (
                  <div key={a.metric}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 2 }}>
                      <span style={{ color: '#e2e8f0', fontWeight: 600 }}>{a.metric}</span>
                      <span style={{ color: isOver ? '#ef4444' : '#94a3b8' }}>
                        잔차 {a.residual?.toFixed(2)} / 임계 {a.threshold.toFixed(2)}
                      </span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, color: '#64748b' }}>
                      <span>실제 {a.value.toFixed(1)}</span>
                      <span>→</span>
                      <span>예측 {a.forecast?.toFixed(1)}</span>
                      {isOver && (
                        <span style={{
                          marginLeft: 'auto',
                          color: '#ef4444', fontWeight: 600,
                          padding: '1px 6px', borderRadius: 4,
                          backgroundColor: '#450a0a', fontSize: 10,
                        }}>
                          임계 초과 ({residualPct.toFixed(0)}%)
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
