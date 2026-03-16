import ReactECharts from 'echarts-for-react';
import { useMemo } from 'react';

interface Props {
  score: number;
  agentId?: string;
}

export function HealthScore({ score, agentId }: Props) {
  const option = useMemo(() => {
    // Determine color based on score
    let color: string;
    let status: string;
    if (score >= 80) {
      color = '#22c55e'; // green
      status = '정상';
    } else if (score >= 60) {
      color = '#f59e0b'; // yellow
      status = '주의';
    } else {
      color = '#ef4444'; // red
      status = '위험';
    }

    return {
      series: [
        {
          type: 'gauge',
          startAngle: 180,
          endAngle: 0,
          min: 0,
          max: 100,
          splitNumber: 10,
          radius: '100%',
          center: ['50%', '70%'],
          axisLine: {
            lineStyle: {
              width: 20,
              color: [
                [0.3, '#ef4444'],
                [0.6, '#f59e0b'],
                [1, '#22c55e'],
              ],
            },
          },
          pointer: {
            icon: 'path://M12.8,0.7l12,40.1H0.7L12.8,0.7z',
            length: '50%',
            width: 10,
            offsetCenter: [0, '-10%'],
            itemStyle: {
              color: 'auto',
            },
          },
          axisTick: {
            length: 8,
            lineStyle: {
              color: 'auto',
              width: 2,
            },
          },
          splitLine: {
            length: 15,
            lineStyle: {
              color: 'auto',
              width: 3,
            },
          },
          axisLabel: {
            color: '#666',
            fontSize: 12,
            distance: -50,
            formatter: (value: number) => {
              if (value === 0) return '0';
              if (value === 50) return '50';
              if (value === 100) return '100';
              return '';
            },
          },
          title: {
            offsetCenter: [0, '20%'],
            fontSize: 14,
            color: '#666',
          },
          detail: {
            fontSize: 32,
            offsetCenter: [0, '-20%'],
            valueAnimation: true,
            formatter: (value: number) => `${value}`,
            color: color,
          },
          data: [
            {
              value: score,
              name: status,
            },
          ],
        },
      ],
    };
  }, [score]);

  return (
    <div style={{
      backgroundColor: '#fff',
      borderRadius: '12px',
      padding: '16px',
      boxShadow: '0 1px 3px rgba(0,0,0,0.1)',
    }}>
      <h3 style={{
        margin: '0 0 8px 0',
        fontSize: '14px',
        color: '#374151',
        textAlign: 'center',
      }}>
        {agentId ? `${agentId} 건강도` : '시스템 건강도'}
      </h3>
      <ReactECharts
        option={option}
        style={{ height: '180px', width: '100%' }}
      />
    </div>
  );
}
