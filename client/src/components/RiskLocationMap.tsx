import React from 'react';
import { MapContainer, TileLayer, Marker, Popup } from 'react-leaflet';
import L from 'leaflet';

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
}

interface RiskLocationMapProps {
  devices: RiskDevice[];
  onDeviceSelect: (device: RiskDevice) => void;
}

// 위험도별 마커 아이콘
const createMarkerIcon = (severity: 'critical' | 'warning') => {
  const color = severity === 'critical' ? '#ef4444' : '#f59e0b';
  return L.divIcon({
    html: `<div style="background-color: ${color}; width: 32px; height: 32px; border-radius: 50%; border: 3px solid white; display: flex; align-items: center; justify-content: center; font-weight: bold; color: white; font-size: 18px;">⚠️</div>`,
    iconSize: [32, 32],
    className: 'custom-icon'
  });
};

export function RiskLocationMap({ devices, onDeviceSelect }: RiskLocationMapProps) {
  const center: [number, number] = [37.5665, 126.978];

  return (
    <MapContainer
      center={center}
      zoom={11}
      style={{ height: '400px', borderRadius: '8px', backgroundColor: '#1a1a2e' }}
    >
      <TileLayer
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution='&copy; OpenStreetMap contributors'
      />
      {devices.map((device) => (
        <Marker
          key={device.id}
          position={[device.lat, device.lng]}
          icon={createMarkerIcon(device.severity)}
          eventHandlers={{
            click: () => onDeviceSelect(device),
          }}
        >
          <Popup>
            <div style={{ fontSize: '12px', color: '#333' }}>
              <strong>{device.id}</strong>
              <br />
              {device.metric} - {device.component}
            </div>
          </Popup>
        </Marker>
      ))}
    </MapContainer>
  );
}
