import React, { useState, useEffect } from 'react'
import './App.css'
import { getStationsWithStatus } from './api'
import Header from './components/Header/Header'
import MapView from './components/Map/MapView'
import AlertPanel from './components/Panel/AlertPanel'
import DetailPanel from './components/Panel/DetailPanel'

const isMobile = () => window.innerWidth <= 768

function KpiCard({ label, value, color, highlight = false }) {
  return (
    <div style={{
      ...kpiStyles.card,
      ...(highlight ? { background: color + '12', border: `1px solid ${color}44` } : {}),
    }}>
      <span style={{ ...kpiStyles.value, color }}>{value}</span>
      <span style={kpiStyles.label}>{label}</span>
    </div>
  )
}

const kpiStyles = {
  card: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 2,
    padding: '6px 4px',
    borderRadius: 6,
    border: '1px solid transparent',
  },
  value: {
    fontSize: 20,
    fontWeight: 700,
    fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
    fontVariantNumeric: 'tabular-nums',
    lineHeight: 1.1,
  },
  label: {
    fontSize: 10,
    color: '#8899AA',
    letterSpacing: '0.3px',
    whiteSpace: 'nowrap',
  },
}

export default function App() {
  const [stations, setStations] = useState([])
  const [selectedStation, setSelectedStation] = useState(null)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [loading, setLoading] = useState(true)
  const [fetchError, setFetchError] = useState(null)
  const [panelOpen, setPanelOpen] = useState(!isMobile())
  const [mobile, setMobile] = useState(isMobile())

  useEffect(() => {
    const handler = () => {
      const m = isMobile()
      setMobile(m)
      if (!m) setPanelOpen(true)
    }
    window.addEventListener('resize', handler)
    return () => window.removeEventListener('resize', handler)
  }, [])

  useEffect(() => {
    getStationsWithStatus()
      .then((res) => {
        const data = (res.data.stations ?? []).map((s) => ({
          ...s,
          predictions: typeof s.predictions === 'string'
            ? JSON.parse(s.predictions)
            : s.predictions,
        }))
        setStations(data)
        const latest = data
          .map((s) => s.predicted_at)
          .filter(Boolean)
          .sort()
          .at(-1)
        setLastUpdated(latest ?? null)
      })
      .catch((err) => setFetchError(err.message))
      .finally(() => setLoading(false))
  }, [])

  const handleSelectStation = (station) => {
    setSelectedStation(station)
    if (mobile) setPanelOpen(false)
  }

  const counts = {
    total: stations.length,
    red: stations.filter((s) => s.pin_status === 'red').length,
    yellow: stations.filter((s) => s.pin_status === 'yellow').length,
    green: stations.filter((s) => s.pin_status === 'green').length,
    gray: stations.filter((s) => s.pin_status === 'gray').length,
  }

  return (
    <div style={styles.root}>
      <Header lastUpdated={lastUpdated} panelOpen={panelOpen} onTogglePanel={() => setPanelOpen((v) => !v)} stations={stations} />
      {stations.length > 0 && (
        <div style={styles.kpiBar}>
          <KpiCard label="전체 관측소" value={counts.total} color="#4A9EFF" />
          <div style={styles.kpiDivider} />
          <KpiCard label="경보" value={counts.red} color="#FF4444" highlight={counts.red > 0} />
          <div style={styles.kpiDivider} />
          <KpiCard label="주의" value={counts.yellow} color="#FFB800" highlight={counts.yellow > 0} />
          <div style={styles.kpiDivider} />
          <KpiCard label="정상" value={counts.green} color="#00CC66" />
          <div style={styles.kpiDivider} />
          <KpiCard label="데이터없음" value={counts.gray} color="#4A5568" />
        </div>
      )}
      <div style={styles.body}>
        {/* 모바일: 오버레이 딤 */}
        {mobile && panelOpen && (
          <div style={styles.dim} onClick={() => setPanelOpen(false)} />
        )}

        {/* 패널 */}
        <aside style={{
          ...styles.panel,
          ...(mobile ? styles.panelMobile : {}),
          ...(mobile ? { transform: panelOpen ? 'translateX(0)' : 'translateX(-100%)' } : { width: panelOpen ? 280 : 0 }),
        }}>
          <AlertPanel
            stations={stations}
            selectedStation={selectedStation}
            onSelectStation={handleSelectStation}
          />
        </aside>


        <main style={styles.mapArea}>
          <MapView
            stations={stations}
            selectedStation={selectedStation}
            onSelectStation={handleSelectStation}
          />
          {loading && (
            <div style={styles.overlay}>
              <div style={styles.overlaySpinner} />
              <span style={styles.overlayText}>관측소 데이터 불러오는 중...</span>
            </div>
          )}
          {fetchError && !loading && (
            <div style={styles.overlay}>
              <span style={styles.overlayError}>데이터 로드 실패: {fetchError}</span>
            </div>
          )}
        </main>
        <DetailPanel
          station={selectedStation}
          onClose={() => setSelectedStation(null)}
          mobile={mobile}
        />
      </div>
    </div>
  )
}

const styles = {
  root: {
    height: '100vh',
    display: 'flex',
    flexDirection: 'column',
    background: '#0A1628',
  },
  kpiBar: {
    display: 'flex',
    alignItems: 'stretch',
    padding: '6px 20px',
    background: '#0D1F3C',
    borderBottom: '1px solid #1E3A5F',
    flexShrink: 0,
    gap: 4,
  },
  kpiDivider: {
    width: 1,
    background: '#1E3A5F',
    alignSelf: 'stretch',
    margin: '4px 0',
  },
  body: {
    flex: 1,
    display: 'flex',
    overflow: 'hidden',
    position: 'relative',
  },
  dim: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(0,0,0,0.6)',
    zIndex: 1000,
  },
  panel: {
    background: '#112240',
    flexShrink: 0,
    overflow: 'hidden',
    transition: 'width 0.25s ease',
    boxShadow: '2px 0 12px rgba(0,0,0,0.3)',
    borderRight: '1px solid #1E3A5F',
  },
  panelMobile: {
    position: 'fixed',
    top: 0,
    left: 0,
    bottom: 0,
    width: 280,
    zIndex: 1001,
    transition: 'transform 0.25s ease',
    boxShadow: '4px 0 24px rgba(0,0,0,0.5)',
  },
  panelToggle: {
    alignSelf: 'center',
    flexShrink: 0,
    zIndex: 10,
    background: 'transparent',
    border: 'none',
    borderRadius: '0 8px 8px 0',
    width: 20,
    height: 56,
    cursor: 'pointer',
    boxShadow: 'none',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 0,
    opacity: 0.25,
    transition: 'opacity 0.2s',
  },
  panelToggleMobile: {
    position: 'absolute',
    top: 12,
    left: 0,
    zIndex: 301,
    alignSelf: 'unset',
    borderRadius: '0 8px 8px 0',
  },
  mapArea: {
    flex: 1,
    position: 'relative',
    overflow: 'hidden',
  },
  overlay: {
    position: 'absolute',
    inset: 0,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
    background: 'rgba(10,22,40,0.85)',
    zIndex: 200,
    pointerEvents: 'none',
  },
  overlaySpinner: {
    width: 32,
    height: 32,
    border: '3px solid #1E3A5F',
    borderTop: '3px solid #4A9EFF',
    borderRadius: '50%',
    animation: 'spin 0.8s linear infinite',
  },
  overlayText: {
    fontSize: 13,
    color: '#8899AA',
  },
  overlayError: {
    fontSize: 13,
    color: '#FF4444',
  },
}
