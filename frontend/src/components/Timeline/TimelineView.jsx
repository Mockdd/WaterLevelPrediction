import React from 'react'
import { STATUS_COLOR, STATUS_LABEL } from '../../constants/colors'

const STATUS_BG = {
  red:    'rgba(211,47,47,0.08)',
  yellow: 'rgba(251,192,45,0.08)',
  green:  'rgba(56,142,60,0.08)',
  gray:   'rgba(160,174,192,0.08)',
}
const HOURS = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']

function parseTime(predictedAt, offsetHours) {
  if (!predictedAt) return null
  // "2026-05-16 13:00:00" or "2026-05-16T13:00:00"
  const normalized = predictedAt.replace(' ', 'T')
  const date = new Date(normalized)
  if (isNaN(date)) return null
  date.setHours(date.getHours() + offsetHours)
  return date.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false })
}

export default function TimelineView({ station }) {
  if (!station) {
    return (
      <div style={styles.container}>
        <span style={styles.empty}>관측소 마커를 클릭하면 시간별 예측 수위를 확인할 수 있습니다.</span>
      </div>
    )
  }

  const { name, region, predictions, statuses, alert_level, warning_level, predicted_at } = station

  if (!predictions || !statuses) {
    return (
      <div style={styles.container}>
        <div style={styles.stationRow}>
          <span style={styles.stationName}>{name}</span>
          <span style={styles.region}>{region}</span>
        </div>
        <span style={styles.empty}>예측 데이터 없음</span>
      </div>
    )
  }

  return (
    <div style={styles.container}>
      <div style={styles.stationRow}>
        <span style={styles.stationName}>{name}</span>
        <span style={styles.region}>{region}</span>
        <div style={styles.levelInfo}>
          <span style={styles.levelChip}>
            <span style={{ color: '#D69E2E' }}>●</span> 주의 {alert_level?.toFixed(1)}m
          </span>
          <span style={styles.levelChip}>
            <span style={{ color: '#E53E3E' }}>●</span> 경보 {warning_level?.toFixed(1)}m
          </span>
        </div>
      </div>

      <div style={styles.timelineRow}>
        {HOURS.map((h, i) => {
          const status = statuses[h] ?? 'gray'
          const predicted = predictions[h]?.predicted
          const lower = predictions[h]?.lower
          const upper = predictions[h]?.upper
          const color = STATUS_COLOR[status]
          const bg = STATUS_BG[status]
          const timeLabel = parseTime(predicted_at, i + 1)

          return (
            <div key={h} style={{ ...styles.cell, background: bg, borderTop: `3px solid ${color}` }}>
              <div style={styles.cellHeader}>
                <span style={{ ...styles.statusDot, background: color }} />
                <span style={styles.statusText}>{STATUS_LABEL[status]}</span>
              </div>
              <div style={{ ...styles.level, color }}>
                {predicted != null ? `${predicted.toFixed(2)}m` : '—'}
              </div>
              <div style={styles.band}>
                {lower != null && upper != null && (
                  <span style={styles.bandText}>
                    {lower.toFixed(2)} ~ {upper.toFixed(2)}
                  </span>
                )}
              </div>
              <div style={styles.timeLabel}>
                {timeLabel ?? h} <span style={styles.hLabel}>(+{i + 1}h)</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

const styles = {
  container: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    background: 'rgba(255,255,255,0.96)',
    borderTop: '1px solid #E2E8F0',
    boxShadow: '0 -2px 12px rgba(0,0,0,0.08)',
    zIndex: 100,
    padding: '10px 16px 12px',
  },
  stationRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    marginBottom: 8,
  },
  stationName: {
    fontSize: 13,
    fontWeight: 700,
    color: '#1A202C',
  },
  region: {
    fontSize: 11,
    color: '#718096',
    background: '#F0F4F8',
    padding: '1px 6px',
    borderRadius: 8,
  },
  levelInfo: {
    display: 'flex',
    gap: 10,
    marginLeft: 'auto',
  },
  levelChip: {
    fontSize: 11,
    color: '#4A5568',
    display: 'flex',
    alignItems: 'center',
    gap: 4,
  },
  timelineRow: {
    display: 'flex',
    gap: 6,
  },
  cell: {
    flex: 1,
    borderRadius: 6,
    padding: '6px 8px',
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
    border: '1px solid #E2E8F0',
    borderTopWidth: 3,
  },
  cellHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
  },
  statusDot: {
    width: 7,
    height: 7,
    borderRadius: '50%',
    flexShrink: 0,
  },
  statusText: {
    fontSize: 10,
    color: '#718096',
    fontWeight: 600,
  },
  level: {
    fontSize: 15,
    fontWeight: 700,
    lineHeight: 1.2,
    fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
    fontVariantNumeric: 'tabular-nums',
  },
  band: {
    minHeight: 14,
  },
  bandText: {
    fontSize: 9,
    color: '#A0AEC0',
  },
  timeLabel: {
    fontSize: 10,
    color: '#718096',
    marginTop: 2,
  },
  hLabel: {
    color: '#A0AEC0',
  },
  empty: {
    fontSize: 12,
    color: '#A0AEC0',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '8px 0',
  },
}
