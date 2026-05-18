import React, { useState, useEffect } from 'react'
import { refreshData } from '../../api'
import { STATUS_COLOR } from '../../constants/colors'

function getElapsed(lastUpdated) {
  if (!lastUpdated) return null
  const diff = Math.floor((Date.now() - new Date(lastUpdated)) / 60000)
  if (diff < 1) return '방금 전'
  if (diff < 60) return `${diff}분 전`
  const h = Math.floor(diff / 60)
  return `${h}시간 전`
}

function isStale(lastUpdated) {
  if (!lastUpdated) return false
  return Date.now() - new Date(lastUpdated) > 2 * 60 * 60 * 1000
}

const isMobile = () => typeof window !== 'undefined' && window.innerWidth <= 768

function useClock() {
  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  return now
}

export default function Header({ lastUpdated, panelOpen, onTogglePanel, stations = [] }) {
  const [refreshing, setRefreshing] = useState(false)
  const [refreshError, setRefreshError] = useState(null)
  const [, setTick] = useState(0)
  const now = useClock()

  // 경과 시간 매분 갱신
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 60000)
    return () => clearInterval(id)
  }, [])

  const handleRefresh = () => {
    if (refreshing) return
    setRefreshing(true)
    setRefreshError(null)
    refreshData()
      .then(() => window.location.reload())
      .catch(() => {
        setRefreshError('새로고침 실패')
        setRefreshing(false)
      })
  }

  const formattedTime = lastUpdated
    ? new Date(lastUpdated).toLocaleString('ko-KR', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
    : null

  const elapsed = getElapsed(lastUpdated)
  const stale = isStale(lastUpdated)

  const redStations = stations.filter((s) => s.pin_status === 'red')
  const yellowStations = stations.filter((s) => s.pin_status === 'yellow')
  const hasAlert = redStations.length > 0 || yellowStations.length > 0

  return (
    <>
      <header style={styles.header}>
        <div style={styles.left}>
          <button style={styles.menuBtn} onClick={onTogglePanel} title={panelOpen ? '목록 닫기' : '목록 열기'}>
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
              <rect x="2" y="4" width="14" height="1.8" rx="0.9" fill="#8899AA"/>
              <rect x="2" y="8.1" width="14" height="1.8" rx="0.9" fill="#8899AA"/>
              <rect x="2" y="12.2" width="14" height="1.8" rx="0.9" fill="#8899AA"/>
            </svg>
          </button>
          <span style={styles.title}>한강홍수통제소 수위 예측 대시보드</span>
        </div>

        <div style={styles.right}>
          <div style={styles.clock}>
            <span style={styles.clockTime}>
              {now.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })}
            </span>
            <span style={styles.clockDate}>
              {now.toLocaleDateString('ko-KR', { month: '2-digit', day: '2-digit', weekday: 'short' })}
            </span>
          </div>
          {formattedTime && (
            <span style={{ ...styles.updatedAt, color: stale ? '#ED8936' : '#718096' }}>
              마지막 업데이트: {formattedTime}
              {elapsed && <span style={styles.elapsed}>{elapsed}</span>}
            </span>
          )}
          {refreshError && (
            <span style={styles.errorMsg}>{refreshError}</span>
          )}
          <button
            style={{ ...styles.refreshBtn, opacity: refreshing ? 0.6 : 1 }}
            onClick={handleRefresh}
            disabled={refreshing}
          >
            {refreshing ? (
              <>
                <span style={styles.spinner} />
                예측 갱신 중...
              </>
            ) : (
              <>↻ 새로고침</>
            )}
          </button>
        </div>
      </header>

      {/* 경보 배너 */}
      {hasAlert && (
        <div style={{
          ...styles.alertBanner,
          background: redStations.length > 0 ? '#C53030' : '#B7791F',
        }}>
          <span style={styles.alertDot} />
          <span style={styles.alertText}>
            {isMobile()
              ? `수위 경보 감지 · ${redStations.length > 0 ? `경보 ${redStations.length}` : ''}${yellowStations.length > 0 ? ` 주의 ${yellowStations.length}` : ''}개소`
              : redStations.length > 0
                ? `수위 경보 감지 · ${redStations.map((s) => s.name).join(', ')}${yellowStations.length > 0 ? ` 외 주의 ${yellowStations.length}개소` : ''}`
                : `수위 주의 감지 · ${yellowStations.map((s) => s.name).join(', ')}`
            }
          </span>
        </div>
      )}

      {/* 정상 배너 (데이터 로드 완료 + 경보 없음) */}
      {!hasAlert && stations.length > 0 && (
        <div style={styles.normalBanner}>
          <span style={styles.normalDot} />
          <span style={styles.normalText}>전체 관측소 정상</span>
        </div>
      )}
    </>
  )
}

const styles = {
  header: {
    height: 56,
    background: '#0D1F3C',
    borderBottom: '1px solid #1E3A5F',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '0 20px',
    flexShrink: 0,
    boxShadow: '0 2px 8px rgba(0,0,0,0.3)',
  },
  left: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
  },
  menuBtn: {
    background: 'none',
    border: 'none',
    cursor: 'pointer',
    padding: 4,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 6,
    flexShrink: 0,
  },
  title: {
    fontSize: 15,
    fontWeight: 700,
    color: '#FFFFFF',
    letterSpacing: '-0.3px',
  },
  right: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
  },
  clock: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-end',
    gap: 1,
    padding: '4px 10px',
    background: '#0A1628',
    border: '1px solid #1E3A5F',
    borderRadius: 6,
  },
  clockTime: {
    fontSize: 14,
    fontWeight: 700,
    color: '#4A9EFF',
    fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
    fontVariantNumeric: 'tabular-nums',
    letterSpacing: '0.5px',
  },
  clockDate: {
    fontSize: 10,
    color: '#8899AA',
    letterSpacing: '0.3px',
  },
  updatedAt: {
    fontSize: 12,
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  },
  elapsed: {
    fontSize: 11,
    color: '#8899AA',
    background: '#1A2F50',
    padding: '1px 6px',
    borderRadius: 10,
  },
  errorMsg: {
    fontSize: 12,
    color: '#FF4444',
  },
  refreshBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    background: '#1A3A6B',
    color: '#4A9EFF',
    border: '1px solid #1E3A5F',
    borderRadius: 6,
    padding: '6px 14px',
    fontSize: 12,
    fontWeight: 600,
    cursor: 'pointer',
    transition: 'opacity 0.15s',
  },
  spinner: {
    display: 'inline-block',
    width: 12,
    height: 12,
    border: '2px solid rgba(74,158,255,0.3)',
    borderTop: '2px solid #4A9EFF',
    borderRadius: '50%',
    animation: 'spin 0.8s linear infinite',
  },
  alertBanner: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '5px 16px',
    flexShrink: 0,
  },
  alertDot: {
    width: 7,
    height: 7,
    borderRadius: '50%',
    background: 'rgba(255,255,255,0.8)',
    animation: 'pulse 1.4s ease-out infinite',
    flexShrink: 0,
  },
  alertText: {
    fontSize: 12,
    fontWeight: 600,
    color: '#FFFFFF',
    letterSpacing: '0.1px',
  },
  normalBanner: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '5px 20px',
    background: 'rgba(0, 204, 102, 0.1)',
    borderBottom: '1px solid rgba(0, 204, 102, 0.2)',
    flexShrink: 0,
  },
  normalDot: {
    width: 7,
    height: 7,
    borderRadius: '50%',
    background: '#00CC66',
    flexShrink: 0,
  },
  normalText: {
    fontSize: 12,
    fontWeight: 600,
    color: '#00CC66',
  },
}
