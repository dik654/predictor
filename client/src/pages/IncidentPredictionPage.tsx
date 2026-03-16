import { useEffect, useState, useCallback } from 'react';
import { IncidentPrediction } from '../components/IncidentPrediction';

const DEFAULT_AGENT_ID = 'V135-POS-03';
const POLL_INTERVAL = 30_000; // 30초마다 폴링

export function IncidentPredictionPage() {
  const serverUrl = `${window.location.protocol}//${window.location.hostname}:8080`;
  const [evaluation, setEvaluation] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchEvaluation = useCallback(async () => {
    try {
      const resp = await fetch(
        `${serverUrl}/api/forecast-evaluation?agent_id=${DEFAULT_AGENT_ID}`
      );
      if (resp.ok) {
        const data = await resp.json();
        if (data && data.horizons?.length > 0) {
          setEvaluation(data);
          setLastUpdated(new Date());
          setError(null);
        }
      } else if (resp.status === 404) {
        setError('아직 분석 데이터가 없습니다. 서버에서 데이터를 수집 중입니다.');
      }
    } catch {
      setError('서버에 연결할 수 없습니다.');
    } finally {
      setLoading(false);
    }
  }, [serverUrl]);

  // 초기 로드 + 주기적 폴링
  useEffect(() => {
    fetchEvaluation();
    const interval = setInterval(fetchEvaluation, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [fetchEvaluation]);

  const dataAge = evaluation?.timestamp ? getDataAge(evaluation.timestamp) : null;

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0f172a', color: '#e2e8f0', padding: '20px' }}>
      {/* Header */}
      <header style={{
        backgroundColor: '#1e293b',
        borderRadius: '12px',
        padding: '16px 24px',
        marginBottom: '20px',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
      }}>
        <div>
          <h1 style={{ margin: 0, fontSize: '24px' }}>사고 예측 분석</h1>
          <p style={{ margin: '4px 0 0', fontSize: '14px', color: '#94a3b8' }}>
            ARIMA 예측값을 장기 ECOD 모델로 평가하여 1시간~2일 후 사고 가능성을 판단합니다
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          {dataAge && (
            <span style={{
              padding: '4px 10px',
              borderRadius: '12px',
              fontSize: '11px',
              backgroundColor: '#1e3a5f',
              color: '#60a5fa',
            }}>
              {dataAge} 갱신
            </span>
          )}
          {evaluation?.agent_id && (
            <span style={{
              padding: '6px 12px',
              borderRadius: '16px',
              fontSize: '12px',
              backgroundColor: '#1e3a5f',
              color: '#60a5fa',
            }}>
              {evaluation.agent_id}
            </span>
          )}
        </div>
      </header>

      {/* Loading */}
      {loading && (
        <div style={{
          backgroundColor: '#1e293b',
          borderRadius: 12,
          padding: 32,
          textAlign: 'center',
          color: '#94a3b8',
        }}>
          데이터를 불러오는 중...
        </div>
      )}

      {/* Error */}
      {!loading && error && !evaluation && (
        <div style={{
          backgroundColor: '#1e293b',
          borderRadius: 12,
          padding: 32,
          textAlign: 'center',
          color: '#94a3b8',
        }}>
          <div style={{ fontSize: 16, marginBottom: 8 }}>{error}</div>
          <div style={{ fontSize: 13 }}>30초마다 자동으로 재시도합니다.</div>
        </div>
      )}

      {/* Incident Prediction */}
      {!loading && evaluation && (
        <IncidentPrediction
          evaluation={evaluation}
          agentId={evaluation.agent_id}
        />
      )}
    </div>
  );
}


function getDataAge(timestamp: string): string | null {
  try {
    const ts = new Date(timestamp);
    const now = new Date();
    const diffMs = now.getTime() - ts.getTime();
    const diffMin = Math.floor(diffMs / 60000);

    if (diffMin < 1) return '방금';
    if (diffMin < 60) return `${diffMin}분 전`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}시간 전`;
    return `${Math.floor(diffHr / 24)}일 전`;
  } catch {
    return null;
  }
}
