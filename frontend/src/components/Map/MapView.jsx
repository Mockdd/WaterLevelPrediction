import React, { useState, useEffect, useRef } from 'react'
import { MapContainer, TileLayer, WMSTileLayer, useMap } from 'react-leaflet'
import MarkerClusterGroup from 'react-leaflet-cluster'
import StationMarker from '../Marker/StationMarker'
import TimelineView from '../Timeline/TimelineView'

const INITIAL_CENTER = [37.5, 127.8]
const INITIAL_ZOOM = 8
const MAX_BOUNDS = [[33.0, 124.0], [43.0, 132.0]]

function getZoomStage(zoom) {
  if (zoom >= 14) return 'timeline'
  if (zoom >= 11) return 'detail'
  return 'overview'
}

function FlyToStation({ station }) {
  const map = useMap()
  const prevId = useRef(null)
  useEffect(() => {
    if (station && station.station_id !== prevId.current) {
      prevId.current = station.station_id
      map.flyTo([station.lat, station.lng], 13, { duration: 0.8 })
    }
  }, [station, map])
  return null
}

function ZoomWatcher({ onZoom }) {
  const map = useMap()
  useEffect(() => {
    const handler = () => onZoom(map.getZoom())
    map.on('zoomend', handler)
    return () => map.off('zoomend', handler)
  }, [map, onZoom])
  return null
}

function ResizeWatcher() {
  const map = useMap()
  useEffect(() => {
    const observer = new ResizeObserver(() => map.invalidateSize())
    const container = map.getContainer()
    observer.observe(container)
    return () => observer.disconnect()
  }, [map])
  return null
}

export default function MapView({ stations = [], selectedStation, onSelectStation }) {
  const [zoomLevel, setZoomLevel] = useState(INITIAL_ZOOM)
  const zoomStage = getZoomStage(zoomLevel)

  return (
    <div style={styles.wrapper}>
      <MapContainer
        center={INITIAL_CENTER}
        zoom={INITIAL_ZOOM}
        minZoom={7}
        maxBounds={MAX_BOUNDS}
        maxBoundsViscosity={1.0}
        style={styles.map}
        zoomControl={true}
      >
        {/* Vworld 기본 배경지도 (지명 포함) */}
        <TileLayer
          url={`https://api.vworld.kr/req/wmts/1.0.0/${process.env.REACT_APP_VWORLD_KEY}/Base/{z}/{y}/{x}.png`}
          attribution='&copy; <a href="https://www.vworld.kr">브이월드(국토지리정보원)</a>'
        />

        <FlyToStation station={selectedStation} />
        <ZoomWatcher onZoom={setZoomLevel} />
        <ResizeWatcher />

        <MarkerClusterGroup chunkedLoading>
          {stations.map((station) => (
            <StationMarker
              key={station.station_id}
              station={station}
              zoomStage={zoomStage}
              isSelected={selectedStation?.station_id === station.station_id}
              onClick={() => onSelectStation(station)}
            />
          ))}
        </MarkerClusterGroup>
      </MapContainer>

      {zoomStage === 'timeline' && (
        <TimelineView station={selectedStation} />
      )}

      {/* 범례 */}
      <div style={styles.legend}>
        <div style={styles.legendTitle}>신호등 기준</div>
        {[
          { color: '#FF4444', label: '경보', sub: '경보수위 초과' },
          { color: '#FFB800', label: '주의', sub: '주의보수위 초과' },
          { color: '#00CC66', label: '정상', sub: '주의보수위 미만' },
          { color: '#4A5568', label: '데이터 없음', sub: null },
        ].map(({ color, label, sub }) => (
          <div key={label} style={styles.legendItem}>
            <span style={{ ...styles.legendDot, background: color }} />
            <span style={styles.legendLabel}>{label}</span>
            {sub && <span style={styles.legendSub}>{sub}</span>}
          </div>
        ))}
      </div>
    </div>
  )
}

const styles = {
  wrapper: {
    width: '100%',
    height: '100%',
    position: 'relative',
  },
  map: {
    width: '100%',
    height: '100%',
  },
  legend: {
    position: 'absolute',
    bottom: 28,
    right: 10,
    zIndex: 1000,
    background: 'rgba(13, 31, 60, 0.92)',
    border: '1px solid #1E3A5F',
    borderRadius: 8,
    padding: '10px 12px',
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
    minWidth: 130,
    backdropFilter: 'blur(4px)',
  },
  legendTitle: {
    fontSize: 10,
    fontWeight: 700,
    color: '#8899AA',
    letterSpacing: '0.8px',
    textTransform: 'uppercase',
    marginBottom: 2,
  },
  legendItem: {
    display: 'flex',
    alignItems: 'center',
    gap: 7,
  },
  legendDot: {
    width: 9,
    height: 9,
    borderRadius: '50%',
    flexShrink: 0,
  },
  legendLabel: {
    fontSize: 12,
    color: '#FFFFFF',
    fontWeight: 600,
    minWidth: 48,
  },
  legendSub: {
    fontSize: 10,
    color: '#8899AA',
  },
}
