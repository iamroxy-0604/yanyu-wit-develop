import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 动态读取环境变量中的后端端口，默认为 7020
const BACKEND_PORT = process.env.WIT_PORT || '7020';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 7021,
    host: true,
    proxy: {
      '/api': {
        target: `http://localhost:${BACKEND_PORT}`,
        changeOrigin: true,
      },
      '/auth': {
        target: `http://localhost:${BACKEND_PORT}`,
        changeOrigin: true,
      },
      '/health': {
        target: `http://localhost:${BACKEND_PORT}`,
        changeOrigin: true,
      },
    },
  },
})
