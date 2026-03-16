import { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import { AlertTriangle, Shield, ShieldAlert, ShieldCheck, Clock, Cpu, HardDrive, Database, Activity } from 'lucide-react';

interface HorizonData {
  horizon_min: number;
  horizon_label: string;
  pred_cpu: number;
  pred_memory: number;
  pred_disk_io: number;
  ecod_score: number;
  rule_score: number;
  final_score: number;
  reliability: number;
  severity: string;
  is_outlier: boolean;
  prediction_interval?: {
    lo_90?: number;
    hi_90?: number;
    lo_95?: number;
    hi_95?: number;
  };
}

interface ForecastEvaluation {
  type: 'forecast_evaluation';
  agent_id: string;
  timestamp: string;
  overall_severity: string;
  model_ready: boolean;
  data_source: string;
  horizons: HorizonData[];
}

interface IncidentPredictionProps {
  evaluation: ForecastEvaluation | null;
  agentId?: string;
}

const SEVERITY_CONFIG = {
  critical: { color: '#ef4444', bg: '#450a0a', border: '#991b1b', label: '위험', icon: ShieldAlert },
  warning: { color: '#f59e0b', bg: '#451a03', border: '#92400e', label: '주의', icon: AlertTriangle },
  normal: { color: '#22c55e', bg: '#052e16', border: '#166534', label: '정상', icon: ShieldCheck },
};

const HORIZON_LABELS: Record<number, string> = {
  60: '1시간',
  360: '6시간',
  720: '12시간',
  1440: '1일',
  2880: '2일',
};

export function IncidentPrediction({ evaluation, agentId }: IncidentPredictionProps) {
  if (!evaluation) {
    return (
      <div style={{
        backgroundColor: '#1e293b',
        borderRadius: 12,
        padding: 32,
        textAlign: 'center',
        color: '#94a3b8',
      }}>
        <Shield size={48} style={{ margin: '0 auto 16px', opacity: 0.4 }} />
        <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>사고 예측 대기 중</div>
        <div style={{ fontSize: 14 }}>
          {agentId
            ? 'ARIMA 예측 데이터가 수집되면 사고 예측 분석이 시작됩니다.'
            : 'POS 데이터 수신을 기다리고 있습니다.'}
        </div>
      </div>
    );
  }

  const overallConfig = SEVERITY_CONFIG[evaluation.overall_severity as keyof typeof SEVERITY_CONFIG] || SEVERITY_CONFIG.normal;
  const OverallIcon = overallConfig.icon;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* Header: Overall Status */}
      <div style={{
        backgroundColor: overallConfig.bg,
        border: `1px solid ${overallConfig.border}`,
        borderRadius: 12,
        padding: '20px 24px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <OverallIcon size={36} color={overallConfig.color} />
          <div>
            <div style={{ fontSize: 20, fontWeight: 700, color: overallConfig.color }}>
              {evaluation.overall_severity === 'critical' && '사고 발생 가능성 높음'}
              {evaluation.overall_severity === 'warning' && '주의 관찰 필요'}
              {evaluation.overall_severity === 'normal' && '정상 운영 예상'}
            </div>
            <div style={{ fontSize: 13, color: '#94a3b8', marginTop: 4 }}>
              {evaluation.agent_id} | {evaluation.timestamp}
              {!evaluation.model_ready && ' | ECOD 모델 학습 중 (임시 임계값 사용)'}
            </div>
          </div>
        </div>
        <div style={{
          fontSize: 13,
          color: '#64748b',
          textAlign: 'right',
        }}>
          <div>데이터: {evaluation.data_source === 'influxdb' ? 'InfluxDB 7일' : evaluation.data_source === 'buffer' ? '버퍼' : '대기중'}</div>
          <div>모델: {evaluation.model_ready ? '장기 ECOD' : '고정 임계값'}</div>
        </div>
      </div>

      {/* Timeline: Horizon Cards */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: `repeat(${evaluation.horizons.length}, 1fr)`,
        gap: 12,
      }}>
        {evaluation.horizons.map((h) => {
          const config = SEVERITY_CONFIG[h.severity as keyof typeof SEVERITY_CONFIG] || SEVERITY_CONFIG.normal;
          const Icon = config.icon;
          const label = HORIZON_LABELS[h.horizon_min] || h.horizon_label;

          return (
            <div
              key={h.horizon_min}
              style={{
                backgroundColor: '#1e293b',
                borderRadius: 10,
                padding: 16,
                border: `2px solid ${h.severity === 'normal' ? '#334155' : config.border}`,
                position: 'relative',
                overflow: 'hidden',
              }}
            >
              {/* Severity indicator bar */}
              <div style={{
                position: 'absolute',
                top: 0,
                left: 0,
                right: 0,
                height: 3,
                backgroundColor: config.color,
              }} />

              {/* Horizon label + severity */}
              <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                marginBottom: 12,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <Clock size={14} color="#94a3b8" />
                  <span style={{ fontSize: 15, fontWeight: 600, color: '#e2e8f0' }}>{label}</span>
                </div>
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 4,
                  padding: '2px 8px',
                  borderRadius: 12,
                  backgroundColor: config.bg,
                  border: `1px solid ${config.border}`,
                }}>
                  <Icon size={12} color={config.color} />
                  <span style={{ fontSize: 11, fontWeight: 600, color: config.color }}>{config.label}</span>
                </div>
              </div>

              {/* Predicted metrics */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 12 }}>
                <MetricRow icon={<Cpu size={12} />} label="CPU" value={h.pred_cpu} unit="%" severity={h.pred_cpu >= 80 ? 'warning' : 'normal'} />
                <MetricRow icon={<HardDrive size={12} />} label="Memory" value={h.pred_memory} unit="%" severity={h.pred_memory >= 85 ? 'warning' : 'normal'} />
                <MetricRow icon={<Database size={12} />} label="Disk I/O" value={h.pred_disk_io} unit="" severity={h.pred_disk_io >= 70 ? 'warning' : 'normal'} />
              </div>

              {/* Scores */}
              <div style={{
                backgroundColor: '#0f172a',
                borderRadius: 6,
                padding: '8px 10px',
                fontSize: 11,
                color: '#94a3b8',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span>ECOD 이상 점수</span>
                  <span style={{ color: h.ecod_score >= 0.7 ? '#ef4444' : h.ecod_score >= 0.5 ? '#f59e0b' : '#22c55e', fontWeight: 600 }}>
                    {(h.ecod_score * 100).toFixed(0)}%
                  </span>
                </div>
                {h.rule_score > 0 && (
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <span>주변장치 이슈</span>
                    <span style={{ color: '#f59e0b', fontWeight: 600 }}>
                      {(h.rule_score * 100).toFixed(0)}%
                    </span>
                  </div>
                )}
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span>예측 신뢰도</span>
                  <span style={{ color: h.reliability >= 0.7 ? '#22c55e' : '#f59e0b', fontWeight: 600 }}>
                    {(h.reliability * 100).toFixed(0)}%
                  </span>
                </div>
                <div style={{
                  borderTop: '1px solid #334155',
                  paddingTop: 4,
                  marginTop: 4,
                  display: 'flex',
                  justifyContent: 'space-between',
                  fontWeight: 700,
                  color: '#e2e8f0',
                }}>
                  <span>종합 위험도</span>
                  <span style={{ color: config.color }}>
                    {(h.final_score * 100).toFixed(0)}%
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Risk Timeline Chart */}
      <RiskTimelineChart horizons={evaluation.horizons} />
    </div>
  );
}


function MetricRow({ icon, label, value, unit, severity }: {
  icon: React.ReactNode;
  label: string;
  value: number;
  unit: string;
  severity: string;
}) {
  const color = severity === 'warning' ? '#f59e0b' : severity === 'critical' ? '#ef4444' : '#94a3b8';
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, color: '#94a3b8', fontSize: 12 }}>
        {icon}
        <span>{label}</span>
      </div>
      <span style={{ fontSize: 13, fontWeight: 600, color }}>
        {value.toFixed(1)}{unit}
      </span>
    </div>
  );
}


