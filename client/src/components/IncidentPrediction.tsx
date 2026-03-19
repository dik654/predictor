import { useMemo, useEffect, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import { AlertTriangle, Shield, ShieldAlert, ShieldCheck, Clock, TrendingUp } from 'lucide-react';

// CSS 애니메이션 주입 (한 번만)
const STYLE_ID = 'pulse-bar-animations';
function injectAnimationStyles() {
  if (typeof document === 'undefined') return;
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    @keyframes shimmer {
      0% { transform: translateX(-100%); }
      100% { transform: translateX(200%); }
    }
    @keyframes pulseGlow {
      0%, 100% { opacity: 0.4; }
      50% { opacity: 0.8; }
    }
    @keyframes barFillIn {
      from { width: 0%; }
    }
    @keyframes barGrowUp {
      from { height: 0%; }
    }
    .pulse-bar-shimmer {
      position: relative;
      overflow: hidden;
    }
    .pulse-bar-shimmer::after {
      content: '';
      position: absolute;
      top: 0;
      left: 0;
      width: 50%;
      height: 100%;
      background: linear-gradient(
        90deg,
        transparent 0%,
        rgba(255,255,255,0.12) 40%,
        rgba(255,255,255,0.25) 50%,
        rgba(255,255,255,0.12) 60%,
        transparent 100%
      );
      animation: shimmer 2.5s ease-in-out infinite;
    }
    .pulse-bar-glow {
      position: relative;
    }
    .pulse-bar-glow::before {
      content: '';
      position: absolute;
      top: -1px;
      left: -1px;
      right: -1px;
      bottom: -1px;
      border-radius: inherit;
      background: inherit;
      filter: blur(6px);
      animation: pulseGlow 3s ease-in-out infinite;
      z-index: -1;
    }
    .pulse-bar-fill-h {
      animation: barFillIn 0.8s ease-out forwards;
    }
    .pulse-bar-fill-v {
      animation: barGrowUp 0.8s ease-out forwards;
    }
  `;
  document.head.appendChild(style);
}

interface FeatureContribution {
  metric: string;
  score: number;
  pct: number;
  predicted_value: number;
}

interface HorizonData {
  horizon_min: number;
  horizon_label: string;
  pred_cpu: number;
  pred_memory: number;
  pred_disk_io: number;
  pred_network_sent?: number;
  pred_network_recv?: number;
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
  feature_contributions?: FeatureContribution[];
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

const METRIC_KO: Record<string, string> = {
  CPU: 'CPU 사용률',
  Memory: '메모리 사용률',
  DiskIO: '디스크 사용률',
  NetworkSent: '네트워크 송신량',
  NetworkRecv: '네트워크 수신량',
};

const METRIC_THRESHOLDS: Record<string, { warning: number; critical: number; unit: string }> = {
  CPU: { warning: 80, critical: 90, unit: '%' },
  Memory: { warning: 85, critical: 95, unit: '%' },
  DiskIO: { warning: 70, critical: 85, unit: '' },
  NetworkSent: { warning: 50000, critical: 100000, unit: 'B' },
  NetworkRecv: { warning: 50000, critical: 100000, unit: 'B' },
};

function horizonLabel(min: number): string {
  if (min < 60) return `${min}분 후`;
  if (min < 1440) return `${min / 60}시간 후`;
  return `${min / 1440}일 후`;
}

export function IncidentPrediction({ evaluation, agentId }: IncidentPredictionProps) {
  useEffect(() => { injectAnimationStyles(); }, []);

  // 분석 결과 도출
  const analysis = useMemo(() => {
    if (!evaluation || evaluation.horizons.length === 0) return null;

    // final_score 기반으로 severity 재계산 (DB 저장값 무시)
    const horizons = evaluation.horizons.map(h => ({
      ...h,
      severity: h.final_score >= 0.7 ? 'critical' : h.final_score >= 0.4 ? 'warning' : 'normal',
    }));

    // 가장 위험한 시점 찾기
    const worst = horizons.reduce((a, b) => a.final_score > b.final_score ? a : b);
    const isRisky = worst.severity !== 'normal';

    // overall_severity도 재계산
    const overallSeverity = horizons.some(h => h.severity === 'critical') ? 'critical'
      : horizons.some(h => h.severity === 'warning') ? 'warning' : 'normal';

    // 주요 원인 (feature contribution 기반)
    const topFeature = worst.feature_contributions?.[0];

    // 메트릭별 예측값 추출 + 추세 분석
    const metricTrends = analyzeMetricTrends(horizons);

    // 위험 시점들 (normal이 아닌 horizon)
    const riskyHorizons = horizons.filter(h => h.severity !== 'normal');

    // 가장 빠른 위험 시점
    const earliestRisk = riskyHorizons.length > 0
      ? riskyHorizons.reduce((a, b) => a.horizon_min < b.horizon_min ? a : b)
      : null;

    // 권장 조치
    const recommendation = getRecommendation(worst, metricTrends);

    return {
      worst,
      isRisky,
      overallSeverity,
      topFeature,
      metricTrends,
      riskyHorizons,
      earliestRisk,
      recommendation,
      horizons,
    };
  }, [evaluation]);

  if (!evaluation) {
    return (
      <div style={{
        backgroundColor: '#1e293b', borderRadius: 12, padding: 32,
        textAlign: 'center', color: '#cbd5e1',
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

  if (!analysis) return null;

  const { worst, isRisky, overallSeverity, topFeature, metricTrends, earliestRisk, recommendation, horizons } = analysis;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

      {/* 1. 핵심 요약 카드 — "언제, 뭐가, 왜" */}
      <SummaryCard
        evaluation={{ ...evaluation, overall_severity: overallSeverity }}
        worst={worst}
        isRisky={isRisky}
        topFeature={topFeature}
        earliestRisk={earliestRisk}
        recommendation={recommendation}
      />

      {/* 2. 미래 예측 + 원인 분해 (좌: 1-미래예측, 우: 2-원인분해) */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <MetricTrendCard trends={metricTrends} horizons={horizons} />
        <FeatureBreakdown worst={worst} horizons={horizons} />
      </div>

      {/* 3. 최종 위험도 + 시간대별 차트 */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <RiskCalculationCard horizons={horizons} />
        <RiskTimelineChart horizons={horizons} />
      </div>

    </div>
  );
}


/* ────── 1. 핵심 요약 ────── */

function SummaryCard({ evaluation, worst, isRisky, topFeature, earliestRisk, recommendation }: {
  evaluation: ForecastEvaluation;
  worst: HorizonData;
  isRisky: boolean;
  topFeature?: FeatureContribution;
  earliestRisk: HorizonData | null;
  recommendation: string;
}) {
  const sevConfig = {
    critical: { color: '#ef4444', bg: '#450a0a', border: '#991b1b', icon: ShieldAlert, emoji: '🔴' },
    warning: { color: '#f59e0b', bg: '#451a03', border: '#92400e', icon: AlertTriangle, emoji: '🟡' },
    normal: { color: '#22c55e', bg: '#052e16', border: '#166534', icon: ShieldCheck, emoji: '🟢' },
  };
  const sev = sevConfig[evaluation.overall_severity as keyof typeof sevConfig] || sevConfig.normal;

  // 한줄 요약 생성
  let headline = '';
  if (evaluation.overall_severity === 'normal') {
    headline = '현재 예측 범위 내 이상 징후 없음';
  } else if (earliestRisk && topFeature) {
    const metricName = METRIC_KO[topFeature.metric] || topFeature.metric;
    headline = `${horizonLabel(earliestRisk.horizon_min)} ${metricName} 이상 예상`;
  } else if (earliestRisk) {
    headline = `${horizonLabel(earliestRisk.horizon_min)} 이상 발생 가능성`;
  }

  return (
    <div style={{
      backgroundColor: sev.bg, border: `1px solid ${sev.border}`,
      borderRadius: 12, padding: '20px 24px',
    }}>
      {/* 한줄 요약 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: isRisky ? 16 : 0 }}>
        <span style={{ fontSize: 32 }}>{sev.emoji}</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 20, fontWeight: 700, color: sev.color }}>{headline}</div>
          <div style={{ fontSize: 13, color: '#cbd5e1', marginTop: 4 }}>
            {evaluation.agent_id} | 기준 시점: {new Date(evaluation.timestamp).toLocaleString('ko-KR')}
            {' | '}데이터: {evaluation.data_source === 'influxdb' ? '최근 7일' : evaluation.data_source === 'buffer' ? '버퍼' : '대기중'}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 28, fontWeight: 700, color: sev.color }}>
            {(worst.final_score * 100).toFixed(0)}%
          </div>
          <div style={{ fontSize: 11, color: '#cbd5e1' }}>최대 위험도</div>
        </div>
      </div>

      {/* 위험 시 상세 설명 */}
      {isRisky && (
        <div style={{
          backgroundColor: 'rgba(0,0,0,0.3)', borderRadius: 8, padding: '12px 16px',
          fontSize: 14, lineHeight: 1.6, color: '#e2e8f0',
        }}>
          {topFeature && (
            <div style={{ marginBottom: 8 }}>
              <strong style={{ color: sev.color }}>원인:</strong>{' '}
              {METRIC_KO[topFeature.metric] || topFeature.metric}이(가)
              예측값 <strong>{topFeature.predicted_value.toFixed(1)}</strong>로
              정상 범위를 벗어날 가능성 (기여도 {topFeature.pct.toFixed(0)}%)
            </div>
          )}
          <div>
            <strong style={{ color: '#38bdf8' }}>권장 조치:</strong> {recommendation}
          </div>
        </div>
      )}
    </div>
  );
}


/* ────── 2a. Feature 기여도 분해 ────── */

function FeatureBreakdown({ worst, horizons }: { worst: HorizonData; horizons: HorizonData[] }) {
  const [selectedIdx, setSelectedIdx] = useState(
    horizons.findIndex(h => h.horizon_min === worst.horizon_min)
  );
  const selected = horizons[selectedIdx] || worst;
  const contribs = selected.feature_contributions || [];

  if (contribs.length === 0) {
    return (
      <div style={{ backgroundColor: '#1e293b', borderRadius: 12, padding: 20 }}>
        <h4 style={{ margin: '0 0 12px', fontSize: 14, color: '#e2e8f0' }}>STEP 2. 왜 이상인가?</h4>
        <div style={{ color: '#cbd5e1', fontSize: 13, textAlign: 'center', padding: 20 }}>
          7일간 데이터를 학습 중입니다. 학습이 완료되면 원인 분석이 표시됩니다.
        </div>
      </div>
    );
  }

  return (
    <div style={{ backgroundColor: '#1e293b', borderRadius: 12, padding: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <h4 style={{ margin: 0, fontSize: 14, color: '#e2e8f0' }}>2 - 이상 원인 분해</h4>
        <div style={{ display: 'flex', gap: 3 }}>
          {horizons.map((h, i) => (
            <button key={h.horizon_min} onClick={() => setSelectedIdx(i)} style={{
              padding: '2px 8px', fontSize: 10, fontWeight: 500, cursor: 'pointer',
              borderRadius: 4, border: `1px solid ${selectedIdx === i ? '#6366f1' : '#1f2937'}`,
              backgroundColor: selectedIdx === i ? '#1e1b4b' : 'transparent',
              color: selectedIdx === i ? '#a5b4fc' : '#cbd5e1',
              transition: 'all 0.15s',
            }}>{horizonLabel(h.horizon_min)}</button>
          ))}
        </div>
      </div>
      <div style={{ fontSize: 12, color: '#cbd5e1', marginBottom: 12, lineHeight: 1.5 }}>
        {horizonLabel(selected.horizon_min)} 예측값을 과거 7일과 비교하여, 평소와 가장 다른 지표 순으로 보여줍니다.
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {contribs.map((fc, idx) => {
          const threshold = METRIC_THRESHOLDS[fc.metric];
          const unit = threshold?.unit || '';
          const isTop = idx === 0;
          const barColor = isTop ? '#8b5cf6' : '#6366f1';
          const barBg = isTop
            ? 'linear-gradient(90deg, #8b5cf6 0%, #a78bfa 100%)'
            : 'linear-gradient(90deg, #6366f1 0%, #818cf8 100%)';

          return (
            <div key={fc.metric} style={{
              backgroundColor: isTop ? '#1a1040' : '#0f172a',
              borderRadius: 8,
              padding: '10px 12px',
              border: isTop ? '1px solid #3b1c6e' : '1px solid transparent',
            }}>
              {/* 상단: 이름 + 배지 + 퍼센트 */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ fontSize: 13, color: '#e2e8f0', fontWeight: 600 }}>
                    {METRIC_KO[fc.metric] || fc.metric}
                  </span>
                  {isTop && (
                    <span style={{
                      fontSize: 10, color: '#c084fc', fontWeight: 600,
                      backgroundColor: '#2e1065', padding: '1px 8px', borderRadius: 10,
                    }}>
                      주요 원인
                    </span>
                  )}
                </div>
                <div style={{ textAlign: 'right' }}>
                  <span style={{ fontSize: 16, fontWeight: 700, color: isTop ? '#c084fc' : '#a5b4fc' }}>
                    {fc.pct.toFixed(0)}%
                  </span>
                  <div style={{ fontSize: 11, color: '#cbd5e1' }}>기여도</div>
                </div>
              </div>

              {/* 바: 내부에 퍼센트 라벨 포함 */}
              <div style={{
                height: 24, backgroundColor: '#0f172a', borderRadius: 6, overflow: 'hidden',
                position: 'relative',
              }}>
                <div
                  className="pulse-bar-shimmer pulse-bar-fill-h"
                  style={{
                    height: '100%',
                    width: `${Math.max(fc.pct, 8)}%`,
                    background: barBg,
                    borderRadius: 6,
                  }}
                />
              </div>

              {/* 하단: 예측값 + 임계값 */}
              <div style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                marginTop: 6, fontSize: 11, color: '#cbd5e1',
              }}>
                <span>
                  예측값 <strong style={{ color: '#e2e8f0' }}>{fc.predicted_value.toFixed(1)}{unit}</strong>
                </span>
                {threshold && (
                  <span>
                    주의 <span style={{ color: '#f59e0b' }}>{threshold.warning}{unit}</span>
                    {' / '}위험 <span style={{ color: '#ef4444' }}>{threshold.critical}{unit}</span>
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}


/* ────── 2b. 메트릭 추세 ────── */

interface MetricTrend {
  metric: string;
  values: number[];
  labels: string[];
  direction: 'up' | 'down' | 'stable';
  changePerHour: number;
}

function analyzeMetricTrends(horizons: HorizonData[]): MetricTrend[] {
  const metrics = [
    { key: 'CPU', getter: (h: HorizonData) => h.pred_cpu },
    { key: 'Memory', getter: (h: HorizonData) => h.pred_memory },
    { key: 'DiskIO', getter: (h: HorizonData) => h.pred_disk_io },
    { key: 'NetworkSent', getter: (h: HorizonData) => h.pred_network_sent ?? 0 },
    { key: 'NetworkRecv', getter: (h: HorizonData) => h.pred_network_recv ?? 0 },
  ];

  return metrics.map(({ key, getter }) => {
    const values = horizons.map(getter);
    const labels = horizons.map(h => horizonLabel(h.horizon_min));

    // 추세: 처음 vs 마지막
    const first = values[0] || 0;
    const last = values[values.length - 1] || 0;
    const totalHours = (horizons[horizons.length - 1]?.horizon_min || 60) / 60;
    const changePerHour = totalHours > 0 ? (last - first) / totalHours : 0;

    let direction: 'up' | 'down' | 'stable' = 'stable';
    if (Math.abs(changePerHour) > 0.5) {
      direction = changePerHour > 0 ? 'up' : 'down';
    }

    return { metric: key, values, labels, direction, changePerHour };
  });
}

function MetricTrendCard({ trends, horizons }: { trends: MetricTrend[]; horizons: HorizonData[] }) {
  const directionEmoji = { up: '📈', down: '📉', stable: '➡️' };
  const directionText = { up: '상승 추세', down: '하락 추세', stable: '안정' };
  const directionColor = { up: '#ef4444', down: '#22c55e', stable: '#94a3b8' };

  return (
    <div style={{ backgroundColor: '#1e293b', borderRadius: 12, padding: 20, display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* STEP 1: 미래 예측값 */}
      <div>
        <h4 style={{ margin: '0 0 8px', fontSize: 14, color: '#e2e8f0' }}>
          1 - 미래 예측 수치
        </h4>
        <div style={{ fontSize: 12, color: '#cbd5e1', marginBottom: 12, lineHeight: 1.5 }}>
          과거 패턴 기반 ARIMA 예측. 노란색=주의 구간, 빨간색=위험 구간.
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {trends.map(t => {
            const threshold = METRIC_THRESHOLDS[t.metric];
            const unit = threshold?.unit || '';
            return (
              <div key={t.metric} style={{ backgroundColor: '#0f172a', borderRadius: 8, padding: '10px 12px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                  <span style={{ fontSize: 13, color: '#e2e8f0', fontWeight: 600 }}>
                    {METRIC_KO[t.metric] || t.metric}
                  </span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    {threshold && (
                      <span style={{ fontSize: 11, color: '#cbd5e1' }}>
                        주의 {threshold.warning}{unit} / 위험 {threshold.critical}{unit}
                      </span>
                    )}
                    <span style={{
                      fontSize: 11, fontWeight: 600,
                      color: directionColor[t.direction],
                      backgroundColor: t.direction === 'up' ? '#450a0a' : t.direction === 'down' ? '#052e16' : '#1e293b',
                      padding: '2px 8px', borderRadius: 10,
                    }}>
                      {directionEmoji[t.direction]} {directionText[t.direction]}
                    </span>
                  </div>
                </div>
                {/* 값 셀: 미니 바 차트 */}
                <div style={{ display: 'flex', gap: 3 }}>
                  {t.values.map((v, i) => {
                    const val = v ?? 0;
                    const pct = threshold ? Math.min((val / threshold.critical) * 100, 100) : 50;
                    const isWarn = threshold && val >= threshold.warning;
                    const isCrit = threshold && val >= threshold.critical;
                    const barColor = isCrit ? '#ef4444' : isWarn ? '#f59e0b' : '#3b82f6';
                    return (
                      <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
                        <div style={{
                          width: '100%', height: 32, backgroundColor: '#1e293b', borderRadius: 4,
                          display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', overflow: 'hidden',
                        }}>
                          <div
                            className="pulse-bar-shimmer pulse-bar-fill-v"
                            style={{
                              width: '100%', height: `${Math.max(pct, 5)}%`,
                              background: `linear-gradient(180deg, ${barColor} 0%, ${barColor}88 100%)`,
                              borderRadius: '4px 4px 0 0',
                            }}
                          />
                        </div>
                        <span style={{
                          fontSize: 10, fontWeight: 600,
                          color: isCrit ? '#ef4444' : isWarn ? '#f59e0b' : '#cbd5e1',
                        }}>
                          {val.toFixed(1)}{unit}
                        </span>
                        <span style={{ fontSize: 9, color: '#cbd5e1' }}>{t.labels[i]}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      </div>

    </div>
  );
}

function RiskCalculationCard({ horizons }: { horizons: HorizonData[] }) {
  return (
    <div style={{ backgroundColor: '#1e293b', borderRadius: 12, padding: 20 }}>
      <h4 style={{ margin: '0 0 8px', fontSize: 14, color: '#e2e8f0' }}>
        3 - 최종 위험도 계산
      </h4>
        <div style={{ fontSize: 12, color: '#cbd5e1', marginBottom: 12, lineHeight: 1.5 }}>
          이상 점수 x 신뢰도 = 위험도. 먼 미래일수록 신뢰도가 낮아져 위험도가 보정됩니다.
        </div>

        {/* 이상 점수 해설 */}
        <div style={{
          backgroundColor: '#0f172a', borderRadius: 8, padding: '10px 12px',
          marginBottom: 8, fontSize: 11, lineHeight: 1.6, color: '#cbd5e1',
        }}>
          <div style={{ fontWeight: 600, color: '#c084fc', marginBottom: 4 }}>
            이상 점수란?
          </div>
          <div>
            STEP 1에서 예측한 CPU·메모리·디스크·네트워크 값의 조합을 과거 7일간의 정상 패턴(ECOD 모델)과 비교한 결과입니다.
          </div>
          <div style={{
            display: 'flex', gap: 12, marginTop: 6, fontSize: 10,
          }}>
            <span><strong style={{ color: '#22c55e' }}>0~30%</strong> 평소와 비슷</span>
            <span><strong style={{ color: '#f59e0b' }}>30~70%</strong> 다소 특이</span>
            <span><strong style={{ color: '#ef4444' }}>70~100%</strong> 거의 본 적 없는 조합</span>
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {horizons.map((h, i) => {
            const ecodPct = h.ecod_score * 100;
            const relPct = h.reliability * 100;
            const finalPct = h.final_score * 100;
            const finalColor = finalPct >= 70 ? '#ef4444' : finalPct >= 40 ? '#f59e0b' : '#22c55e';
            return (
              <div key={i} style={{
                backgroundColor: '#0f172a', borderRadius: 8, padding: '8px 12px',
                display: 'flex', alignItems: 'center', gap: 8,
              }}>
                {/* 시점 */}
                <span style={{
                  width: 56, fontSize: 11, fontWeight: 600, color: '#cbd5e1', flexShrink: 0,
                }}>
                  {horizonLabel(h.horizon_min)}
                </span>

                {/* 이상 점수 미니 바 */}
                <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <div style={{ flex: 1, height: 6, backgroundColor: '#1e293b', borderRadius: 3, overflow: 'hidden' }}>
                    <div className="pulse-bar-shimmer pulse-bar-fill-h" style={{
                      height: '100%', width: `${Math.min(ecodPct, 100)}%`,
                      background: 'linear-gradient(90deg, #7c3aed, #c084fc)',
                      borderRadius: 3,
                    }} />
                  </div>
                  <span style={{ fontSize: 11, fontWeight: 700, color: '#c084fc', width: 32, textAlign: 'right' }}>
                    {ecodPct.toFixed(0)}%
                  </span>
                </div>

                <span style={{ color: '#334155', fontSize: 11 }}>x</span>

                {/* 신뢰도 미니 바 */}
                <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <div style={{ flex: 1, height: 6, backgroundColor: '#1e293b', borderRadius: 3, overflow: 'hidden' }}>
                    <div className="pulse-bar-shimmer pulse-bar-fill-h" style={{
                      height: '100%', width: `${Math.min(relPct, 100)}%`,
                      background: `linear-gradient(90deg, ${relPct >= 70 ? '#16a34a' : '#d97706'}, ${relPct >= 70 ? '#4ade80' : '#fbbf24'})`,
                      borderRadius: 3,
                    }} />
                  </div>
                  <span style={{
                    fontSize: 11, fontWeight: 700, width: 32, textAlign: 'right',
                    color: relPct >= 70 ? '#4ade80' : relPct >= 40 ? '#fbbf24' : '#ef4444',
                  }}>
                    {relPct.toFixed(0)}%
                  </span>
                </div>

                <span style={{ color: '#334155', fontSize: 11 }}>=</span>

                {/* 최종 위험도 */}
                <div style={{
                  width: 52, height: 28, borderRadius: 6, flexShrink: 0,
                  background: `linear-gradient(135deg, ${finalColor}33, ${finalColor}11)`,
                  border: `1px solid ${finalColor}55`,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}>
                  <span style={{ fontSize: 13, fontWeight: 700, color: finalColor }}>
                    {finalPct.toFixed(0)}%
                  </span>
                </div>
              </div>
            );
          })}
        </div>
        {/* 컬럼 라벨 */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8, marginTop: 6, padding: '0 12px',
          fontSize: 10, color: '#cbd5e1',
        }}>
          <span style={{ width: 56 }}></span>
          <span style={{ flex: 1, textAlign: 'center', color: '#c084fc' }}>이상 점수</span>
          <span style={{ width: 12 }}></span>
          <span style={{ flex: 1, textAlign: 'center', color: '#4ade80' }}>신뢰도</span>
          <span style={{ width: 12 }}></span>
          <span style={{ width: 52, textAlign: 'center', color: '#e2e8f0' }}>위험도</span>
        </div>
    </div>
  );
}


/* ────── 3. 타임라인 차트 ────── */

function RiskTimelineChart({ horizons }: { horizons: HorizonData[] }) {
  const option = useMemo(() => {
    const labels = horizons.map(h => horizonLabel(h.horizon_min));
    const riskData = horizons.map(h => h.final_score * 100);

    const metricSeries = [
      { name: 'CPU 사용률', data: horizons.map(h => h.pred_cpu), color: '#3b82f6' },
      { name: '메모리 사용률', data: horizons.map(h => h.pred_memory), color: '#22c55e' },
      { name: '디스크 사용률', data: horizons.map(h => h.pred_disk_io), color: '#f59e0b' },
      { name: '네트워크 송신', data: horizons.map(h => h.pred_network_sent ?? 0), color: '#06b6d4' },
      { name: '네트워크 수신', data: horizons.map(h => h.pred_network_recv ?? 0), color: '#14b8a6' },
    ];

    return {
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis' as const,
        backgroundColor: 'rgba(15, 23, 42, 0.95)',
        borderColor: '#1f2937',
        textStyle: { color: '#e2e8f0', fontSize: 11 },
      },
      legend: {
        bottom: 0,
        textStyle: { color: '#cbd5e1', fontSize: 11 },
        itemWidth: 12, itemHeight: 8,
      },
      grid: { top: 30, right: 60, bottom: 50, left: 50 },
      xAxis: {
        type: 'category' as const,
        data: labels,
        axisLabel: { color: '#cbd5e1', fontSize: 11, interval: 0 },
        axisTick: { show: false },
        axisLine: { lineStyle: { color: '#1f2937' } },
      },
      yAxis: [
        {
          type: 'value' as const, name: '메트릭', position: 'left' as const,
          axisLabel: { color: '#64748b', fontSize: 10 },
          nameTextStyle: { color: '#64748b', fontSize: 11 },
          splitLine: { lineStyle: { color: '#1e293b' } },
        },
        {
          type: 'value' as const, name: '위험도 %', min: 0, max: 100, position: 'right' as const,
          axisLabel: { color: '#64748b', fontSize: 10 },
          nameTextStyle: { color: '#64748b', fontSize: 11 },
          splitLine: { show: false },
        },
      ],
      series: [
        ...metricSeries.map(s => ({
          name: s.name, type: 'line' as const, data: s.data, smooth: true,
          lineStyle: { color: s.color, width: 1.5 },
          itemStyle: { color: s.color }, symbol: 'none' as const,
        })),
        {
          name: '위험도', type: 'bar' as const, yAxisIndex: 1, data: riskData.map(v => ({
            value: v,
            itemStyle: {
              color: v >= 70 ? 'rgba(239,68,68,0.7)' : v >= 50 ? 'rgba(251,191,36,0.6)' : 'rgba(59,130,246,0.4)',
              borderRadius: [3, 3, 0, 0],
            },
          })),
          barWidth: '25%',
        },
      ],
    };
  }, [horizons]);

  return (
    <div style={{ backgroundColor: '#1e293b', borderRadius: 12, padding: 20 }}>
      <h4 style={{ margin: '0 0 12px', fontSize: 14, color: '#e2e8f0' }}>3 - 시간대별 예측값 및 위험도</h4>
      <ReactECharts option={option} style={{ height: 300 }} />
    </div>
  );
}



/* ────── 권장 조치 생성 ────── */

function getRecommendation(worst: HorizonData, trends: MetricTrend[]): string {
  const topFeature = worst.feature_contributions?.[0];
  if (!topFeature) return '지표를 계속 모니터링하세요.';

  const metric = topFeature.metric;
  const value = topFeature.predicted_value;
  const threshold = METRIC_THRESHOLDS[metric];
  const trend = trends.find(t => t.metric === metric);

  if (metric === 'Memory') {
    if (value >= 90) return '메모리 부족 예상. POS 재부팅을 권장합니다.';
    if (value >= 80) return '메모리 사용량이 높아지고 있습니다. 불필요한 프로세스를 종료하거나 재부팅을 준비하세요.';
    if (trend?.direction === 'up') return '메모리가 서서히 증가하는 추세입니다. 메모리 누수 가능성을 확인하세요.';
  }

  if (metric === 'CPU') {
    if (value >= 90) return 'CPU 과부하 예상. POS 응답 지연이 발생할 수 있습니다. 재부팅을 검토하세요.';
    if (value >= 80) return 'CPU 사용률이 높아지고 있습니다. 백그라운드 프로세스를 확인하세요.';
  }

  if (metric === 'DiskIO') {
    if (value >= 70) return '디스크 I/O가 높아지고 있습니다. 디스크 공간과 로그 파일을 확인하세요.';
  }

  if (worst.rule_score > 0.3) {
    return '주변장치 연결 문제가 감지되었습니다. 동글, 스캐너 등의 연결 상태를 확인하세요.';
  }

  return '예측 지표가 정상 범위를 벗어나는 추세입니다. 지속적으로 모니터링하세요.';
}
