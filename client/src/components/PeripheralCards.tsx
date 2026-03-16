import { useMemo } from 'react';

interface PeripheralAlert {
  engine: string;
  metric: string;
  details: string;
  severity: string;
}

interface Props {
  alerts: PeripheralAlert[];
}

// 기본 주변장치 목록 (POS 기준)
const DEFAULT_PERIPHERALS = [
  { id: '키패드', icon: '⌨️', name: '키패드' },
  { id: '스캐너-2D스캐너', icon: '📷', name: '2D 스캐너' },
  { id: 'OCR', icon: '🔤', name: 'OCR' },
  { id: '카드리더기', icon: '💳', name: '카드리더기' },
  { id: '휴대폰충전기', icon: '🔌', name: '충전기' },
  { id: '고객단말기', icon: '📱', name: '고객단말기' },
];

export function PeripheralCards({ alerts }: Props) {
  // 장비별 상태 집계
  const deviceStatus = useMemo(() => {
    const statusMap = new Map<string, { failCount: number; lastAlert?: string }>();
    
    // 기본값 설정
    DEFAULT_PERIPHERALS.forEach(p => {
      statusMap.set(p.id, { failCount: 0 });
    });
    
    // 알림 기반 상태 업데이트
    alerts.forEach(alert => {
      const deviceId = alert.metric;
      const match = alert.details?.match(/연속 (\d+)회 실패/);
      const failCount = match ? parseInt(match[1], 10) : 1;
      
      const current = statusMap.get(deviceId) || { failCount: 0 };
      statusMap.set(deviceId, {
        failCount: Math.max(current.failCount, failCount),
        lastAlert: alert.details,
      });
    });
    
    return statusMap;
  }, [alerts]);

  return (
    <div style={{
      backgroundColor: '#1e293b',
      borderRadius: '12px',
      padding: '16px',
      marginBottom: '20px',
    }}>
      <h3 style={{ margin: '0 0 12px', fontSize: '14px', color: '#94a3b8' }}>
        🔌 주변장치 연결 상태
      </h3>
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
        gap: '12px',
      }}>
        {DEFAULT_PERIPHERALS.map(device => {
          const status = deviceStatus.get(device.id) || { failCount: 0 };
          const isError = status.failCount >= 10;
          const isWarning = status.failCount >= 3 && status.failCount < 10;
          const isOk = status.failCount < 3;
          
          const bgColor = isError ? '#7f1d1d' : isWarning ? '#713f12' : '#14532d';
          const borderColor = isError ? '#ef4444' : isWarning ? '#f59e0b' : '#22c55e';
          const statusIcon = isError ? '❌' : isWarning ? '⚠️' : '✅';
          const statusText = isError ? '연결 실패' : isWarning ? '불안정' : '정상';
          
          return (
            <div
              key={device.id}
              style={{
                backgroundColor: bgColor,
                border: `1px solid ${borderColor}`,
                borderRadius: '8px',
                padding: '12px',
                textAlign: 'center',
                transition: 'transform 0.2s',
              }}
            >
              <div style={{ fontSize: '24px', marginBottom: '4px' }}>
                {device.icon}
              </div>
              <div style={{
                fontSize: '12px',
                fontWeight: 'bold',
                color: '#e2e8f0',
                marginBottom: '6px',
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
                gap: '4px',
                fontSize: '11px',
                color: isError ? '#fca5a5' : isWarning ? '#fcd34d' : '#86efac',
              }}>
                <span>{statusIcon}</span>
                <span>{statusText}</span>
              </div>
              {status.failCount > 0 && (
                <div style={{
                  marginTop: '6px',
                  fontSize: '10px',
                  color: '#94a3b8',
                }}>
                  실패: {status.failCount}회
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
