import { useEffect, useState, useCallback, useRef } from 'react';
import ReactECharts from 'echarts-for-react';
import { PeripheralCards } from '../components/PeripheralCards';
import { StatusInsightCard } from '../components/StatusInsightCard';

interface InfluxMetric {
  timestamp: string;
  agent_id: string;
  cpu: number;
  memory: number;
  disk_io: number;
  network_sent: number;
  network_recv: number;
}

interface InfluxDetection {
  timestamp: string;
  engine: string;
  metric: string;
  score: number;
  threshold: number;
  severity: string;
  confidence: number;
  arima_predicted?: number;
  arima_deviation?: number;
  details?: string;
}

type ViewMode = 'db' | 'realtime';

const MODE_CONFIG = {
  db:       { metricsLimit: 100, detectionsLimit: 200, pollInterval: 5_000 },
  realtime: { metricsLimit: 20,  detectionsLimit: 50,  pollInterval: 2_000 },
} as const;

export function Dashboard() {
  const serverUrl = `${window.location.protocol}//${window.location.hostname}:8080`;
  const [viewMode, setViewMode] = useState<ViewMode>('realtime');

  const [dbMetrics, setDbMetrics] = useState<InfluxMetric[]>([]);
  const [dbDetections, setDbDetections] = useState<InfluxDetection[]>([]);
  const [dbHealthScore, setDbHealthScore] = useState(100);
  const [dbConnected, setDbConnected] = useState(false);
  const prevMetricsLen = useRef(0);
  const prevDetectionsLen = useRef(0);

  const fetchMetrics = useCallback(async (limit: number) => {
    try {
      const resp = await fetch(`${serverUrl}/api/recent-metrics?agent_id=V135-POS-03&limit=${limit}`);
      if (resp.ok) {
        const data = await resp.json();
        const metrics: InfluxMetric[] = data.metrics || [];
        setDbConnected(metrics.length > 0);
        if (metrics.length !== prevMetricsLen.current) {
          prevMetricsLen.current = metrics.length;
          setDbMetrics(metrics);
        }
      } else {
        setDbConnected(false);
      }
    } catch {
      setDbConnected(false);
    }
  }, [serverUrl]);

  const fetchDetections = useCallback(async (limit: number) => {
    try {
      const resp = await fetch(`${serverUrl}/api/recent-detections?agent_id=V135-POS-03&limit=${limit}`);
      if (resp.ok) {
        const data = await resp.json();
        const detections: InfluxDetection[] = data.detections || [];
        if (detections.length !== prevDetectionsLen.current) {
          prevDetectionsLen.current = detections.length;
          setDbDetections(detections);
          let score = 100;
          for (const d of detections.slice(-20)) {
            if (d.severity === 'critical') score -= Math.round(20 * d.confidence);
            else if (d.severity === 'warning') score -= Math.round(10 * d.confidence);
          }
          setDbHealthScore(Math.max(0, Math.min(100, score)));
        }
      }
    } catch { /* ignore */ }
  }, [serverUrl]);

  // 모드에 따라 폴링 설정
  useEffect(() => {
    const cfg = MODE_CONFIG[viewMode];
    // 모드 전환 시 이전 데이터 초기화
    prevMetricsLen.current = 0;
    prevDetectionsLen.current = 0;

    fetchMetrics(cfg.metricsLimit);
    fetchDetections(cfg.detectionsLimit);
    const metricsInterval = setInterval(() => fetchMetrics(cfg.metricsLimit), cfg.pollInterval);
    const detectionsInterval = setInterval(() => fetchDetections(cfg.detectionsLimit), cfg.pollInterval);
    return () => {
      clearInterval(metricsInterval);
      clearInterval(detectionsInterval);
    };
  }, [viewMode, fetchMetrics, fetchDetections]);

  const ecodData = dbDetections.filter(d => d.engine === 'ecod').slice(-100);
  const arimaData = dbDetections.filter(d => d.engine === 'arima').slice(-50);
  const peripheralAlerts = dbDetections.filter(d => d.engine === 'peripheral').slice(-20);
  const cpuArimaData = arimaData.filter(d => d.metric === 'CPU');
  const latestDetections = dbDetections.slice(-10);
  const allDetections = dbDetections.slice(-30).reverse();
  const metricsCount = dbMetrics.length;
  const healthScore = dbHealthScore;
  const isConnected = dbConnected;

  const ecodChartOption = {
    title: { text: `ECOD 다변량 이상 점수 (${viewMode === 'realtime' ? '실시간' : 'DB 전체'})`, left: 'center', textStyle: { fontSize: 14, color: '#e2e8f0' } },
    tooltip: { trigger: 'axis' },
    legend: { bottom: 0, data: ['Multivariate', 'CPU', 'Memory', 'DiskIO'], textStyle: { color: '#94a3b8' } },
    grid: { left: '10%', right: '5%', top: '18%', bottom: '18%' },
    xAxis: { type: 'category', data: ecodData.filter(d => d.metric === 'Multivariate').map((_: InfluxDetection, i: number) => i + 1), axisLabel: { color: '#94a3b8' } },
    yAxis: { type: 'value', name: 'Score', min: 0, max: 1, axisLabel: { color: '#94a3b8' }, nameTextStyle: { color: '#94a3b8' } },
    series: [
      { name: 'Multivariate', type: 'line', data: ecodData.filter(d => d.metric === 'Multivariate').map(d => d.score), itemStyle: { color: '#f43f5e' }, lineStyle: { width: 3 }, smooth: true, areaStyle: { color: 'rgba(244, 63, 94, 0.2)' } },
      { name: 'CPU', type: 'line', data: ecodData.filter(d => d.metric === 'CPU').map(d => d.score), itemStyle: { color: '#3b82f6' }, smooth: true },
      { name: 'Memory', type: 'line', data: ecodData.filter(d => d.metric === 'Memory').map(d => d.score), itemStyle: { color: '#22c55e' }, smooth: true },
      { name: 'DiskIO', type: 'line', data: ecodData.filter(d => d.metric === 'DiskIO').map(d => d.score), itemStyle: { color: '#f59e0b' }, smooth: true },
    ],
  };

  const arimaChartOption = {
    title: { text: `AutoARIMA 예측 vs 실제 (${viewMode === 'realtime' ? '실시간' : 'DB 전체'})`, left: 'center', textStyle: { fontSize: 14, color: '#e2e8f0' } },
    tooltip: { trigger: 'axis' },
    legend: { bottom: 0, data: ['예측값', '잔차'], textStyle: { color: '#94a3b8' } },
    grid: { left: '10%', right: '10%', top: '18%', bottom: '18%' },
    xAxis: { type: 'category', data: cpuArimaData.map((_: InfluxDetection, i: number) => i + 1), axisLabel: { color: '#94a3b8' } },
    yAxis: [
      { type: 'value', name: 'Value', position: 'left', axisLabel: { color: '#94a3b8' }, nameTextStyle: { color: '#94a3b8' } },
      { type: 'value', name: 'Residual', position: 'right', axisLabel: { color: '#94a3b8' }, nameTextStyle: { color: '#94a3b8' } },
    ],
    series: [
      { name: '예측값', type: 'line', data: cpuArimaData.map(d => d.arima_predicted), itemStyle: { color: '#8b5cf6' }, lineStyle: { type: 'dashed' }, smooth: true },
      {
        name: '잔차', type: 'bar', yAxisIndex: 1, data: cpuArimaData.map(d => d.arima_deviation),
        itemStyle: { color: (params: any) => { const th = cpuArimaData[params.dataIndex]?.threshold || 1; return params.value > th ? '#ef4444' : '#64748b'; } },
      },
    ],
  };

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0f172a', color: '#e2e8f0', padding: '20px' }}>
      {/* Header */}
      <header style={{
        backgroundColor: '#1e293b', borderRadius: '12px', padding: '16px 24px', marginBottom: '20px',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      }}>
        <div>
          <h1 style={{ margin: 0, fontSize: '24px' }}>PulseAI Lite v2.0</h1>
          <p style={{ margin: '4px 0 0', fontSize: '14px', color: '#94a3b8' }}>
            다변량 ECOD 이상탐지 + AutoARIMA 미래예측
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          {/* 모드 토글 */}
          <div style={{
            display: 'flex', borderRadius: '8px', overflow: 'hidden',
            border: '1px solid #334155',
          }}>
            <button
              onClick={() => setViewMode('realtime')}
              style={{
                padding: '6px 14px', fontSize: '12px', fontWeight: 600, cursor: 'pointer',
                border: 'none', transition: 'all 0.2s',
                backgroundColor: viewMode === 'realtime' ? '#14532d' : '#1e293b',
                color: viewMode === 'realtime' ? '#4ade80' : '#64748b',
              }}
            >
              실시간
            </button>
            <button
              onClick={() => setViewMode('db')}
              style={{
                padding: '6px 14px', fontSize: '12px', fontWeight: 600, cursor: 'pointer',
                border: 'none', borderLeft: '1px solid #334155', transition: 'all 0.2s',
                backgroundColor: viewMode === 'db' ? '#0c4a6e' : '#1e293b',
                color: viewMode === 'db' ? '#38bdf8' : '#64748b',
              }}
            >
              DB 전체
            </button>
          </div>

          {/* 연결 상태 */}
          <span style={{ display: 'flex', alignItems: 'center', gap: '6px', color: isConnected ? '#4ade80' : '#f87171' }}>
            <span style={{ width: '10px', height: '10px', borderRadius: '50%', backgroundColor: isConnected ? '#4ade80' : '#f87171' }} />
            {isConnected
              ? `InfluxDB ${viewMode === 'realtime' ? '실시간' : '전체'} (${metricsCount}건)`
              : 'InfluxDB No Data'}
          </span>
        </div>
      </header>

      {/* Stats Row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '16px', marginBottom: '20px' }}>
        <StatCard title="수신 데이터" value={metricsCount} icon="📊" />
        <StatCard title="ECOD 분석" value={ecodData.filter(d => d.metric === 'Multivariate').length} icon="🔍" color="#f43f5e" />
        <StatCard title="ARIMA 예측" value={arimaData.length} icon="📈" color="#8b5cf6" />
        <StatCard title="주변장치 경고" value={peripheralAlerts.length} icon="⚠️" color="#f59e0b" />
      </div>

      {/* Charts Row 1 */}
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '20px', marginBottom: '20px' }}>
        <div style={{ backgroundColor: '#1e293b', borderRadius: '12px', padding: '16px' }}>
          <ReactECharts option={ecodChartOption} style={{ height: '300px' }} />
        </div>
        <StatusInsightCard detections={latestDetections} healthScore={healthScore} />
      </div>

      {/* Charts Row 2 - ARIMA */}
      <div style={{ backgroundColor: '#1e293b', borderRadius: '12px', padding: '16px', marginBottom: '20px' }}>
        <ReactECharts option={arimaChartOption} style={{ height: '280px' }} />
      </div>

      {/* Peripheral Status Cards */}
      <PeripheralCards alerts={peripheralAlerts.map(a => ({ ...a, details: a.details || '' }))} />

      {/* Detection Table */}
      <div style={{ backgroundColor: '#1e293b', borderRadius: '12px', padding: '16px', marginTop: '20px' }}>
        <h3 style={{ margin: '0 0 16px', fontSize: '16px' }}>
          탐지 히스토리 ({viewMode === 'realtime' ? '실시간' : 'DB 전체'})
        </h3>
        {allDetections.length === 0 ? (
          <p style={{ color: '#64748b', textAlign: 'center', padding: '40px' }}>
            InfluxDB에서 데이터 조회 중... ({viewMode === 'realtime' ? '2초' : '5초'} 주기 폴링)
          </p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid #334155' }}>
                  <th style={{ padding: '10px', textAlign: 'left', color: '#94a3b8' }}>시간</th>
                  <th style={{ padding: '10px', textAlign: 'left', color: '#94a3b8' }}>엔진</th>
                  <th style={{ padding: '10px', textAlign: 'left', color: '#94a3b8' }}>메트릭</th>
                  <th style={{ padding: '10px', textAlign: 'right', color: '#94a3b8' }}>Score</th>
                  <th style={{ padding: '10px', textAlign: 'right', color: '#94a3b8' }}>신뢰도</th>
                  <th style={{ padding: '10px', textAlign: 'center', color: '#94a3b8' }}>심각도</th>
                  <th style={{ padding: '10px', textAlign: 'left', color: '#94a3b8' }}>상세</th>
                </tr>
              </thead>
              <tbody>
                {allDetections.map((d, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid #1e293b' }}>
                    <td style={{ padding: '8px 10px' }}>
                      {d.timestamp ? new Date(d.timestamp).toLocaleTimeString('ko-KR') : '-'}
                    </td>
                    <td style={{ padding: '8px 10px' }}><EngineTag engine={d.engine} /></td>
                    <td style={{ padding: '8px 10px' }}>{d.metric}</td>
                    <td style={{ padding: '8px 10px', textAlign: 'right' }}>{d.score?.toFixed(3)}</td>
                    <td style={{ padding: '8px 10px', textAlign: 'right' }}>
                      {d.confidence ? `${(d.confidence * 100).toFixed(0)}%` : '-'}
                    </td>
                    <td style={{ padding: '8px 10px', textAlign: 'center' }}><SeverityTag severity={d.severity} /></td>
                    <td style={{ padding: '8px 10px', color: '#94a3b8', fontSize: '11px' }}>
                      {d.details || (d.arima_predicted ? `예측: ${d.arima_predicted?.toFixed(1)}` : '-')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function StatCard({ title, value, icon, color = '#3b82f6' }: { title: string; value: number; icon: string; color?: string }) {
  return (
    <div style={{ backgroundColor: '#1e293b', borderRadius: '12px', padding: '16px', borderLeft: `4px solid ${color}` }}>
      <div style={{ fontSize: '20px', marginBottom: '4px' }}>{icon}</div>
      <div style={{ fontSize: '24px', fontWeight: 'bold' }}>{value}</div>
      <div style={{ fontSize: '11px', color: '#94a3b8' }}>{title}</div>
    </div>
  );
}

function EngineTag({ engine }: { engine: string }) {
  const colors: Record<string, { bg: string; text: string }> = {
    ecod: { bg: '#1e3a5f', text: '#60a5fa' },
    arima: { bg: '#3b1c4a', text: '#c084fc' },
    ensemble: { bg: '#134e4a', text: '#5eead4' },
    peripheral: { bg: '#713f12', text: '#fcd34d' },
  };
  const c = colors[engine] || { bg: '#374151', text: '#9ca3af' };
  return (
    <span style={{ padding: '2px 8px', borderRadius: '4px', backgroundColor: c.bg, color: c.text, fontSize: '10px', fontWeight: 'bold' }}>
      {engine.toUpperCase()}
    </span>
  );
}

function SeverityTag({ severity }: { severity: string }) {
  const styles: Record<string, { bg: string; text: string; icon: string }> = {
    critical: { bg: '#7f1d1d', text: '#fca5a5', icon: '🔴' },
    warning: { bg: '#713f12', text: '#fcd34d', icon: '🟡' },
    normal: { bg: '#14532d', text: '#86efac', icon: '🟢' },
  };
  const s = styles[severity] || styles.normal;
  return (
    <span style={{ padding: '2px 8px', borderRadius: '4px', backgroundColor: s.bg, color: s.text, fontSize: '10px' }}>
      {s.icon} {severity}
    </span>
  );
}
