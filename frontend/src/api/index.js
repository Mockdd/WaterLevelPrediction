import axios from 'axios'

// setupProxy.js가 localhost:3000 → ngrok으로 중계하므로 BASE_URL은 빈 문자열
const BASE_URL = ''
const headers = {}

export const getStationsWithStatus = () =>
  axios.get(`${BASE_URL}/stations/with-status`, { headers })

export const getPredictions = (stationId) =>
  axios.get(`${BASE_URL}/stations/${stationId}/predictions`, { headers })

export const getObservations = (stationId, hours = 24) =>
  axios.get(`${BASE_URL}/stations/${stationId}/observations?hours=${hours}`, { headers })

export const getAlerts = () =>
  axios.get(`${BASE_URL}/alerts`, { headers })

export const refreshData = () =>
  axios.post(`${BASE_URL}/admin/refresh`, {}, { timeout: 180000, headers })
