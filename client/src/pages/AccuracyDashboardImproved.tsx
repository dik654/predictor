import React, { useMemo, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import { useAccuracyData } from '../hooks/useAccuracyData';
import { Target } from 'lucide-react';

export function AccuracyDashboardImproved() {
  const [selectedMetric, setSelectedMetric] = useState('CPU');
  const [selectedHorizon, setSelectedHorizon] = useState(360); // Start with 6-hour horizon which has data
  const [selectedBucket, setSelectedBucket] = useState<'pos_metrics' | 'sample_metrics'>('pos_metrics');

  const ALL_METRICS = ['CPU', 'Memory', 'DiskIO', 'NetworkSent', 'NetworkRecv'];
  const { data: currentData, error: currentError, loading: isLoading } = useAccuracyData('V135-POS-03', selectedMetric, selectedHorizon, 30000, selectedBucket);

  const hasAnyData = currentData?.records?.length > 0;

  const errorChartOption = useMemo(() => {
    if (!currentData?.records?.length) {
      return {
        backgroundColor: 'transparent',
        title: { text: 'No data', textStyle: { color: '#e2e8f0' } },
        grid: { left: '8%', right: '5%', top: '12%', bottom: '12%' },
        xAxis: { type: 'category', data: [], boundaryGap: false, axisLabel: { color: '#94a3b8', fontSize: 10 }, axisLine: { lineStyle: { color: '#1f2937' } } },
        yAxis: { type: 'value', axisLabel: { formatter: '{value}%', color: '#94a3b8', fontSize: 10 }, axisLine: { lineStyle: { color: '#1f2937' } }, splitLine: { lineStyle: { color: '#1e293b' } } },
        series: [{ data: [], type: 'line', smooth: true, itemStyle: { color: '#3b82f6' }, areaStyle: { color: 'rgba(59, 130, 246, 0.15)' }, lineStyle: { width: 2.5 } }]
      };
    }

    const filteredRecords = currentData.records.filter(r => r.error_pct != null);
    const timestamps = filteredRecords.map(r => new Date(r.timestamp).toLocaleTimeString());
    const rawErrors = filteredRecords.map(r => parseFloat(r.error_pct.toFixed(2)));

    // 이상치 제거: 상위 5% 오차 제외 (보통 매우 큰 값들)
    const sortedErrors = [...rawErrors].sort((a, b) => a - b);
    const percentile95Index = Math.ceil(sortedErrors.length * 0.95);
    const maxNormalError = sortedErrors[percentile95Index - 1] || 100;

    // 정상/이상치 데이터 분리 (시각화용)
    const normalData = rawErrors.map((e, i) => e <= maxNormalError ? e : null);
    const outlierData = rawErrors.map((e, i) => e > maxNormalError ? e : null);

    // Y축 범위 자동 설정 (최대값에 여유 두기)
    const yAxisMax = Math.ceil(maxNormalError * 1.2);

    return {
      backgroundColor: 'transparent',
      title: { text: `오차율 - ${selectedMetric} (${selectedHorizon}분)`, left: 'center', textStyle: { fontSize: 14, fontWeight: '600', color: '#e2e8f0' } },
      tooltip: {
        trigger: 'axis',
        formatter: (p: any) => {
          if (!p.length) return '';
          const idx = p[0].dataIndex;
          const actualError = rawErrors[idx];
          return `${p[0].axisValue}: ${actualError > maxNormalError ? '⚠️ 이상치 ' : ''}${actualError.toFixed(1)}% 오차`;
        },
        backgroundColor: 'rgba(15, 23, 42, 0.95)',
        borderColor: '#475569',
        textStyle: { color: '#e2e8f0' }
      },
      grid: { left: '8%', right: '5%', top: '12%', bottom: '12%' },
      xAxis: { type: 'category', data: timestamps, boundaryGap: false, axisLabel: { color: '#94a3b8', fontSize: 10 }, axisLine: { lineStyle: { color: '#1f2937' } } },
      yAxis: { type: 'value', max: yAxisMax, axisLabel: { formatter: '{value}%', color: '#94a3b8', fontSize: 10 }, axisLine: { lineStyle: { color: '#1f2937' } }, splitLine: { lineStyle: { color: '#1e293b' } } },
      series: [
        {
          name: '정상 오차',
          data: normalData,
          type: 'scatter',
          symbolSize: 6,
          itemStyle: { color: '#3b82f6' },
          lineStyle: { width: 0 }
        },
        {
          name: '이상치 ⚠️',
          data: outlierData,
          type: 'scatter',
          symbolSize: 10,
          itemStyle: { color: '#ef4444', borderColor: '#fca5a5', borderWidth: 2 },
          lineStyle: { width: 0 }
        },
        {
          name: '추세',
          data: rawErrors.map(e => Math.min(e, maxNormalError * 1.1)),
          type: 'line',
          smooth: true,
          lineStyle: { width: 2, color: '#3b82f6', opacity: 0.5 },
          areaStyle: { color: 'rgba(59, 130, 246, 0.08)' },
          symbolSize: 0
        }
      ]
    };
  }, [currentData, selectedMetric, selectedHorizon]);

  const stats = useMemo(() => {
    if (!currentData?.records?.length) return { avg: '-', max: '-', min: '-', latest: '-', confidence: '0', count: '0', outliers: '0', hasData: false };
    const validRecords = currentData.records.filter(r => r.error_pct != null);
    if (validRecords.length === 0) return { avg: '-', max: '-', min: '-', latest: '-', confidence: '0', count: '0', outliers: '0', hasData: false };
    const rawErrors = validRecords.map(r => parseFloat(r.error_pct.toFixed(2)));

    // 이상치 제거: 상위 5% 제외
    const sortedErrors = [...rawErrors].sort((a, b) => a - b);
    const percentile95Index = Math.ceil(sortedErrors.length * 0.95);
    const maxNormalError = sortedErrors[percentile95Index - 1] || 100;

    // 이상치를 제외한 정상 오차들
    const normalErrors = rawErrors.filter(e => e <= maxNormalError);
    const outlierCount = rawErrors.filter(e => e > maxNormalError).length;

    // horizon별 허용 오차 기준 (먼 미래일수록 넓게)
    // 설명: "1시간 예측은 5% 이내가 우수, 2일 예측은 30% 이내도 우수로 인정"
    const HORIZON_THRESHOLD: Record<number, { excellent: number; good: number }> = {
      60: { excellent: 5, good: 10 },
      360: { excellent: 12, good: 20 },
      720: { excellent: 15, good: 25 },
      1440: { excellent: 8, good: 15 },
      2880: { excellent: 10, good: 18 },
    };
    const th = HORIZON_THRESHOLD[selectedHorizon] || { excellent: 10, good: 20 };
    const excellent = normalErrors.filter(e => e < th.excellent).length;
    const good = normalErrors.filter(e => e >= th.excellent && e < th.good).length;
    const accuracy = normalErrors.length > 0 ? ((excellent + good * 0.8) / normalErrors.length * 100).toFixed(0) : '0';

    return {
      avg: normalErrors.length > 0 ? (normalErrors.reduce((a, b) => a + b, 0) / normalErrors.length).toFixed(2) : '-',
      max: normalErrors.length > 0 ? Math.max(...normalErrors).toFixed(2) : '-',
      min: normalErrors.length > 0 ? Math.min(...normalErrors).toFixed(2) : '-',
      latest: rawErrors[rawErrors.length - 1]?.toFixed(2) || '0',
      confidence: accuracy,
      count: normalErrors.length.toString(),
      outliers: outlierCount.toString(),
      hasData: true
    };
  }, [currentData]);

  const trustLevel = parseInt(stats.confidence || '0');
  const trustColor = trustLevel > 80 ? '#10b981' : trustLevel > 60 ? '#f59e0b' : '#ef4444';
  const trustMessage = trustLevel > 80 ? '높은 신뢰도 - 중요한 의사결정에 사용 가능' : trustLevel > 60 ? '중간 신뢰도 - 검증 후 사용' : '낮은 신뢰도 - 수동 검토 필수';

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0a0e1a', color: '#e2e8f0', display: 'flex', flexDirection: 'column', fontFamily: "'Inter', -apple-system, sans-serif" }}>
      {/* Header */}
      <header style={{
        backgroundColor: '#111827',
        border: '1px solid #1f2937',
        borderRadius: '10px',
        margin: '24px',
        marginBottom: '0',
        padding: '14px 24px',
        display: 'flex',
        alignItems: 'center',
        gap: '12px',
      }}>
        <Target size={20} color="#3b82f6" />
        <div>
          <h1 style={{ margin: 0, fontSize: '16px', fontWeight: 600, color: '#f1f5f9' }}>Accuracy Analytics</h1>
          <p style={{ margin: 0, fontSize: '12px', color: '#cbd5e1' }}>예측 정확도 분석 — 예측을 얼마나 신뢰할 수 있는가?</p>
        </div>
      </header>

      <main style={{ flex: 1, padding: '24px', overflow: 'auto' }}>
        {/* Loading/Error State */}
        {isLoading && (
          <div style={{ backgroundColor: '#111827', borderRadius: '10px', border: '1px solid #1f2937', padding: '20px', marginBottom: '24px', textAlign: 'center', color: '#cbd5e1' }}>
            Loading accuracy data...
          </div>
        )}
        {currentError && (
          <div style={{ backgroundColor: '#111827', borderRadius: '10px', border: '1px solid #ef4444', borderLeft: '3px solid #ef4444', padding: '20px', marginBottom: '24px' }}>
            <p style={{ margin: 0, color: '#ef4444', fontWeight: '600' }}>오류: {currentError}</p>
          </div>
        )}

        {/* Controls */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '12px', marginBottom: '24px' }}>
          <div style={{ backgroundColor: '#111827', borderRadius: '10px', border: '1px solid #1f2937', padding: '14px' }}>
            <p style={{ margin: '0 0 8px 0', fontSize: '11px', fontWeight: '600', color: '#cbd5e1', textTransform: 'uppercase', letterSpacing: '0.5px' }}>지표</p>
            <div style={{ display: 'flex', gap: '6px' }}>
              {ALL_METRICS.map(m => (
                <button key={m} onClick={() => setSelectedMetric(m)} style={{ padding: '6px 14px', borderRadius: '6px', border: selectedMetric === m ? '1px solid #3b82f6' : '1px solid #1f2937', backgroundColor: selectedMetric === m ? '#1e293b' : 'transparent', color: selectedMetric === m ? '#3b82f6' : '#cbd5e1', cursor: 'pointer', fontSize: '11px', fontWeight: selectedMetric === m ? '600' : '400', transition: 'all 0.2s' }}>
                  {m}
                </button>
              ))}
            </div>
          </div>

          <div style={{ backgroundColor: '#111827', borderRadius: '10px', border: '1px solid #1f2937', padding: '14px' }}>
            <p style={{ margin: '0 0 8px 0', fontSize: '11px', fontWeight: '600', color: '#cbd5e1', textTransform: 'uppercase', letterSpacing: '0.5px' }}>데이터 소스</p>
            <div style={{ display: 'flex', gap: '6px' }}>
              {[
                { value: 'pos_metrics' as const, label: 'Live' },
                { value: 'sample_metrics' as const, label: 'Sample' },
              ].map(b => (
                <button key={b.value} onClick={() => setSelectedBucket(b.value)} style={{ padding: '6px 14px', borderRadius: '6px', border: selectedBucket === b.value ? '1px solid #10b981' : '1px solid #1f2937', backgroundColor: selectedBucket === b.value ? '#1e293b' : 'transparent', color: selectedBucket === b.value ? '#10b981' : '#cbd5e1', cursor: 'pointer', fontSize: '11px', fontWeight: selectedBucket === b.value ? '600' : '400', transition: 'all 0.2s' }}>
                  {b.label}
                </button>
              ))}
            </div>
          </div>

          <div style={{ backgroundColor: '#111827', borderRadius: '10px', border: '1px solid #1f2937', padding: '14px' }}>
            <p style={{ margin: '0 0 8px 0', fontSize: '11px', fontWeight: '600', color: '#cbd5e1', textTransform: 'uppercase', letterSpacing: '0.5px' }}>기간</p>
            <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
              {[
                { value: 60, label: '1시간' },
                { value: 360, label: '6시간' },
                { value: 720, label: '12시간' },
                { value: 1440, label: '1일' },
                { value: 2880, label: '2일' }
              ].map(h => (
                <button key={h.value} onClick={() => setSelectedHorizon(h.value)} style={{ padding: '6px 14px', borderRadius: '6px', border: selectedHorizon === h.value ? '1px solid #3b82f6' : '1px solid #1f2937', backgroundColor: selectedHorizon === h.value ? '#1e293b' : 'transparent', color: selectedHorizon === h.value ? '#3b82f6' : '#cbd5e1', cursor: 'pointer', fontSize: '11px', fontWeight: selectedHorizon === h.value ? '600' : '400', transition: 'all 0.2s' }}>
                  {h.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Primary KPI */}
        <div style={{
          backgroundColor: '#111827',
          borderRadius: '10px',
          border: `1px solid ${trustColor}40`,
          borderTop: `3px solid ${trustColor}`,
          padding: '28px',
          marginBottom: '24px',
          textAlign: 'center'
        }}>
          <p style={{ margin: '0 0 10px 0', fontSize: '12px', fontWeight: '600', color: '#cbd5e1', textTransform: 'uppercase', letterSpacing: '0.5px' }}>신뢰도 점수</p>
          {stats.hasData ? (<>
            <div style={{
              fontSize: '52px',
              fontWeight: '700',
              color: trustColor,
              marginBottom: '8px',
              letterSpacing: '-0.5px'
            }}>
              {stats.confidence}%
            </div>
            <p style={{ margin: '0 0 6px 0', fontSize: '13px', color: '#e2e8f0', fontWeight: '500' }}>
              {trustMessage}
            </p>
            <p style={{ margin: 0, fontSize: '11px', color: '#cbd5e1' }}>
              {stats.count}개 최근 예측 기반
            </p>
          </>) : (
            <div style={{ padding: '20px 0' }}>
              <div style={{ fontSize: '32px', color: '#475569', marginBottom: '8px' }}>—</div>
              <p style={{ margin: 0, fontSize: '13px', color: '#64748b' }}>예측 정확도 데이터가 아직 없습니다</p>
            </div>
          )}
        </div>

        {/* Metrics */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '12px', marginBottom: '24px' }}>
          <MetricCard label="최근 오차" value={`${stats.latest}%`} />
          <MetricCard label="평균 오차" value={`${stats.avg}%`} />
          <MetricCard label="최소 오차" value={`${stats.min}%`} />
          <MetricCard label="최대 오차" value={`${stats.max}%`} />
          <MetricCard label="⚠️ 이상치" value={`${stats.outliers}개`} />
        </div>

        {/* Chart */}
        <div style={{ backgroundColor: '#111827', borderRadius: '10px', border: '1px solid #1f2937', padding: '20px', marginBottom: '24px' }}>
          <ReactECharts option={errorChartOption} style={{ height: '350px' }} notMerge={true} />
        </div>

        {/* Decision Framework */}
        <div style={{ marginBottom: '24px' }}>
          <h3 style={{ margin: '0 0 16px 0', fontSize: '13px', fontWeight: '600', color: '#e2e8f0', textTransform: 'uppercase', letterSpacing: '0.5px' }}>의사결정 기준</h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '12px' }}>
            <DecisionBox title="사용 가능" threshold="80%+" color="#10b981" examples={['주요 의사결정', '장비 구매 계획', '위험 평가']} />
            <DecisionBox title="검증 필요" threshold="60-80%" color="#f59e0b" examples={['2차 검증 필요', '인적 검토 권고', '부분 자동화']} />
            <DecisionBox title="수동 검토" threshold="<60%" color="#ef4444" examples={['수동 검증 필수', '자동화 불가', '모델 개선']} />
          </div>
        </div>

        {/* Recent Records */}
        <div style={{ backgroundColor: '#111827', borderRadius: '10px', border: '1px solid #1f2937', padding: '20px', overflow: 'auto' }}>
          <h3 style={{ margin: '0 0 16px 0', fontSize: '13px', fontWeight: '600', color: '#e2e8f0' }}>최근 오차율</h3>
          {!currentData?.records?.length ? (
            <p style={{ color: '#cbd5e1', textAlign: 'center', padding: '32px', margin: 0 }}>데이터 없음</p>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid #1f2937' }}>
                    <th style={{ padding: '10px', textAlign: 'left', color: '#cbd5e1', fontWeight: '600', fontSize: '12px' }}>시간</th>
                    <th style={{ padding: '10px', textAlign: 'right', color: '#cbd5e1', fontWeight: '600', fontSize: '12px' }}>오차%</th>
                    <th style={{ padding: '10px', textAlign: 'center', color: '#cbd5e1', fontWeight: '600', fontSize: '12px' }}>신뢰도</th>
                  </tr>
                </thead>
                <tbody>
                  {currentData.records.slice(-10).reverse().map((r, i) => {
                    const error = r.error_pct != null ? parseFloat(r.error_pct.toFixed(2)) : 0;
                    const color = error < 5 ? '#10b981' : error < 10 ? '#3b82f6' : error < 20 ? '#f59e0b' : '#ef4444';
                    return (
                      <tr key={i} style={{ borderBottom: '1px solid #1e293b' }}>
                        <td style={{ padding: '8px 10px', color: '#cbd5e1' }}>{new Date(r.timestamp).toLocaleTimeString()}</td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', color, fontWeight: '600', fontFamily: 'monospace' }}>{error}%</td>
                        <td style={{ padding: '8px 10px', textAlign: 'center' }}>
                          <span style={{ padding: '2px 6px', borderRadius: '3px', backgroundColor: `${color}20`, color, fontSize: '9px', fontWeight: '600', textTransform: 'uppercase' }}>
                            {error < 5 ? '우수' : error < 10 ? '양호' : error < 20 ? '보통' : '미흡'}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div style={{
      backgroundColor: '#111827',
      borderRadius: '10px',
      border: '1px solid #1f2937',
      padding: '14px',
      textAlign: 'center'
    }}>
      <p style={{ margin: '0 0 6px 0', fontSize: '12px', fontWeight: '600', color: '#cbd5e1', textTransform: 'uppercase', letterSpacing: '0.5px' }}>{label}</p>
      <p style={{ margin: 0, fontSize: '24px', fontWeight: '700', color: '#3b82f6' }}>{value}</p>
    </div>
  );
}

function DecisionBox({ title, threshold, color, examples }: { title: string; threshold: string; color: string; examples: string[] }) {
  return (
    <div style={{
      backgroundColor: '#111827',
      borderRadius: '10px',
      border: `1px solid ${color}40`,
      borderLeft: `3px solid ${color}`,
      padding: '16px'
    }}>
      <h4 style={{ margin: '0 0 8px 0', fontSize: '14px', fontWeight: '700', color }}>{title}</h4>
      <p style={{ margin: '0 0 10px 0', fontSize: '13px', color: '#cbd5e1', fontWeight: '500' }}>신뢰도: {threshold}</p>
      <ul style={{ margin: 0, paddingLeft: '16px', fontSize: '13px', color: '#cbd5e1' }}>
        {examples.map((ex, i) => (
          <li key={i} style={{ margin: '2px 0' }}>{ex}</li>
        ))}
      </ul>
    </div>
  );
}
