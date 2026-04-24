import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { loadSharedWebConfig } from './sharedConfig'

const sharedConfig = loadSharedWebConfig()
const browserSharedConfig = {
  serverPublicBaseUrl: sharedConfig.serverPublicBaseUrl,
  serverWebSocketPublicUrl: sharedConfig.serverWebSocketPublicUrl,
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  define: {
    __WEB_SHARED_CONFIG__: JSON.stringify(browserSharedConfig),
  },
  server: {
    host: sharedConfig.webDevHost,
    port: sharedConfig.webDevPort,
    proxy: {
      '/api': {
        target: sharedConfig.serverPublicBaseUrl,
        changeOrigin: true,
      },
      '/health': {
        target: sharedConfig.serverPublicBaseUrl,
        changeOrigin: true,
      },
      '/ws': {
        target: sharedConfig.serverWebSocketPublicUrl,
        ws: true,
        changeOrigin: true,
      },
    },
  },
})

