import React, { useState, useEffect } from 'react'
import { getObservations } from '../../api'
import PredictionChart from '../Chart/PredictionChart'
import { STATUS_COLOR, STATUS_LABEL } from '../../constants/colors'

const HOURS = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']

export default function ChartModal({ station, onClose }) {
  const [tab, setTab] = useState('prediction')
  const [observations, setObservations] = useState([])
  const [obsLoading, setObsLoading] = useState(false)

  useEffect(() => {
    if (!station) return
    setTab('prediction')
    setObsLoading(true)
    getObservations(station.station_id)
      .then((res) => setObservations(res.data.observations ?? []))
      .catch(() => setObservations([]))
      .finally(() => setObsLoading(false))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [station?.station_id])

  if (!station) return null

  const { name, region, pin_status, alert_level, warning_level, predictions, statuses, predicted_at } = station
  const color = STATUS_COLOR[pin_status] ?? STATUS_COLOR.gray
  const currentLevel = observations.length > 0 ? observations[observations.length - 1].water_level : null

  return (
    <div style={styles.backdrop} onClick={onClose}>
      <div style={styles.modal} onClick={(e) => e.stopPropagation()}>

        {/* 헤더 */}
        <div style={styles.header}>
          <div style={styles.headerLeft}>
            <div style={styles.titleRow}>
              <span style={styles.name}>{name}</span>
              <span style={{ ...styles.badge, color, background: color + '18', border: `1px solid ${color}55` }}>
                {STATUS_LABEL[pin_status] ?? '—'}
              </span>
              <span style={styles.region}>{region}</span>
            </div>
            <div style={styles.levelRow}>
              <LevelChip label="현재 수위" value={currentLevel} color="#1565C0" />
              <LevelChip label="주의보" value={alert_level} color="#FBC02D" />
              <LevelChip label="경보" value={warning_level} color="#D32F2F" />
              {predicted_at && (
                <span style={styles.predictedAt}>
                  예측 기준: {predicted_at.replace('T', ' ').slice(0, 16)}
                </span>
              )}
            </div>
          </div>
          <button style={styles.closeBtn} onClick={onClose}>✕</button>
        </div>

        {/* h1~h6 상태 바 */}
        {statuses && (
          <div style={styles.statusBar}>
            {HOURS.map((h, i) => {
              const s = statuses[h] ?? 'gray'
              const c = STATUS_COLOR[s]
              const pred = predictions?.[h]?.predicted
              return (
                <div key={h} style={{ ...styles.statusCell, borderTop: `3px solid ${c}`, background: c + '12' }}>
                  <span style={styles.statusHour}>+{i + 1}h</span>
                  <span style={{ ...styles.statusLevel, color: c }}>
                    {pred != null ? `${pred.toFixed(2)}m` : '—'}
                  </span>
                  <span style={styles.statusLabel}>{STATUS_LABEL[s]}</span>
                </div>
              )
            })}
          </div>
        )}

        {/* 탭 */}
        <div style={styles.tabRow}>
          <TabBtn label="예측 수위 (h1~h6)" active={tab === 'prediction'} onClick={() => setTab('prediction')} />
          <TabBtn label="과거 24시간 관측" active={tab === 'observation'} onClick={() => setTab('observation')} />
        </div>

        {/* 차트 */}
        <div style={styles.chartWrap}>
          {tab === 'prediction' ? (
            <PredictionChart
              predictions={predictions}
              alertLevel={alert_level}
              warningLevel={warning_level}
              mode="prediction"
              large
            />
          ) : obsLoading ? (
            <div style={styles.loading}>데이터 불러오는 중...</div>
          ) : (
            <PredictionChart
              observations={observations}
              alertLevel={alert_level}
              warningLevel={warning_level}
              mode="observation"
              large
            />
          )}
        </div>

      </div>
    </div>
  )
}

function LevelChip({ label, value, color }) {
  return (
    <div style={styles.levelChip}>
      <span style={styles.levelLabel}>{label}</span>
      <span style={{ ...styles.levelValue, color }}>
        {value != null ? `${value.toFixed(2)}m` : '—'}
      </span>
    </div>
  )
}

function TabBtn({ label, active, onClick }) {
  return (
    <button style={{ ...styles.tab, ...(active ? styles.tabActive : {}) }} onClick={onClick}>
      {label}
    </button>
  )
}

const styles = {
  backdrop: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(15, 23, 42, 0.6)',
    zIndex: 500,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    backdropFilter: 'blur(2px)',
  },
  modal: {
    background: '#FFFFFF',
    borderRadius: 16,
    boxShadow: '0 24px 64px rgba(0,0,0,0.2)',
    width: '80vw',
    maxWidth: 900,
    maxHeight: '88vh',
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
  },
  header: {
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    padding: '20px 24px 16px',
    borderBottom: '1px solid #E2E8F0',
    flexShrink: 0,
  },
  headerLeft: {
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
  },
  titleRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  name: {
    fontSize: 20,
    fontWeight: 700,
    color: '#1A202C',
  },
  badge: {
    fontSize: 12,
    fontWeight: 700,
    padding: '3px 8px',
    borderRadius: 5,
  },
  region: {
    fontSize: 13,
    color: '#718096',
    background: '#F0F4F8',
    padding: '2px 8px',
    borderRadius: 8,
  },
  levelRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 20,
  },
  levelChip: {
    display: 'flex',
    flexDirection: 'column',
    gap: 1,
  },
  levelLabel: {
    fontSize: 10,
    color: '#718096',
  },
  levelValue: {
    fontSize: 16,
    fontWeight: 700,
    fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
    fontVariantNumeric: 'tabular-nums',
  },
  predictedAt: {
    fontSize: 11,
    color: '#A0AEC0',
    marginLeft: 8,
  },
  closeBtn: {
    background: 'none',
    border: 'none',
    fontSize: 18,
    color: '#A0AEC0',
    cursor: 'pointer',
    padding: 4,
    lineHeight: 1,
    flexShrink: 0,
  },
  statusBar: {
    display: 'flex',
    padding: '12px 24px',
    gap: 8,
    borderBottom: '1px solid #E2E8F0',
    background: '#FAFBFC',
    flexShrink: 0,
  },
  statusCell: {
    flex: 1,
    borderRadius: 6,
    padding: '8px 10px',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 3,
    border: '1px solid #E2E8F0',
    borderTopWidth: 3,
  },
  statusHour: {
    fontSize: 11,
    color: '#718096',
    fontWeight: 600,
  },
  statusLevel: {
    fontSize: 14,
    fontWeight: 700,
    fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
    fontVariantNumeric: 'tabular-nums',
  },
  statusLabel: {
    fontSize: 10,
    color: '#718096',
  },
  tabRow: {
    display: 'flex',
    borderBottom: '1px solid #E2E8F0',
    flexShrink: 0,
  },
  tab: {
    flex: 1,
    background: 'none',
    border: 'none',
    padding: '12px 0',
    fontSize: 13,
    color: '#718096',
    cursor: 'pointer',
  },
  tabActive: {
    color: '#1565C0',
    borderBottom: '2px solid #1565C0',
    fontWeight: 700,
  },
  chartWrap: {
    flex: 1,
    padding: '16px 16px 12px',
    overflow: 'hidden',
    minHeight: 0,
  },
  loading: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    height: '100%',
    fontSize: 13,
    color: '#718096',
  },
}
