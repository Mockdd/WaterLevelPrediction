import React from 'react'
import { CHART_BLUE, CHART_BAND } from '../../constants/colors'
import {
  ResponsiveContainer,
  Line,
  Area,
  ComposedChart,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
} from 'recharts'

// predictions: { h1: {predicted, lower, upper}, ... h6 }
// observations: [{ datetime, water_level }, ...]
// alertLevel, warningLevel: number
// mode: 'prediction' | 'observation'
export default function PredictionChart({ predictions, observations, alertLevel, warningLevel, mode = 'prediction', large = false }) {
  const height = large ? 340 : 200
  if (mode === 'observation') {
    if (!observations || observations.length === 0) {
      return <div style={styles.empty}>과거 수위 데이터 없음</div>
    }

    const data = observations.map((o) => ({
      time: o.datetime.slice(11, 16), // HH:MM
      수위: o.water_level,
    }))

    return (
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1E3A5F" />
          <XAxis dataKey="time" tick={styles.tick} interval="preserveStartEnd" />
          <YAxis tick={styles.tick} />
          <Tooltip contentStyle={styles.tooltip} />
          {alertLevel != null && (
            <ReferenceLine y={alertLevel} stroke="#FBC02D" strokeDasharray="4 4" label={{ value: '주의보', fill: '#FBC02D', fontSize: 10 }} />
          )}
          {warningLevel != null && (
            <ReferenceLine y={warningLevel} stroke="#D32F2F" strokeDasharray="4 4" label={{ value: '경보', fill: '#D32F2F', fontSize: 10 }} />
          )}
          <Line type="monotone" dataKey="수위" stroke={CHART_BLUE} dot={false} strokeWidth={2} />
        </ComposedChart>
      </ResponsiveContainer>
    )
  }

  // prediction mode
  if (!predictions) {
    return <div style={styles.empty}>예측 데이터 없음</div>
  }

  const data = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6'].map((key, i) => ({
    time: `+${i + 1}h`,
    predicted: predictions[key]?.predicted,
    lower: predictions[key]?.lower,
    upper: predictions[key]?.upper,
    // AreaChart용: [lower, upper] 범위
    range: predictions[key]
      ? [predictions[key].lower, predictions[key].upper]
      : null,
  }))

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1E3A5F" />
        <XAxis dataKey="time" tick={styles.tick} />
        <YAxis tick={styles.tick} />
        <Tooltip contentStyle={styles.tooltip} />
        {alertLevel != null && (
          <ReferenceLine y={alertLevel} stroke="#FBC02D" strokeDasharray="4 4" label={{ value: '주의보', fill: '#FBC02D', fontSize: 10 }} />
        )}
        {warningLevel != null && (
          <ReferenceLine y={warningLevel} stroke="#D32F2F" strokeDasharray="4 4" label={{ value: '경보', fill: '#D32F2F', fontSize: 10 }} />
        )}
        {/* 신뢰구간 음영 */}
        <Area type="monotone" dataKey="upper" stroke="none" fill={CHART_BAND} legendType="none" />
        <Area type="monotone" dataKey="lower" stroke="none" fill="#112240" legendType="none" />
        {/* 예측선 */}
        <Line
          type="monotone"
          dataKey="predicted"
          stroke={CHART_BLUE}
          strokeWidth={2}
          dot={{ fill: CHART_BLUE, r: 3 }}
          name="예측 수위"
        />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

const styles = {
  tick: { fill: '#8899AA', fontSize: 11 },
  tooltip: {
    background: '#0D1F3C',
    border: '1px solid #1E3A5F',
    borderRadius: 6,
    color: '#FFFFFF',
    fontSize: 12,
  },
  empty: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    height: 200,
    color: '#4A5568',
    fontSize: 13,
  },
}
