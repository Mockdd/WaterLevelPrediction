import React, { useState, useEffect } from 'react'
import { getObservations } from '../../api'
import PredictionChart from '../Chart/PredictionChart'
import { STATUS_COLOR, STATUS_LABEL } from '../../constants/colors'

export default function PredictionPopup({ station, onClose, onExpand }) {
  const [tab, setTab] = useState('prediction')
  const [observations, setObservations] = useState([])
  const [obsLoading, setObsLoading] = useState(false)

  useEffect(() => {
    if (!station) return
    setTab('prediction')
    setObsLoading(true)
    getObservations(station.station_id)
      .then((res) => setObservations(res.data.observations))
      .catch(() => setObservations([]))
      .finally(() => setObsLoading(false))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [station?.station_id])

  if (!station) return null

  const { name, pin_status, alert_level, warning_level, predictions, statuses } = station
  const color = STATUS_COLOR[pin_status] ?? STATUS_COLOR.gray
  const currentLevel = observations.length > 0 ? observations[observations.length - 1].water_level : null

  return (
    <div style={styles.container}>
      {/* 헤더 */}
      <div style={styles.header}>
        <div style={styles.titleRow}>
          <span style={styles.name}>{name}</span>
          <span style={{ ...styles.badge, background: color + '22', color, border: `1px solid ${color}66` }}>
            {STATUS_LABEL[pin_status] ?? '—'}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
          <button style={styles.expandBtn} onClick={onExpand}>크게 보기 ↗</button>
          <button style={styles.closeBtn} onClick={onClose}>✕</button>
        </div>
      </div>

      {/* 스크롤 영역 */}
      <div style={styles.scrollBody}>

      {/* 수위 요약 */}
      <div style={styles.levelRow}>
        <LevelItem label="현재 수위" value={currentLevel} unit="m" color="#4A9EFF" />
        <LevelItem label="주의보" value={alert_level} unit="m" color="#FFB800" />
        <LevelItem label="경보" value={warning_level} unit="m" color="#FF4444" />
      </div>

      {/* 탭 */}
      <div style={styles.tabRow}>
        <TabBtn label="예측 수위" active={tab === 'prediction'} onClick={() => setTab('prediction')} />
        <TabBtn label="과거 24h" active={tab === 'observation'} onClick={() => setTab('observation')} />
      </div>

      {/* 신호등 행 (예측 탭일 때) */}
      {tab === 'prediction' && statuses && (
        <div style={styles.statusRow}>
          {['h1','h2','h3','h4','h5','h6'].map((h) => (
            <div key={h} style={styles.statusCell}>
              <div style={{ ...styles.statusDot, background: STATUS_COLOR[statuses[h]] ?? STATUS_COLOR.gray }} />
              <span style={styles.statusLabel}>{h}</span>
            </div>
          ))}
        </div>
      )}

      {/* 차트 */}
      <div style={styles.chartArea}>
        {tab === 'prediction' ? (
          <PredictionChart
            predictions={predictions}
            alertLevel={alert_level}
            warningLevel={warning_level}
            mode="prediction"
          />
        ) : obsLoading ? (
          <div style={styles.loading}>데이터 불러오는 중...</div>
        ) : (
          <PredictionChart
            observations={observations}
            alertLevel={alert_level}
            warningLevel={warning_level}
            mode="observation"
          />
        )}
      </div>

      </div>{/* scrollBody 끝 */}
    </div>
  )
}

function LevelItem({ label, value, unit, color }) {
  return (
    <div style={styles.levelItem}>
      <span style={styles.levelLabel}>{label}</span>
      <span style={{ ...styles.levelValue, color }}>
        {value != null ? `${value.toFixed(2)}${unit}` : '—'}
      </span>
    </div>
  )
}

function TabBtn({ label, active, onClick }) {
  return (
    <button
      style={{ ...styles.tab, ...(active ? styles.tabActive : {}) }}
      onClick={onClick}
    >
      {label}
    </button>
  )
}

const styles = {
  container: {
    position: 'absolute',
    top: 16,
    right: 16,
    width: 340,
    maxHeight: 'calc(100% - 32px)',
    background: '#FFFFFF',
    border: '1px solid #E2E8F0',
    borderRadius: 12,
    boxShadow: '0 4px 20px rgba(0,0,0,0.12)',
    zIndex: 100,
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '12px 14px',
    borderBottom: '1px solid #E2E8F0',
    gap: 8,
    flexShrink: 0,
  },
  titleRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    minWidth: 0,
    flex: 1,
  },
  name: {
    color: '#1A202C',
    fontSize: 14,
    fontWeight: 700,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  badge: {
    fontSize: 11,
    fontWeight: 600,
    borderRadius: 4,
    padding: '2px 7px',
  },
  expandBtn: {
    background: '#EBF4FF',
    border: '1px solid #BEE3F8',
    color: '#1565C0',
    cursor: 'pointer',
    fontSize: 11,
    fontWeight: 600,
    padding: '4px 8px',
    borderRadius: 5,
    whiteSpace: 'nowrap',
  },
  closeBtn: {
    background: 'none',
    border: 'none',
    color: '#A0AEC0',
    cursor: 'pointer',
    fontSize: 14,
    padding: 0,
    lineHeight: 1,
  },
  levelRow: {
    display: 'flex',
    padding: '10px 16px',
    gap: 8,
    borderBottom: '1px solid #E2E8F0',
    background: '#F8FAFC',
  },
  levelItem: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 2,
  },
  levelLabel: {
    fontSize: 10,
    color: '#718096',
  },
  levelValue: {
    fontSize: 14,
    fontWeight: 700,
    fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
    fontVariantNumeric: 'tabular-nums',
  },
  tabRow: {
    display: 'flex',
    borderBottom: '1px solid #E2E8F0',
  },
  tab: {
    flex: 1,
    background: 'none',
    border: 'none',
    color: '#718096',
    fontSize: 12,
    padding: '8px 0',
    cursor: 'pointer',
  },
  tabActive: {
    color: '#3182CE',
    borderBottom: '2px solid #3182CE',
    fontWeight: 600,
  },
  statusRow: {
    display: 'flex',
    justifyContent: 'space-around',
    padding: '8px 16px',
    borderBottom: '1px solid #E2E8F0',
    background: '#F8FAFC',
  },
  statusCell: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 4,
  },
  statusDot: {
    width: 10,
    height: 10,
    borderRadius: '50%',
  },
  statusLabel: {
    fontSize: 10,
    color: '#718096',
  },
  scrollBody: {
    overflowY: 'auto',
    flex: 1,
  },
  chartArea: {
    padding: '8px 4px 12px',
  },
  loading: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    height: 200,
    color: '#718096',
    fontSize: 13,
  },
}
