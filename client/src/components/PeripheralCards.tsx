import { useMemo } from 'react';
import { Usb, ScanBarcode, BookOpen, Smartphone, Keyboard, CreditCard, CircleDot, Wifi, WifiOff, HelpCircle } from 'lucide-react';
import type { ReactNode } from 'react';

interface PeripheralAlert {
  engine: string;
  metric: string;
  details: string;
  severity: string;
}

interface Props {
  alerts: PeripheralAlert[];
}

// PulseAI-WindowsAgent 송신전문 v0.1 기준 주변장치 목록
const PERIPHERALS: { id: string; name: string; icon: ReactNode }[] = [
  { id: '동글이', name: '동글이', icon: <Usb size={18} /> },
  { id: '스캐너-핸드스캐너', name: '핸드스캐너', icon: <ScanBarcode size={18} /> },
  { id: '여권리더기', name: '여권리더기', icon: <BookOpen size={18} /> },
  { id: '스캐너-2D스캐너', name: '2D 스캐너', icon: <CircleDot size={18} /> },
  { id: '휴대폰충전기', name: '충전기', icon: <Smartphone size={18} /> },
  { id: '키보드', name: '키보드', icon: <Keyboard size={18} /> },
  { id: 'MSR', name: 'MSR', icon: <CreditCard size={18} /> },
];

type DeviceState = 'connected' | 'disconnected' | 'no_data';

export function PeripheralCards({ alerts }: Props) {
  // 장비별 최신 상태 집계
  const deviceStatus = useMemo(() => {
    const statusMap = new Map<string, DeviceState>();

    // 기본값: 데이터 없음
    PERIPHERALS.forEach(p => statusMap.set(p.id, 'no_data'));

    // alert에서 상태 추출 (details에 "연결", "실패", "미사용" 등)
    alerts.forEach(alert => {
      const deviceId = alert.metric;
      if (!statusMap.has(deviceId)) return;

      const details = alert.details || '';
      if (details.includes('연결')) {
        statusMap.set(deviceId, 'connected');
      } else if (details.includes('실패') || details.includes('미사용')) {
        statusMap.set(deviceId, 'disconnected');
      }
    });

    return statusMap;
  }, [alerts]);

  const stateConfig: Record<DeviceState, { label: string; color: string; bg: string; border: string; icon: ReactNode }> = {
    connected: { label: '정상', color: '#4ade80', bg: '#052e16', border: '#166534', icon: <Wifi size={12} /> },
    disconnected: { label: '연결 안됨', color: '#f87171', bg: '#450a0a', border: '#7f1d1d', icon: <WifiOff size={12} /> },
    no_data: { label: '데이터없음', color: '#64748b', bg: '#0f172a', border: '#1e293b', icon: <HelpCircle size={12} /> },
  };

  return (
    <div style={{
      backgroundColor: '#111827',
      borderRadius: '10px',
      border: '1px solid #1f2937',
      padding: '16px',
      marginBottom: '12px',
    }}>
      <h3 style={{ margin: '0 0 12px', fontSize: '13px', fontWeight: 500, color: '#94a3b8' }}>
        주변장치 연결 상태
      </h3>
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))',
        gap: '8px',
      }}>
        {PERIPHERALS.map(device => {
          const state = deviceStatus.get(device.id) || 'no_data';
          const cfg = stateConfig[state];

          return (
            <div
              key={device.id}
              style={{
                backgroundColor: cfg.bg,
                border: `1px solid ${cfg.border}`,
                borderRadius: '8px',
                padding: '10px',
                textAlign: 'center',
              }}
            >
              <div style={{ color: cfg.color, marginBottom: '6px', display: 'flex', justifyContent: 'center' }}>
                {device.icon}
              </div>
              <div style={{
                fontSize: '11px',
                fontWeight: 600,
                color: '#e2e8f0',
                marginBottom: '4px',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}>
                {device.name}
              </div>
              <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '3px',
                fontSize: '10px',
                color: cfg.color,
              }}>
                {cfg.icon}
                <span>{cfg.label}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
