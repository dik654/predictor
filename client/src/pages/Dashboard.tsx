import { useEffect, useState, useCallback, useRef, type ReactNode } from 'react';
import ReactECharts from 'echarts-for-react';
import { PeripheralCards } from '../components/PeripheralCards';
import { StatusInsightCard } from '../components/StatusInsightCard';
import { Activity, Search, TrendingUp, AlertTriangle, Database, Wifi, WifiOff } from 'lucide-react';

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
  realtime: { metricsLimit: 100, detectionsLimit: 200, pollInterval: 2_000 },
} as const;

export function Dashboard() {
  const serverUrl = `${window.location.protocol}//${window.location.hostname}:8080`;
  const [viewMode, setViewMode] = useState<ViewMode>('realtime');
  const [arimaMetric, setArimaMetric] = useState<string>('CPU');

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

  const CHART_POINTS = 20;
  const ecodMulti = dbDetections.filter(d => d.engine === 'ecod' && d.metric === 'Multivariate').slice(-CHART_POINTS);
  const ecodCpu = dbDetections.filter(d => d.engine === 'ecod' && d.metric === 'CPU').slice(-CHART_POINTS);
  const ecodMem = dbDetections.filter(d => d.engine === 'ecod' && d.metric === 'Memory').slice(-CHART_POINTS);
  const ecodDisk = dbDetections.filter(d => d.engine === 'ecod' && d.metric === 'DiskIO').slice(-CHART_POINTS);
  const ecodNetSent = dbDetections.filter(d => d.engine === 'ecod' && d.metric === 'NetworkSent').slice(-CHART_POINTS);
  const ecodNetRecv = dbDetections.filter(d => d.engine === 'ecod' && d.metric === 'NetworkRecv').slice(-CHART_POINTS);
  const ecodProc = dbDetections.filter(d => d.engine === 'ecod' && d.metric === 'Process').slice(-CHART_POINTS);
  const ecodData = dbDetections.filter(d => d.engine === 'ecod').slice(-100);
  const arimaData = dbDetections.filter(d => d.engine === 'arima').slice(-50);
  const peripheralAlerts = dbDetections.filter(d => d.engine === 'peripheral').slice(-20);
  const selectedArimaData = arimaData.filter(d => d.metric === arimaMetric).slice(-CHART_POINTS);
  const arimaMetrics = [...new Set(arimaData.map(d => d.metric))];
  const latestDetections = dbDetections.slice(-10);
  const allDetections = dbDetections.slice(-30).reverse();
  const metricsCount = dbMetrics.length;
  const healthScore = dbHealthScore;
  const isConnected = dbConnected;

  // 타임스탬프를 HH:mm:ss 형식으로 변환
  const fmtTime = (ts: string) => {
    try { return new Date(ts).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }); }
    catch { return ts; }
  };

  const chartTooltip = {
    trigger: 'axis' as const,
    backgroundColor: 'rgba(15, 23, 42, 0.95)',
    borderColor: '#334155',
    textStyle: { color: '#e2e8f0', fontSize: 12 },
  };

  const chartGrid = { left: '8%', right: '8%', top: '15%', bottom: '20%' };

  const ecodChartOption = {
    title: { text: 'ECOD 다변량 이상 점수', left: 'center', textStyle: { fontSize: 13, fontWeight: 500, color: '#cbd5e1' } },
    tooltip: chartTooltip,
    legend: { bottom: 0, data: ['종합', 'CPU', 'Memory', 'DiskIO', 'NetSent', 'NetRecv', 'Process'], textStyle: { color: '#94a3b8', fontSize: 11 }, itemWidth: 12, itemHeight: 8 },
    grid: chartGrid,
    xAxis: { type: 'category', data: ecodMulti.map(d => fmtTime(d.timestamp)), axisLabel: { color: '#64748b', fontSize: 10, rotate: 30 }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#334155' } } },
    yAxis: { type: 'value', name: 'Score', min: 0, max: 1, splitNumber: 4, axisLabel: { color: '#64748b', fontSize: 10 }, nameTextStyle: { color: '#64748b', fontSize: 11 }, splitLine: { lineStyle: { color: '#1e293b' } } },
    series: [
      { name: '종합', type: 'line', data: ecodMulti.map(d => d.score), itemStyle: { color: '#f43f5e' }, lineStyle: { width: 2.5 }, smooth: true, areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(244, 63, 94, 0.3)' }, { offset: 1, color: 'rgba(244, 63, 94, 0.02)' }] } }, symbol: 'none' },
      { name: 'CPU', type: 'line', data: ecodCpu.map(d => d.score), itemStyle: { color: '#3b82f6' }, lineStyle: { width: 1.5 }, smooth: true, symbol: 'none' },
      { name: 'Memory', type: 'line', data: ecodMem.map(d => d.score), itemStyle: { color: '#22c55e' }, lineStyle: { width: 1.5 }, smooth: true, symbol: 'none' },
      { name: 'DiskIO', type: 'line', data: ecodDisk.map(d => d.score), itemStyle: { color: '#f59e0b' }, lineStyle: { width: 1.5 }, smooth: true, symbol: 'none' },
      { name: 'NetSent', type: 'line', data: ecodNetSent.map(d => d.score), itemStyle: { color: '#06b6d4' }, lineStyle: { width: 1.5 }, smooth: true, symbol: 'none' },
      { name: 'NetRecv', type: 'line', data: ecodNetRecv.map(d => d.score), itemStyle: { color: '#14b8a6' }, lineStyle: { width: 1.5 }, smooth: true, symbol: 'none' },
      { name: 'Process', type: 'line', data: ecodProc.map(d => d.score), itemStyle: { color: '#ec4899' }, lineStyle: { width: 1.5, type: 'dotted' }, smooth: true, symbol: 'none' },
    ],
  };

  // ARIMA 타임스탬프로 metrics 실제값 매칭
  const metricsMap = new Map(dbMetrics.map(m => [m.timestamp, m]));
  const metricKeyMap: Record<string, keyof InfluxMetric> = { CPU: 'cpu', Memory: 'memory', DiskIO: 'disk_io' };
  const metricKey = metricKeyMap[arimaMetric] || 'cpu';
  const arimaActual = selectedArimaData.map(d => {
    const m = metricsMap.get(d.timestamp);
    if (m) return m[metricKey] as number;
    return d.arima_predicted != null && d.arima_deviation != null ? d.arima_predicted - d.arima_deviation : null;
  });

  // 잔차 영역: 두 라인 사이 band = lower(투명) + bandWidth(색상)
  const bandLower = selectedArimaData.map((d, i) => {
    const pred = d.arima_predicted ?? 0;
    const act = arimaActual[i] ?? pred;
    return Math.min(pred, act);
  });
  const bandWidth = selectedArimaData.map(() => 0).map((_, i) => Math.abs(selectedArimaData[i]?.arima_deviation || 0));

  // 상단/하단 y축 범위를 통일 → band 간격과 bar 높이가 같은 비율로 보임
  const allValues = [...arimaActual.filter((v): v is number => v != null), ...selectedArimaData.map(d => d.arima_predicted ?? 0), ...bandWidth];
  const sharedMax = Math.ceil(Math.max(30, ...allValues) * 1.1);

  const arimaChartOption = {
    title: { text: `AutoARIMA 예측 vs 실제 (${arimaMetric})`, left: 'center', textStyle: { fontSize: 13, fontWeight: 500, color: '#cbd5e1' } },
    tooltip: chartTooltip,
    legend: { bottom: 0, data: ['예측값', '실제값', '잔차'], textStyle: { color: '#94a3b8', fontSize: 11 }, itemWidth: 12, itemHeight: 8 },
    grid: [
      { left: '8%', right: '8%', top: '12%', bottom: '42%' },
      { left: '8%', right: '8%', top: '72%', bottom: '12%' },
    ],
    xAxis: [
      { type: 'category', gridIndex: 0, data: selectedArimaData.map(d => fmtTime(d.timestamp)), axisLabel: { show: false }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#1f2937' } } },
      { type: 'category', gridIndex: 1, data: selectedArimaData.map(d => fmtTime(d.timestamp)), axisLabel: { color: '#64748b', fontSize: 10, rotate: 30 }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#1f2937' } } },
    ],
    yAxis: [
      { type: 'value', gridIndex: 0, name: arimaMetric, min: 0, max: sharedMax, axisLabel: { color: '#64748b', fontSize: 10 }, nameTextStyle: { color: '#64748b', fontSize: 11 }, splitLine: { lineStyle: { color: '#1e293b' } } },
      { type: 'value', gridIndex: 1, name: '잔차', min: 0, max: sharedMax, axisLabel: { color: '#64748b', fontSize: 10 }, nameTextStyle: { color: '#64748b', fontSize: 11 }, splitLine: { lineStyle: { color: '#1e293b' } } },
    ],
    series: [
      // Band: lower base (invisible)
      { type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: bandLower, lineStyle: { opacity: 0 }, areaStyle: { opacity: 0 }, stack: 'band', symbol: 'none', tooltip: { show: false } },
      // Band: width (colored area between lines)
      { name: '잔차 영역', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: bandWidth, lineStyle: { opacity: 0 }, areaStyle: { color: 'rgba(239, 68, 68, 0.12)' }, stack: 'band', symbol: 'none', tooltip: { show: false } },
      // Actual line
      { name: '실제값', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: arimaActual, itemStyle: { color: '#3b82f6' }, lineStyle: { width: 2 }, smooth: true, symbol: 'none' },
      // Predicted line
      { name: '예측값', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: selectedArimaData.map(d => d.arima_predicted), itemStyle: { color: '#a78bfa' }, lineStyle: { width: 2, type: 'dashed' }, smooth: true, symbol: 'none' },
      // Residual bar (bottom panel)
      {
        name: '잔차', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: bandWidth, barWidth: '50%',
        itemStyle: { color: (params: any) => {
          const v = params.value || 0;
          if (v > 20) return 'rgba(239, 68, 68, 0.7)';
          if (v > 10) return 'rgba(251, 191, 36, 0.6)';
          return 'rgba(100, 116, 139, 0.35)';
        }, borderRadius: [2, 2, 0, 0] },
      },
    ],
  };

  const card = { backgroundColor: '#111827', borderRadius: '10px', border: '1px solid #1f2937' };

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0a0e1a', color: '#e2e8f0', padding: '24px', fontFamily: "'Inter', -apple-system, sans-serif" }}>
      {/* Header */}
      <header style={{
        ...card, padding: '14px 24px', marginBottom: '16px',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <Activity size={20} color="#3b82f6" />
          <div>
            <h1 style={{ margin: 0, fontSize: '16px', fontWeight: 600, letterSpacing: '-0.01em', color: '#f1f5f9' }}>PulseAI</h1>
            <p style={{ margin: 0, fontSize: '11px', color: '#475569', fontWeight: 400 }}>ECOD + AutoARIMA Anomaly Detection</p>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div style={{ display: 'flex', borderRadius: '6px', overflow: 'hidden', border: '1px solid #1f2937' }}>
            {(['realtime', 'db'] as const).map(mode => (
              <button key={mode} onClick={() => setViewMode(mode)} style={{
                padding: '5px 12px', fontSize: '11px', fontWeight: 500, cursor: 'pointer',
                border: 'none', transition: 'all 0.15s',
                backgroundColor: viewMode === mode ? '#1e293b' : 'transparent',
                color: viewMode === mode ? '#e2e8f0' : '#475569',
              }}>
                {mode === 'realtime' ? '실시간' : 'DB'}
              </button>
            ))}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', color: isConnected ? '#4ade80' : '#64748b' }}>
            {isConnected ? <Wifi size={13} /> : <WifiOff size={13} />}
            <span>{isConnected ? `${metricsCount}건` : 'No Data'}</span>
          </div>
        </div>
      </header>

      {/* Stats Row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '10px', marginBottom: '16px' }}>
        <StatCard title="수신 데이터" value={metricsCount} color="#3b82f6" icon={<Database size={14} />} />
        <StatCard title="ECOD 분석" value={ecodData.filter(d => d.metric === 'Multivariate').length} color="#f43f5e" icon={<Search size={14} />} />
        <StatCard title="ARIMA 예측" value={arimaData.length} color="#8b5cf6" icon={<TrendingUp size={14} />} />
        <StatCard title="주변장치 경고" value={peripheralAlerts.length} color="#f59e0b" icon={<AlertTriangle size={14} />} />
      </div>

      {/* Charts Row 1 */}
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '12px', marginBottom: '12px' }}>
        <div style={{ ...card, padding: '16px' }}>
          <ReactECharts option={ecodChartOption} style={{ height: '300px' }} />
        </div>
        <StatusInsightCard detections={latestDetections} healthScore={healthScore} />
      </div>

      {/* Charts Row 2 - ARIMA */}
      <div style={{ ...card, padding: '16px', marginBottom: '12px' }}>
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '4px', gap: '4px' }}>
          {(arimaMetrics.length > 0 ? arimaMetrics : ['CPU', 'Memory', 'DiskIO']).map(m => (
            <button key={m} onClick={() => setArimaMetric(m)} style={{
              padding: '3px 10px', fontSize: '10px', fontWeight: 500, cursor: 'pointer',
              borderRadius: '4px', border: '1px solid #1f2937', transition: 'all 0.15s',
              backgroundColor: arimaMetric === m ? '#1e293b' : 'transparent',
              color: arimaMetric === m ? '#e2e8f0' : '#475569',
            }}>{m}</button>
          ))}
        </div>
        <ReactECharts option={arimaChartOption} style={{ height: '400px' }} />
      </div>

      {/* Peripheral Status Cards */}
      <PeripheralCards alerts={peripheralAlerts.map(a => ({ ...a, details: a.details || '' }))} />

      {/* Detection Table */}
      <div style={{ ...card, padding: '16px', marginTop: '12px' }}>
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

function StatCard({ title, value, color = '#3b82f6', icon }: { title: string; value: number; color?: string; icon?: ReactNode }) {
  return (
    <div style={{ backgroundColor: '#111827', borderRadius: '10px', padding: '14px 16px', border: '1px solid #1f2937', borderTop: `2px solid ${color}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', color: '#64748b', marginBottom: '6px', fontWeight: 500 }}>
        {icon && <span style={{ color, display: 'flex' }}>{icon}</span>}
        {title}
      </div>
      <div style={{ fontSize: '22px', fontWeight: 600, color: '#e2e8f0' }}>{value}</div>
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
  const styles: Record<string, { bg: string; text: string; dot: string }> = {
    critical: { bg: '#7f1d1d', text: '#fca5a5', dot: '#ef4444' },
    warning: { bg: '#713f12', text: '#fcd34d', dot: '#f59e0b' },
    normal: { bg: '#14532d', text: '#86efac', dot: '#22c55e' },
  };
  const s = styles[severity] || styles.normal;
  return (
    <span style={{ padding: '2px 8px', borderRadius: '4px', backgroundColor: s.bg, color: s.text, fontSize: '10px', display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
      <span style={{ width: '6px', height: '6px', borderRadius: '50%', backgroundColor: s.dot }} />
      {severity}
    </span>
  );
}
