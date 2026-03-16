import { useEffect, useState } from 'react';
import { useWebRTC } from '../hooks/useWebRTC';
import ReactECharts from 'echarts-for-react';
import { RiskLocationMap } from '../components/RiskLocationMap';

function generateClientId(): string {
  return `viewer-${Math.random().toString(36).substring(2, 8)}`;
}

interface RiskDevice {
  id: string;
  lat: number;
  lng: number;
  severity: 'critical' | 'warning';
  metric: string;
  component: string;
  reason: string;
  thresholdHours: number;
  dispatchTimeMin: number;
  repairTimeMin: number;
  hourlyLossWon: number;
  modelConfidence: number; // 0-100 예측 신뢰도
  partsStock: 'available' | 'limited' | 'unavailable'; // 부품 재고 상태
  similarCases: number; // 유사 사례 수
  affectedDevices: string[]; // 영향받을 주변 기기
}

// 샘플 위험 기기 데이터
const SAMPLE_RISK_DEVICES: RiskDevice[] = [
  {
    id: 'V135-POS-01',
    lat: 37.5650,
    lng: 126.9800,
    severity: 'critical',
    metric: 'CPU',
    component: 'CPU 냉각팬',
    reason: 'ECOD 다변량 이상 점수 0.92 (임계값 0.8), CPU 온도 지속 상승 추세',
    thresholdHours: 2,
    dispatchTimeMin: 15,
    repairTimeMin: 45,
    hourlyLossWon: 180000,
    modelConfidence: 92,
    partsStock: 'available',
    similarCases: 3,
    affectedDevices: ['V135-POS-05', 'V135-POS-06'],
  },
  {
    id: 'V135-POS-02',
    lat: 37.5650,
    lng: 126.9800,
    severity: 'critical',
    metric: 'Memory',
    component: '메모리 모듈',
    reason: 'ARIMA 예측과 실제값 벌어짐 8.5%, 메모리 사용률 비정상 급등',
    thresholdHours: 3,
    dispatchTimeMin: 20,
    repairTimeMin: 60,
    hourlyLossWon: 220000,
    modelConfidence: 87,
    partsStock: 'limited',
    similarCases: 5,
    affectedDevices: ['V135-POS-08'],
  },
  {
    id: 'V135-POS-03',
    lat: 37.5650,
    lng: 126.9800,
    severity: 'warning',
    metric: 'DiskIO',
    component: '디스크 드라이브',
    reason: 'DiskIO 레이턴시 증가, ECOD 점수 0.65 (임계값 0.8 근처)',
    thresholdHours: 6,
    dispatchTimeMin: 25,
    repairTimeMin: 90,
    hourlyLossWon: 150000,
    modelConfidence: 78,
    partsStock: 'available',
    similarCases: 2,
    affectedDevices: [],
  },
  {
    id: 'V135-POS-04',
    lat: 37.5650,
    lng: 126.9800,
    severity: 'critical',
    metric: 'CPU',
    component: 'CPU 전원 공급',
    reason: 'CPU 전압 불안정, Ensemble 모델 신뢰도 95% 장애 예측',
    thresholdHours: 1,
    dispatchTimeMin: 10,
    repairTimeMin: 40,
    hourlyLossWon: 250000,
    modelConfidence: 95,
    partsStock: 'unavailable',
    similarCases: 8,
    affectedDevices: ['V135-POS-02', 'V135-POS-03', 'V135-POS-05'],
  },
  {
    id: 'V135-FRIDGE-01',
    lat: 37.5650,
    lng: 126.9800,
    severity: 'warning',
    metric: 'Temperature',
    component: '냉각 압축기',
    reason: '온도 센서 신호 불안정, 냉각 효율 71% 저하 감지',
    thresholdHours: 8,
    dispatchTimeMin: 30,
    repairTimeMin: 120,
    hourlyLossWon: 120000,
    modelConfidence: 82,
    partsStock: 'available',
    similarCases: 6,
    affectedDevices: [],
  },
  {
    id: 'V135-PRINTER-01',
    lat: 37.5650,
    lng: 126.9800,
    severity: 'critical',
    metric: 'Network',
    component: '네트워크 인터페이스',
    reason: '통신 패킷 손실 12%, 평균 응답시간 2800ms 초과',
    thresholdHours: 4,
    dispatchTimeMin: 20,
    repairTimeMin: 35,
    hourlyLossWon: 95000,
    modelConfidence: 88,
    partsStock: 'limited',
    similarCases: 4,
    affectedDevices: ['V135-POS-01'],
  },
  {
    id: 'V135-CCTV-01',
    lat: 37.5650,
    lng: 126.9800,
    severity: 'warning',
    metric: 'Storage',
    component: '저장 드라이브',
    reason: '저장소 사용률 89%, 녹화 프레임 드롭 발생',
    thresholdHours: 12,
    dispatchTimeMin: 35,
    repairTimeMin: 60,
    hourlyLossWon: 50000,
    modelConfidence: 75,
    partsStock: 'available',
    similarCases: 2,
    affectedDevices: [],
  },
];

