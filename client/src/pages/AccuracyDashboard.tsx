import React, { useMemo, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import { useAccuracyData } from '../hooks/useAccuracyData';

export function AccuracyDashboard() {
  const [selectedMetric, setSelectedMetric] = useState('CPU');
  const [selectedHorizon, setSelectedHorizon] = useState(30);

  const { data: cpuData } = useAccuracyData('V135-POS-03', 'CPU', selectedHorizon);
  const { data: memoryData } = useAccuracyData('V135-POS-03', 'Memory', selectedHorizon);
  const currentData = selectedMetric === 'CPU' ? cpuData : memoryData;

  // Error chart: time series of error percentages
  const errorChartOption = useMemo(() => {
    if (!currentData?.records?.length) {
      return { title: { text: 'No data available' } };
    }

    const timestamps = currentData.records.map(r => {
      const d = new Date(r.timestamp);
      return d.toLocaleTimeString();
    });
    const errors = currentData.records.map(r => parseFloat(r.error_pct.toFixed(2)));

    return {
      title: {
        text: `${selectedMetric} Prediction Error (${selectedHorizon}min horizon)`,
        left: 'center',
      },
      tooltip: {
        trigger: 'axis',
        formatter: '{b}: {c}%',
      },
      grid: { left: '5%', right: '5%', top: '15%', bottom: '10%', containLabel: true },
      xAxis: {
        type: 'category',
        data: timestamps,
        boundaryGap: false,
      },
      yAxis: {
        type: 'value',
        name: 'Error %',
        axisLabel: { formatter: '{value}%' },
      },
      series: [
        {
          data: errors,
          type: 'line',
          smooth: true,
          itemStyle: { color: '#ff6b6b' },
          areaStyle: { color: 'rgba(255, 107, 107, 0.1)' },
        },
      ],
    };
  }, [currentData, selectedMetric, selectedHorizon]);

  // Stats cards
  const stats = currentData?.stats;

  return (
    <div style={{ padding: '20px', backgroundColor: '#f5f5f5', minHeight: '100vh' }}>
      <h1>Prediction Accuracy Dashboard</h1>

      {/* Controls */}
      <div style={{ marginBottom: '20px', display: 'flex', gap: '20px' }}>
        <div>
          <label>Metric:</label>
          <select
            value={selectedMetric}
            onChange={e => setSelectedMetric(e.target.value)}
            style={{ marginLeft: '10px', padding: '5px' }}
          >
            <option value="CPU">CPU</option>
            <option value="Memory">Memory</option>
          </select>
        </div>

        <div>
          <label>Forecast Horizon:</label>
          <select
            value={selectedHorizon}
            onChange={e => setSelectedHorizon(Number(e.target.value))}
            style={{ marginLeft: '10px', padding: '5px' }}
          >
            <option value={30}>30 minutes</option>
            <option value={60}>1 hour</option>
            <option value={120}>2 hours</option>
          </select>
        </div>
      </div>

      {/* Stats Summary */}
      {stats && stats.count > 0 ? (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
            gap: '15px',
            marginBottom: '20px',
          }}
        >
          <StatCard
            title="Sample Count"
            value={stats.count}
            unit=""
          />
          <StatCard
            title="Mean Error"
            value={stats.mean_error_pct}
            unit="%"
            color={stats.mean_error_pct < 5 ? '#51cf66' : stats.mean_error_pct < 10 ? '#ffd93d' : '#ff6b6b'}
          />
          <StatCard
            title="Std Dev"
            value={stats.std_error}
            unit="%"
          />
          <StatCard
            title="Within 3σ"
            value={stats.within_3sigma_pct}
            unit="%"
            color={stats.within_3sigma_pct > 95 ? '#51cf66' : stats.within_3sigma_pct > 85 ? '#ffd93d' : '#ff6b6b'}
          />
          <StatCard
            title="Min Error"
            value={stats.min_error_pct}
            unit="%"
            color="#51cf66"
          />
          <StatCard
            title="Max Error"
            value={stats.max_error_pct}
            unit="%"
            color={stats.max_error_pct < 20 ? '#51cf66' : stats.max_error_pct < 40 ? '#ffd93d' : '#ff6b6b'}
          />
        </div>
      ) : (
        <div style={{ padding: '20px', textAlign: 'center', color: '#999' }}>
          No accuracy data available yet. Predictions need 30-120 minutes to accumulate data.
        </div>
      )}

      {/* Error Time Series Chart */}
      <div style={{
        backgroundColor: 'white',
        borderRadius: '8px',
        padding: '15px',
        marginBottom: '20px',
        boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
      }}>
        <ReactECharts option={errorChartOption} style={{ height: '400px' }} />
      </div>

      {/* Interpretation Guide */}
      <div style={{
        backgroundColor: '#e7f5ff',
        border: '1px solid #b3d9ff',
        borderRadius: '8px',
        padding: '15px',
      }}>
        <h3>📊 Interpretation Guide</h3>
        <ul style={{ margin: '10px 0', paddingLeft: '20px' }}>
          <li><strong>Mean Error:</strong> Lower is better. &lt;5% is excellent</li>
          <li><strong>Within 3σ:</strong> Percentage of predictions within 3 standard deviations. &gt;99% is ideal</li>
          <li><strong>Horizon:</strong> 30min, 1hr, 2hr horizons have different accuracy characteristics</li>
          <li><strong>Trend:</strong> Watch for increasing errors over time (model drift)</li>
        </ul>
      </div>
    </div>
  );
}

interface StatCardProps {
  title: string;
  value: number;
  unit: string;
  color?: string;
}

function StatCard({ title, value, unit, color = '#333' }: StatCardProps) {
  return (
    <div style={{
      backgroundColor: 'white',
      borderRadius: '8px',
      padding: '20px',
      boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
      textAlign: 'center',
    }}>
      <div style={{ color: '#999', marginBottom: '10px', fontSize: '14px' }}>
        {title}
      </div>
      <div style={{ color, fontSize: '24px', fontWeight: 'bold' }}>
        {typeof value === 'number' && value % 1 !== 0 ? value.toFixed(2) : value}{unit}
      </div>
    </div>
  );
}
