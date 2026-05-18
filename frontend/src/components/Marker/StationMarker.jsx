import React from 'react'
import { Marker } from 'react-leaflet'
import L from 'leaflet'
import { STATUS_COLOR } from '../../constants/colors'

function createIcon(color, isSelected, isRed, zoomStage, name, h1) {
  const size = isSelected ? 20 : 14

  const pulse = isRed ? `
    <div style="
      position:absolute;
      top:50%;left:50%;
      transform:translate(-50%,-50%);
      width:${size * 2.4}px;height:${size * 2.4}px;
      border-radius:50%;
      background:${color};
      opacity:0.35;
      animation:markerPulse 1.4s ease-out infinite;
    "></div>` : ''

  const label = (zoomStage === 'detail' || zoomStage === 'timeline') ? `
    <div style="
      position:absolute;
      top:${size + 4}px;
      left:50%;
      transform:translateX(-50%);
      background:rgba(255,255,255,0.95);
      border:1px solid #E2E8F0;
      border-radius:4px;
      padding:2px 6px;
      white-space:nowrap;
      font-size:10px;
      font-weight:600;
      color:#1A202C;
      box-shadow:0 1px 3px rgba(0,0,0,0.1);
      pointer-events:none;
    ">
      ${name}${h1 != null ? `<span style="color:${color};margin-left:4px;">${h1.toFixed(1)}m</span>` : ''}
    </div>` : ''

  const html = `
    <div style="position:relative;width:${size}px;height:${size}px;">
      ${pulse}
      <div style="
        width:${size}px;height:${size}px;
        border-radius:50%;
        background:${color};
        border:2px solid white;
        box-shadow:0 1px 4px rgba(0,0,0,0.3);
        position:relative;z-index:1;
      "></div>
      ${label}
    </div>
    <style>
      @keyframes markerPulse {
        0%{transform:translate(-50%,-50%) scale(1);opacity:0.35}
        100%{transform:translate(-50%,-50%) scale(2.5);opacity:0}
      }
    </style>
  `

  const labelHeight = (zoomStage === 'detail' || zoomStage === 'timeline') ? 28 : 0

  return L.divIcon({
    html,
    className: 'flood-marker',
    iconSize: [size, size + labelHeight],
    iconAnchor: [size / 2, size / 2],
  })
}

export default function StationMarker({ station, zoomStage, isSelected, onClick }) {
  const { lat, lng, pin_status, name, predictions } = station
  const color = STATUS_COLOR[pin_status] ?? STATUS_COLOR.gray
  const isRed = pin_status === 'red'
  const h1 = predictions?.h1?.predicted
  const icon = createIcon(color, isSelected, isRed, zoomStage, name, h1)

  return (
    <Marker
      position={[lat, lng]}
      icon={icon}
      eventHandlers={{ click: onClick }}
      zIndexOffset={isSelected ? 1000 : 0}
    />
  )
}
