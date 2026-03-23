import { useEffect, useState, useCallback } from 'react';
import { IncidentPrediction } from '../components/IncidentPrediction';
import { ShieldAlert } from 'lucide-react';

const DEFAULT_AGENT_ID = 'V135-POS-03';
const POLL_INTERVAL = 30_000;

type BucketMode = 'pos_metrics' | 'sample_metrics';

export function IncidentPredictionPage() {
  const serverUrl = `${window.location.protocol}//${window.location.hostname}:8080`;
  const [bucket, setBucket] = useState<BucketMode>('sample_metrics');
  const [evaluation, setEvaluation] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchEvaluation = useCallback(async () => {
    try {
      const resp = await fetch(
        `${serverUrl}/api/forecast-evaluation?agent_id=${DEFAULT_AGENT_ID}&bucket=${bucket}`
      );
      if (resp.ok) {
        const data = await resp.json();
        if (data && data.horizons?.length > 0) {
          setEvaluation(data);
          setLastUpdated(new Date());
          setError(null);
        } else {
          setEvaluation(null);
          setError('분석 데이터가 없습니다.');
        }
      } else if (resp.status === 404) {
        setEvaluation(null);
        setError('아직 분석 데이터가 없습니다. 서버에서 데이터를 수집 중입니다.');
      }
    } catch {
      setError('서버에 연결할 수 없습니다.');
    } finally {
      setLoading(false);
    }
  }, [serverUrl, bucket]);

  // 버킷 변경 시 초기화 + 재조회
  useEffect(() => {
    setLoading(true);
    setEvaluation(null);
    setError(null);
    fetchEvaluation();
    const interval = setInterval(fetchEvaluation, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [fetchEvaluation]);

  const dataAge = evaluation?.timestamp ? getDataAge(evaluation.timestamp) : null;

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0a0e1a', color: '#e2e8f0', padding: '20px', fontFamily: "'Inter', -apple-system, sans-serif" }}>
      {/* Header */}
      <header style={{
        backgroundColor: '#111827',
        border: '1px solid #1f2937',
        borderRadius: '10px',
        padding: '14px 24px',
        marginBottom: '16px',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <ShieldAlert size={20} color="#ef4444" />
          <div>
            <h1 style={{ margin: 0, fontSize: '16px', fontWeight: 600, color: '#f1f5f9' }}>사고 예측 분석</h1>
            <p style={{ margin: 0, fontSize: '12px', color: '#cbd5e1' }}>
              ARIMA + ECOD 앙상블로 1시간~48시간 후 장애 가능성 평가
            </p>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          {/* Bucket Toggle */}
          <div style={{
            display: 'flex', borderRadius: '6px', overflow: 'hidden',
            border: '1px solid #1f2937',
          }}>
            <button
              onClick={() => setBucket('pos_metrics')}
              style={{
                padding: '6px 14px', fontSize: '11px', fontWeight: 600, cursor: 'pointer',
                border: 'none', borderRadius: '6px', transition: 'all 0.2s',
                backgroundColor: bucket === 'pos_metrics' ? '#1e293b' : 'transparent',
                color: bucket === 'pos_metrics' ? '#e2e8f0' : '#cbd5e1',
              }}
            >
              Live
            </button>
            <button
              onClick={() => setBucket('sample_metrics')}
              style={{
                padding: '6px 14px', fontSize: '11px', fontWeight: 600, cursor: 'pointer',
                border: 'none', borderLeft: '1px solid #1f2937', borderRadius: '6px', transition: 'all 0.2s',
                backgroundColor: bucket === 'sample_metrics' ? '#1e293b' : 'transparent',
                color: bucket === 'sample_metrics' ? '#e2e8f0' : '#cbd5e1',
              }}
            >
              DB
            </button>
          </div>

          {dataAge && (
            <span style={{
              padding: '3px 8px',
              borderRadius: '6px',
              fontSize: '11px',
              backgroundColor: '#1e293b',
              color: '#60a5fa',
            }}>
              {dataAge} 갱신
            </span>
          )}
          {evaluation?.agent_id && (
            <span style={{
              padding: '3px 8px',
              borderRadius: '6px',
              fontSize: '11px',
              backgroundColor: '#1e293b',
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
          backgroundColor: '#111827',
          border: '1px solid #1f2937',
          borderRadius: 10,
          padding: 32,
          textAlign: 'center',
          color: '#cbd5e1',
        }}>
          데이터를 불러오는 중...
        </div>
      )}

      {/* Error */}
      {!loading && error && !evaluation && (
        <div style={{
          backgroundColor: '#111827',
          border: '1px solid #1f2937',
          borderRadius: 10,
          padding: 32,
          textAlign: 'center',
          color: '#cbd5e1',
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