export function DashboardImproved() {
  const [clientId] = useState(generateClientId);
  // 기본적으로 V135-POS-03을 선택 (현재 데이터가 있는 기기)
  const [selectedDeviceId, setSelectedDeviceId] = useState('V135-POS-03');
  const [selectedDevice, setSelectedDevice] = useState<RiskDevice | null>(null);
  const serverUrl = `${window.location.protocol}//${window.location.hostname}:8080`;

  const {
    connected,
    mode,
    metrics,
    anomalies,
    healthScore,
    connect,
    disconnect,
  } = useWebRTC({
    serverUrl,
    clientId,
    role: 'viewer',
  });

  useEffect(() => {
    connect();
    return () => {
      disconnect();
    };
  }, []);

  // selectedDeviceId 변경 시 해당 기기 정보 로드
  useEffect(() => {
    const device = SAMPLE_RISK_DEVICES.find(d => d.id === selectedDeviceId);
    if (device) {
      setSelectedDevice(device);
    }
  }, [selectedDeviceId]);

  // V135 편의점 기기 통계
  const totalDevices = SAMPLE_RISK_DEVICES.length;
  const criticalDevices = SAMPLE_RISK_DEVICES.filter(d => d.severity === 'critical');
  const warningDevices = SAMPLE_RISK_DEVICES.filter(d => d.severity === 'warning');
  const criticalRisk = criticalDevices.length;
  const warningRisk = warningDevices.length;
  const healthyDevices = totalDevices - criticalRisk - warningRisk;

  const estimatedDowntime = (criticalRisk * 0.8 + warningRisk * 0.3).toFixed(1);
  const revenueAtRisk = (criticalRisk * 15000 + warningRisk * 5000).toLocaleString('ko-KR');

  const healthTrendOption = {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(15, 23, 42, 0.95)', borderColor: '#475569', textStyle: { color: '#e2e8f0' } },
    grid: { left: '8%', right: '5%', top: '12%', bottom: '12%' },
    xAxis: { type: 'category', data: ['6시간전', '4시간전', '2시간전', '현재'], axisLabel: { color: '#94a3b8', fontSize: 11 }, axisLine: { lineStyle: { color: '#334155' } } },
    yAxis: { type: 'value', min: 0, max: 100, axisLabel: { color: '#94a3b8', fontSize: 11 }, axisLine: { lineStyle: { color: '#334155' } }, splitLine: { lineStyle: { color: '#1e293b' } } },
    series: [{
      data: [healthScore - 8, healthScore - 4, healthScore - 2, healthScore],
      type: 'line',
      smooth: true,
      itemStyle: { color: '#10b981' },
      areaStyle: { color: 'rgba(16, 185, 129, 0.15)' },
      lineStyle: { width: 2.5 }
    }]
  };

  const riskChartOption = {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item', backgroundColor: 'rgba(15, 23, 42, 0.95)', borderColor: '#475569', textStyle: { color: '#e2e8f0' } },
    series: [{
      data: [
        { value: healthyDevices, name: '정상' },
        { value: warningRisk, name: '주의' },
        { value: criticalRisk, name: '위험' }
      ],
      type: 'pie',
      radius: ['35%', '65%'],
      itemStyle: { borderColor: '#0f172a', borderWidth: 2 },
      label: { color: '#e2e8f0', fontSize: 12, fontWeight: '500' },
      color: ['#10b981', '#f59e0b', '#ef4444']
    }]
  };

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0f172a', color: '#e2e8f0', display: 'flex', flexDirection: 'column', fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif' }}>
      {/* Header */}
      <header style={{
        backgroundColor: '#1e293b',
        borderBottom: '1px solid #334155',
        padding: '24px 32px',
        backdropFilter: 'blur(10px)'
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <h1 style={{ margin: '0 0 6px 0', fontSize: '28px', fontWeight: '700', letterSpacing: '-0.5px' }}>V135 편의점 - 시스템 건강도 & 위험 평가</h1>
            <p style={{ margin: 0, fontSize: '13px', color: '#94a3b8', fontWeight: '400' }}>논현로점 | POS + 주변기기 8대 시스템 실시간 모니터링 및 장애 예측</p>
          </div>
          <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
            <div style={{ fontSize: '11px', fontWeight: '600', textTransform: 'uppercase', color: mode === 'sample' ? '#60a5fa' : '#4ade80', backgroundColor: 'rgba(30, 41, 59, 0.8)', padding: '6px 12px', borderRadius: '6px', border: '1px solid rgba(148, 163, 184, 0.2)' }}>
              {mode === 'sample' ? 'Sample' : 'Live'}
            </div>
            <div style={{ fontSize: '12px', color: connected ? '#4ade80' : '#f87171', fontWeight: '500' }}>
              {connected ? '● Connected' : '● Disconnected'}
            </div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main style={{ flex: 1, padding: '28px 32px', overflow: 'auto' }}>
        {/* Primary KPI */}
        <div style={{
          backgroundColor: '#1e293b',
          borderRadius: '8px',
          border: '1px solid #334155',
          padding: '32px',
          marginBottom: '24px',
          textAlign: 'center'
        }}>
          <p style={{ margin: '0 0 12px 0', fontSize: '12px', fontWeight: '600', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.5px' }}>시스템 건강도</p>
          <div style={{
            fontSize: '56px',
            fontWeight: '700',
            color: healthScore > 80 ? '#10b981' : healthScore > 50 ? '#f59e0b' : '#ef4444',
            marginBottom: '8px',
            letterSpacing: '-1px'
          }}>
            {Math.round(healthScore)}%
          </div>
          <p style={{ margin: 0, fontSize: '13px', color: '#e2e8f0', fontWeight: '500' }}>
            {healthScore > 80 ? '모든 시스템이 정상 운영 중' : healthScore > 50 ? '모니터링 필요 - 이슈 감지됨' : '긴급 상황 - 즉시 조치 필요'}
          </p>
        </div>

        {/* KPI Cards */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px', marginBottom: '24px' }}>
          <RiskCard label="위험 기기 (긴급)" value={criticalRisk.toString()} color="#ef4444" />
          <RiskCard label="위험 기기 (주의)" value={warningRisk.toString()} color="#f59e0b" />
          <RiskCard label="정상 기기" value={healthyDevices.toString()} color="#10b981" />
          <RiskCard label="예상 다운타임 (24h)" value={`${estimatedDowntime}시간`} color="#8b5cf6" />
        </div>

        {/* Financial Impact */}
        <div style={{
          backgroundColor: '#1e293b',
          borderRadius: '8px',
          border: '1px solid #ef444440',
          padding: '20px 24px',
          marginBottom: '24px',
          borderLeft: '4px solid #ef4444'
        }}>
          <h3 style={{ margin: '0 0 8px 0', fontSize: '12px', fontWeight: '600', color: '#ef4444', textTransform: 'uppercase', letterSpacing: '0.5px' }}>재정적 위험 노출액</h3>
          <p style={{ margin: 0, fontSize: '32px', fontWeight: '700', color: '#ef4444', letterSpacing: '-0.5px' }}>₩{revenueAtRisk}</p>
          <p style={{ margin: '8px 0 0 0', fontSize: '12px', color: '#94a3b8' }}>향후 24시간 내 위험 기기 장애 시 예상 손실</p>
        </div>

        {/* Charts */}
        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '24px', marginBottom: '24px' }}>
          <div style={{ backgroundColor: '#1e293b', borderRadius: '8px', border: '1px solid #334155', padding: '20px' }}>
            <h3 style={{ margin: '0 0 16px 0', fontSize: '13px', fontWeight: '600', color: '#e2e8f0' }}>건강도 추이 (24시간)</h3>
            <ReactECharts option={healthTrendOption} style={{ height: '280px' }} />
          </div>

          <div style={{ backgroundColor: '#1e293b', borderRadius: '8px', border: '1px solid #334155', padding: '20px' }}>
            <h3 style={{ margin: '0 0 16px 0', fontSize: '13px', fontWeight: '600', color: '#e2e8f0' }}>기기 위험도 분포</h3>
            <ReactECharts option={riskChartOption} style={{ height: '280px' }} />
          </div>
        </div>

        {/* Actions */}
        <div style={{ marginBottom: '24px' }}>
          <h3 style={{ margin: '0 0 12px 0', fontSize: '13px', fontWeight: '600', color: '#e2e8f0', textTransform: 'uppercase', letterSpacing: '0.5px' }}>권고 조치 사항</h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '12px' }}>
            <ActionItem title="긴급 유지보수 스케줄링" desc={`${criticalRisk}대 기기 즉시 대응`} color="#ef4444" />
            <ActionItem title="예방적 유지보수 계획" desc={`${warningRisk}대 기기 이상 신호`} color="#f59e0b" />
            <ActionItem title="예측 모델 검증" desc="최신 데이터로 모델 업데이트" color="#8b5cf6" />
          </div>
        </div>

        {/* Location Map & Risk Details */}
        {criticalDevices.length > 0 && (
          <>
            <div style={{ backgroundColor: '#1e293b', borderRadius: '8px', border: '1px solid #334155', padding: '20px', marginBottom: '24px' }}>
              <h3 style={{ margin: '0 0 16px 0', fontSize: '13px', fontWeight: '600', color: '#e2e8f0', textTransform: 'uppercase', letterSpacing: '0.5px' }}>기기 위치 맵</h3>
              <RiskLocationMap devices={criticalDevices} onDeviceSelect={setSelectedDevice} />
            </div>

            {/* Selected Device Details */}
            {selectedDevice && (
              <div style={{ backgroundColor: '#1e293b', borderRadius: '8px', border: `1px solid ${selectedDevice.severity === 'critical' ? '#ef4444' : '#f59e0b'}40`, borderLeft: `3px solid ${selectedDevice.severity === 'critical' ? '#ef4444' : '#f59e0b'}`, padding: '24px', marginBottom: '24px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: '20px' }}>
                  <div>
                    <h3 style={{ margin: '0 0 4px 0', fontSize: '18px', fontWeight: '700', color: '#e2e8f0' }}>
                      {selectedDevice.id}
                    </h3>
                    <p style={{ margin: 0, fontSize: '12px', color: '#94a3b8' }}>선택됨 - 지도에서 클릭</p>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: '24px', fontWeight: '700', color: selectedDevice.severity === 'critical' ? '#ef4444' : '#f59e0b', marginBottom: '4px' }}>
                      우선순위 {calculatePriority(selectedDevice)}
                    </div>
                    <p style={{ margin: 0, fontSize: '10px', color: '#94a3b8', fontStyle: 'italic' }}>손해액 + 긴급도 기반</p>
                  </div>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px', marginBottom: '24px' }}>
                  <DetailCard label="문제 부품" value={selectedDevice.component} color="#ef4444" />
                  <DetailCard label="영향 지표" value={selectedDevice.metric} color="#3b82f6" />
                  <DetailCard label="조치 시간" value={`${selectedDevice.thresholdHours}시간 이내`} color="#f59e0b" />
                  <DetailCard label="예상 출동시간" value={`${selectedDevice.dispatchTimeMin}분`} color="#10b981" />
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '12px', marginBottom: '20px' }}>
                  <InfoBox label="예측 신뢰도" value={`${selectedDevice.modelConfidence}%`} color={selectedDevice.modelConfidence > 85 ? '#10b981' : selectedDevice.modelConfidence > 70 ? '#f59e0b' : '#ef4444'} />
                  <InfoBox label="부품 재고" value={getStockStatus(selectedDevice.partsStock)} color={getStockColor(selectedDevice.partsStock)} />
                  <InfoBox label="유사 사례" value={`${selectedDevice.similarCases}건`} color="#3b82f6" />
                </div>

                <div style={{ backgroundColor: '#0f172a', borderRadius: '6px', padding: '16px', marginBottom: '20px' }}>
                  <h4 style={{ margin: '0 0 10px 0', fontSize: '12px', fontWeight: '600', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.5px' }}>판단 근거</h4>
                  <p style={{ margin: 0, fontSize: '12px', color: '#e2e8f0', lineHeight: '1.6' }}>
                    {selectedDevice.reason}
                  </p>
                </div>

                {selectedDevice.affectedDevices.length > 0 && (
                  <div style={{ backgroundColor: '#7f1d1d20', borderRadius: '6px', padding: '12px', marginBottom: '20px', border: '1px solid #ef444440' }}>
                    <h4 style={{ margin: '0 0 8px 0', fontSize: '11px', fontWeight: '600', color: '#fca5a5', textTransform: 'uppercase', letterSpacing: '0.5px' }}>⚠️ 영향받을 기기 (주변 시스템)</h4>
                    <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                      {selectedDevice.affectedDevices.map((id, i) => (
                        <span key={i} style={{ padding: '4px 8px', backgroundColor: '#ef444430', color: '#fca5a5', borderRadius: '4px', fontSize: '11px', fontWeight: '600' }}>
                          {id}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                <div style={{ backgroundColor: '#0f172a', borderRadius: '6px', padding: '16px' }}>
                  <h4 style={{ margin: '0 0 12px 0', fontSize: '12px', fontWeight: '600', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.5px' }}>예상 손해액</h4>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                    <div>
                      <p style={{ margin: '0 0 4px 0', fontSize: '11px', color: '#94a3b8' }}>시간당 손해액</p>
                      <p style={{ margin: 0, fontSize: '18px', fontWeight: '700', color: '#ef4444' }}>₩{selectedDevice.hourlyLossWon.toLocaleString('ko-KR')}</p>
                    </div>
                    <div>
                      <p style={{ margin: '0 0 4px 0', fontSize: '11px', color: '#94a3b8' }}>{`임계값(${selectedDevice.thresholdHours}시간) 내 미조치시`}</p>
                      <p style={{ margin: 0, fontSize: '18px', fontWeight: '700', color: '#ef4444' }}>
                        ₩{(selectedDevice.hourlyLossWon * selectedDevice.thresholdHours).toLocaleString('ko-KR')}
                      </p>
                    </div>
                  </div>
                  <p style={{ margin: '12px 0 0 0', fontSize: '10px', color: '#94a3b8', fontStyle: 'italic' }}>
                    💡 예상 수리 시간: {selectedDevice.repairTimeMin}분 | 총 차단 시간: {(selectedDevice.dispatchTimeMin + selectedDevice.repairTimeMin)}분
                  </p>
                </div>
              </div>
            )}

            {/* Critical Devices Table */}
            <div style={{ backgroundColor: '#1e293b', borderRadius: '8px', border: '1px solid #334155', padding: '20px', overflow: 'auto' }}>
              <h3 style={{ margin: '0 0 16px 0', fontSize: '13px', fontWeight: '600', color: '#ef4444', textTransform: 'uppercase', letterSpacing: '0.5px' }}>긴급 대응 기기 목록</h3>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid #334155' }}>
                    <th style={{ padding: '10px', textAlign: 'left', color: '#94a3b8', fontWeight: '600', fontSize: '11px' }}>기기 ID</th>
                    <th style={{ padding: '10px', textAlign: 'left', color: '#94a3b8', fontWeight: '600', fontSize: '11px' }}>부품/이슈</th>
                    <th style={{ padding: '10px', textAlign: 'center', color: '#94a3b8', fontWeight: '600', fontSize: '11px' }}>조치시간</th>
                    <th style={{ padding: '10px', textAlign: 'right', color: '#94a3b8', fontWeight: '600', fontSize: '11px' }}>손해액 (시간)</th>
                    <th style={{ padding: '10px', textAlign: 'center', color: '#94a3b8', fontWeight: '600', fontSize: '11px' }}>상태</th>
                  </tr>
                </thead>
                <tbody>
                  {criticalDevices.map((device) => (
                    <tr key={device.id} style={{ borderBottom: '1px solid #1e293b', cursor: 'pointer', backgroundColor: selectedDevice?.id === device.id ? '#334155' : 'transparent' }} onClick={() => setSelectedDevice(device)}>
                      <td style={{ padding: '10px', color: '#e2e8f0', fontWeight: '500' }}>{device.id}</td>
                      <td style={{ padding: '10px', color: '#94a3b8', fontSize: '11px' }}>{device.component}</td>
                      <td style={{ padding: '10px', textAlign: 'center', color: '#f59e0b', fontWeight: '600' }}>{device.thresholdHours}시간</td>
                      <td style={{ padding: '10px', textAlign: 'right', color: '#ef4444', fontWeight: '600' }}>₩{device.hourlyLossWon.toLocaleString('ko-KR')}</td>
                      <td style={{ padding: '10px', textAlign: 'center' }}>
                        <span style={{ padding: '3px 8px', borderRadius: '4px', backgroundColor: '#7f1d1d', color: '#fca5a5', fontSize: '10px', fontWeight: '600' }}>긴급</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </main>
    </div>
  );
}

function RiskCard({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{
      backgroundColor: '#1e293b',
      borderRadius: '8px',
      border: '1px solid #334155',
      padding: '16px',
      borderTop: `3px solid ${color}`
    }}>
      <p style={{ margin: '0 0 8px 0', fontSize: '11px', fontWeight: '600', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.5px' }}>{label}</p>
      <p style={{ margin: '0 0 4px 0', fontSize: '28px', fontWeight: '700', color }}>{value}</p>
    </div>
  );
}

function DetailCard({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{
      backgroundColor: '#0f172a',
      borderRadius: '6px',
      border: `1px solid ${color}40`,
      borderLeft: `3px solid ${color}`,
      padding: '12px'
    }}>
      <p style={{ margin: '0 0 6px 0', fontSize: '10px', fontWeight: '600', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.5px' }}>{label}</p>
      <p style={{ margin: 0, fontSize: '14px', fontWeight: '600', color }}>{value}</p>
    </div>
  );
}

function InfoBox({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{
      backgroundColor: '#0f172a',
      borderRadius: '6px',
      border: '1px solid #334155',
      padding: '12px',
      textAlign: 'center'
    }}>
      <p style={{ margin: '0 0 6px 0', fontSize: '10px', fontWeight: '600', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.5px' }}>{label}</p>
      <p style={{ margin: 0, fontSize: '16px', fontWeight: '700', color }}>{value}</p>
    </div>
  );
}

function calculatePriority(device: RiskDevice): number {
  const lossScore = Math.min(device.hourlyLossWon / 100000, 5);
  const urgencyScore = 5 / device.thresholdHours;
  const confidenceBonus = device.modelConfidence > 85 ? 1 : 0;
  return Math.min(Math.round(lossScore + urgencyScore + confidenceBonus), 5);
}

function getStockStatus(stock: 'available' | 'limited' | 'unavailable'): string {
  const map = {
    available: '재고 있음',
    limited: '재고 부족',
    unavailable: '재고 없음',
  };
  return map[stock];
}

function getStockColor(stock: 'available' | 'limited' | 'unavailable'): string {
  const map = {
    available: '#10b981',
    limited: '#f59e0b',
    unavailable: '#ef4444',
  };
  return map[stock];
}

function ActionItem({ title, desc, color }: { title: string; desc: string; color: string }) {
  return (
    <div style={{
      backgroundColor: '#1e293b',
      borderRadius: '8px',
      border: `1px solid ${color}40`,
      borderLeft: `3px solid ${color}`,
      padding: '14px'
    }}>
      <h4 style={{ margin: '0 0 4px 0', fontSize: '12px', fontWeight: '600', color: '#e2e8f0' }}>{title}</h4>
      <p style={{ margin: 0, fontSize: '11px', color: '#94a3b8' }}>{desc}</p>
    </div>
  );
}
