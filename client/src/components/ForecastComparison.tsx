import React, { useState, useMemo } from 'react';
import EChartsReact from 'echarts-for-react';
import { TrendingUp, HardDrive, AlertCircle, Cpu } from 'lucide-react';
import { useForecastComparison } from '../hooks/useForecastComparison';

export function ForecastComparison() {
  const [selectedBucket, setSelectedBucket] = useState<'pos_metrics' | 'sample_metrics'>('pos_metrics');
  const [selectedMetric, setSelectedMetric] = useState<'cpu' | 'memory'>('cpu');
  const [selectedHorizon, setSelectedHorizon] = useState<'60' | '360' | '720' | '1440' | '2880'>('60');

  const { data, loading, error } = useForecastComparison(
    'V135-POS-03',
    selectedMetric,
    selectedHorizon,
    selectedBucket,
  );

  const chartData = useMemo(() => {
    const empty = { times: [], actual: [], forecast: [], latestActual: null as number | null, latestForecast: null as number | null, medianError: null as number | null, errorCount: 0 };
    if (!data || !data.records || data.records.length === 0) return empty;

    const records = data.records;
    const errors = records.filter(r => r.error_pct != null).map(r => r.error_pct).sort((a, b) => a - b);
    const medianError = errors.length > 0
      ? errors.length % 2 === 0
        ? (errors[errors.length / 2 - 1] + errors[errors.length / 2]) / 2
        : errors[Math.floor(errors.length / 2)]
      : null;

    return {
      times: records.map(r => new Date(r.time).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })),
      actual: records.map(r => r.actual_value != null ? parseFloat(r.actual_value.toFixed(2)) : null),
      forecast: records.map(r => r.forecast_value != null ? parseFloat(r.forecast_value.toFixed(2)) : null),
      latestActual: records[records.length - 1].actual_value ?? null,
      latestForecast: records[records.length - 1].forecast_value ?? null,
      medianError,
      errorCount: errors.length,
    };
  }, [data]);

  const accuracy = useMemo(() => {
    if (chartData.medianError === null) return { error: null, status: 'Insufficient Data', color: '#9ca3af' };
    const e = chartData.medianError;
    const status = e < 5 ? 'Excellent' : e < 10 ? 'Good' : e < 20 ? 'Fair' : 'Poor';
    const color = e < 5 ? '#10b981' : e < 10 ? '#3b82f6' : e < 20 ? '#f59e0b' : '#ef4444';
    return { error: e.toFixed(1), status, color };
  }, [chartData]);

  const chartOption = {
    backgroundColor: 'transparent',
    grid: { left: 60, right: 20, top: 40, bottom: 60, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(15, 23, 42, 0.95)',
      borderColor: '#475569',
      textStyle: { color: '#e2e8f0' },
      borderWidth: 1,
    },
    legend: {
      data: ['Actual', 'Forecast'],
      textStyle: { color: '#94a3b8' },
      top: 10,
      itemGap: 20,
    },
    xAxis: {
      type: 'category',
      data: chartData.times,
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: { color: '#64748b', fontSize: 10, rotate: 30 },
      axisTick: { lineStyle: { color: '#334155' } },
    },
    yAxis: {
      type: 'value',
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: { color: '#64748b', fontSize: 11 },
      splitLine: { lineStyle: { color: '#1e293b' } },
    },
    series: [
      {
        name: 'Actual',
        type: 'line',
        data: chartData.actual,
        smooth: 0.4,
        itemStyle: { color: '#10b981' },
        lineStyle: { width: 2.5 },
        areaStyle: { color: 'rgba(16, 185, 129, 0.08)' },
      },
      {
        name: 'Forecast',
        type: 'line',
        data: chartData.forecast,
        smooth: 0.4,
        itemStyle: { color: '#3b82f6' },
        lineStyle: { width: 2.5, type: 'dashed' },
        areaStyle: { color: 'rgba(59, 130, 246, 0.08)' },
      },
    ],
  };

  const metrics = [
    { key: 'cpu' as const, label: 'CPU', icon: Cpu, color: '#ef4444' },
    { key: 'memory' as const, label: 'Memory', icon: HardDrive, color: '#f59e0b' },
  ];

  if (loading) {
    return (
      <div style={{ backgroundColor: '#1e293b', borderRadius: '8px', padding: '48px', textAlign: 'center', color: '#94a3b8', border: '1px solid #334155' }}>
        <div style={{ fontSize: '14px' }}>Loading data...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ backgroundColor: '#1e293b', borderRadius: '8px', padding: '48px', textAlign: 'center', color: '#f87171', border: '1px solid #334155', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '12px' }}>
        <AlertCircle size={20} />
        <div style={{ fontSize: '14px' }}>Error: {error}</div>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
      {/* Controls row */}
      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', backgroundColor: '#1e293b', borderRadius: '8px', padding: '16px', border: '1px solid #334155', alignItems: 'center' }}>
        {/* Bucket toggle */}
        <div style={{ display: 'flex', gap: '8px' }}>
          {([{ value: 'pos_metrics', label: 'Live' }, { value: 'sample_metrics', label: 'Sample' }] as const).map(b => (
            <button
              key={b.value}
              onClick={() => setSelectedBucket(b.value)}
              style={{
                padding: '8px 14px', borderRadius: '6px',
                border: selectedBucket === b.value ? '1.5px solid #22c55e' : '1px solid #334155',
                backgroundColor: selectedBucket === b.value ? '#22c55e15' : 'transparent',
                color: selectedBucket === b.value ? '#22c55e' : '#94a3b8',
                cursor: 'pointer', fontSize: '12px', fontWeight: selectedBucket === b.value ? '600' : '400',
              }}
            >
              {b.label}
            </button>
          ))}
        </div>

        <div style={{ width: '1px', height: '28px', backgroundColor: '#334155' }} />

        {/* Metric toggle */}
        {metrics.map(({ key, label, icon: Icon, color }) => (
          <button
            key={key}
            onClick={() => setSelectedMetric(key)}
            style={{
              display: 'flex', alignItems: 'center', gap: '8px',
              padding: '8px 14px', borderRadius: '6px',
              border: selectedMetric === key ? `1.5px solid ${color}` : '1px solid #334155',
              backgroundColor: selectedMetric === key ? `${color}15` : 'transparent',
              color: selectedMetric === key ? color : '#94a3b8',
              cursor: 'pointer', fontSize: '13px', fontWeight: selectedMetric === key ? '600' : '400',
            }}
          >
            <Icon size={15} />
            {label}
          </button>
        ))}
      </div>

      {/* Main layout */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 300px', gap: '24px' }}>
        {/* Chart */}
        <div style={{ backgroundColor: '#1e293b', borderRadius: '8px', padding: '24px', border: '1px solid #334155' }}>
          <div style={{ marginBottom: '20px', fontSize: '15px', fontWeight: '600', color: '#e2e8f0', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <TrendingUp size={17} style={{ color: '#94a3b8' }} />
            예측 vs 실제 ({selectedMetric.toUpperCase()}, {selectedHorizon}분 후)
          </div>
          {data && data.records.length === 0 ? (
            <div style={{ height: '380px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#64748b', fontSize: '14px' }}>
              No matched forecast/actual pairs yet
            </div>
          ) : (
            <EChartsReact option={chartOption} style={{ height: '380px' }} />
          )}
        </div>

        {/* Right panel */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {/* Latest actual */}
          <div style={{ backgroundColor: '#0f172a', borderRadius: '8px', padding: '20px', border: '1px solid #10b98140', borderLeft: '3px solid #10b981' }}>
            <div style={{ fontSize: '11px', fontWeight: '600', color: '#64748b', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
              최근 실제값
            </div>
            <div style={{ fontSize: '28px', fontWeight: '700', color: '#10b981', marginBottom: '4px' }}>
              {chartData.latestActual !== null ? chartData.latestActual.toFixed(2) : '—'}
            </div>
            <div style={{ fontSize: '12px', color: '#64748b' }}>Latest actual value</div>
          </div>

          {/* Latest forecast */}
          <div style={{ backgroundColor: '#0f172a', borderRadius: '8px', padding: '20px', border: '1px solid #3b82f640', borderLeft: '3px solid #3b82f6' }}>
            <div style={{ fontSize: '11px', fontWeight: '600', color: '#64748b', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
              해당 시점 예측값
            </div>
            <div style={{ fontSize: '28px', fontWeight: '700', color: '#3b82f6', marginBottom: '4px' }}>
              {chartData.latestForecast !== null ? chartData.latestForecast.toFixed(2) : '—'}
            </div>
            <div style={{ fontSize: '12px', color: '#64748b' }}>{selectedHorizon}분 전 예측</div>
          </div>

          {/* Mean accuracy */}
          <div style={{ backgroundColor: '#0f172a', borderRadius: '8px', padding: '20px', border: `1px solid ${accuracy.color}40`, borderLeft: `3px solid ${accuracy.color}` }}>
            <div style={{ fontSize: '11px', fontWeight: '600', color: '#64748b', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
              평균 오차율
            </div>
            <div style={{ fontSize: '28px', fontWeight: '700', color: accuracy.color, marginBottom: '4px' }}>
              {accuracy.error !== null ? `${accuracy.error}%` : '—'}
            </div>
            <div style={{ fontSize: '12px', color: '#64748b' }}>{accuracy.status} ({chartData.errorCount}건 중앙값)</div>
          </div>

          {/* Horizon selector */}
          <div style={{ backgroundColor: '#0f172a', borderRadius: '8px', padding: '16px', border: '1px solid #334155' }}>
            <div style={{ fontSize: '11px', fontWeight: '600', color: '#64748b', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
              예측 구간
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              {([
                { value: '60' as const, label: '1시간' },
                { value: '360' as const, label: '6시간' },
                { value: '720' as const, label: '12시간' },
                { value: '1440' as const, label: '1일' },
                { value: '2880' as const, label: '2일' },
              ]).map(({ value, label }) => (
                <button
                  key={value}
                  onClick={() => setSelectedHorizon(value)}
                  style={{
                    padding: '9px 12px', borderRadius: '6px',
                    border: selectedHorizon === value ? '1px solid #3b82f6' : '1px solid #334155',
                    backgroundColor: selectedHorizon === value ? '#3b82f615' : 'transparent',
                    color: selectedHorizon === value ? '#3b82f6' : '#94a3b8',
                    cursor: 'pointer', fontSize: '13px', fontWeight: selectedHorizon === value ? '600' : '400',
                    textAlign: 'left', transition: 'all 0.2s',
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
