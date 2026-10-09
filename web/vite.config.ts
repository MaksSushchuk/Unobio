import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Python backend: `python -m app serve` in server/ (default port 8000)
    proxy: { '/api': { target: process.env.UNOBIO_API ?? 'http://127.0.0.1:8000', changeOrigin: true } },
  },
})
