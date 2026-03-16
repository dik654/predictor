import ReactECharts from 'echarts-for-react';
import { useMemo } from 'react';

interface MetricsData {
  timestamp: string;
  cpu: number;
  memory: number;
  disk_io: number;
}

interface Props {
  data: MetricsData[];
  title: string;
  metric: 'cpu' | 'memory' | 'disk_io';
  color?: string;
}

export function MetricsChart({ data, title, metric, color = '#5470c6' }: Props) {
  const option = useMemo(() => {
    const timestamps = data.map((d) => {
      const ts = d.timestamp;
      // Extract time part only (HH:MM:SS)
      return ts ? ts.split(' ')[1] || ts : '';
    });
    
    const values = data.map((d) => {
      switch (metric) {
        case 'cpu':
          return d.cpu;
        case 'memory':
          return d.memory;
        case 'disk_io':
          return d.disk_io;
        default:
          return 0;
      }
    });

    return {
      title: {
        text: title,
        left: 'center',
        textStyle: {
          fontSize: 14,
          color: '#333',
        },
      },
      tooltip: {
        trigger: 'axis',
        formatter: (params: any) => {
          const p = params[0];
          return `${p.axisValue}<br/>${p.seriesName}: ${p.value?.toFixed(2)}%`;
        },
      },
      grid: {
        left: '10%',
        right: '5%',
        top: '20%',
        bottom: '15%',
      },
      xAxis: {
        type: 'category',
        data: timestamps,
        axisLabel: {
          rotate: 45,
          fontSize: 10,
        },
      },
      yAxis: {
        type: 'value',
        min: 0,
        max: metric === 'disk_io' ? 'dataMax' : 100,
        axisLabel: {
          formatter: '{value}%',
        },
      },
      series: [
        {
          name: title,
          type: 'line',
          data: values,
          smooth: true,
          symbol: 'none',
          lineStyle: {
            color: color,
            width: 2,
          },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: color + '80' },
                { offset: 1, color: color + '10' },
              ],
            },
          },
        },
      ],
    };
  }, [data, title, metric, color]);

  return (
    <div style={{ width: '100%', height: '250px' }}>
      <ReactECharts option={option} style={{ height: '100%', width: '100%' }} />
    </div>
  );
}
