import { useEffect, useState } from 'react';

export interface AccuracyRecord {
  time: string;
  actual_value: number;
  forecast_value: number;
  error_pct: number;
}

interface ComparisonData {
  agent_id: string;
  metric: string;
  horizon_min: string;
  records: AccuracyRecord[];
}

export function useForecastComparison(
  agentId: string = 'V135-POS-03',
  metric: string = 'cpu',
  horizonMin: string = '60',
  bucket: string = 'pos_metrics',
) {
  const [data, setData] = useState<ComparisonData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    const fetchData = async () => {
      try {
        setLoading(true);
        const params = new URLSearchParams({ agent_id: agentId, metric, horizon_min: horizonMin, bucket });
        const url = `http://localhost:8080/api/forecast-vs-actual?${params}`;
        console.log('[useForecastComparison] Fetching from:', url);

        const response = await fetch(url);
        if (!response.ok) throw new Error('Failed to fetch forecast comparison');
        const result = await response.json();

        console.log('[useForecastComparison] Records received:', result.records?.length || 0);

        if (isMounted) {
          setData(result);
          setError(null);
        }
      } catch (err) {
        const errorMsg = err instanceof Error ? err.message : 'Unknown error';
        console.error('[useForecastComparison] Error:', errorMsg);
        if (isMounted) {
          setError(errorMsg);
          setData(null);
        }
      } finally {
        if (isMounted) setLoading(false);
      }
    };

    fetchData();
    const interval = setInterval(fetchData, 30000);

    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, [agentId, metric, horizonMin, bucket]);

  return { data, loading, error };
}
