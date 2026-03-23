import { useEffect, useState, useCallback, useRef, type ReactNode } from 'react';
import ReactECharts from 'echarts-for-react';
import { PeripheralCards } from '../components/PeripheralCards';
import { StatusInsightCard } from '../components/StatusInsightCard';
import { Activity, Search, TrendingUp, AlertTriangle, Database, Wifi, WifiOff } from 'lucide-react';

interface InfluxMetric {
  timestamp: string;
  agent_id: string;
  cpu: number;
  memory: number;
  disk_io: number;
  network_sent_bytes: number;
  network_received_bytes: number;
}

interface InfluxDetection {
  timestamp: string;
  engine: string;
  metric: string;
  score: number;
  threshold: number;
  severity: string;
  confidence: number;
  actual_value?: number;
  arima_predicted?: number;
  arima_deviation?: number;
  details?: string;
}

type ViewMode = 'db' | 'realtime';

const METRIC_KO: Record<string, string> = {
  Dongle: '동글', HandScanner: '핸드스캐너', '2DScanner': '2D스캐너',
  PassportReader: '여권리더기', PhoneCharger: '충전기', Keyboard: '키보드', MSR: 'MSR',
  CPU: 'CPU', Memory: '메모리', DiskIO: '디스크IO',
  NetworkSent: '네트워크 송신', NetworkRecv: '네트워크 수신',
  Process: '프로세스', POS_Idle: 'POS 유휴', Multivariate: '종합',
  // peripheral-status API 소문자 필드명
  dongle: '동글', hand_scanner: '핸드스캐너', '2d_scanner': '2D스캐너',
  passport_reader: '여권리더기', phone_charger: '충전기', keyboard: '키보드', msr: 'MSR',
};

const DATA_LIMIT = 100; // 화면에 표시할 윈도우 크기 (시점 기준)

const DB_FETCH_BATCH = 200;     // DB 모드: 한 번에 가져올 시점 수
const DB_PREFETCH_THRESHOLD = 50; // 남은 데이터가 이만큼 이하면 다음 배치 미리 로드
const DB_REPLAY_INTERVAL = 5000; // 재생 간격 (ms)

const MODE_CONFIG = {
  db:       { metricsLimit: DB_FETCH_BATCH, detectionsLimit: DB_FETCH_BATCH, pollInterval: 5_000 },
  realtime: { metricsLimit: DATA_LIMIT, detectionsLimit: DATA_LIMIT, pollInterval: 2_000 },
} as const;

