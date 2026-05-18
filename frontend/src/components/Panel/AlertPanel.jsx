import React, { useMemo, useState } from 'react'
import { STATUS_COLOR, STATUS_LABEL } from '../../constants/colors'

const STATUS_ORDER = { red: 0, yellow: 1 }

export default function AlertPanel({ stations = [], selectedStation, onSelectStation }) {
  const [query, setQuery] = useState('')

  const alerts = useMemo(() => {
    return stations
      .filter((s) => s.pin_status === 'red' || s.pin_status === 'yellow')
      .sort((a, b) => (STATUS_ORDER[a.pin_status] ?? 9) - (STATUS_ORDER[b.pin_status] ?? 9))
  }, [stations])

  const searchResults = useMemo(() => {
    const q = query.trim()
    if (!q) return []
    return stations.filter(
      (s) =>
        s.name?.includes(q) || s.region?.includes(q)
    )
  }, [query, stations])

  const isSearching = query.trim().length > 0

  const listToShow = isSearching ? searchResults : alerts

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={styles.title}>대응 우선순위</span>
        <span style={styles.count}>{alerts.length}개소</span>
      </div>

      <div style={styles.searchBox}>
        <input
          style={styles.searchInput}
          type="text"
          placeholder="관측소명 또는 지역 검색"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {query && (
          <button style={styles.clearBtn} onClick={() => setQuery('')}>✕</button>
        )}
      </div>

      {isSearching && (
        <div style={styles.searchLabel}>
          검색 결과 {searchResults.length}개소
        </div>
      )}

      {listToShow.length === 0 ? (
        <div style={styles.empty}>
          {isSearching ? '검색 결과 없음' : '현재 주의/경보 관측소 없음'}
        </div>
      ) : (
        <ul style={styles.list}>
          {listToShow.map((station) => {
            const isSelected = selectedStation?.station_id === station.station_id
            const color = STATUS_COLOR[station.pin_status] ?? STATUS_COLOR.gray
            const h1 = station.predictions?.h1?.predicted
            return (
              <li
                key={station.station_id}
                style={{
                  ...styles.item,
                  background: isSelected ? '#1A3A6B' : 'transparent',
                  borderLeft: `3px solid ${color}`,
                }}
                onClick={() => onSelectStation(station)}
              >
                <div style={styles.itemTop}>
                  <span style={styles.stationName}>{station.name}</span>
                  <span style={{ ...styles.badge, color, background: color + '18', border: `1px solid ${color}55` }}>
                    {STATUS_LABEL[station.pin_status] ?? '정상'}
                  </span>
                </div>
                <div style={styles.itemBottom}>
                  <span style={styles.region}>{station.region}</span>
                  {h1 != null && (
                    <span style={styles.level}>
                      h1: <strong style={{ color }}>{h1.toFixed(2)}m</strong>
                    </span>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

const styles = {
  container: {
    width: '100%',
    height: '100%',
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
    background: '#112240',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '14px 16px 10px',
    borderBottom: '1px solid #1E3A5F',
    flexShrink: 0,
  },
  title: {
    fontSize: 11,
    fontWeight: 700,
    color: '#8899AA',
    letterSpacing: '0.8px',
    textTransform: 'uppercase',
  },
  count: {
    fontSize: 11,
    color: '#4A9EFF',
    background: 'rgba(74,158,255,0.1)',
    padding: '2px 7px',
    borderRadius: 10,
    border: '1px solid rgba(74,158,255,0.2)',
  },
  searchBox: {
    display: 'flex',
    alignItems: 'center',
    padding: '8px 12px',
    borderBottom: '1px solid #1E3A5F',
    flexShrink: 0,
    gap: 6,
  },
  searchInput: {
    flex: 1,
    border: '1px solid #1E3A5F',
    borderRadius: 6,
    padding: '6px 10px',
    fontSize: 12,
    outline: 'none',
    color: '#FFFFFF',
    background: '#0D1F3C',
  },
  clearBtn: {
    background: 'none',
    border: 'none',
    cursor: 'pointer',
    color: '#4A5568',
    fontSize: 12,
    padding: '2px 4px',
    flexShrink: 0,
  },
  searchLabel: {
    fontSize: 11,
    color: '#8899AA',
    padding: '4px 16px',
    background: '#0D1F3C',
    borderBottom: '1px solid #1E3A5F',
    flexShrink: 0,
  },
  empty: {
    flex: 1,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 12,
    color: '#4A5568',
    padding: 24,
    textAlign: 'center',
  },
  list: {
    flex: 1,
    overflowY: 'auto',
    margin: 0,
    padding: '6px 0',
    listStyle: 'none',
  },
  item: {
    padding: '10px 14px',
    cursor: 'pointer',
    borderBottom: '1px solid #1A2F50',
    transition: 'background 0.15s',
  },
  itemTop: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 6,
    marginBottom: 3,
  },
  stationName: {
    fontSize: 13,
    fontWeight: 600,
    color: '#FFFFFF',
    flex: 1,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  badge: {
    fontSize: 10,
    fontWeight: 700,
    padding: '2px 6px',
    borderRadius: 4,
    flexShrink: 0,
  },
  itemBottom: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  region: {
    fontSize: 11,
    color: '#8899AA',
  },
  level: {
    fontSize: 11,
    color: '#8899AA',
    fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
    fontVariantNumeric: 'tabular-nums',
  },
}