function RiskTimelineChart({ horizons }: { horizons: HorizonData[] }) {
  const option = useMemo(() => {
    const labels = horizons.map(h => HORIZON_LABELS[h.horizon_min] || h.horizon_label);
    const scores = horizons.map(h => h.final_score * 100);
    const ecodScores = horizons.map(h => h.ecod_score * 100);

    return {
      backgroundColor: 'transparent',
      title: {
        text: '시간대별 위험도 추이',
        textStyle: { color: '#e2e8f0', fontSize: 14, fontWeight: 600 },
        left: 0,
        top: 0,
      },
      tooltip: {
        trigger: 'axis' as const,
        backgroundColor: '#1e293b',
        borderColor: '#334155',
        textStyle: { color: '#e2e8f0', fontSize: 12 },
        formatter: (params: any) => {
          const idx = params[0]?.dataIndex;
          if (idx === undefined) return '';
          const h = horizons[idx];
          return `<b>${labels[idx]}</b><br/>` +
            `종합 위험도: <b style="color:${h.final_score >= 0.7 ? '#ef4444' : h.final_score >= 0.5 ? '#f59e0b' : '#22c55e'}">${(h.final_score * 100).toFixed(1)}%</b><br/>` +
            `ECOD 이상 점수: ${(h.ecod_score * 100).toFixed(1)}%<br/>` +
            `예측 신뢰도: ${(h.reliability * 100).toFixed(0)}%<br/>` +
            `CPU: ${h.pred_cpu.toFixed(1)}% | Memory: ${h.pred_memory.toFixed(1)}%`;
        },
      },
      grid: { top: 40, right: 20, bottom: 30, left: 50 },
      xAxis: {
        type: 'category' as const,
        data: labels,
        axisLabel: { color: '#94a3b8', fontSize: 12 },
        axisLine: { lineStyle: { color: '#334155' } },
      },
      yAxis: {
        type: 'value' as const,
        min: 0,
        max: 100,
        axisLabel: { color: '#94a3b8', fontSize: 11, formatter: '{value}%' },
        axisLine: { lineStyle: { color: '#334155' } },
        splitLine: { lineStyle: { color: '#1e293b' } },
      },
      series: [
        {
          name: '종합 위험도',
          type: 'bar',
          data: scores.map((v) => ({
            value: v,
            itemStyle: {
              color: v >= 70 ? '#ef4444' : v >= 50 ? '#f59e0b' : '#22c55e',
              borderRadius: [4, 4, 0, 0],
            },
          })),
          barWidth: '40%',
        },
        {
          name: 'ECOD 이상 점수',
          type: 'line',
          data: ecodScores,
          smooth: true,
          lineStyle: { color: '#8b5cf6', width: 2 },
          symbol: 'circle',
          symbolSize: 6,
          itemStyle: { color: '#8b5cf6' },
        },
        {
          // Warning threshold line
          name: '주의 기준',
          type: 'line',
          data: labels.map(() => 50),
          lineStyle: { color: '#f59e0b', type: 'dashed', width: 1, opacity: 0.5 },
          symbol: 'none',
          silent: true,
        },
        {
          // Critical threshold line
          name: '위험 기준',
          type: 'line',
          data: labels.map(() => 70),
          lineStyle: { color: '#ef4444', type: 'dashed', width: 1, opacity: 0.5 },
          symbol: 'none',
          silent: true,
        },
      ],
    };
  }, [horizons]);

  return (
    <div style={{
      backgroundColor: '#1e293b',
      borderRadius: 10,
      padding: 20,
    }}>
      <ReactECharts option={option} style={{ height: 240 }} />
    </div>
  );
}