export function Dashboard() {
  const serverUrl = `${window.location.protocol}//${window.location.hostname}:8080`;
  const [viewMode, setViewMode] = useState<ViewMode>('db');
  const [arimaMetric, setArimaMetric] = useState<string>('CPU');
  const [ecodGroup, setEcodGroup] = useState<'all' | 'system' | 'peripheral' | 'status'>('all');
  const [historyEngine, setHistoryEngine] = useState<'all' | 'ecod' | 'arima' | 'peripheral'>('all');
  const historyRef = useRef<HTMLDivElement>(null);
  const scrollToHistory = (engine: 'ecod' | 'arima' | 'peripheral') => {
    setHistoryEngine(engine);
    setTimeout(() => historyRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
  };

  const [dbMetrics, setDbMetrics] = useState<InfluxMetric[]>([]);
  const [dbDetections, setDbDetections] = useState<InfluxDetection[]>([]);
  const [dbHealthScore, setDbHealthScore] = useState(100);
  const [dbConnected, setDbConnected] = useState(false);
  const prevMetricsLen = useRef(0);
  const prevDetectionsLen = useRef(0);
  const [periphStatus, setPeriphStatus] = useState<Record<string, { status: number | null }>>({});

  // 주변장치 상태 조회 (peripheral_status 측정값 직접)
  const fetchPeriphStatus = useCallback(async () => {
    try {
      const resp = await fetch(`${serverUrl}/api/peripheral-status?agent_id=V135-POS-03`);
      if (resp.ok) {
        const data = await resp.json();
        setPeriphStatus(data.devices || {});
      }
    } catch { /* ignore */ }
  }, [serverUrl]);

  // DB 재생 모드 상태
  const replayMetricsAll = useRef<InfluxMetric[]>([]);
  const replayDetectionsAll = useRef<InfluxDetection[]>([]);
  const replayIdx = useRef(0);
  const [replayPlaying, setReplayPlaying] = useState(false);

  const fetchMetrics = useCallback(async (limit: number) => {
    try {
      const resp = await fetch(`${serverUrl}/api/recent-metrics?agent_id=V135-POS-03&limit=${limit}`);
      if (resp.ok) {
        const data = await resp.json();
        const metrics: InfluxMetric[] = data.metrics || [];
        setDbConnected(metrics.length > 0);
        setDbMetrics(metrics);
      } else {
        setDbConnected(false);
      }
    } catch {
      setDbConnected(false);
    }
  }, [serverUrl]);

  const fetchDetections = useCallback(async (limit: number) => {
    try {
      const resp = await fetch(`${serverUrl}/api/recent-detections?agent_id=V135-POS-03&limit=${limit}`);
      if (resp.ok) {
        const data = await resp.json();
        const detections: InfluxDetection[] = data.detections || [];
        setDbDetections(detections);
        {
          let score = 100;
          for (const d of detections.slice(-20)) {
            if (d.severity === 'critical') score -= Math.round(20 * d.confidence);
            else if (d.severity === 'warning') score -= Math.round(10 * d.confidence);
          }
          setDbHealthScore(Math.max(0, Math.min(100, score)));
        }
      }
    } catch { /* ignore */ }
  }, [serverUrl]);

  // DB 재생: 배치 단위로 데이터를 가져오면서 슬라이딩 윈도우 재생
  const isFetchingBatch = useRef(false);
  // 다음 루프용 과거 데이터를 미리 담아두는 버퍼
  const nextLoopMetrics = useRef<InfluxMetric[] | null>(null);
  const nextLoopDetections = useRef<InfluxDetection[] | null>(null);

  useEffect(() => {
    if (viewMode !== 'db') return;

    prevMetricsLen.current = 0;
    prevDetectionsLen.current = 0;
    replayIdx.current = 0;
    replayMetricsAll.current = [];
    replayDetectionsAll.current = [];
    isFetchingBatch.current = false;
    nextLoopMetrics.current = null;
    nextLoopDetections.current = null;
    setDbMetrics([]);
    setDbDetections([]);
    setReplayPlaying(true);

    let cancelled = false;

    // 배치를 가져와서 현재 버퍼에 이어붙이기 (커서 기반)
    const fetchNextBatch = async (afterTs: string) => {
      if (isFetchingBatch.current) return;
      isFetchingBatch.current = true;
      try {
        const afterParam = `&after=${encodeURIComponent(afterTs)}`;
        const [mResp, dResp] = await Promise.all([
          fetch(`${serverUrl}/api/recent-metrics?agent_id=V135-POS-03&limit=${DB_FETCH_BATCH}&order=oldest${afterParam}`),
          fetch(`${serverUrl}/api/recent-detections?agent_id=V135-POS-03&limit=${DB_FETCH_BATCH}&order=oldest${afterParam}`),
        ]);
        if (cancelled) return;
        const mData = mResp.ok ? await mResp.json() : { metrics: [] };
        const dData = dResp.ok ? await dResp.json() : { detections: [] };
        const newMetrics: InfluxMetric[] = mData.metrics || [];
        const newDetections: InfluxDetection[] = dData.detections || [];
        if (newMetrics.length > 0) {
          replayMetricsAll.current = [...replayMetricsAll.current, ...newMetrics];
          replayDetectionsAll.current = [...replayDetectionsAll.current, ...newDetections];
        }
      } catch { /* ignore */ } finally {
        isFetchingBatch.current = false;
      }
    };

    // 다음 루프를 위해 과거 데이터를 미리 fetch해두기
    const prefetchLoopStart = async () => {
      if (nextLoopMetrics.current !== null) return; // 이미 준비됨
      try {
        const [mResp, dResp] = await Promise.all([
          fetch(`${serverUrl}/api/recent-metrics?agent_id=V135-POS-03&limit=${DB_FETCH_BATCH}&order=oldest`),
          fetch(`${serverUrl}/api/recent-detections?agent_id=V135-POS-03&limit=${DB_FETCH_BATCH}&order=oldest`),
        ]);
        if (cancelled) return;
        const mData = mResp.ok ? await mResp.json() : { metrics: [] };
        const dData = dResp.ok ? await dResp.json() : { detections: [] };
        nextLoopMetrics.current = mData.metrics || [];
        nextLoopDetections.current = dData.detections || [];
      } catch { /* ignore */ }
    };

    // 초기 배치 fetch (가장 오래된 데이터)
    const fetchInitial = async () => {
      try {
        const [mResp, dResp] = await Promise.all([
          fetch(`${serverUrl}/api/recent-metrics?agent_id=V135-POS-03&limit=${DB_FETCH_BATCH}&order=oldest`),
          fetch(`${serverUrl}/api/recent-detections?agent_id=V135-POS-03&limit=${DB_FETCH_BATCH}&order=oldest`),
        ]);
        if (cancelled) return 0;
        const mData = mResp.ok ? await mResp.json() : { metrics: [] };
        const dData = dResp.ok ? await dResp.json() : { detections: [] };
        replayMetricsAll.current = mData.metrics || [];
        replayDetectionsAll.current = dData.detections || [];
        return replayMetricsAll.current.length;
      } catch { return 0; }
    };

    (async () => {
      const count = await fetchInitial();
      if (cancelled) return;
      setDbConnected(count > 0);
      fetchPeriphStatus();

      if (replayMetricsAll.current.length === 0) {
        setReplayPlaying(false);
        return;
      }

      replayIdx.current = Math.min(DATA_LIMIT, replayMetricsAll.current.length);

      const step = () => {
        if (cancelled) return;
        const totalLen = replayMetricsAll.current.length;
        let idx = replayIdx.current;

        // 데이터 끝 도달 → 미리 받아둔 과거 데이터로 즉시 교체 (끊김 없음)
        if (idx >= totalLen) {
          if (nextLoopMetrics.current && nextLoopMetrics.current.length > 0) {
            replayMetricsAll.current = nextLoopMetrics.current;
            replayDetectionsAll.current = nextLoopDetections.current || [];
            nextLoopMetrics.current = null;
            nextLoopDetections.current = null;
            idx = Math.min(DATA_LIMIT, replayMetricsAll.current.length);
            replayIdx.current = idx;
          } else {
            // prefetch가 아직 안됐으면 처음부터 재사용 (fallback)
            idx = Math.min(DATA_LIMIT, totalLen);
            replayIdx.current = idx;
          }
        }

        const windowEnd = idx;
        const windowStart = Math.max(0, windowEnd - DATA_LIMIT);
        setDbMetrics(replayMetricsAll.current.slice(windowStart, windowEnd));

        // Detection: 해당 시점 범위의 데이터 필터
        const startTs = replayMetricsAll.current[windowStart].timestamp;
        const endTs = replayMetricsAll.current[windowEnd - 1].timestamp;
        setDbDetections(replayDetectionsAll.current.filter(d =>
          d.timestamp >= startTs && d.timestamp <= endTs
        ));

        // Health score 계산
        const recentDets = replayDetectionsAll.current.filter(d =>
          d.timestamp >= startTs && d.timestamp <= endTs
        ).slice(-20);
        let score = 100;
        for (const d of recentDets) {
          if (d.severity === 'critical') score -= Math.round(20 * d.confidence);
          else if (d.severity === 'warning') score -= Math.round(10 * d.confidence);
        }
        setDbHealthScore(Math.max(0, Math.min(100, score)));

        replayIdx.current = idx + 1;

        const remaining = totalLen - idx;

        // 끝에 가까우면 (1) 다음 배치 이어붙이기 + (2) 다음 루프 과거 데이터 미리 fetch
        if (remaining <= DB_PREFETCH_THRESHOLD) {
          if (!isFetchingBatch.current) {
            const lastTs = replayMetricsAll.current[replayMetricsAll.current.length - 1].timestamp;
            fetchNextBatch(lastTs);
          }
          prefetchLoopStart();
        }

        setTimeout(step, DB_REPLAY_INTERVAL);
      };

      step();
    })();

    return () => { cancelled = true; };
  }, [viewMode, serverUrl]);

  // 실시간 모드: 기존 폴링
  useEffect(() => {
    if (viewMode !== 'realtime') return;
    const cfg = MODE_CONFIG.realtime;
    prevMetricsLen.current = 0;
    prevDetectionsLen.current = 0;

    fetchMetrics(cfg.metricsLimit);
    fetchDetections(cfg.detectionsLimit);
    fetchPeriphStatus();
    const metricsInterval = setInterval(() => fetchMetrics(cfg.metricsLimit), cfg.pollInterval);
    const detectionsInterval = setInterval(() => fetchDetections(cfg.detectionsLimit), cfg.pollInterval);
    const periphInterval = setInterval(fetchPeriphStatus, cfg.pollInterval);
    return () => {
      clearInterval(metricsInterval);
      clearInterval(detectionsInterval);
      clearInterval(periphInterval);
    };
  }, [viewMode, fetchMetrics, fetchDetections]);

  const CHART_POINTS = 20;
  const ecodMulti = dbDetections.filter(d => d.engine === 'ecod' && d.metric === 'Multivariate').slice(-CHART_POINTS);
  const ecodCpu = dbDetections.filter(d => d.engine === 'ecod' && d.metric === 'CPU').slice(-CHART_POINTS);
  const ecodMem = dbDetections.filter(d => d.engine === 'ecod' && d.metric === 'Memory').slice(-CHART_POINTS);
  const ecodDisk = dbDetections.filter(d => d.engine === 'ecod' && d.metric === 'DiskIO').slice(-CHART_POINTS);
  const ecodNetSent = dbDetections.filter(d => d.engine === 'ecod' && d.metric === 'NetworkSent').slice(-CHART_POINTS);
  const ecodNetRecv = dbDetections.filter(d => d.engine === 'ecod' && d.metric === 'NetworkRecv').slice(-CHART_POINTS);
  const ecodProc = dbDetections.filter(d => d.engine === 'ecod' && d.metric === 'Process').slice(-CHART_POINTS);
  const ecodDongle = dbDetections.filter(d => d.engine === 'ecod' && d.metric === 'Dongle').slice(-CHART_POINTS);
  const ecodHandScanner = dbDetections.filter(d => d.engine === 'ecod' && d.metric === 'HandScanner').slice(-CHART_POINTS);
  const ecodPassport = dbDetections.filter(d => d.engine === 'ecod' && d.metric === 'PassportReader').slice(-CHART_POINTS);
  const ecod2dScanner = dbDetections.filter(d => d.engine === 'ecod' && d.metric === '2DScanner').slice(-CHART_POINTS);
  const ecodPhoneCharger = dbDetections.filter(d => d.engine === 'ecod' && d.metric === 'PhoneCharger').slice(-CHART_POINTS);
  const ecodKeyboard = dbDetections.filter(d => d.engine === 'ecod' && d.metric === 'Keyboard').slice(-CHART_POINTS);
  const ecodMsr = dbDetections.filter(d => d.engine === 'ecod' && d.metric === 'MSR').slice(-CHART_POINTS);
  const ecodIdle = dbDetections.filter(d => d.engine === 'ecod' && d.metric === 'POS_Idle').slice(-CHART_POINTS);
  const PERIPHERAL_METRICS = new Set(['Dongle', 'HandScanner', '2DScanner', 'PassportReader', 'PhoneCharger', 'Keyboard', 'MSR']);
  const ecodData = dbDetections.filter(d => d.engine === 'ecod');
  const arimaData = dbDetections.filter(d => d.engine === 'arima');
  // 주변장치 꺼짐: peripheral-status API 기반 (PeripheralCards와 동일 소스)
  const peripheralWarnDevices = Object.entries(periphStatus)
    .filter(([_, v]) => v.status === 0)
    .map(([k]) => k);
  const selectedArimaData = arimaData.filter(d => d.metric === arimaMetric).slice(-CHART_POINTS);
  const arimaMetrics = [...new Set(arimaData.map(d => d.metric))];
  const latestDetections = dbDetections.slice(-30);
  const filteredDetections = historyEngine === 'all' ? dbDetections
    : historyEngine === 'peripheral' ? dbDetections.filter(d => d.engine === 'peripheral' || (d.engine === 'ecod' && PERIPHERAL_METRICS.has(d.metric)))
    : dbDetections.filter(d => d.engine === historyEngine);
  const allDetections = filteredDetections.slice(-50).reverse();
  const BINARY_METRICS = new Set(['Dongle', 'HandScanner', '2DScanner', 'PassportReader', 'PhoneCharger', 'Keyboard', 'MSR', 'Process', 'POS_Idle']);
  // ECOD 경고: 시스템 메트릭만 (주변장치는 별도 카드에서 표시)
  const ecodWarnings = ecodData.filter(d =>
    (d.severity === 'warning' || d.severity === 'critical') && !BINARY_METRICS.has(d.metric)
  );
  // ARIMA 경고: 메트릭별 최신 임계값 기준으로 클라이언트에서 재판정
  const arimaWarnings = (() => {
    // 메트릭별 최신 threshold 수집
    const latestThreshold = new Map<string, number>();
    for (const d of arimaData) {
      if (d.threshold > 0) latestThreshold.set(d.metric, d.threshold);
    }
    return arimaData.filter(d => {
      const th = latestThreshold.get(d.metric) || d.threshold || 0;
      const residual = Math.abs(d.arima_deviation || 0);
      return th > 0 && residual > th;
    });
  })();

  // ECOD 경고 요약: 이진(주변장치) vs 연속(시스템) 분리 — ecodWarnings는 이미 시스템만 필터됨
  const ecodBinaryWarns: string[] = []; // 주변장치는 별도 카드에서 처리
  const ecodSystemWarns = ecodWarnings.filter(d => !BINARY_METRICS.has(d.metric) && d.metric !== 'Multivariate');
  const ecodSystemSummary = [...new Set(ecodSystemWarns.map(d => d.metric))].map(m => {
    const latest = ecodSystemWarns.filter(d => d.metric === m).slice(-1)[0];
    const ko = METRIC_KO[m] || m;
    return latest ? `${ko} ${latest.details?.match(/[\d.]+%|[\d.]+bytes/)?.[0] || `score ${latest.score?.toFixed(2)}`}` : ko;
  });
  const ecodSummary = (() => {
    const parts: string[] = [];
    if (ecodSystemSummary.length > 0) parts.push(`시스템: ${ecodSystemSummary.join(', ')}`);
    if (ecodBinaryWarns.length > 0) parts.push(`장치 꺼짐/비정상: ${ecodBinaryWarns.length}개 (${ecodBinaryWarns.slice(0, 3).map(m => METRIC_KO[m] || m).join(', ')}${ecodBinaryWarns.length > 3 ? ' 등' : ''})`);
    return parts.length > 0 ? parts.join(' | ') : '';
  })();
  const metricsCount = dbMetrics.length;
  const healthScore = dbHealthScore;
  const isConnected = dbConnected;

  // 타임스탬프를 HH:mm:ss 형식으로 변환
  const fmtTime = (ts: string) => {
    try { return new Date(ts).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }); }
    catch { return ts; }
  };

  // 상세 텍스트를 한국어로 변환
  const fmtDetails = (d: InfluxDetection) => {
    if (!d.details) return d.arima_predicted ? `예측: ${d.arima_predicted.toFixed(1)}` : '-';
    // 영문 메트릭명을 한글로 치환
    let text = d.details;
    for (const [eng, ko] of Object.entries(METRIC_KO)) {
      text = text.replace(new RegExp(`\\b${eng}\\b`, 'g'), ko);
    }
    if (d.engine === 'arima') return text;
    // "메모리 81.0% (상위 27%)" → "81.0% — 상위 27% (보통)"
    const val = text.match(/[\d.]+[%a-z]*/i);
    const valStr = val ? val[0] : '';
    const top = text.match(/상위 (\d+)%/);
    const topPct = top ? top[1] : null;
    if (topPct) {
      const pctNum = parseInt(topPct);
      const level = pctNum <= 5 ? '매우 높음' : pctNum <= 20 ? '높음' : pctNum <= 80 ? '보통' : '낮음';
      return `${valStr} — 상위 ${topPct}% (${level})`;
    }
    return text;
  };

  const chartTooltip = {
    trigger: 'axis' as const,
    backgroundColor: 'rgba(15, 23, 42, 0.95)',
    borderColor: '#334155',
    textStyle: { color: '#e2e8f0', fontSize: 12 },
  };

  const chartGrid = { left: '8%', right: '8%', top: '15%', bottom: '20%' };

  const ecodChartOption = {
    title: { text: 'ECOD 다변량 이상 점수', left: 'center', textStyle: { fontSize: 13, fontWeight: 500, color: '#cbd5e1' } },
    tooltip: chartTooltip,
    legend: { bottom: 0, data: ecodGroup === 'all'
      ? ['종합', 'CPU', '메모리', '디스크IO', '네트워크 송신', '네트워크 수신', '동글', '핸드스캐너', '여권리더기', '2D스캐너', '충전기', '키보드', 'MSR', '프로세스', 'POS유휴']
      : ecodGroup === 'system'
      ? ['종합', 'CPU 사용률', '메모리 사용률', '디스크IO', '네트워크 송신', '네트워크 수신']
      : ecodGroup === 'peripheral'
      ? ['종합', '동글', '핸드스캐너', '여권리더기', '2D스캐너', '충전기', '키보드', 'MSR']
      : ['종합', '프로세스 상태', 'POS 유휴 상태'],
      textStyle: { color: '#cbd5e1', fontSize: 10 }, itemWidth: 10, itemHeight: 6, itemGap: 8, type: 'scroll' as any },
    grid: chartGrid,
    xAxis: { type: 'category', data: ecodMulti.map(d => fmtTime(d.timestamp)), axisLabel: { color: '#64748b', fontSize: 10, rotate: 30 }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#1f2937' } } },
    yAxis: { type: 'value', name: 'Score', min: 0, max: 1, splitNumber: 4, axisLabel: { color: '#64748b', fontSize: 10 }, nameTextStyle: { color: '#64748b', fontSize: 11 }, splitLine: { lineStyle: { color: '#1e293b' } } },
    series: [
      { name: '종합', type: 'line', data: ecodMulti.map(d => d.score), itemStyle: { color: '#f43f5e' }, lineStyle: { width: 2.5 }, smooth: true, areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(244, 63, 94, 0.3)' }, { offset: 1, color: 'rgba(244, 63, 94, 0.02)' }] } }, symbol: 'none' },
      ...(ecodGroup === 'all' ? [
        { name: 'CPU', type: 'line', data: ecodCpu.map(d => d.score), itemStyle: { color: '#3b82f6' }, lineStyle: { width: 1 }, smooth: true, symbol: 'none' },
        { name: '메모리', type: 'line', data: ecodMem.map(d => d.score), itemStyle: { color: '#22c55e' }, lineStyle: { width: 1 }, smooth: true, symbol: 'none' },
        { name: '디스크IO', type: 'line', data: ecodDisk.map(d => d.score), itemStyle: { color: '#f59e0b' }, lineStyle: { width: 1 }, smooth: true, symbol: 'none' },
        { name: '네트워크 송신', type: 'line', data: ecodNetSent.map(d => d.score), itemStyle: { color: '#06b6d4' }, lineStyle: { width: 1 }, smooth: true, symbol: 'none' },
        { name: '네트워크 수신', type: 'line', data: ecodNetRecv.map(d => d.score), itemStyle: { color: '#14b8a6' }, lineStyle: { width: 1 }, smooth: true, symbol: 'none' },
        { name: '동글', type: 'line', data: ecodDongle.map(d => d.score), itemStyle: { color: '#f472b6' }, lineStyle: { width: 1, type: 'dashed' }, smooth: true, symbol: 'none' },
        { name: '핸드스캐너', type: 'line', data: ecodHandScanner.map(d => d.score), itemStyle: { color: '#fb923c' }, lineStyle: { width: 1, type: 'dashed' }, smooth: true, symbol: 'none' },
        { name: '여권리더기', type: 'line', data: ecodPassport.map(d => d.score), itemStyle: { color: '#a3e635' }, lineStyle: { width: 1, type: 'dashed' }, smooth: true, symbol: 'none' },
        { name: '2D스캐너', type: 'line', data: ecod2dScanner.map(d => d.score), itemStyle: { color: '#38bdf8' }, lineStyle: { width: 1, type: 'dashed' }, smooth: true, symbol: 'none' },
        { name: '충전기', type: 'line', data: ecodPhoneCharger.map(d => d.score), itemStyle: { color: '#818cf8' }, lineStyle: { width: 1, type: 'dashed' }, smooth: true, symbol: 'none' },
        { name: '키보드', type: 'line', data: ecodKeyboard.map(d => d.score), itemStyle: { color: '#fbbf24' }, lineStyle: { width: 1, type: 'dashed' }, smooth: true, symbol: 'none' },
        { name: 'MSR', type: 'line', data: ecodMsr.map(d => d.score), itemStyle: { color: '#34d399' }, lineStyle: { width: 1, type: 'dashed' }, smooth: true, symbol: 'none' },
        { name: '프로세스', type: 'line', data: ecodProc.map(d => d.score), itemStyle: { color: '#ec4899' }, lineStyle: { width: 1, type: 'dotted' }, smooth: true, symbol: 'none' },
        { name: 'POS유휴', type: 'line', data: ecodIdle.map(d => d.score), itemStyle: { color: '#a78bfa' }, lineStyle: { width: 1, type: 'dotted' }, smooth: true, symbol: 'none' },
      ] : ecodGroup === 'system' ? [
        { name: 'CPU 사용률', type: 'line', data: ecodCpu.map(d => d.score), itemStyle: { color: '#3b82f6' }, lineStyle: { width: 1.5 }, smooth: true, symbol: 'none' },
        { name: '메모리 사용률', type: 'line', data: ecodMem.map(d => d.score), itemStyle: { color: '#22c55e' }, lineStyle: { width: 1.5 }, smooth: true, symbol: 'none' },
        { name: '디스크IO', type: 'line', data: ecodDisk.map(d => d.score), itemStyle: { color: '#f59e0b' }, lineStyle: { width: 1.5 }, smooth: true, symbol: 'none' },
        { name: '네트워크 송신', type: 'line', data: ecodNetSent.map(d => d.score), itemStyle: { color: '#06b6d4' }, lineStyle: { width: 1.5 }, smooth: true, symbol: 'none' },
        { name: '네트워크 수신', type: 'line', data: ecodNetRecv.map(d => d.score), itemStyle: { color: '#14b8a6' }, lineStyle: { width: 1.5 }, smooth: true, symbol: 'none' },
      ] : ecodGroup === 'peripheral' ? [
        { name: '동글', type: 'line', data: ecodDongle.map(d => d.score), itemStyle: { color: '#3b82f6' }, lineStyle: { width: 1.5 }, smooth: true, symbol: 'none' },
        { name: '핸드스캐너', type: 'line', data: ecodHandScanner.map(d => d.score), itemStyle: { color: '#22c55e' }, lineStyle: { width: 1.5 }, smooth: true, symbol: 'none' },
        { name: '여권리더기', type: 'line', data: ecodPassport.map(d => d.score), itemStyle: { color: '#f59e0b' }, lineStyle: { width: 1.5 }, smooth: true, symbol: 'none' },
        { name: '2D스캐너', type: 'line', data: ecod2dScanner.map(d => d.score), itemStyle: { color: '#06b6d4' }, lineStyle: { width: 1.5 }, smooth: true, symbol: 'none' },
        { name: '충전기', type: 'line', data: ecodPhoneCharger.map(d => d.score), itemStyle: { color: '#14b8a6' }, lineStyle: { width: 1.5 }, smooth: true, symbol: 'none' },
        { name: '키보드', type: 'line', data: ecodKeyboard.map(d => d.score), itemStyle: { color: '#ec4899' }, lineStyle: { width: 1.5 }, smooth: true, symbol: 'none' },
        { name: 'MSR', type: 'line', data: ecodMsr.map(d => d.score), itemStyle: { color: '#a78bfa' }, lineStyle: { width: 1.5 }, smooth: true, symbol: 'none' },
      ] : [
        { name: '프로세스 상태', type: 'line', data: ecodProc.map(d => d.score), itemStyle: { color: '#ec4899' }, lineStyle: { width: 1.5, type: 'dotted' }, smooth: true, symbol: 'none' },
        { name: 'POS 유휴 상태', type: 'line', data: ecodIdle.map(d => d.score), itemStyle: { color: '#a78bfa' }, lineStyle: { width: 1.5, type: 'dotted' }, smooth: true, symbol: 'none' },
      ]),
    ],
  };

  // ARIMA 타임스탬프로 metrics 실제값 매칭
  // metricsMap은 더 이상 ARIMA 차트에 불필요 (actual_value가 detection에 포함됨)
  // ARIMA detection에 저장된 actual_value를 직접 사용
  const arimaActual = selectedArimaData.map(d => d.actual_value ?? d.arima_predicted ?? 0);

  // 잔차 영역: 두 라인 사이 band = lower(투명) + bandWidth(색상)
  const bandWidth = selectedArimaData.map(d => Math.abs(d.arima_deviation || 0));

  // 상단 차트: 예측/실제 스케일
  const mainValues = [...arimaActual.filter((v): v is number => v != null), ...selectedArimaData.map(d => d.arima_predicted ?? 0)];
  const sharedMax = Math.ceil(Math.max(30, ...mainValues) * 1.1);
  // 하단 잔차 차트: 잔차 + 임계값 기준 스케일
  const arimaThresholds = selectedArimaData.map(d => d.threshold || 0);
  const fixedThreshold = arimaThresholds.length > 0 ? arimaThresholds[arimaThresholds.length - 1] : 0;
  const residualMax = Math.ceil(Math.max(1, ...bandWidth, fixedThreshold * 1.8) * 1.1);

  const arimaChartOption = {
    title: { text: `AutoARIMA 예측 vs 실제 (${arimaMetric})`, left: 'center', textStyle: { fontSize: 13, fontWeight: 500, color: '#cbd5e1' } },
    tooltip: chartTooltip,
    legend: { bottom: 0, data: ['예측값', '실제값', '잔차', '임계값'], textStyle: { color: '#94a3b8', fontSize: 11 }, itemWidth: 12, itemHeight: 8 },
    grid: [
      { left: '8%', right: '8%', top: '10%', bottom: '52%' },
      { left: '8%', right: '8%', top: '58%', bottom: '10%' },
    ],
    xAxis: [
      { type: 'category', gridIndex: 0, data: selectedArimaData.map(d => fmtTime(d.timestamp)), axisLabel: { show: false }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#1f2937' } } },
      { type: 'category', gridIndex: 1, data: selectedArimaData.map(d => fmtTime(d.timestamp)), axisLabel: { color: '#64748b', fontSize: 10, rotate: 30 }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#1f2937' } } },
    ],
    yAxis: [
      { type: 'value', gridIndex: 0, name: arimaMetric, min: 0, max: sharedMax, axisLabel: { color: '#64748b', fontSize: 10 }, nameTextStyle: { color: '#64748b', fontSize: 11 }, splitLine: { lineStyle: { color: '#1e293b' } } },
      { type: 'value', gridIndex: 1, name: '잔차', min: 0, max: residualMax, axisLabel: { color: '#64748b', fontSize: 10 }, nameTextStyle: { color: '#64748b', fontSize: 11 }, splitLine: { lineStyle: { color: '#1e293b' } } },
    ],
    series: [
      // Custom series: fill area between actual and predicted
      {
        type: 'custom', xAxisIndex: 0, yAxisIndex: 0, silent: true,
        renderItem: (_params: any, api: any) => {
          const idx = api.value(0);
          if (idx >= arimaActual.length - 1) return;
          const act0 = arimaActual[idx] ?? 0, act1 = arimaActual[idx + 1] ?? 0;
          const pred0 = selectedArimaData[idx]?.arima_predicted ?? 0, pred1 = selectedArimaData[idx + 1]?.arima_predicted ?? 0;
          const p0 = api.coord([idx, act0]), p1 = api.coord([idx, pred0]);
          const p2 = api.coord([idx + 1, pred1]), p3 = api.coord([idx + 1, act1]);
          return { type: 'polygon', shape: { points: [p0, p1, p2, p3] }, style: { fill: 'rgba(148, 163, 184, 0.08)' } };
        },
        data: arimaActual.map((_: any, i: number) => [i]),
        tooltip: { show: false },
      },
      // Predicted line
      { name: '예측값', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: selectedArimaData.map(d => d.arima_predicted), itemStyle: { color: '#a78bfa' }, lineStyle: { width: 2, type: 'dashed' }, smooth: false, symbol: 'none' },
      // Actual line
      { name: '실제값', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: arimaActual, itemStyle: { color: '#3b82f6' }, lineStyle: { width: 2 }, smooth: false, symbol: 'none' },
      // Residual bar (bottom panel)
      {
        name: '잔차', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: bandWidth, barWidth: '50%',
        color: 'rgba(59, 130, 246, 0.5)',
        itemStyle: { color: (params: any) => {
          const v = params.value || 0;
          if (v > fixedThreshold * 1.5) return 'rgba(239, 68, 68, 0.8)';
          if (v > fixedThreshold) return 'rgba(251, 191, 36, 0.7)';
          return 'rgba(59, 130, 246, 0.5)';
        }, borderRadius: [2, 2, 0, 0] },
      },
      // Threshold reference (latest value as fixed horizontal line)
      { name: '임계값', type: 'line', xAxisIndex: 1, yAxisIndex: 1,
        data: selectedArimaData.map(() => arimaThresholds.length > 0 ? arimaThresholds[arimaThresholds.length - 1] : null),
        itemStyle: { color: '#fbbf24' }, lineStyle: { width: 1, type: 'dashed' }, smooth: false, symbol: 'none' },
    ],
  };

  const card = { backgroundColor: '#111827', borderRadius: '10px', border: '1px solid #1f2937' };

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0a0e1a', color: '#e2e8f0', padding: '24px', fontFamily: "'Inter', -apple-system, sans-serif" }}>
      <style>{`
        @keyframes fadeSlideIn {
          from { opacity: 0; transform: translateY(-8px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
      {/* Header */}
      <header style={{
        ...card, padding: '14px 24px', marginBottom: '16px',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <Activity size={20} color="#3b82f6" />
          <div>
            <h1 style={{ margin: 0, fontSize: '16px', fontWeight: 600, letterSpacing: '-0.01em', color: '#f1f5f9' }}>PulseAI</h1>
            <p style={{ margin: 0, fontSize: '12px', color: '#cbd5e1', fontWeight: 400 }}>ECOD + AutoARIMA Anomaly Detection</p>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div style={{ display: 'flex', borderRadius: '6px', overflow: 'hidden', border: '1px solid #1f2937' }}>
            {(['realtime', 'db'] as const).map(mode => (
              <button key={mode} onClick={() => setViewMode(mode)} style={{
                padding: '5px 12px', fontSize: '11px', fontWeight: 500, cursor: 'pointer',
                border: 'none', transition: 'all 0.15s',
                backgroundColor: viewMode === mode ? '#1e293b' : 'transparent',
                color: viewMode === mode ? '#e2e8f0' : '#cbd5e1',
              }}>
                {mode === 'realtime' ? '실시간' : 'DB'}
              </button>
            ))}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', color: isConnected ? '#4ade80' : '#cbd5e1' }}>
            {isConnected ? <Wifi size={13} /> : <WifiOff size={13} />}
            <span>{isConnected ? `${metricsCount}건` : 'No Data'}</span>
          </div>
        </div>
      </header>

      {/* Stats Row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '10px', marginBottom: '16px' }}>
        <StatCard title="최근 메트릭 수신" value={metricsCount} color="#3b82f6" icon={<Database size={14} />} desc={`최근 ${DATA_LIMIT}개 시점 조회 기준\n(CPU·메모리·디스크IO·네트워크)\n주변장치는 POS 유휴 상태에서만 수집`} />
        <StatCard title="ECOD 이상탐지" value={ecodWarnings.length} color="#f43f5e" icon={<Search size={14} />} onClick={() => scrollToHistory('ecod')}
          desc={ecodWarnings.length > 0
            ? `동일 ${DATA_LIMIT}개 시점 기준 ${ecodWarnings.length}건 경고 — ${ecodSummary}`
            : `동일 ${DATA_LIMIT}개 시점 기준 — 이상 없음`} />
        <StatCard title="ARIMA 예측 경고" value={arimaWarnings.length} color="#8b5cf6" icon={<TrendingUp size={14} />} onClick={() => scrollToHistory('arima')}
          desc={arimaWarnings.length > 0
            ? `동일 ${DATA_LIMIT}개 시점 기준 ${arimaWarnings.length}건 — ${arimaWarnings.map(d => `${METRIC_KO[d.metric] || d.metric}: ${d.details || `score ${d.score?.toFixed(2)}`}`).slice(0, 3).join('; ')}`
            : `동일 ${DATA_LIMIT}개 시점 기준 — 경고 없음`} />
        <StatCard title="주변장치 꺼짐" value={peripheralWarnDevices.length} color="#f59e0b" icon={<AlertTriangle size={14} />} onClick={() => scrollToHistory('peripheral')}
          desc={peripheralWarnDevices.length > 0 ? `최신 상태 기준 — 꺼짐: ${peripheralWarnDevices.map(d => METRIC_KO[d] || d).join(', ')} (7개 중 ${peripheralWarnDevices.length}개)\n※ 미사용 장치(-1)는 원래 사용하지 않는 장치로 제외` : '최신 상태 기준 — 7개 장치 모두 연결\n※ 미사용 장치(-1)는 원래 사용하지 않는 장치로 제외'} />
      </div>

      {/* Charts Row 1 */}
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '12px', marginBottom: '12px', alignItems: 'stretch' }}>
        <div style={{ ...card, padding: '16px', display: 'flex', flexDirection: 'column' }}>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '4px', gap: '4px' }}>
            {([['all', '전체'], ['system', '시스템'], ['peripheral', '주변장치'], ['status', '상태']] as const).map(([key, label]) => (
              <button key={key} onClick={() => setEcodGroup(key)} style={{
                padding: '3px 10px', fontSize: '11px', fontWeight: 500, cursor: 'pointer',
                borderRadius: '4px', border: '1px solid #1f2937', transition: 'all 0.15s',
                backgroundColor: ecodGroup === key ? '#1e293b' : 'transparent',
                color: ecodGroup === key ? '#e2e8f0' : '#cbd5e1',
              }}>{label}</button>
            ))}
          </div>
          <ReactECharts option={ecodChartOption} style={{ flex: 1, minHeight: '300px' }} />
          <div style={{ fontSize: '11px', color: '#525e6f', lineHeight: '1.7', padding: '10px 12px', marginTop: '8px', backgroundColor: '#0d1117', borderRadius: '6px', border: '1px solid #1e293b' }}>
            <div style={{ color: '#94a3b8', fontWeight: 600, marginBottom: '4px' }}>ECOD 이상 탐지 원리</div>
            <div>1. 과거 7일간 수집된 메트릭·로그·주변장치 데이터로 <b style={{ color: '#94a3b8' }}>정상 패턴 학습</b> (ECOD 모델)</div>
            <div>2. 현재 데이터 포인트의 <b style={{ color: '#94a3b8' }}>백분위 순위</b> 계산 → Score (0.0=평범, 1.0=극단)</div>
            <div style={{ paddingLeft: '12px', color: '#475569' }}>Score = (학습 데이터 중 현재 값보다 낮은 비율). 예) Score 0.95 = 상위 5%</div>
            <div>3. 14개 지표를 동시 분석하여 <b style={{ color: '#94a3b8' }}>복합 이상 판별</b></div>
            <div style={{ paddingLeft: '12px', color: '#475569' }}>CPU·메모리·디스크IO·네트워크(송수신)·프로세스·주변장치(7종)·POS유휴</div>
            <div>4. ECOD 종합 Score ≥ 0.95 또는 개별 메트릭 임계값 초과 시 <b style={{ color: '#f59e0b' }}>warning</b> (CPU ≥ 80%, 메모리 ≥ 85%)</div>
            <div style={{ paddingLeft: '12px' }}>개별 메트릭이 위험 수준 초과 시 <b style={{ color: '#ef4444' }}>critical</b> (CPU ≥ 90%, 메모리 ≥ 95%)</div>
          </div>
        </div>
        <StatusInsightCard detections={latestDetections} healthScore={healthScore} />
      </div>

      {/* Peripheral Status Cards */}
      <PeripheralCards />

      {/* Charts Row 2 - ARIMA */}
      <div style={{ ...card, padding: '16px', marginTop: '12px', marginBottom: '12px' }}>
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '4px', gap: '4px' }}>
          {(arimaMetrics.length > 0 ? arimaMetrics : ['CPU', 'Memory', 'DiskIO', 'NetworkSent', 'NetworkRecv']).map(m => (
            <button key={m} onClick={() => setArimaMetric(m)} style={{
              padding: '3px 10px', fontSize: '11px', fontWeight: 500, cursor: 'pointer',
              borderRadius: '4px', border: '1px solid #1f2937', transition: 'all 0.15s',
              backgroundColor: arimaMetric === m ? '#1e293b' : 'transparent',
              color: arimaMetric === m ? '#e2e8f0' : '#cbd5e1',
            }}>{m}</button>
          ))}
        </div>
        <ReactECharts option={arimaChartOption} style={{ height: '500px' }} />
        {arimaWarnings.length > 0 && (
          <div style={{ marginTop: '8px', padding: '10px 14px', backgroundColor: '#1e1b4b', border: '1px solid #4c1d95', borderRadius: '6px', fontSize: '12px' }}>
            <div style={{ color: '#a78bfa', fontWeight: 600, marginBottom: '4px' }}>ARIMA 경고 {arimaWarnings.length}건</div>
            {arimaWarnings.slice(0, 5).map((d, i) => (
              <div key={i} style={{ color: '#c4b5fd', lineHeight: '1.6' }}>
                <span style={{ color: '#e2e8f0', fontWeight: 500 }}>{d.metric}</span>
                {' — '}
                <span>{d.details || `score ${d.score?.toFixed(3)}, 임계값 ${d.threshold?.toFixed(3)}`}</span>
                <span style={{ color: '#64748b', marginLeft: '8px' }}>
                  {d.timestamp ? new Date(d.timestamp).toLocaleTimeString('ko-KR') : ''}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Detection Table */}
      <div ref={historyRef} style={{ ...card, padding: '16px', marginTop: '12px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
          <h3 style={{ margin: 0, fontSize: '16px' }}>
            탐지 히스토리 ({viewMode === 'realtime' ? '실시간' : 'DB 전체'})
          </h3>
          <div style={{ display: 'flex', gap: '4px' }}>
            {([['all', '전체'], ['ecod', 'ECOD'], ['arima', 'ARIMA'], ['peripheral', '주변장치']] as const).map(([key, label]) => (
              <button key={key} onClick={() => setHistoryEngine(key)} style={{
                padding: '3px 10px', fontSize: '11px', fontWeight: 500, cursor: 'pointer',
                borderRadius: '4px', border: '1px solid #1f2937', transition: 'all 0.15s',
                backgroundColor: historyEngine === key ? '#1e293b' : 'transparent',
                color: historyEngine === key ? '#e2e8f0' : '#cbd5e1',
              }}>{label}</button>
            ))}
          </div>
        </div>
        {historyEngine !== 'peripheral' && (
        <div style={{ fontSize: '11px', color: '#64748b', lineHeight: '1.8', marginBottom: '10px', padding: '10px 12px', backgroundColor: '#0d1117', borderRadius: '6px', border: '1px solid #1e293b' }}>
          <div><b style={{ color: '#94a3b8' }}>Score</b> — 과거 학습 데이터에서 현재 값이 얼마나 극단적인지 (0.0=평범, 1.0=매우 이례적)</div>
          <div style={{ paddingLeft: '12px', color: '#525e6f' }}>예) 메모리 Score 0.47 → 학습 기간 중 47%보다 높은 수준 (평범)</div>
          <div><b style={{ color: '#94a3b8' }}>신뢰도</b> — 판단 근거가 되는 학습 데이터의 양 (많을수록 신뢰↑)</div>
          <div style={{ paddingLeft: '12px', color: '#525e6f' }}>20건 미만: 40% · 20~60건: 70% · 60건 이상: 90%</div>
          <div><b style={{ color: '#94a3b8' }}>심각도</b> — normal: 정상 범위 · warning: 주의 (CPU≥80%, 메모리≥85%, 또는 Score≥0.95) · critical: 위험 (CPU≥90%, 메모리≥95%)</div>
        </div>
        )}
        {allDetections.length === 0 ? (
          <p style={{ color: '#cbd5e1', textAlign: 'center', padding: '40px' }}>
            InfluxDB에서 데이터 조회 중... ({viewMode === 'realtime' ? '2초' : '5초'} 주기 폴링)
          </p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid #334155' }}>
                  <th style={{ padding: '10px', textAlign: 'left', color: '#cbd5e1' }}>시간</th>
                  {historyEngine === 'peripheral' ? (
                    <>
                      <th style={{ padding: '10px', textAlign: 'left', color: '#cbd5e1' }}>장치</th>
                      <th style={{ padding: '10px', textAlign: 'center', color: '#cbd5e1' }}>상태</th>
                      <th style={{ padding: '10px', textAlign: 'left', color: '#cbd5e1' }}>상세</th>
                    </>
                  ) : (
                    <>
                      <th style={{ padding: '10px', textAlign: 'left', color: '#cbd5e1' }}>엔진</th>
                      <th style={{ padding: '10px', textAlign: 'left', color: '#cbd5e1' }}>메트릭</th>
                      <th style={{ padding: '10px', textAlign: 'right', color: '#cbd5e1' }}>Score</th>
                      <th style={{ padding: '10px', textAlign: 'right', color: '#cbd5e1' }}>신뢰도</th>
                      <th style={{ padding: '10px', textAlign: 'center', color: '#cbd5e1' }}>심각도</th>
                      <th style={{ padding: '10px', textAlign: 'left', color: '#cbd5e1' }}>상세</th>
                    </>
                  )}
                </tr>
              </thead>
              <tbody>
                {allDetections.map((d) => {
                  const isPeriph = PERIPHERAL_METRICS.has(d.metric) && d.engine === 'ecod';
                  const periphDesc = d.actual_value === 1 ? '정상 연결' : d.actual_value === 0 ? '연결 끊김 — 확인 필요' : '미사용 장치';
                  return (
                  <tr key={`${d.timestamp}-${d.engine}-${d.metric}`} style={{ borderBottom: '1px solid #1e293b', animation: 'fadeSlideIn 0.3s ease' }}>
                    <td style={{ padding: '8px 10px' }}>
                      {d.timestamp ? new Date(d.timestamp).toLocaleTimeString('ko-KR') : '-'}
                    </td>
                    {historyEngine === 'peripheral' ? (
                      <>
                        <td style={{ padding: '8px 10px' }}>{METRIC_KO[d.metric] || d.metric}</td>
                        <td style={{ padding: '8px 10px', textAlign: 'center' }}><PeripheralStatusTag value={d.actual_value} /></td>
                        <td style={{ padding: '8px 10px', color: '#cbd5e1', fontSize: '11px' }}>{periphDesc}</td>
                      </>
                    ) : (
                      <>
                        <td style={{ padding: '8px 10px' }}>{isPeriph ? <EngineTag engine="peripheral" /> : <EngineTag engine={d.engine} />}</td>
                        <td style={{ padding: '8px 10px' }}>{METRIC_KO[d.metric] || d.metric}</td>
                        <td style={{ padding: '8px 10px', textAlign: 'right' }}>{isPeriph ? '-' : d.score?.toFixed(3)}</td>
                        <td style={{ padding: '8px 10px', textAlign: 'right' }}>{isPeriph ? '-' : (d.confidence ? `${(d.confidence * 100).toFixed(0)}%` : '-')}</td>
                        <td style={{ padding: '8px 10px', textAlign: 'center' }}>{isPeriph ? <PeripheralStatusTag value={d.actual_value} /> : <SeverityTag severity={d.severity} />}</td>
                        <td style={{ padding: '8px 10px', color: '#cbd5e1', fontSize: '11px' }}>
                          {isPeriph ? periphDesc : fmtDetails(d)}
                        </td>
                      </>
                    )}
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function StatCard({ title, value, color = '#3b82f6', icon, desc, onClick }: { title: string; value: number; color?: string; icon?: ReactNode; desc?: string; onClick?: () => void }) {
  return (
    <div onClick={onClick} style={{ backgroundColor: '#111827', borderRadius: '10px', padding: '14px 16px', border: '1px solid #1f2937', borderTop: `2px solid ${color}`, cursor: onClick ? 'pointer' : 'default', transition: 'background-color 0.15s' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', color: '#cbd5e1', marginBottom: '6px', fontWeight: 500 }}>
        {icon && <span style={{ color, display: 'flex' }}>{icon}</span>}
        {title}
      </div>
      <div style={{ fontSize: '22px', fontWeight: 600, color: '#e2e8f0' }}>{value}</div>
      {desc && <div style={{ fontSize: '11px', color: '#94a3b8', marginTop: '6px', lineHeight: '1.4', whiteSpace: 'pre-line' }}>{desc}</div>}
    </div>
  );
}

function EngineTag({ engine }: { engine: string }) {
  const colors: Record<string, { bg: string; text: string }> = {
    ecod: { bg: '#1e3a5f', text: '#60a5fa' },
    arima: { bg: '#3b1c4a', text: '#c084fc' },
    ensemble: { bg: '#134e4a', text: '#5eead4' },
    peripheral: { bg: '#713f12', text: '#fcd34d' },
  };
  const c = colors[engine] || { bg: '#374151', text: '#9ca3af' };
  return (
    <span style={{ padding: '2px 8px', borderRadius: '4px', backgroundColor: c.bg, color: c.text, fontSize: '11px', fontWeight: 'bold' }}>
      {engine.toUpperCase()}
    </span>
  );
}

function PeripheralStatusTag({ value }: { value?: number | null }) {
  // 1=연결, 0=꺼짐, -1=미사용
  const v = value ?? -1;
  const cfg = v === 1 ? { bg: '#14532d', text: '#86efac', dot: '#22c55e', label: '연결' }
    : v === 0 ? { bg: '#7f1d1d', text: '#fca5a5', dot: '#ef4444', label: '꺼짐' }
    : { bg: '#374151', text: '#9ca3af', dot: '#6b7280', label: '미사용' };
  return (
    <span style={{ padding: '2px 8px', borderRadius: '4px', backgroundColor: cfg.bg, color: cfg.text, fontSize: '11px', display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
      <span style={{ width: '6px', height: '6px', borderRadius: '50%', backgroundColor: cfg.dot }} />
      {cfg.label}
    </span>
  );
}

function SeverityTag({ severity }: { severity: string }) {
  const styles: Record<string, { bg: string; text: string; dot: string }> = {
    critical: { bg: '#7f1d1d', text: '#fca5a5', dot: '#ef4444' },
    warning: { bg: '#713f12', text: '#fcd34d', dot: '#f59e0b' },
    normal: { bg: '#14532d', text: '#86efac', dot: '#22c55e' },
  };
  const s = styles[severity] || styles.normal;
  return (
    <span style={{ padding: '2px 8px', borderRadius: '4px', backgroundColor: s.bg, color: s.text, fontSize: '11px', display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
      <span style={{ width: '6px', height: '6px', borderRadius: '50%', backgroundColor: s.dot }} />
      {severity}
    </span>
  );
}
