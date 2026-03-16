import { useEffect, useState } from 'react';

interface AccuracyStats {
  count: number;
  mean_error_pct: number;
  std_error: number;
  min_error_pct: number;
  max_error_pct: number;
  within_3sigma_pct: number;
}

interface AccuracyRecord {
  timestamp: string;
  actual_value: number | null;
  forecast_value: number | null;
  error_pct: number;
  horizon_min: number;
}

interface AccuracyData {
  stats: AccuracyStats;
  records: AccuracyRecord[];
}

interface UseAccuracyDataResult {
  data: AccuracyData | null;
  loading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
}

const API_BASE = `http://localhost:8080`;

export function useAccuracyData(
  agentId: string = 'V135-POS-03',
  metric: string = 'CPU',
  horizonMin: number = 30,
  pollInterval: number = 30000, // 30 seconds
  bucket: string = 'pos_metrics',
): UseAccuracyDataResult {
  const [data, setData] = useState<AccuracyData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchAccuracyData = async () => {
    try {
      setError(null);
      const params = new URLSearchParams({
        agent_id: agentId,
        metric,
        horizon_min: horizonMin.toString(),
        bucket,
      });

      const url = `${API_BASE}/api/accuracy?${params}`;
      console.log('[useAccuracyData] Fetching from:', url);

      const response = await fetch(url);
      console.log('[useAccuracyData] Response status:', response.status, response.statusText);

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const result = await response.json();
      console.log('[useAccuracyData] Full response:', result);
      console.log('[useAccuracyData] Response received:', {
        recordCount: result.records?.length || 0,
        stats: result.stats,
        recordsType: typeof result.records,
        statsType: typeof result.stats
      });

      setData({
        stats: result.stats || {
          count: 0,
          mean_error_pct: 0,
          std_error: 0,
          min_error_pct: 0,
          max_error_pct: 0,
          within_3sigma_pct: 0,
        },
        records: result.records || [],
      });
    } catch (e) {
      const errorMsg = e instanceof Error ? e.message : 'Unknown error';
      console.error('[useAccuracyData] Error:', errorMsg);
      setError(errorMsg);
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAccuracyData();
    const interval = setInterval(fetchAccuracyData, pollInterval);
    return () => clearInterval(interval);
  }, [agentId, metric, horizonMin, pollInterval, bucket]);

  const refetch = fetchAccuracyData;

  return { data, loading, error, refetch };
}
