import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // @aitt/map imports MapLibre's worker with '?url'; the dev pre-bundler can't
  // load that, so serve the package as source (the production build is fine).
  optimizeDeps: { exclude: ['@aitt/map'] },
  server: {
    port: 3000,
    strictPort: true,
    proxy: {
      // Frontend hits /api/* → backend (FastAPI) sees the path with /api stripped.
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
