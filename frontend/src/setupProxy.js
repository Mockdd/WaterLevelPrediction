const { createProxyMiddleware } = require('http-proxy-middleware')
require('dotenv').config({ path: '.env.local' })

module.exports = function (app) {
  const target = process.env.REACT_APP_API_BASE_URL

  if (!target) {
    console.warn('[setupProxy] REACT_APP_API_BASE_URL가 설정되지 않았습니다.')
    return
  }

  const proxy = createProxyMiddleware({
    target,
    changeOrigin: true,
    headers: { 'ngrok-skip-browser-warning': 'true' },
  })

  app.use('/stations', proxy)
  app.use('/alerts', proxy)
  app.use('/admin', proxy)
}
