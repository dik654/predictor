import { useEffect, useState, useCallback } from 'react';
import { Usb, ScanBarcode, BookOpen, Smartphone, Keyboard, CreditCard, CircleDot, Wifi, WifiOff, HelpCircle } from 'lucide-react';
import type { ReactNode } from 'react';

// PulseAI-WindowsAgent 송신전문 v0.1 기준 주변장치 목록 (영문 field명)
const PERIPHERALS: { id: string; name: string; icon: ReactNode }[] = [
  { id: 'dongle', name: '동글이', icon: <Usb size={18} /> },
  { id: 'hand_scanner', name: '핸드스캐너', icon: <ScanBarcode size={18} /> },
  { id: 'passport_reader', name: '여권리더기', icon: <BookOpen size={18} /> },
  { id: '2d_scanner', name: '2D 스캐너', icon: <CircleDot size={18} /> },
  { id: 'phone_charger', name: '충전기', icon: <Smartphone size={18} /> },
  { id: 'keyboard', name: '키보드', icon: <Keyboard size={18} /> },
  { id: 'msr', name: 'MSR', icon: <CreditCard size={18} /> },
];

type DeviceState = 'connected' | 'disconnected' | 'unused' | 'no_data';

const stateConfig: Record<DeviceState, { label: string; color: string; bg: string; border: string; icon: ReactNode }> = {
  connected:    { label: '연결',   color: '#4ade80', bg: '#052e16', border: '#166534', icon: <Wifi size={12} /> },
  disconnected: { label: '미연결', color: '#f87171', bg: '#450a0a', border: '#7f1d1d', icon: <WifiOff size={12} /> },
  unused:       { label: '미사용', color: '#a78bfa', bg: '#1e1b4b', border: '#3730a3', icon: <HelpCircle size={12} /> },
  no_data:      { label: '미전송', color: '#94a3b8', bg: '#1e293b', border: '#334155', icon: <HelpCircle size={12} /> },
};

function toState(value: number | null | undefined): DeviceState {
  if (value === undefined || value === null) return 'no_data';
  if (value === 1) return 'connected';
  if (value === 0) return 'disconnected';
  if (value === -1) return 'unused';
  return 'no_data';
}

function formatSince(since: string | null): string {
  if (!since) return '';
  try {
    const diff = Date.now() - new Date(since).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 60) return `${mins}분 전부터`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}시간 전부터`;
    return `${Math.floor(hours / 24)}일 전부터`;
  } catch { return ''; }
}

interface PeripheralCardsProps {
  externalDevices?: Record<string, { status: number | null; since?: string | null }>;
}

export function PeripheralCards({ externalDevices }: PeripheralCardsProps = {}) {
  const serverUrl = `${window.location.protocol}//${window.location.hostname}:8080`;
  const [devices, setDevices] = useState<Record<string, { status: number | null; since: string | null }>>({});

  const fetchStatus = useCallback(async () => {
    if (externalDevices) return; // 외부 데이터 사용 시 fetch 안 함
    try {
      const resp = await fetch(`${serverUrl}/api/peripheral-status?agent_id=V135-POS-03`);
      if (resp.ok) {
        const data = await resp.json();
        setDevices(data.devices || {});
      }
    } catch { /* ignore */ }
  }, [serverUrl, externalDevices]);

  useEffect(() => {
    if (externalDevices) return;
    fetchStatus();
    const interval = setInterval(fetchStatus, 5000);
    return () => clearInterval(interval);
  }, [fetchStatus, externalDevices]);

  const displayDevices = externalDevices
    ? Object.fromEntries(Object.entries(externalDevices).map(([k, v]) => [k, { status: v.status, since: v.since || null }]))
    : devices;

  return (
    <div style={{
      backgroundColor: '#111827',
      borderRadius: '10px',
      border: '1px solid #1f2937',
      padding: '16px',
      marginBottom: '12px',
    }}>
      <h3 style={{ margin: '0 0 12px', fontSize: '13px', fontWeight: 500, color: '#cbd5e1' }}>
        주변장치 연결 상태
      </h3>
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
        gap: '10px',
      }}>
        {PERIPHERALS.map(device => {
          const deviceData = displayDevices[device.id];
          const state = toState(deviceData?.status);
          const cfg = stateConfig[state];
          const sinceText = state === 'disconnected' ? formatSince(deviceData?.since) : '';

          return (
            <div
              key={device.id}
              style={{
                backgroundColor: cfg.bg,
                border: `1px solid ${cfg.border}`,
                borderRadius: '8px',
                padding: '14px 12px',
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
                fontSize: '11px',
                color: cfg.color,
              }}>
                {cfg.icon}
                <span>{cfg.label}</span>
              </div>
              {sinceText && (
                <div style={{ fontSize: '8px', color: '#cbd5e1', marginTop: '3px' }}>
                  {sinceText}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
