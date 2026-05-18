import React, { useState, useEffect } from 'react'
import { getObservations } from '../../api'
import PredictionChart from '../Chart/PredictionChart'
import { STATUS_COLOR, STATUS_LABEL } from '../../constants/colors'

const HOURS = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']

export default function DetailPanel({ station, onClose, mobile = false }) {
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

  const visible = !!station

  if (mobile) {
    return (
      <div style={{
        ...styles.bottomSheet,
        transform: visible ? 'translateY(0)' : 'translateY(100%)',
      }}>
        {station && <div style={styles.bottomSheetInner}>
          <div style={styles.bottomSheetHandle} />
          {/* 헤더 */}
          <div style={styles.header}>
            <div style={styles.headerMain}>
              <div style={styles.titleRow}>
                <span style={styles.name}>{station.name}</span>
                <span style={{
                  ...styles.badge,
                  color: STATUS_COLOR[station.pin_status] ?? STATUS_COLOR.gray,
                  background: (STATUS_COLOR[station.pin_status] ?? STATUS_COLOR.gray) + '18',
                  border: `1px solid ${(STATUS_COLOR[station.pin_status] ?? STATUS_COLOR.gray)}55`,
                }}>
                  {STATUS_LABEL[station.pin_status] ?? '—'}
                </span>
              </div>
              <span style={styles.region}>{station.region}</span>
            </div>
            <button style={styles.closeBtn} onClick={onClose}>✕</button>
          </div>
          {/* 수위 요약 */}
          <div style={styles.levelRow}>
            <LevelItem label="현재 수위" value={observations.length > 0 ? observations[observations.length - 1].water_level : null} color="#1565C0" />
            <div style={styles.levelDivider} />
            <LevelItem label="주의보" value={station.alert_level} color="#FBC02D" />
            <div style={styles.levelDivider} />
            <LevelItem label="경보" value={station.warning_level} color="#D32F2F" />
          </div>
          {/* 탭 */}
          <div style={styles.tabRow}>
            <TabBtn label="예측 수위" active={tab === 'prediction'} onClick={() => setTab('prediction')} />
            <TabBtn label="과거 24시간" active={tab === 'observation'} onClick={() => setTab('observation')} />
          </div>
          <div style={{ ...styles.chartArea, height: 200 }}>
            {tab === 'prediction' ? (
              <PredictionChart predictions={station.predictions} alertLevel={station.alert_level} warningLevel={station.warning_level} mode="prediction" large />
            ) : obsLoading ? (
              <div style={styles.loading}>데이터 불러오는 중...</div>
            ) : (
              <PredictionChart observations={observations} alertLevel={station.alert_level} warningLevel={station.warning_level} mode="observation" large />
            )}
          </div>
        </div>}
      </div>
    )
  }

  return (
    <div style={{ ...styles.panel, width: visible ? 400 : 0 }}>
      {station && (
        <>
          {/* 헤더 */}
          <div style={styles.header}>
            <div style={styles.headerMain}>
              <div style={styles.titleRow}>
                <span style={styles.name}>{station.name}</span>
                <span style={{
                  ...styles.badge,
                  color: STATUS_COLOR[station.pin_status] ?? STATUS_COLOR.gray,
                  background: (STATUS_COLOR[station.pin_status] ?? STATUS_COLOR.gray) + '18',
                  border: `1px solid ${(STATUS_COLOR[station.pin_status] ?? STATUS_COLOR.gray)}55`,
                }}>
                  {STATUS_LABEL[station.pin_status] ?? '—'}
                </span>
              </div>
              <span style={styles.region}>{station.region}</span>
            </div>
            <button style={styles.closeBtn} onClick={onClose}>✕</button>
          </div>

          {/* 수위 요약 */}
          <div style={styles.levelRow}>
            <LevelItem
              label="현재 수위"
              value={observations.length > 0 ? observations[observations.length - 1].water_level : null}
              color="#1565C0"
            />
            <div style={styles.levelDivider} />
            <LevelItem label="주의보" value={station.alert_level} color="#FBC02D" />
            <div style={styles.levelDivider} />
            <LevelItem label="경보" value={station.warning_level} color="#D32F2F" />
          </div>

          {/* h1~h6 상태 바 */}
          {(station.statuses || station.predictions) && (
            <div style={styles.statusBar}>
              {HOURS.map((h, i) => {
                const pred = station.predictions?.[h]?.predicted
                let s = station.statuses?.[h] ?? 'gray'
                if (s === 'gray' && pred != null) {
                  if (station.warning_level != null && pred >= station.warning_level) s = 'red'
                  else if (station.alert_level != null && pred >= station.alert_level) s = 'yellow'
                  else s = 'green'
                }
                const c = STATUS_COLOR[s]
                return (
                  <div key={h} style={{ ...styles.statusCell, borderTop: `3px solid ${c}`, background: c + '10' }}>
                    <span style={styles.statusHour}>+{i + 1}h</span>
                    <span style={{ ...styles.statusValue, color: c }}>
                      {pred != null ? `${pred.toFixed(2)}` : '—'}
                    </span>
                    <span style={styles.statusUnit}>m</span>
                  </div>
                )
              })}
            </div>
          )}

          {/* 탭 */}
          <div style={styles.tabRow}>
            <TabBtn label="예측 수위" active={tab === 'prediction'} onClick={() => setTab('prediction')} />
            <TabBtn label="과거 24시간" active={tab === 'observation'} onClick={() => setTab('observation')} />
          </div>

          {/* 예측 기준 시각 */}
          {station.predicted_at && (
            <div style={styles.predictedAt}>
              예측 기준: {station.predicted_at.replace('T', ' ').slice(0, 16)}
            </div>
          )}

          {/* 차트 */}
          <div style={styles.chartArea}>
            {tab === 'prediction' ? (
              <PredictionChart
                predictions={station.predictions}
                alertLevel={station.alert_level}
                warningLevel={station.warning_level}
                mode="prediction"
                large
              />
            ) : obsLoading ? (
              <div style={styles.loading}>데이터 불러오는 중...</div>
            ) : (
              <PredictionChart
                observations={observations}
                alertLevel={station.alert_level}
                warningLevel={station.warning_level}
                mode="observation"
                large
              />
            )}
          </div>
        </>
      )}
    </div>
  )
}

function LevelItem({ label, value, color }) {
  return (
    <div style={styles.levelItem}>
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
  bottomSheet: {
    position: 'fixed',
    bottom: 0,
    left: 0,
    right: 0,
    zIndex: 1002,
    background: '#112240',
    borderRadius: '16px 16px 0 0',
    boxShadow: '0 -4px 24px rgba(0,0,0,0.5)',
    border: '1px solid #1E3A5F',
    borderBottom: 'none',
    transition: 'transform 0.3s ease',
    maxHeight: '75vh',
    overflow: 'hidden',
  },
  bottomSheetInner: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
  },
  bottomSheetHandle: {
    width: 36,
    height: 4,
    background: '#1E3A5F',
    borderRadius: 2,
    margin: '10px auto 0',
    flexShrink: 0,
  },
  panel: {
    height: '100%',
    background: '#112240',
    borderLeft: '1px solid #1E3A5F',
    display: 'flex',
    flexDirection: 'column',
    flexShrink: 0,
    transition: 'width 0.3s ease',
    overflow: 'hidden',
    minWidth: 0,
  },
  header: {
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    padding: '18px 20px 14px',
    borderBottom: '1px solid #1E3A5F',
    flexShrink: 0,
  },
  headerMain: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  titleRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  name: {
    fontSize: 17,
    fontWeight: 700,
    color: '#FFFFFF',
  },
  badge: {
    fontSize: 11,
    fontWeight: 700,
    padding: '2px 8px',
    borderRadius: 4,
  },
  region: {
    fontSize: 12,
    color: '#8899AA',
  },
  closeBtn: {
    background: 'none',
    border: 'none',
    fontSize: 16,
    color: '#4A5568',
    cursor: 'pointer',
    padding: 4,
  },
  levelRow: {
    display: 'flex',
    alignItems: 'center',
    padding: '14px 20px',
    background: '#0D1F3C',
    borderBottom: '1px solid #1E3A5F',
    flexShrink: 0,
  },
  levelItem: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 3,
  },
  levelDivider: {
    width: 1,
    height: 32,
    background: '#1E3A5F',
  },
  levelLabel: {
    fontSize: 10,
    color: '#8899AA',
    letterSpacing: '0.3px',
  },
  levelValue: {
    fontSize: 18,
    fontWeight: 700,
    fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
    fontVariantNumeric: 'tabular-nums',
  },
  statusBar: {
    display: 'flex',
    padding: '12px 16px',
    gap: 6,
    borderBottom: '1px solid #1E3A5F',
    flexShrink: 0,
    background: '#0D1F3C',
  },
  statusCell: {
    flex: 1,
    borderRadius: 6,
    padding: '7px 4px',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 2,
    border: '1px solid #1E3A5F',
    borderTopWidth: 3,
  },
  statusHour: {
    fontSize: 10,
    color: '#8899AA',
    fontWeight: 600,
  },
  statusValue: {
    fontSize: 13,
    fontWeight: 700,
    fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
    fontVariantNumeric: 'tabular-nums',
  },
  statusUnit: {
    fontSize: 9,
    color: '#4A5568',
  },
  tabRow: {
    display: 'flex',
    borderBottom: '1px solid #1E3A5F',
    flexShrink: 0,
  },
  tab: {
    flex: 1,
    background: 'none',
    border: 'none',
    padding: '11px 0',
    fontSize: 13,
    color: '#8899AA',
    cursor: 'pointer',
  },
  tabActive: {
    color: '#4A9EFF',
    borderBottom: '2px solid #4A9EFF',
    fontWeight: 700,
  },
  predictedAt: {
    fontSize: 11,
    color: '#4A5568',
    padding: '6px 20px 0',
    flexShrink: 0,
  },
  chartArea: {
    flex: 1,
    padding: '8px 12px 16px',
    overflow: 'hidden',
    minHeight: 0,
  },
  loading: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    height: '100%',
    fontSize: 13,
    color: '#8899AA',
  },
}
